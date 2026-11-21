from __future__ import annotations

import json
from contextlib import aclosing
import urllib.parse
from typing import Callable

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.schemas.bid_content import ChapterContentSaveRequest, ChapterGenerateRequest, GenerateAllRequest
from app.services import bid_content_service


router = APIRouter(prefix="/bid")


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: bid_content_service.BidContentServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


def _run(action: Callable[[], object], message: str = "success") -> dict[str, object | None] | JSONResponse:
    try:
        return _ok(action(), message)
    except bid_content_service.BidContentServiceError as exc:
        return _error(exc)


def _sse(event: dict[str, object]) -> str:
    event_id = event.get("chunk_id")
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{id_line}event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def _docx_format_options(
    title_color: str | None = None,
    title_size: str | None = None,
    title_font: str | None = None,
    heading1_color: str | None = None,
    heading1_size: str | None = None,
    heading1_font: str | None = None,
    heading2_color: str | None = None,
    heading2_size: str | None = None,
    heading2_font: str | None = None,
    heading3_color: str | None = None,
    heading3_size: str | None = None,
    heading3_font: str | None = None,
    body_font: str | None = None,
    body_size: str | None = None,
) -> dict[str, str]:
    options = {
        "title_color": title_color,
        "title_size": title_size,
        "title_font": title_font,
        "heading1_color": heading1_color,
        "heading1_size": heading1_size,
        "heading1_font": heading1_font,
        "heading2_color": heading2_color,
        "heading2_size": heading2_size,
        "heading2_font": heading2_font,
        "heading3_color": heading3_color,
        "heading3_size": heading3_size,
        "heading3_font": heading3_font,
        "body_font": body_font,
        "body_size": body_size,
    }
    return {key: str(value) for key, value in options.items() if value is not None and str(value).strip()}


async def _encode_events(events):
    async with aclosing(events):
        async for event in events:
            yield _sse(event)


