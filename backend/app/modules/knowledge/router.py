from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core.tenant_context import get_current_tenant

from . import service
from .schemas import KnowledgeAssetAnnotationUpdate, KnowledgeAssetBatchTagRequest, KnowledgeAssetDimensionsUpdate, KnowledgeAssetMetadataUpdate, KnowledgeAssetReextractBatchRequest, KnowledgeBatchRequest, KnowledgeCategoryCreate, KnowledgeCategoryUpdate, KnowledgeDeepExtractBatchRequest, KnowledgeEvidenceReview, KnowledgeItemUpdate, KnowledgeLocalImportRequest, KnowledgeProjectMatchRequest, KnowledgeProjectRenameRequest, KnowledgeProjectReuseRequest, KnowledgeSearchRequest


router = APIRouter(prefix="/knowledge")


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: service.KnowledgeModuleError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message, "data": None})


async def init_knowledge_schema() -> None:
    """Compatibility helper for callers that initialize the knowledge schema manually."""
    service.ensure_schema()
    print("[Knowledge] router ready: /api/knowledge diagnostics/categories/items/batch-import-local/vector/reindex", flush=True)


@router.get("/diagnostics")
async def diagnostics():
    return _ok(service.diagnostics())


@router.post("/vector/reindex")
async def rebuild_vector_index():
    try:
        return _ok(service.rebuild_vector_index(), "Vector index rebuilt")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/categories")
async def list_categories():
    return _ok(service.list_categories())


@router.post("/categories")
async def create_category(payload: KnowledgeCategoryCreate):
    try:
        return _ok(service.create_category(payload), "category created")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.put("/categories/{category_id}")
async def update_category(category_id: int, payload: KnowledgeCategoryUpdate):
    try:
        return _ok(service.update_category(category_id, payload), "category updated")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int):
    try:
        service.delete_category(category_id)
        return _ok(None, "category deleted")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/items/upload")
async def upload_items(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    category_id: int | None = Form(default=None),
    tags: str = Form(default=""),
    valid_until: str | None = Form(default=None),
):
    try:
        result = await service.upload_items(files, category_id=category_id, tags=tags, valid_until=valid_until)
        for item in result["items"]:
            background_tasks.add_task(service.process_item, int(item["item_id"]))
        message = "files uploaded and processing started" if result["items"] else "no files uploaded"
        return _ok(result, message)
    except service.KnowledgeModuleError as exc:
        return _error(exc)



@router.post("/evidence/upload")
async def upload_evidence(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    evidence_kind: str = Form(...),
):
    try:
        result = await service.upload_items(files, category_id=None, evidence_kind=evidence_kind)
        for item in result["items"]:
            background_tasks.add_task(service.process_item, int(item["item_id"]))
        return _ok(result)
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.put("/evidence/{item_id}/review")
def review_evidence(item_id: int, payload: KnowledgeEvidenceReview):
    try:
        return _ok(service.review_evidence_item(item_id, payload))
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/projects/upload")
async def upload_project(
    background_tasks: BackgroundTasks,
    project_name: str = Form(default=""),
    tender_file: UploadFile = File(...),
    bid_file: UploadFile = File(...),
):
    try:
        result = await service.upload_project(project_name, tender_file, bid_file)
        background_tasks.add_task(
            service.process_project_upload_tasks,
            int(result["project_id"]),
            int(result["tender_doc_id"]),
            int(result["bid_doc_id"]),
        )
        return _ok(result, "project files uploaded and processing started")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/projects")
async def list_knowledge_projects(limit: int = Query(default=200, ge=1, le=500)):
    return _ok(await run_in_threadpool(service.list_projects, limit))


