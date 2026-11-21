from __future__ import annotations

import json
import re
import shutil
import tempfile
import urllib.parse
from contextlib import aclosing
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.core.runtime_paths import configure_runtime_paths
from app.schemas.bid_graphic import AssetVerifyRequest, ChapterMaterialMatchAllRequest, ChapterMaterialSelectionRequest, GraphicSectionGenerateRequest, GraphicSectionSaveRequest, MermaidGenerateRequest, MermaidRenderRequest, MermaidSaveToRagRequest, TableFillRequest
from app.services import bid_graphic_service, word_toc_finalize, blind_check, storage
from app.core.tenant_context import get_current_tenant


router = APIRouter(prefix="/bid")


def _ok(data: object | None = None, message: str = "success") -> dict[str, object | None]:
    return {"code": 0, "message": message, "data": data}


def _error(exc: bid_graphic_service.BidGraphicServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message, "data": None})


def _sse(event: dict[str, object]) -> str:
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/{bid_id}/mermaid/generate")
async def generate_mermaid(bid_id: int, payload: MermaidGenerateRequest):
    try:
        return _ok(await bid_graphic_service.generate_mermaid(bid_id, payload), "Mermaid代码已生成")
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/mermaid/render")
async def render_mermaid(bid_id: int, payload: MermaidRenderRequest):
    try:
        return _ok(bid_graphic_service.render_mermaid_asset(bid_id, payload), "图表资产已保存")
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.get("/{bid_id}/mermaid/list")
async def list_mermaid(bid_id: int):
    try:
        return _ok(bid_graphic_service.list_mermaid_assets(bid_id))
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/mermaid/save-to-rag")
async def save_mermaid_to_rag(bid_id: int, payload: MermaidSaveToRagRequest):
    try:
        return _ok(bid_graphic_service.save_mermaid_to_rag(bid_id, payload.asset_id), "图表已写入企业知识库")
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/graphic-section/generate")
async def generate_graphic_section(bid_id: int, payload: GraphicSectionGenerateRequest):
    try:
        if payload.chapter_ids:
            return _ok(await bid_graphic_service.generate_graphic_section_batch(bid_id, payload), "选中章节图文正文已生成")
        return _ok(await bid_graphic_service.generate_graphic_section(bid_id, payload), "图文段落已生成")
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.get("/{bid_id}/catalog/chapter/{chapter_id}/materials")
async def get_chapter_materials(
    bid_id: int,
    chapter_id: int,
    bid_scenario: str | None = Query(default=None),
    length_level: str | None = Query(default=None),
    chapter_theme: str | None = Query(default=None, max_length=800),
):
    try:
        return _ok(bid_graphic_service.get_chapter_materials(bid_id, chapter_id, bid_scenario=bid_scenario, length_level=length_level, chapter_theme=chapter_theme))
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.put("/{bid_id}/catalog/chapter/{chapter_id}/materials")
async def save_chapter_materials(bid_id: int, chapter_id: int, payload: ChapterMaterialSelectionRequest):
    try:
        return _ok(bid_graphic_service.save_chapter_materials(bid_id, chapter_id, payload))
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/catalog/materials/match-all")
async def match_all_chapter_materials(bid_id: int, payload: ChapterMaterialMatchAllRequest | None = None):
    try:
        return _ok(bid_graphic_service.match_all_chapter_materials(bid_id, payload or ChapterMaterialMatchAllRequest()))
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/audit-assets")
async def audit_graphic_assets(bid_id: int):
    try:
        return _ok(bid_graphic_service.audit_graphic_assets(bid_id), "标书素材核验完成")
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/table/fill")
async def fill_bid_table(bid_id: int, payload: TableFillRequest):
    try:
        return _ok(bid_graphic_service.fill_bid_table(bid_id, payload), "表格填充数据已生成")
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/certificates/verify")
async def verify_certificates(bid_id: int, payload: AssetVerifyRequest | None = None):
    try:
        return _ok(bid_graphic_service.verify_bid_assets(bid_id, "certificates", payload or AssetVerifyRequest()), "证件核验完成")
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/invoices/verify")
async def verify_invoices(bid_id: int, payload: AssetVerifyRequest | None = None):
    try:
        return _ok(bid_graphic_service.verify_bid_assets(bid_id, "invoices", payload or AssetVerifyRequest()), "发票核验完成")
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.get("/{bid_id}/graphic-section/generate-stream")
async def generate_graphic_section_stream(
    bid_id: int,
    chapter_id: int = Query(...),
    bidder_name: str = Query(default="", max_length=200),
    chapter_ids: str = Query(default=""),
    only_missing: bool = Query(default=False),
    word_limit: int = Query(default=1200),
    length_target: int | None = Query(default=None, ge=1, le=500000),
    length_mode: str = Query(default="default", pattern="^(shrink|expand|default)$"),
    rag_enable: bool = Query(default=True),
    image_strategy: str = Query(default="rag_auto"),
    asset_sub_type: str = Query(default=""),
    priority_expire: bool = Query(default=False),
    exclude_ids: str = Query(default=""),
    chapter_theme: str | None = Query(default=None),
    chart_strategy: str | None = Query(default=None),
    format_template_id: str | None = Query(default=None),
    selected_asset_ids: str = Query(default=""),
    selected_chunk_ids: str = Query(default=""),
    bid_scenario: str | None = Query(default=None),
    length_level: str | None = Query(default=None),
):
    async def event_stream():
        try:
            subtype_values = [item.strip() for item in asset_sub_type.split(",") if item.strip()]
            excluded_values = [int(item) for item in exclude_ids.split(",") if item.strip().isdigit()]
            selected_asset_values = [int(item) for item in selected_asset_ids.split(",") if item.strip().isdigit()]
            selected_chunk_values = [int(item) for item in selected_chunk_ids.split(",") if item.strip().isdigit()]
            chapter_id_values = list(dict.fromkeys(int(item) for item in chapter_ids.split(",") if item.strip().isdigit()))
            payload = GraphicSectionGenerateRequest(
                chapter_id=chapter_id,
                bidder_name=bidder_name,
                chapter_ids=chapter_id_values,
                word_limit=word_limit,
                length_target=length_target,
                length_mode=length_mode,
                rag_enable=rag_enable,
                image_strategy=image_strategy,
                asset_sub_type=subtype_values,
                priority_expire=priority_expire,
                exclude_ids=excluded_values,
                chapter_theme=chapter_theme,
                chart_strategy=chart_strategy,
                format_template_id=format_template_id,
                selected_asset_ids=selected_asset_values,
                selected_chunk_ids=selected_chunk_values,
                bid_scenario=bid_scenario,
                length_level=length_level,
            )
            events = (
                bid_graphic_service.stream_graphic_section_batch(bid_id, payload, only_missing=only_missing)
                if chapter_id_values or only_missing
                else bid_graphic_service.stream_graphic_section(bid_id, payload)
            )
            async with aclosing(events):
                async for event in events:
                    yield _sse(event)
        except bid_graphic_service.BidGraphicServiceError as exc:
            yield _sse({"type": "server_error", "message": exc.message})
        except Exception as exc:  # noqa: BLE001 - report stream errors to the client.
            yield _sse({"type": "server_error", "message": str(exc) or "图文正文生成失败"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.get("/{bid_id}/graphic-section/templates")
async def list_graphic_templates(bid_id: int):
    try:
        return _ok(bid_graphic_service.list_format_templates(bid_id))
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/graphic-section/upload-image")
async def upload_graphic_image(
    bid_id: int,
    file: UploadFile = File(...),
    caption: str = Form(default=""),
    category: str = Form(default="其他附件类"),
    save_to_knowledge: bool = Form(default=False),
):
    try:
        return _ok(await bid_graphic_service.upload_illustration(bid_id, file, caption=caption, category=category, save_to_knowledge=save_to_knowledge), "插图已上传")
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.get("/{bid_id}/graphic-section/blind-mode")
async def get_blind_mode(bid_id: int):
    try:
        project = storage.load_project_by_bid_id(bid_id)
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"code": 40401, "message": "项目不存在或无权访问", "data": None})
    return _ok({"blind": project.settings.bidType == "blind"})


