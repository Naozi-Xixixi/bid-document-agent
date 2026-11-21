from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from docx import Document
from docx.shared import Pt, RGBColor

from app.services.officecli_docx_formatter import format_docx_with_officecli


class WordFormatServiceError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


DEFAULT_OPTIONS = {
    "title_color": "3A3A32",
    "title_size": "20pt",
    "title_font": "宋体",
    "heading1_color": "3A3A32",
    "heading1_size": "16pt",
    "heading1_font": "宋体",
    "heading2_color": "3A3A32",
    "heading2_size": "14pt",
    "heading2_font": "宋体",
    "heading3_color": "3A3A32",
    "heading3_size": "13pt",
    "heading3_font": "宋体",
    "body_font": "宋体",
    "body_size": "12pt",
}


def format_uploaded_docx(docx_bytes: bytes, filename: str, options: dict[str, Any]) -> tuple[bytes, str, dict[str, Any]]:
    if not filename.lower().endswith(".docx"):
        raise WordFormatServiceError(40001, "当前仅支持 .docx 文件格式修改，请先将 doc/pdf 转换为 docx 后上传")
    if not docx_bytes:
        raise WordFormatServiceError(40002, "上传文件为空，请重新选择标书文件")

    normalized = _normalize_options(options)
    heading_styles = _officecli_heading_styles(normalized)

    formatted_bytes, officecli_meta = format_docx_with_officecli(
        docx_bytes,
        heading_styles,
        body_font=normalized["body_font"],
        body_size=normalized["body_size"],
    )

    fallback_bytes, fallback_meta = _apply_python_docx_fallback(formatted_bytes, normalized)
    output_name = _output_filename(filename)
    meta = {
        **officecli_meta,
        **fallback_meta,
        "filename": output_name,
    }
    return fallback_bytes, output_name, meta


def _normalize_options(options: dict[str, Any]) -> dict[str, str]:
    result = dict(DEFAULT_OPTIONS)
    for key in result:
        value = str(options.get(key) or "").strip()
        if not value:
            continue
        if key.endswith("_color"):
            result[key] = _normalize_hex(value)
        elif key.endswith("_size"):
            result[key] = _normalize_size(value)
        else:
            result[key] = value[:40]
    return result


def _officecli_heading_styles(options: dict[str, str]) -> list[dict[str, Any]]:
    return [
        _officecli_style("paragraph[style=Title]", options["title_color"], options["title_size"], options["title_font"]),
        _officecli_style("paragraph[style=Heading1]", options["heading1_color"], options["heading1_size"], options["heading1_font"]),
        _officecli_style("paragraph[style=Heading2]", options["heading2_color"], options["heading2_size"], options["heading2_font"]),
        _officecli_style("paragraph[style=Heading3]", options["heading3_color"], options["heading3_size"], options["heading3_font"]),
    ]


def _officecli_style(selector: str, color: str, size: str, font: str) -> dict[str, Any]:
    return {
        "selector": selector,
        "props": {
            "color": color,
            "bold": "true",
            "size": size,
            "font": font,
        },
    }


def _apply_python_docx_fallback(docx_bytes: bytes, options: dict[str, str]) -> tuple[bytes, dict[str, Any]]:
    doc = Document(BytesIO(docx_bytes))
    counts = {"title": 0, "heading1": 0, "heading2": 0, "heading3": 0, "body": 0}

    try:
        normal = doc.styles["Normal"]
        normal.font.name = options["body_font"]
        normal.font.size = Pt(_size_to_pt(options["body_size"]))
    except Exception:
        pass

    for index, paragraph in enumerate(doc.paragraphs):
        text = paragraph.text.strip()
        if not text:
            continue
        level = _paragraph_level(paragraph, text, index)
        if level == "title":
            _format_paragraph(paragraph, options["title_font"], options["title_size"], options["title_color"], bold=True)
            counts["title"] += 1
        elif level == "heading1":
            _format_paragraph(paragraph, options["heading1_font"], options["heading1_size"], options["heading1_color"], bold=True)
            counts["heading1"] += 1
        elif level == "heading2":
            _format_paragraph(paragraph, options["heading2_font"], options["heading2_size"], options["heading2_color"], bold=True)
            counts["heading2"] += 1
        elif level == "heading3":
            _format_paragraph(paragraph, options["heading3_font"], options["heading3_size"], options["heading3_color"], bold=True)
            counts["heading3"] += 1
        else:
            _format_paragraph(paragraph, options["body_font"], options["body_size"], "000000", bold=False, only_empty_direct_format=True)
            counts["body"] += 1

    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue(), {"python_docx_fallback_applied": True, "formatted_counts": counts}


def _paragraph_level(paragraph, text: str, index: int) -> str:
    style_name = (getattr(paragraph.style, "name", "") or "").lower().replace(" ", "")
    if style_name == "title":
        return "title"
    if style_name in {"heading1", "标题1"}:
        return "heading1"
    if style_name in {"heading2", "标题2"}:
        return "heading2"
    if style_name in {"heading3", "标题3"}:
        return "heading3"
    if index <= 2 and len(text) <= 40 and any(token in text for token in ("投标文件", "标书", "方案")):
        return "title"
    if re.match(r"^第[一二三四五六七八九十百0-9]+[章节篇]\s*", text) or re.match(r"^[一二三四五六七八九十]+[、.]", text):
        return "heading1"
    if re.match(r"^\d+\.\d+(?!\.)", text) or re.match(r"^\([一二三四五六七八九十]+\)", text):
        return "heading2"
    if re.match(r"^\d+\.\d+\.\d+", text) or re.match(r"^\([0-9]+\)", text):
        return "heading3"
    return "body"


def _format_paragraph(paragraph, font: str, size: str, color: str, *, bold: bool, only_empty_direct_format: bool = False) -> None:
    if not paragraph.runs:
        return
    rgb = _rgb_color(color)
    for run in paragraph.runs:
        if only_empty_direct_format and (run.font.name or run.font.size or run.font.color.rgb):
            continue
        run.font.name = font
        run.font.size = Pt(_size_to_pt(size))
        run.font.color.rgb = rgb
        run.bold = bold


def _normalize_hex(value: str) -> str:
    cleaned = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", cleaned):
        return "3A3A32"
    return cleaned.upper()


def _normalize_size(value: str) -> str:
    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        return "12pt"
    number = max(8.0, min(36.0, float(match.group(0))))
    return f"{number:g}pt"


def _size_to_pt(value: str) -> float:
    match = re.search(r"\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else 12.0


def _rgb_color(value: str) -> RGBColor:
    normalized = _normalize_hex(value)
    return RGBColor(int(normalized[0:2], 16), int(normalized[2:4], 16), int(normalized[4:6], 16))


def _output_filename(filename: str) -> str:
    base = re.sub(r"[\\/:*?\"<>|]+", "_", filename.rsplit(".", 1)[0] or "标书文件")
    if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", base):
        base = "标书文件"
    return f"{base}_格式修改.docx"