@router.get("/projects/{project_id}")
async def get_knowledge_project(project_id: int):
    try:
        return _ok(service.get_project(project_id))
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/projects/{project_id}/interpret")
async def resume_project_interpretation(project_id: int, background_tasks: BackgroundTasks):
    """Retry only unfinished AI batches; completed source fingerprints are reused."""
    from . import recovery
    from app.services import storage
    try:
        project = await run_in_threadpool(service.get_project, project_id)
        if project["tender_status"] != "completed" or project["bid_status"] != "completed":
            raise service.KnowledgeModuleError(40951, "请先完成两个文件的基础解析", 409)
        state = recovery.workflow_state(project_id)
        if state and state["status"] in {"pending", "processing"}:
            return _ok(project, "解读已在处理中")
        recovery.prepare_project(project_id)
        with storage.connect() as conn:
            conn.execute(
                "UPDATE knowledge_project_workflow SET status='pending',phase='interpret',error=NULL,updated_at=? WHERE project_id=?",
                (service._now(), project_id),
            )
            conn.commit()
        background_tasks.add_task(
            service.process_project_upload_tasks, project_id, project["tender_doc_id"], project["bid_doc_id"],
        )
        return _ok(await run_in_threadpool(service.get_project, project_id), "已继续解读，将复用已完成批次")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/projects/{project_id}/template/extract")
async def extract_knowledge_project_template(project_id: int, background_tasks: BackgroundTasks):
    try:
        service.mark_project_template_pending(project_id)
        background_tasks.add_task(service.extract_project_template, project_id)
        return _ok(service.get_project(project_id), "format template extraction started")
    except service.KnowledgeModuleError as exc:
        return _error(exc)
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"code": 40452, "message": "项目不存在", "data": None})


@router.get("/projects/{project_id}/template")
async def get_knowledge_project_template(project_id: int):
    try:
        return _ok(service.get_project_template(project_id))
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/projects/{project_id}/template/download")
async def download_knowledge_project_template(project_id: int):
    try:
        path, filename = service.project_template_download_path(project_id)
        return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=filename)
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/projects/{project_id}/template/activate")
async def activate_knowledge_project_template(project_id: int):
    try:
        return _ok(service.activate_project_template(project_id), "format template activated")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.put("/projects/{project_id}")
async def rename_knowledge_project(project_id: int, payload: KnowledgeProjectRenameRequest):
    try:
        return _ok(service.rename_project(project_id, payload.project_name), "project renamed")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.delete("/projects/{project_id}")
async def delete_knowledge_project(project_id: int):
    try:
        service.delete_project(project_id)
        return _ok(None, "project deleted")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/projects/{project_id}/match")
async def match_knowledge_project(project_id: int, payload: KnowledgeProjectMatchRequest | None = None):
    payload = payload or KnowledgeProjectMatchRequest()
    try:
        return _ok(service.match_project(project_id, min_score=payload.min_score, limit=payload.limit), "requirement-solution matching finished")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/projects/{project_id}/reuse")
async def reuse_knowledge_project(project_id: int, payload: KnowledgeProjectReuseRequest | None = None):
    payload = payload or KnowledgeProjectReuseRequest()
    try:
        return _ok(service.reuse_project_solutions(project_id, target_bid_id=payload.target_bid_id, target_project_id=payload.target_project_id, map_ids=payload.map_ids), "solutions prepared for reuse")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/projects/{project_id}/export")
async def export_knowledge_project(project_id: int):
    try:
        return _ok(service.get_project(project_id), "project mapping exported")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/batch-import-local")
async def batch_import_local(background_tasks: BackgroundTasks, request: Request, payload: KnowledgeLocalImportRequest | None = None):
    try:
        tenant = get_current_tenant()
        requested_dir = payload.dir_path if payload else None
        if tenant is not None and tenant.role != "admin":
            allowed_root = tenant.upload_dir.resolve()
            requested_root = Path(requested_dir).expanduser().resolve() if requested_dir else allowed_root
            if requested_root != allowed_root and allowed_root not in requested_root.parents:
                raise service.KnowledgeModuleError(40341, "只能导入当前公司的上传目录", 403)
            requested_dir = str(requested_root)
        result = service.batch_import_local_dir(requested_dir)
        background_tasks.add_task(service.run_batch_import_task, result["task_id"])
        return _ok(result, "batch import task started")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/batch-import/{task_id}/status")