@router.get("/{bid_id}/graphic-section/download")
async def download_graphic_docx(bid_id: int, bidder_name: str = Query(default="", max_length=200)):
    try:
        project = storage.load_project_by_bid_id(bid_id)
        if project.settings.bidType == "blind":
            return JSONResponse(status_code=409, content={"code": 40901, "message": "暗标请使用图文模板导出，并完成导出前匿名检查。", "data": None})
        content, filename = bid_graphic_service.download_graphic_docx(bid_id, bidder_name=bidder_name) if bidder_name else bid_graphic_service.download_graphic_docx(bid_id)
        quoted = urllib.parse.quote(filename)
        return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"code": 40401, "message": "项目不存在或无权访问", "data": None})
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.get("/{bid_id}/graphic-section/{chapter_id}")
async def get_graphic_section(bid_id: int, chapter_id: int, for_export: bool = False, bidder_name: str = Query(default="", max_length=200)):
    try:
        return _ok(bid_graphic_service.get_graphic_section(bid_id, chapter_id, for_export=True, bidder_name=bidder_name) if for_export else bid_graphic_service.get_graphic_section(bid_id, chapter_id))
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.put("/{bid_id}/graphic-section/save")
async def save_graphic_section(bid_id: int, payload: GraphicSectionSaveRequest):
    try:
        return _ok(bid_graphic_service.save_graphic_section(bid_id, payload), "图文段落已保存")
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)


