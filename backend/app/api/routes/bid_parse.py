from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse

from app.schemas.bid_parse import BidParseInfoUpdateRequest, MaterialType, ParseResultEditRequest, PasteParseContentRequest
from app.services import bid_parse_service


router = APIRouter(prefix="/bid")


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: bid_parse_service.BidParseServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


def _run(action: Callable[[], object]) -> dict[str, object | None] | JSONResponse:
    try:
        return _ok(action())
    except bid_parse_service.BidParseServiceError as exc:
        return _error(exc)


@router.get("/{bid_id}/parse-info")
async def get_bid_parse_info(bid_id: int):
    return _run(lambda: bid_parse_service.get_parse_info(bid_id))


@router.put("/{bid_id}/parse-info")
async def save_bid_parse_info(bid_id: int, payload: BidParseInfoUpdateRequest):
    return _run(lambda: bid_parse_service.save_parse_info(bid_id, payload))


@router.post("/{bid_id}/upload-tender-file")
async def upload_tender_file(bid_id: int, file: UploadFile = File(...)):
    try:
        return _ok(await bid_parse_service.upload_tender_file(bid_id, file))
    except bid_parse_service.BidParseServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/upload-and-parse")
async def upload_and_parse_tender_file(
    bid_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    try:
        result = await bid_parse_service.upload_and_start_parse(bid_id, file)
        background_tasks.add_task(bid_parse_service.run_auto_parse_task, bid_id, result["file_path"])
        return _ok(result, "上传完成，解析已启动")
    except bid_parse_service.BidParseServiceError as exc:
        return _error(exc)


@router.get("/{bid_id}/parse-status")
def get_parse_status(bid_id: int):
    return _run(lambda: bid_parse_service.get_parse_status(bid_id))


@router.get("/{bid_id}/parse-detail")
async def get_parse_detail(bid_id: int):
    return _run(lambda: bid_parse_service.get_parse_detail(bid_id))


@router.get("/{bid_id}/duplicate-check")
async def duplicate_check(
    bid_id: int,
    tender_file_name: str = Query(...),
    tender_file_size: int | None = Query(default=None),
):
    return _run(lambda: bid_parse_service.find_duplicate_parse_file(bid_id, tender_file_name, tender_file_size))


@router.post("/{bid_id}/merge-parse/{source_bid_id}")
async def merge_duplicate_parse(bid_id: int, source_bid_id: int):
    return _run(lambda: bid_parse_service.merge_duplicate_parse(bid_id, source_bid_id))


@router.put("/{bid_id}/parse-result/edit")
async def save_parse_result_edit(bid_id: int, payload: ParseResultEditRequest):
    return _run(lambda: bid_parse_service.save_parse_result_edit(bid_id, payload))


@router.post("/{bid_id}/reparse")
async def reparse_bid_file(bid_id: int, background_tasks: BackgroundTasks):
    try:
        result = bid_parse_service.prepare_reparse(bid_id)
        background_tasks.add_task(bid_parse_service.run_auto_parse_task, bid_id, result["file_path"])
        return _ok(result, "解析已重新启动")
    except bid_parse_service.BidParseServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/upload-list-file")
async def upload_list_file(bid_id: int, files: list[UploadFile] = File(...)):
    try:
        return _ok(await bid_parse_service.upload_list_files(bid_id, files))
    except bid_parse_service.BidParseServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/upload-reference")
async def upload_reference_material(
    bid_id: int,
    material_type: MaterialType = Form(...),
    file: UploadFile = File(...),
):
    try:
        return _ok(await bid_parse_service.upload_reference_material(bid_id, file, material_type))
    except bid_parse_service.BidParseServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/trigger-parse")
async def trigger_bid_parse(bid_id: int):
    return _run(lambda: bid_parse_service.trigger_parse(bid_id))


@router.put("/{bid_id}/paste-parse-content")
async def save_paste_parse_content(bid_id: int, payload: PasteParseContentRequest):
    return _run(lambda: bid_parse_service.save_paste_parse_content(bid_id, payload))


@router.post("/{bid_id}/paste-parse-content/cleanup")
@router.delete("/{bid_id}/paste-parse-content")
async def cleanup_paste_parse_content(bid_id: int):
    return _run(lambda: bid_parse_service.clear_paste_parse_content(bid_id, only_if_current_step_2=True))


@router.post("/{bid_id}/auto-parse-content/cleanup")
@router.delete("/{bid_id}/auto-parse-content")
async def cleanup_auto_parse_content(bid_id: int):
    return _run(lambda: bid_parse_service.clear_auto_parse_content(bid_id, only_if_current_step_2=True))


@router.post("/{bid_id}/parse-content/cleanup")
async def cleanup_unsubmitted_parse_content(bid_id: int):
    return _run(lambda: bid_parse_service.cleanup_unsubmitted_parse_content(bid_id))


@router.post("/{bid_id}/step2/complete")
async def complete_bid_step2(bid_id: int):
    return _run(lambda: bid_parse_service.complete_step2(bid_id))