async def batch_import_status(task_id: str):
    try:
        return _ok(service.get_batch_import_status(task_id))
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/batch-import/{task_id}/retry-failed")
async def retry_batch_import_failed(task_id: str, background_tasks: BackgroundTasks):
    try:
        result = service.retry_failed_import(task_id)
        if result["status"] == "processing":
            background_tasks.add_task(service.run_batch_import_task, task_id)
        return _ok(result, "failed files queued for retry")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/items")
async def list_items(
    category_id: int | None = Query(default=None),
    keyword: str = Query(default=""),
    status: str = Query(default=""),
    file_type: str = Query(default=""),
    bid_type: str = Query(default=""),
    bid_scenario: str = Query(default=""),
    length_level: str = Query(default=""),
    include_expired: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
):
    return _ok(await run_in_threadpool(service.list_items, category_id=category_id, keyword=keyword, status=status, file_type=file_type, bid_type=bid_type, bid_scenario=bid_scenario, length_level=length_level, include_expired=include_expired, limit=limit))


@router.get("/assets")
async def list_assets(
    category_id: int | None = Query(default=None),
    keyword: str = Query(default=""),
    image_category: str = Query(default=""),
    employee_role: str = Query(default=""),
    archive_year: str = Query(default=""),
    bid_type: str = Query(default=""),
    bid_scenario: str = Query(default=""),
    length_level: str = Query(default=""),
    asset_sub_type: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
):
    return _ok(service.list_assets(category_id=category_id, keyword=keyword, image_category=image_category, employee_role=employee_role, archive_year=archive_year, bid_type=bid_type, bid_scenario=bid_scenario, length_level=length_level, asset_sub_type=asset_sub_type, limit=limit))


@router.get("/dimensions")
async def knowledge_dimensions():
    return _ok(service.get_dimensions())


@router.post("/assets/batch-tag")
async def batch_tag_assets(payload: KnowledgeAssetBatchTagRequest):
    try:
        return _ok(service.batch_tag_assets(payload), "asset dimensions tagged")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.put("/{asset_id}/dimensions")
async def update_asset_dimensions(asset_id: int, payload: KnowledgeAssetDimensionsUpdate):
    try:
        return _ok(service.update_asset_dimensions(asset_id, payload), "asset dimensions saved")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/reextract/batch")
async def reextract_assets_batch(payload: KnowledgeAssetReextractBatchRequest):
    try:
        result = await service.reextract_assets_batch(
            item_ids=payload.item_ids,
            asset_ids=payload.asset_ids,
            asset_sub_type=payload.asset_sub_type,
            only_missing=payload.only_missing,
            limit=payload.limit,
        )
        return _ok(result, "asset structured extraction finished")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/assets/reextract-batch")
async def reextract_assets_batch_alias(payload: KnowledgeAssetReextractBatchRequest):
    return await reextract_assets_batch(payload)


@router.post("/assets/reidentify-personnel")
async def reidentify_personnel_assets(payload: KnowledgeAssetReextractBatchRequest | None = None):
    try:
        result = await service.reidentify_personnel_assets(limit=(payload.limit if payload else 2000))
        return _ok(result, "personnel certificate re-identification finished")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: int):
    try:
        return _ok(service.get_asset(asset_id))
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/{item_id}/reextract")
async def reextract_item_assets(item_id: int, payload: KnowledgeAssetReextractBatchRequest | None = None):
    payload = payload or KnowledgeAssetReextractBatchRequest()
    try:
        result = await service.reextract_item_assets(
            item_id,
            asset_ids=payload.asset_ids,
            asset_sub_type=payload.asset_sub_type,
            only_missing=payload.only_missing,
            limit=payload.limit,
        )
        return _ok(result, "item asset structured extraction finished")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.put("/assets/{asset_id}/annotation")