@router.post("/{bid_id}/graphic-section/blind-check")
async def check_blind_docx(bid_id: int, file: UploadFile = File(...), terms: str = Form(default=""), force: bool = Form(default=False)):
    """Check the exact finished document; an unavailable check never passes."""
    try:
        project = storage.load_project_by_bid_id(bid_id)
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"code": 40401, "message": "项目不存在或无权访问", "data": None})
    if not force and project.settings.bidType != "blind":
        return _ok({"required": False, "issues": [], "images": [], "blocked": False})
    tenant = get_current_tenant()
    keywords = [tenant.company_name] if tenant else []
    keywords.extend(re.split(r'[\n\r,，;；]+', terms))
    content = await file.read(100 * 1024 * 1024 + 1)
    if len(content) > 100 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"code": 41301, "message": "暗标检查文件超过 100 MB。", "data": None})
    result = await run_in_threadpool(blind_check.inspect_docx, content, keywords)
    return _ok({"required": True, **result})


@router.post("/{bid_id}/graphic-section/finalize-docx")
async def finalize_graphic_docx(bid_id: int, request: Request):
    """Paginate the browser-built DOCX and save actual TOC page numbers."""
    try:
        bid_graphic_service.ensure_schema()
        bid_graphic_service._ensure_bid(bid_id)
    except bid_graphic_service.BidGraphicServiceError as exc:
        return _error(exc)

    temp_root = configure_runtime_paths() / "tmp"
    work_dir = Path(tempfile.mkdtemp(prefix="docx-toc-", dir=temp_root))
    source = work_dir / "source.docx"
    destination = work_dir / "with-pages.docx"
    try:
        size = 0
        with source.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    return JSONResponse(status_code=413, content={"code": 41301, "message": "Word 文件超过 512 MB，无法更新目录页码。", "data": None}, background=BackgroundTask(shutil.rmtree, work_dir, ignore_errors=True))
                output.write(chunk)
        with source.open("rb") as uploaded:
            signature = uploaded.read(4)
        if size < 4 or signature != b"PK\x03\x04":
            raise word_toc_finalize.TocFinalizationError("上传内容不是有效的 DOCX 文件。")
        await run_in_threadpool(word_toc_finalize.finalize_docx, source, destination)
        return FileResponse(
            destination,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"图文标书_{bid_id}_含目录页码.docx",
            background=BackgroundTask(shutil.rmtree, work_dir, ignore_errors=True),
        )
    except word_toc_finalize.TocFinalizationError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        return JSONResponse(status_code=422, content={"code": 42201, "message": str(exc), "data": None})
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
