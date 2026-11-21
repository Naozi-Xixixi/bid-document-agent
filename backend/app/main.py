import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.core.config import settings
from app.core.tenant_context import TenantContext, reset_current_tenant, set_current_tenant
from app.services import auth_service, vector_store
from app.services.project_activity import ProjectActivityMiddleware
from app.modules.knowledge import service as knowledge_service


async def _resume_pending_rag_tasks() -> None:
    """Restore knowledge jobs for every active company without blocking startup."""
    try:
        companies = await asyncio.to_thread(auth_service.list_companies)
    except Exception as exc:
        print(f"[KnowledgeRAG] startup recovery could not list companies: {exc}", flush=True)
        return

    existing_mysql_databases: set[str] | None = None
    if (settings.db_backend or "sqlite").strip().lower() == "mysql":
        try:
            from app.services.mysql_compat import existing_company_databases

            existing_mysql_databases = await asyncio.to_thread(existing_company_databases)
        except Exception as exc:
            print(f"[KnowledgeRAG] startup recovery could not inspect company databases: {exc}", flush=True)
            return

    for company in companies:
        if company.get("status") != "active":
            continue
        company_id = str(company.get("company_id") or "")
        if existing_mysql_databases is not None:
            from app.services.mysql_compat import database_name_for_company

            if database_name_for_company(company_id) not in existing_mysql_databases:
                continue
        elif not Path(company.get("database_path") or "").is_file():
            continue
        try:
            tenant = TenantContext(
                user_id=0,
                username="rag-recovery",
                role="admin",
                company_id=company_id,
                company_name=str(company["company_name"]),
                database_file=Path(company["database_path"]).resolve(),
                upload_dir=Path(company["upload_path"]).resolve(),
            )
            tenant_token = set_current_tenant(tenant)
            try:
                from app.modules.knowledge.recovery import resume_pending_tasks
                queued = await resume_pending_tasks()
                if queued:
                    print(
                        f"[KnowledgeRAG] startup recovery queued company={tenant.company_id} jobs={queued}",
                        flush=True,
                    )
            finally:
                reset_current_tenant(tenant_token)
        except Exception as exc:
            print(
                f"[KnowledgeRAG] startup recovery failed company={company.get('company_id')}: {exc}",
                flush=True,
            )
        await asyncio.sleep(0)


async def _refresh_tender_notices_periodically() -> None:
    """Refresh public notices for active companies while the backend runs."""
    from app.api.routes.tender_search import refresh_notices
    await asyncio.sleep(60)
    while True:
        try:
            companies = await asyncio.to_thread(auth_service.list_companies)
            mysql = (settings.db_backend or 'sqlite').strip().lower() == 'mysql'
            existing = None
            if mysql:
                from app.services.mysql_compat import existing_company_databases
                existing = await asyncio.to_thread(existing_company_databases)
            for company in companies:
                if company.get('status') != 'active':
                    continue
                company_id = str(company.get('company_id') or '')
                if mysql:
                    from app.services.mysql_compat import database_name_for_company
                    if database_name_for_company(company_id) not in existing:
                        continue
                elif not Path(company.get('database_path') or '').is_file():
                    continue
                tenant = TenantContext(user_id=0, username='notice-refresh', role='admin', company_id=company_id, company_name=str(company['company_name']), database_file=Path(company['database_path']).resolve(), upload_dir=Path(company['upload_path']).resolve())
                token = set_current_tenant(tenant)
                try:
                    await refresh_notices()
                except Exception as exc:
                    print(f'[TenderSearch] scheduled refresh failed: {type(exc).__name__}', flush=True)
                finally:
                    reset_current_tenant(token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f'[TenderSearch] scheduler failed: {type(exc).__name__}', flush=True)
        await asyncio.sleep(21600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize application-owned storage and release local vector locks."""
    knowledge_service.ensure_schema()
    print("[Knowledge] router ready: /api/knowledge diagnostics/categories/items/batch-import-local/vector/reindex", flush=True)
    recovery_task = asyncio.create_task(_resume_pending_rag_tasks())
    tender_task = asyncio.create_task(_refresh_tender_notices_periodically())
    try:
        yield
    finally:
        tender_task.cancel()
        await asyncio.gather(tender_task, return_exceptions=True)
        if not recovery_task.done():
            recovery_task.cancel()
            await asyncio.gather(recovery_task, return_exceptions=True)
        from app.modules.knowledge.recovery import shutdown
        await shutdown()
        vector_store.close()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-SmartArt-Export-Meta", "X-Word-Format-Meta"],
)


# Registered before authentication so tenant context is available inside it.
app.add_middleware(ProjectActivityMiddleware)

PUBLIC_PATHS = {
    "/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/health",
    "/api/version",
    "/api/auth/login",
}


def _is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/mermaid/")


def _request_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token
    return request.query_params.get("access_token") or request.cookies.get("wordagent_session") or None


@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    if request.method == "OPTIONS" or _is_public_path(request.url.path):
        return await call_next(request)

    token = _request_token(request)
    identity = await asyncio.to_thread(auth_service.authenticate_token, token)
    if identity is None:
        return JSONResponse(
            status_code=401,
            content={"code": 401, "message": "请先登录", "data": None},
        )

    user, company = identity
    tenant = auth_service.build_tenant_context(user, company)
    request.state.auth_token = token
    request.state.auth_user = user
    request.state.auth_company = company
    request.state.tenant_context = tenant
    tenant_token = set_current_tenant(tenant)
    try:
        return await call_next(request)
    finally:
        reset_current_tenant(tenant_token)

app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/uploads/{relative_path:path}", include_in_schema=False)
async def serve_upload(relative_path: str, request: Request):
    tenant = getattr(request.state, "tenant_context", None)
    if tenant is None:
        return JSONResponse(status_code=401, content={"code": 401, "message": "请先登录", "data": None})
    uploads_root = settings.resolve_local_path(settings.data_dir) / "uploads"
    requested = (uploads_root / relative_path).resolve()
    tenant_root = tenant.upload_dir.resolve()
    if tenant.company_id == "company_default":
        allowed = uploads_root.resolve()
        company_root = (uploads_root / "companies").resolve()
        permitted = requested != allowed and requested.is_relative_to(allowed) and not requested.is_relative_to(company_root)
    else:
        permitted = requested != tenant_root and requested.is_relative_to(tenant_root)
    if not permitted or not requested.is_file():
        return JSONResponse(status_code=404, content={"code": 404, "message": "资源不存在", "data": None})
    return FileResponse(requested, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})


def _resolve_mermaid_dist():
    candidates = []
    if settings.mermaid_dist_dir:
        candidates.append(settings.resolve_local_path(settings.mermaid_dist_dir))
    candidates.extend(
        [
            settings.backend_root.parent / "mermaid-develop" / "packages" / "mermaid" / "dist",
            settings.backend_root.parent.parent / "mermaid-develop" / "packages" / "mermaid" / "dist",
        ]
    )
    for path in candidates:
        if (path / "mermaid.min.js").exists() or (path / "mermaid.js").exists():
            return path
    return None


mermaid_dist = _resolve_mermaid_dist()
if mermaid_dist is not None:
    app.mount("/mermaid", StaticFiles(directory=mermaid_dist), name="mermaid")


@app.get("/")
async def root():
    return {"success": True, "message": settings.app_name, "api": settings.api_prefix}