async def update_asset_annotation(asset_id: int, payload: KnowledgeAssetAnnotationUpdate):
    try:
        return _ok(service.update_asset_annotation(asset_id, payload.annotation), "asset annotation saved")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.put("/assets/{asset_id}/metadata")
async def update_asset_metadata(asset_id: int, payload: KnowledgeAssetMetadataUpdate):
    try:
        return _ok(service.update_asset_metadata(asset_id, bid_type=payload.bid_type, length_level=payload.length_level, extend_tags=payload.extend_tags), "asset metadata saved")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/assets/regenerate-annotations")
async def regenerate_asset_annotations(request: Request):
    try:
        payload = await _safe_json_payload(request)
        asset_ids = _coerce_asset_ids(payload.get("asset_ids") or payload.get("assetIds"))
        only_missing = _coerce_bool(payload.get("only_missing", payload.get("onlyMissing", True)), default=True)
        limit = _coerce_int(payload.get("limit", 120), default=120, minimum=1, maximum=1000)
        return _ok(await service.regenerate_asset_annotations(asset_ids, only_missing=only_missing, limit=limit), "asset annotations regenerated")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


async def _safe_json_payload(request: Request) -> dict[str, object]:
    try:
        value = await request.json()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _coerce_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _coerce_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _coerce_asset_ids(value: object) -> list[int] | None:
    if not isinstance(value, list):
        return None
    ids: list[int] = []
    for item in value:
        try:
            asset_id = int(item)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if asset_id > 0:
            ids.append(asset_id)
    return ids or None


@router.post("/assets/cleanup")
async def cleanup_assets():
    try:
        return _ok(service.cleanup_invalid_assets(), "duplicate and invalid assets cleaned")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/items/{item_id}")
async def get_item(item_id: int):
    try:
        return _ok(await run_in_threadpool(service.get_item, item_id))
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/items/deep-extract/batch")
async def deep_extract_items_batch(payload: KnowledgeDeepExtractBatchRequest):
    try:
        return _ok(service.queue_deep_extract_batch(payload.item_ids), "deep extraction queued")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/items/{item_id}/deep-extract")
async def deep_extract_item(item_id: int):
    try:
        return _ok(service.queue_deep_extract(item_id), "deep extraction queued")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.put("/items/{item_id}")
async def update_item(item_id: int, payload: KnowledgeItemUpdate):
    try:
        return _ok(service.update_item(item_id, payload), "knowledge item updated")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/items/{item_id}/retry")
async def retry_item(item_id: int, background_tasks: BackgroundTasks):
    try:
        item = service.retry_item(item_id)
        background_tasks.add_task(service.process_item, item_id)
        return _ok(item, "knowledge item queued for retry")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.delete("/items/{item_id}")
async def delete_item(item_id: int):
    try:
        service.delete_item(item_id)
        return _ok(None, "knowledge item deleted")
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/items/{item_id}/status")
async def item_status(item_id: int):
    try:
        return _ok(await run_in_threadpool(service.item_status, item_id))
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/items/{item_id}/versions")
async def version_history(item_id: int):
    try:
        return _ok(service.version_history(item_id))
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.post("/search")
async def search(payload: KnowledgeSearchRequest):
    try:
        return _ok(await run_in_threadpool(service.search, payload))
    except service.KnowledgeModuleError as exc:
        return _error(exc)


@router.get("/graph")
async def graph(category_id: int | None = Query(default=None)):
    return _ok(service.graph(category_id=category_id))


@router.post("/batch")
async def batch(payload: KnowledgeBatchRequest):
    try:
        return _ok(service.batch(payload), "batch operation finished")
    except service.KnowledgeModuleError as exc:
        return _error(exc)
