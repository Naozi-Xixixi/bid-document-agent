from __future__ import annotations

import json
import urllib.parse

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.schemas.audit import AuditStartRequest
from app.services import audit_service


router = APIRouter(prefix="/audit")


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: audit_service.AuditServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


def _sse(event: dict[str, object]) -> str:
    event_type = str(event.get("type") or "message")
    return f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/upload-tender")
async def upload_tender(file: UploadFile = File(...), audit_id: int | None = Form(default=None)):
    try:
        data = await audit_service.upload_document("tender", file, audit_id)
        return _ok(data, "招标文件已上传")
    except audit_service.AuditServiceError as exc:
        return _error(exc)


@router.post("/upload-bid")
async def upload_bid(file: UploadFile = File(...), audit_id: int | None = Form(default=None)):
    try:
        data = await audit_service.upload_document("bid", file, audit_id)
        return _ok(data, "投标文件已上传")
    except audit_service.AuditServiceError as exc:
        return _error(exc)


@router.post("/start")
async def start_audit(payload: AuditStartRequest):
    try:
        audit_service.validate_audit_ready(payload.audit_id)
    except audit_service.AuditServiceError as exc:
        return _error(exc)

    async def event_stream():
        try:
            async for event in audit_service.run_audit_stream(payload.audit_id, payload.checks):
                yield _sse(event)
        except audit_service.AuditServiceError as exc:
            yield _sse({"type": "server_error", "message": exc.message})
        except Exception as exc:  # pragma: no cover - defensive SSE surface
            yield _sse({"type": "server_error", "message": f"审核失败：{exc}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{audit_id}/result")
async def get_audit_result(audit_id: int):
    try:
        return _ok(audit_service.get_result(audit_id))
    except audit_service.AuditServiceError as exc:
        return _error(exc)


@router.get("/{audit_id}/export")
async def export_audit_report(audit_id: int):
    try:
        content, filename = audit_service.export_markdown(audit_id)
        quoted = urllib.parse.quote(filename)
        return Response(
            content=content.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
        )
    except audit_service.AuditServiceError as exc:
        return _error(exc)
