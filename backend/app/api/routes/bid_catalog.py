from __future__ import annotations

import urllib.parse
import json
from typing import Callable

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.schemas.bid_catalog import (
    CatalogChapterActionRequest,
    CatalogChapterCreateRequest,
    CatalogChapterMoveRequest,
    CatalogChapterUpdateRequest,
    CatalogRegenerateRequest,
    CatalogSetFinalRequest,
    CatalogVersionTreeUpdateRequest,
    CatalogWordCountUpdateRequest,
)
from app.services import bid_catalog_service, bid_word_count_service


router = APIRouter(prefix="/bid")


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: bid_catalog_service.BidCatalogServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


def _word_count_error(exc: bid_word_count_service.BidWordCountServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


def _run(action: Callable[[], object], message: str = "success") -> dict[str, object | None] | JSONResponse:
    try:
        return _ok(action(), message)
    except bid_catalog_service.BidCatalogServiceError as exc:
        return _error(exc)
    except bid_word_count_service.BidWordCountServiceError as exc:
        return _word_count_error(exc)


def _sse(event: dict[str, object]) -> str:
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/{bid_id}/catalog/generate")
async def generate_bid_catalog(bid_id: int, background_tasks: BackgroundTasks):
    try:
        result = bid_catalog_service.start_catalog_generation(bid_id, force=False)
        if result.get("newly_created") and result.get("task_id"):
            background_tasks.add_task(bid_catalog_service.run_catalog_generation_task, bid_id, str(result["task_id"]), None)
            return _ok(result, "目录生成已启动")
        return _ok(result, "目录生成中" if result.get("status") == "processing" else "目录已生成")
    except bid_catalog_service.BidCatalogServiceError as exc:
        return _error(exc)


@router.get("/{bid_id}/catalog/generate-stream")
async def stream_generate_bid_catalog(
    bid_id: int,
    force: bool = Query(default=False),
    style: str | None = Query(default=None),
):
    async def event_stream():
        stream = bid_catalog_service.stream_catalog_generation_events(bid_id, force=force, style=style)
        try:
            async for event in stream:
                yield _sse(event)
        finally:
            await stream.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{bid_id}/catalog")
async def get_bid_catalog(bid_id: int, version_id: int | None = Query(default=None)):
    return _run(lambda: bid_catalog_service.get_catalog(bid_id, version_id))


@router.get("/{bid_id}/catalog/word-count")
async def get_bid_catalog_word_count(bid_id: int, version_id: int | None = Query(default=None)):
    return _run(lambda: bid_word_count_service.get_word_count_summary(bid_id, version_id))


@router.put("/{bid_id}/catalog/chapter/{chapter_id}/word-count")
async def update_bid_catalog_chapter_word_count(bid_id: int, chapter_id: int, payload: CatalogWordCountUpdateRequest):
    return _run(lambda: bid_word_count_service.update_chapter_target_word_count(bid_id, chapter_id, payload.target_word_count), "目标字数已保存")


@router.post("/{bid_id}/catalog/word-count/recalculate")
async def recalculate_bid_catalog_word_count(bid_id: int, version_id: int | None = Query(default=None)):
    return _run(lambda: bid_word_count_service.calculate_and_assign_word_count(bid_id, version_id, preserve_manual=True), "目标字数已重新分配")


@router.put("/{bid_id}/catalog/chapter/{chapter_id}")
async def update_bid_catalog_chapter(bid_id: int, chapter_id: int, payload: CatalogChapterUpdateRequest):
    return _run(lambda: bid_catalog_service.update_chapter_title(bid_id, chapter_id, payload), "章节已保存")


@router.post("/{bid_id}/catalog/chapter")
async def create_bid_catalog_chapter(bid_id: int, payload: CatalogChapterCreateRequest):
    return _run(lambda: bid_catalog_service.create_chapter(bid_id, payload), "章节已新增")


@router.delete("/{bid_id}/catalog/chapter/{chapter_id}")
async def delete_bid_catalog_chapter(bid_id: int, chapter_id: int):
    return _run(lambda: bid_catalog_service.delete_chapter(bid_id, chapter_id), "章节已删除")


@router.put("/{bid_id}/catalog/chapter/{chapter_id}/move")
async def move_bid_catalog_chapter(bid_id: int, chapter_id: int, payload: CatalogChapterMoveRequest):
    return _run(lambda: bid_catalog_service.move_chapter(bid_id, chapter_id, payload), "章节已调整")


@router.post("/{bid_id}/catalog/set-final")
async def set_bid_catalog_final_version(bid_id: int, payload: CatalogSetFinalRequest):
    return _run(lambda: bid_catalog_service.set_final_version(bid_id, payload.version_id), "最终目录版本已设置")


@router.put("/{bid_id}/catalog/version/{version_id}/tree")
async def replace_bid_catalog_version_tree(bid_id: int, version_id: int, payload: CatalogVersionTreeUpdateRequest):
    return _run(lambda: bid_catalog_service.replace_version_tree(bid_id, version_id, payload.catalog_content), "目录已恢复")


@router.post("/{bid_id}/catalog/chapter/{chapter_id}/expand")
async def expand_bid_catalog_chapter(
    bid_id: int,
    chapter_id: int,
    payload: CatalogChapterActionRequest | None = None,
):
    try:
        result = await bid_catalog_service.expand_chapter(bid_id, chapter_id, payload or CatalogChapterActionRequest())
        return _ok(result, "章节已扩写")
    except bid_catalog_service.BidCatalogServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/catalog/chapter/{chapter_id}/optimize")
async def optimize_bid_catalog_chapter(
    bid_id: int,
    chapter_id: int,
    payload: CatalogChapterActionRequest | None = None,
):
    try:
        result = await bid_catalog_service.optimize_chapter(bid_id, chapter_id, payload or CatalogChapterActionRequest())
        return _ok(result, "章节已优化")
    except bid_catalog_service.BidCatalogServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/catalog/regenerate")
async def regenerate_bid_catalog(
    bid_id: int,
    background_tasks: BackgroundTasks,
    payload: CatalogRegenerateRequest | None = None,
):
    try:
        style = payload.style if payload else None
        result = bid_catalog_service.start_catalog_generation(bid_id, force=True, style=style)
        if result.get("newly_created") and result.get("task_id"):
            background_tasks.add_task(bid_catalog_service.run_catalog_generation_task, bid_id, str(result["task_id"]), style)
        return _ok(result, "目录重新生成已启动")
    except bid_catalog_service.BidCatalogServiceError as exc:
        return _error(exc)


@router.get("/{bid_id}/catalog/download")
async def download_bid_catalog(bid_id: int, version_id: int = Query(...)):
    try:
        content, filename = bid_catalog_service.catalog_docx_bytes(bid_id, version_id)
        quoted = urllib.parse.quote(filename)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
        )
    except bid_catalog_service.BidCatalogServiceError as exc:
        return _error(exc)