def _stream_response(events):
    return StreamingResponse(
        _encode_events(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/{bid_id}/step3/complete")
async def complete_bid_step3(bid_id: int):
    return _run(lambda: bid_content_service.complete_step3(bid_id), "已进入正文编写")


@router.get("/{bid_id}/content/info")
async def get_bid_content_info(bid_id: int, version_id: int | None = Query(default=None)):
    return _run(lambda: bid_content_service.get_content_info(bid_id, version_id))


@router.post("/{bid_id}/content/chapter/{chapter_id}/generate")
async def generate_bid_chapter_content(bid_id: int, chapter_id: int, payload: ChapterGenerateRequest | None = None):
    body = payload or ChapterGenerateRequest()

    return _stream_response(bid_content_service.generate_chapter_events(
            bid_id,
            chapter_id,
            version_id=body.version_id,
            style=body.style,
            rewrite=False,
        ))


@router.get("/{bid_id}/content/chapter/{chapter_id}/generate-stream")
async def stream_generate_bid_chapter_content(
    bid_id: int,
    chapter_id: int,
    version_id: int | None = Query(default=None),
    style: str | None = Query(default=None),
):
    return _stream_response(bid_content_service.generate_chapter_events(
            bid_id,
            chapter_id,
            version_id=version_id,
            style=style,
            rewrite=False,
        ))


@router.get("/{bid_id}/content/chapter/{chapter_id}")
async def get_bid_chapter_content(
    bid_id: int,
    chapter_id: int,
    version_id: int | None = Query(default=None),
):
    return _run(lambda: bid_content_service.get_chapter_content(bid_id, chapter_id, version_id))


@router.put("/{bid_id}/content/chapter/{chapter_id}")
async def save_bid_chapter_content(
    bid_id: int,
    chapter_id: int,
    payload: ChapterContentSaveRequest,
    version_id: int | None = Query(default=None),
):
    return _run(lambda: bid_content_service.save_chapter_content(bid_id, chapter_id, payload, version_id), "正文已保存")


@router.post("/{bid_id}/content/generate-all")
async def generate_all_bid_content(bid_id: int, payload: GenerateAllRequest | None = None):
    body = payload or GenerateAllRequest()

    return _stream_response(bid_content_service.generate_all_events(bid_id, version_id=body.version_id, style=body.style))


@router.get("/{bid_id}/content/generate-all-stream")
async def stream_generate_all_bid_content(
    bid_id: int,
    version_id: int | None = Query(default=None),
    style: str | None = Query(default=None),
):
    return _stream_response(bid_content_service.generate_all_events(bid_id, version_id=version_id, style=style))


@router.post("/{bid_id}/content/chapter/{chapter_id}/rewrite")
async def rewrite_bid_chapter_content(bid_id: int, chapter_id: int, payload: ChapterGenerateRequest | None = None):
    body = payload or ChapterGenerateRequest()

    return _stream_response(bid_content_service.generate_chapter_events(
            bid_id,
            chapter_id,
            version_id=body.version_id,
            style=body.style,
            rewrite=True,
        ))


@router.get("/{bid_id}/content/chapter/{chapter_id}/rewrite-stream")
async def stream_rewrite_bid_chapter_content(
    bid_id: int,
    chapter_id: int,
    version_id: int | None = Query(default=None),
    style: str | None = Query(default=None),
):
    return _stream_response(bid_content_service.generate_chapter_events(
            bid_id,
            chapter_id,
            version_id=version_id,
            style=style,
            rewrite=True,
        ))


@router.get("/{bid_id}/content/download")
async def download_bid_content(
    bid_id: int,
    version_id: int | None = Query(default=None),
    title_color: str | None = Query(default=None),
    title_size: str | None = Query(default=None),
    title_font: str | None = Query(default=None),
    heading1_color: str | None = Query(default=None),
    heading1_size: str | None = Query(default=None),
    heading1_font: str | None = Query(default=None),
    heading2_color: str | None = Query(default=None),
    heading2_size: str | None = Query(default=None),
    heading2_font: str | None = Query(default=None),
    heading3_color: str | None = Query(default=None),
    heading3_size: str | None = Query(default=None),
    heading3_font: str | None = Query(default=None),
    body_font: str | None = Query(default=None),
    body_size: str | None = Query(default=None),
):
    try:
        content, filename = bid_content_service.content_docx_bytes(
            bid_id,
            version_id,
            _docx_format_options(
                title_color,
                title_size,
                title_font,
                heading1_color,
                heading1_size,
                heading1_font,
                heading2_color,
                heading2_size,
                heading2_font,
                heading3_color,
                heading3_size,
                heading3_font,
                body_font,
                body_size,
            ),
        )
        quoted = urllib.parse.quote(filename)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
        )
    except bid_content_service.BidContentServiceError as exc:
        return _error(exc)


@router.get("/{bid_id}/content/chapter/{chapter_id}/download")
async def download_bid_chapter_content(
    bid_id: int,
    chapter_id: int,
    version_id: int | None = Query(default=None),
    title_color: str | None = Query(default=None),
    title_size: str | None = Query(default=None),
    title_font: str | None = Query(default=None),
    heading1_color: str | None = Query(default=None),
    heading1_size: str | None = Query(default=None),
    heading1_font: str | None = Query(default=None),
    heading2_color: str | None = Query(default=None),
    heading2_size: str | None = Query(default=None),
    heading2_font: str | None = Query(default=None),
    heading3_color: str | None = Query(default=None),
    heading3_size: str | None = Query(default=None),
    heading3_font: str | None = Query(default=None),
    body_font: str | None = Query(default=None),
    body_size: str | None = Query(default=None),
):
    try:
        content, filename = bid_content_service.chapter_docx_bytes(
            bid_id,
            chapter_id,
            version_id,
            _docx_format_options(
                title_color,
                title_size,
                title_font,
                heading1_color,
                heading1_size,
                heading1_font,
                heading2_color,
                heading2_size,
                heading2_font,
                heading3_color,
                heading3_size,
                heading3_font,
                body_font,
                body_size,
            ),
        )
        quoted = urllib.parse.quote(filename)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
        )
    except bid_content_service.BidContentServiceError as exc:
        return _error(exc)
