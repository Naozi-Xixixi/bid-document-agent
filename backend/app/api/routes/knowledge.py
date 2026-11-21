from __future__ import annotations

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse

from app.schemas.knowledge import KnowledgeItemUpdateRequest, KnowledgeReuseRequest
from app.services import knowledge_service


router = APIRouter(prefix="/knowledge-items")


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: knowledge_service.KnowledgeServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message, "data": None})


@router.get("")
async def search_knowledge_items(
    q: str = Query(default=""),
    industry: str = Query(default=""),
    year: int | None = Query(default=None),
    type: str = Query(default=""),
    include_expired: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
):
    return _ok(
        knowledge_service.search_items(
            q=q,
            industry=industry,
            year=year,
            item_type=type,
            include_expired=include_expired,
            limit=limit,
        )
    )


@router.post("")
async def ingest_knowledge_items(
    files: list[UploadFile] = File(...),
    type: str = Form(default="proposal"),
    industry: str = Form(default=""),
    region: str = Form(default=""),
    year: int | None = Form(default=None),
    project_type: str = Form(default=""),
    tags: str = Form(default=""),
    valid_until: str | None = Form(default=None),
):
    try:
        result = await knowledge_service.ingest_items(
            files,
            item_type=type,
            industry=industry,
            region=region,
            year=year,
            project_type=project_type,
            tags=tags,
            valid_until=valid_until,
        )
        status_code_message = "入库完成" if result["failure_count"] == 0 else "部分入库完成"
        return _ok(result, status_code_message)
    except knowledge_service.KnowledgeServiceError as exc:
        return _error(exc)


@router.get("/{item_id}")
async def get_knowledge_item(item_id: int):
    try:
        return _ok(knowledge_service.get_item(item_id))
    except knowledge_service.KnowledgeServiceError as exc:
        return _error(exc)


@router.put("/{item_id}")
async def update_knowledge_item(item_id: int, payload: KnowledgeItemUpdateRequest):
    try:
        return _ok(knowledge_service.update_item(item_id, payload), "知识条目已更新")
    except knowledge_service.KnowledgeServiceError as exc:
        return _error(exc)


@router.delete("/{item_id}")
async def delete_knowledge_item(item_id: int):
    try:
        knowledge_service.delete_item(item_id)
        return _ok(None, "知识条目已删除")
    except knowledge_service.KnowledgeServiceError as exc:
        return _error(exc)


@router.post("/{item_id}/reuse")
async def reuse_knowledge_item(item_id: int, payload: KnowledgeReuseRequest):
    try:
        return _ok(knowledge_service.reuse_item(item_id, payload), "已复用到目标章节")
    except knowledge_service.KnowledgeServiceError as exc:
        return _error(exc)

