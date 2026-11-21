from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from app.core.config import settings
from app.services import word_format_service


router = APIRouter(prefix="/format/word")


def _reference_template_records() -> list[dict[str, object]]:
    """List bundled lightweight templates without loading their DOCX bodies."""
    root = settings.backend_root / "data" / "format_templates" / "reference"
    records: list[dict[str, object]] = []
    for manifest_path in sorted(root.glob("*.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        docx_path = manifest_path.with_suffix(".docx")
        if not docx_path.is_file():
            continue
        source_name = Path(str(manifest.get("source_file") or docx_path.name)).stem
        records.append({
            "id": str(manifest.get("template_id") or f"reference:{manifest_path.stem}"),
            "name": f"{source_name} 格式模板",
            "file_name": docx_path.name,
            "description": "从历史标书提取的轻量格式模板，可直接用于正文导出",
            "scenario": "食堂食材配送及综合服务标书",
            "source_file_name": Path(str(manifest.get("source_file") or source_name)).name,
            "size": docx_path.stat().st_size,
            "format_rules": manifest.get("format_rules") or {},
        })
    return records


def _reference_template_path(template_id: str) -> tuple[Path, str] | None:
    for record in _reference_template_records():
        if record["id"] != template_id:
            continue
        file_name = str(record["file_name"])
        path = settings.backend_root / "data" / "format_templates" / "reference" / file_name
        if path.is_file():
            return path, file_name
    return None


def _error(exc: word_format_service.WordFormatServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message, "data": None})


@router.get("/capabilities")
async def word_format_capabilities():
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "supported_formats": ["docx"],
            "editable_scopes": ["title", "heading1", "heading2", "heading3", "body"],
            "engine": "officecli_with_python_docx_fallback",
        },
    }


@router.get("/reference-templates")
async def list_reference_word_templates():
    return {"code": 0, "message": "ok", "data": {"templates": _reference_template_records()}}


@router.get("/reference-templates/{template_id}/download")
async def download_reference_word_template(template_id: str):
    result = _reference_template_path(template_id)
    if result is None:
        return JSONResponse(status_code=404, content={"code": 40401, "message": "参考模板不存在", "data": None})
    path, filename = result
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=filename)


@router.post("/modify")
async def modify_word_format(
    file: UploadFile = File(...),
    title_color: str = Form("3A3A32"),
    title_size: str = Form("20pt"),
    title_font: str = Form("宋体"),
    heading1_color: str = Form("3A3A32"),
    heading1_size: str = Form("16pt"),
    heading1_font: str = Form("宋体"),
    heading2_color: str = Form("3A3A32"),
    heading2_size: str = Form("14pt"),
    heading2_font: str = Form("宋体"),
    heading3_color: str = Form("3A3A32"),
    heading3_size: str = Form("13pt"),
    heading3_font: str = Form("宋体"),
    body_font: str = Form("宋体"),
    body_size: str = Form("12pt"),
):
    try:
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
        content = await file.read()
        output, filename, meta = word_format_service.format_uploaded_docx(content, file.filename or "标书文件.docx", options)
        quoted = urllib.parse.quote(filename)
        return Response(
            content=output,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
                "X-Word-Format-Meta": urllib.parse.quote(json.dumps(meta, ensure_ascii=False)),
            },
        )
    except word_format_service.WordFormatServiceError as exc:
        return _error(exc)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"code": 50001, "message": f"标书格式修改失败：{exc}", "data": None})
