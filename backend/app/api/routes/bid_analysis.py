from __future__ import annotations

import json
import urllib.parse

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.schemas.bid_analysis import AnalysisResolveRequest, AnalysisStartRequest
from app.services import bid_analysis_service


router = APIRouter(prefix="/bid")
standalone_router = APIRouter(prefix="/analysis")


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: bid_analysis_service.BidAnalysisServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message, "data": None})


def _sse(event: dict[str, object]) -> str:
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@standalone_router.post("/prepare")
async def prepare_analysis_files(
    tender_file: UploadFile = File(...),
    bid_file: UploadFile = File(...),
):
    try:
        return _ok(await bid_analysis_service.prepare_standalone_analysis(tender_file, bid_file), "文件解析完成")
    except bid_analysis_service.BidAnalysisServiceError as exc:
        return _error(exc)


@standalone_router.get("/{bid_id}/source")
async def get_analysis_source(bid_id: int):
    return _ok(bid_analysis_service.get_analysis_source(bid_id))


@router.post("/{bid_id}/analysis/start")
async def start_bid_analysis(bid_id: int, payload: AnalysisStartRequest):
    try:
        bid_analysis_service.validate_check_items(payload.check_items)
        bid_analysis_service._ensure_prerequisites(bid_id)
    except bid_analysis_service.BidAnalysisServiceError as exc:
        return _error(exc)

    async def event_stream():
        try:
            async for event in bid_analysis_service.stream_analysis(bid_id, payload.check_items):
                yield _sse(event)
        except bid_analysis_service.BidAnalysisServiceError as exc:
            yield _sse({"type": "error", "message": exc.message, "code": exc.code})
        except Exception:
            yield _sse({"type": "error", "message": "标书检查执行异常，请稍后重新检测"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{bid_id}/analysis/result")
async def get_bid_analysis_result(bid_id: int):
    try:
        return _ok(bid_analysis_service.get_results(bid_id))
    except bid_analysis_service.BidAnalysisServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/analysis/export")
async def export_bid_analysis(bid_id: int):
    try:
        report, filename = bid_analysis_service.export_report(bid_id)
        quoted = urllib.parse.quote(filename)
        return Response(
            content=report.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
        )
    except bid_analysis_service.BidAnalysisServiceError as exc:
        return _error(exc)


@router.put("/{bid_id}/analysis/item/{result_id}/resolve")
async def resolve_bid_analysis_item(bid_id: int, result_id: int, payload: AnalysisResolveRequest):
    try:
        return _ok(bid_analysis_service.resolve_result(bid_id, result_id, payload.resolved), "整改状态已更新")
    except bid_analysis_service.BidAnalysisServiceError as exc:
        return _error(exc)
