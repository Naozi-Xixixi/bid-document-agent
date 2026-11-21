from __future__ import annotations

import os
import re
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class BidParseError(Exception):
    """Base exception for tender document parsing failures."""

    code = 50001


class BidParseUnsupportedError(BidParseError):
    """Raised when the uploaded file type cannot be parsed."""

    code = 40003


class BidParseDocError(BidParseError):
    """Raised when legacy .doc parsing fails."""

    code = 40004


class BidParseScannedError(BidParseError):
    """Raised when a document looks like a scanned/image-only document."""

    code = 40005


@dataclass(slots=True)
class ParsedBidDocument:
    raw_text: str
    raw_markdown: str
    full_markdown: str
    chapter_blocks: list[dict[str, Any]]


SUPPORTED_SUFFIXES = {".doc", ".docx", ".pdf"}
SCANNED_EFFECTIVE_TEXT_THRESHOLD = 500
MAX_MARKDOWN_CELL_LENGTH = 1800

_CJK_OR_ALNUM_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_BIAOSHU_GARBLED_CHARS = set(
    "闁圭兘寮藉ù婊呯獥閻犵偞鍨濇穱濠囨偨闂堟稓鎽嶇紒澶嬪灦婵⊙囨⒖閸℃绀嬮柡鍫濐樀濡炬椽宕楀鍐箯濞戞瑧鍨煎ù鐙呯悼閹﹪寮甸悜妯尖偓顖炴晬濮橆偉鍘€点倕鎼崠妤€鈻介崘鑼处閻犱礁澧介柣鐐叉濞夛箓宕堕姀鈩冪畳闂傚嫭鍔曢崣鏇㈠矗"
)
_BIAOSHU_LATIN1_MOJIBAKE_CHARS = set("氓盲莽猫茅忙茫茂录脗脙")

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def parse_bid_document(file_path: str) -> ParsedBidDocument:
    """Parse a tender file into Markdown plus a plain-text compatibility copy."""

    try:
        markdown = parse_bid_file_to_markdown(file_path)
    except BidParseScannedError:
        markdown = ""
    text = markdown_to_plain_text(markdown)
    blocks = build_chapter_blocks(markdown)
    return ParsedBidDocument(raw_text=text, raw_markdown=markdown, full_markdown=markdown, chapter_blocks=blocks)


def parse_bid_file(file_path: str) -> str:
    """Compatibility entry: return plain text for older service callers."""

    return parse_bid_document(file_path).raw_text


def parse_bid_file_to_markdown(file_path: str) -> str:
    """Return BiaoShu-style Markdown that preserves titles, lists and tables."""

    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise BidParseError("招标文件不存在或无法读取")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise BidParseUnsupportedError("文件格式不支持，仅支持 doc、docx、pdf")

    if suffix == ".doc":
        markdown = convert_doc_to_markdown(str(path))
    elif suffix == ".docx":
        markdown = convert_docx_to_markdown(str(path))
    else:
        markdown = convert_pdf_to_markdown(str(path))

    if _is_biaoshu_error(markdown):
        message = _strip_biaoshu_error_prefix(markdown)
        if suffix == ".doc":
            raise BidParseDocError(message)
        if suffix == ".pdf" and any(word in message for word in ("扫描", "OCR", "无法自动提取")):
            raise BidParseScannedError("检测到文件为图片/扫描版，无法自动提取文本，请使用粘贴模式或上传可编辑的文档版本")
        raise BidParseError(message)

    return _clean_markdown(fix_encoding(markdown))


_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WORD = "{" + _WORD_NS + "}"


def _docx_style_profiles(archive: zipfile.ZipFile) -> dict[str, tuple[int | None, bool]]:
    """Resolve named/inherited heading and list styles without opening media."""
    try:
        with archive.open("word/styles.xml") as stream:
            root = ET.parse(stream).getroot()
    except KeyError:
        return {}
    styles: dict[str, tuple[int | None, bool, str]] = {}
    for style in root.findall(_WORD + "style"):
        style_id = style.get(_WORD + "styleId", "")
        name_node = style.find(_WORD + "name")
        name = name_node.get(_WORD + "val", "") if name_node is not None else ""
        parent_node = style.find(_WORD + "basedOn")
        parent = parent_node.get(_WORD + "val", "") if parent_node is not None else ""
        outline = style.find(f"{_WORD}pPr/{_WORD}outlineLvl")
        raw_level = outline.get(_WORD + "val", "") if outline is not None else ""
        match = re.search(r"(?:heading|标题)\s*([1-6])", f"{style_id} {name}", re.I)
        level = int(raw_level) + 1 if raw_level in {"0", "1", "2", "3", "4", "5"} else (int(match.group(1)) if match else None)
        is_list = "list" in name.lower() or "列表" in name or style.find(f"{_WORD}pPr/{_WORD}numPr") is not None
        styles[style_id] = (level, is_list, parent)
    profiles: dict[str, tuple[int | None, bool]] = {}
    for style_id in styles:
        current, seen = style_id, set()
        level, is_list = None, False
        while current in styles and current not in seen:
            seen.add(current)
            own_level, own_list, current = styles[current]
            level = level or own_level
            is_list = is_list or own_list
        profiles[style_id] = (level, is_list)
    return profiles


def _xml_paragraph_markdown(text: str, paragraph: dict[str, Any], styles: dict[str, tuple[int | None, bool]]) -> str | None:
    text = _normalize_inline_space(text)
    if not text:
        return None
    style_id = paragraph["style"]
    style_level, style_list = styles.get(style_id, (None, False))
    match = re.search(r"(?:heading|标题)\s*([1-6])", style_id, re.I)
    level = paragraph["level"] or style_level or (int(match.group(1)) if match else None)
    if level:
        return f"{'#' * level} {text}"
    if paragraph["list"] or style_list:
        return _list_line_to_markdown(text) if re.match(r"^(?:[-•●]|[（(]\d+[）)])", text) else f"- {text}"
    return _line_to_markdown(text)


def convert_docx_to_markdown(file_path: str) -> str:
    """Stream only Word XML, preserving body order, headings and table cells.

    Do not construct python-docx's package graph: that eagerly loads hundreds
    of MB of image/embedding parts even when only text is required. Every XML
    element is detached after its event, so parsed XML does not accumulate.
    """
    try:
        md_lines: list[str] = []
        paragraphs: list[dict[str, Any]] = []
        tables: list[dict[str, Any]] = []
        elements: list[Any] = []
        with zipfile.ZipFile(file_path) as archive:
            styles = _docx_style_profiles(archive)
            with archive.open("word/document.xml") as stream:
                for event, element in ET.iterparse(stream, events=("start", "end")):
                    tag = element.tag
                    if event == "start":
                        elements.append(element)
                        if tag == _WORD + "p":
                            paragraphs.append({"parts": [], "style": "", "level": None, "list": False})
                        elif tag == _WORD + "tbl":
                            tables.append({"rows": [], "row": None, "cell": None, "vertical": {}})
                        elif tag == _WORD + "tr" and tables:
                            tables[-1]["row"] = []
                        elif tag == _WORD + "tc" and tables:
                            tables[-1]["cell"] = []
                            tables[-1]["span"] = 1
                            tables[-1]["merge"] = None
                        continue
                    if tables and tables[-1]["cell"] is not None:
                        if tag == _WORD + "gridSpan":
                            value = element.get(_WORD + "val", "1")
                            tables[-1]["span"] = max(1, min(1000, int(value))) if value.isdigit() else 1
                        elif tag == _WORD + "vMerge":
                            tables[-1]["merge"] = element.get(_WORD + "val", "continue")
                    if paragraphs:
                        paragraph = paragraphs[-1]
                        if tag == _WORD + "t":
                            paragraph["parts"].append(element.text or "")
                        elif tag == _WORD + "tab":
                            paragraph["parts"].append("\t")
                        elif tag in {_WORD + "br", _WORD + "cr"}:
                            paragraph["parts"].append("\n")
                        elif tag == _WORD + "pStyle":
                            paragraph["style"] = element.get(_WORD + "val", "")
                        elif tag == _WORD + "outlineLvl":
                            value = element.get(_WORD + "val", "")
                            if value in {"0", "1", "2", "3", "4", "5"}:
                                paragraph["level"] = int(value) + 1
                        elif tag == _WORD + "numPr":
                            paragraph["list"] = True
                    if tag == _WORD + "p":
                        paragraph = paragraphs.pop()
                        text = "".join(paragraph["parts"])
                        if paragraphs:
                            # Text boxes can contain paragraphs inside a drawing.
                            paragraphs[-1]["parts"].append("\n" + text + "\n")
                        elif tables and tables[-1]["cell"] is not None:
                            tables[-1]["cell"].append(text)
                        else:
                            line = _xml_paragraph_markdown(text, paragraph, styles)
                            if line is not None:
                                md_lines.append(line)
                            elif md_lines and md_lines[-1] != "":
                                md_lines.append("")
                    elif tag == _WORD + "tc" and tables:
                        table = tables[-1]
                        if table["row"] is not None:
                            column = len(table["row"])
                            value = "\n".join(table["cell"] or [])
                            if table["merge"] == "continue":
                                value = table["vertical"].get(column, value)
                            for offset in range(table["span"]):
                                if table["merge"] is not None:
                                    table["vertical"][column + offset] = value
                                else:
                                    table["vertical"].pop(column + offset, None)
                                table["row"].append(value)
                        table["cell"] = None
                    elif tag == _WORD + "tr" and tables:
                        table = tables[-1]
                        if table["row"] is not None:
                            table["rows"].append(table["row"])
                        table["row"] = None
                    elif tag == _WORD + "tbl":
                        table = tables.pop()
                        table_md = _table_rows_to_markdown(table["rows"])
                        if table_md:
                            if tables and tables[-1]["cell"] is not None:
                                tables[-1]["cell"].append(table_md)
                            else:
                                md_lines.extend(["", table_md, ""])
                    elements.pop()
                    element.clear()
                    if elements:
                        elements[-1].remove(element)
        return "\n".join(md_lines)
    except Exception as exc:  # noqa: BLE001
        return f"[错误] DOCX解析失败: {exc}"


def convert_doc_to_markdown(file_path: str) -> str:
    """Convert legacy .doc through Word/WPS COM when available, then parse as docx."""

    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError:
        return "[错误] DOC格式解析失败：当前环境缺少 pywin32，请转换为 docx 格式后重试"

    word = None
    doc = None
    temp_path: Path | None = None
    try:
        pythoncom.CoInitialize()
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        if hasattr(word, "DisplayAlerts"):
            word.DisplayAlerts = 0

        doc = word.Documents.Open(os.path.abspath(file_path))
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            temp_path = Path(tmp.name)
        doc.SaveAs2(str(temp_path), FileFormat=16)
        doc.Close(False)
        doc = None
        markdown = convert_docx_to_markdown(str(temp_path))
        if not _is_biaoshu_error(markdown):
            return markdown

        doc = word.Documents.Open(os.path.abspath(file_path))
        content = str(doc.Content.Text or "")
        return _plain_lines_to_markdown(content.splitlines())
    except Exception as exc:  # noqa: BLE001
        return f"[错误] DOC格式解析失败：{exc}\n请确认已安装 Microsoft Word 或 WPS Office，或转换为 docx 格式后重试"
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        finally:
            try:
                if word is not None:
                    word.Quit()
            finally:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def convert_pdf_to_markdown(file_path: str) -> str:
    """Convert PDF to Markdown using pdfplumber text and table extraction."""

    try:
        import pdfplumber
    except ImportError:
        return "[错误] 需要安装 pdfplumber: pip install pdfplumber"

    try:
        pages: list[dict[str, object]] = []
        with pdfplumber.open(file_path) as pdf:
            for page_index, page in enumerate(pdf.pages, 1):
                text = page.extract_text(x_tolerance=1.5, y_tolerance=3, layout=False) or ""
                lines = [_normalize_inline_space(fix_encoding(line)) for line in text.split("\n")]
                lines = [line for line in lines if line]
                lines = _merge_fragmented_pdf_lines(lines)
                tables = []
                try:
                    tables = page.extract_tables(_pdf_table_settings()) or []
                except Exception:
                    tables = []
                pages.append({"page": page_index, "lines": lines, "tables": tables})

        repeated_noise = _detect_repeated_pdf_noise(pages)
        md_lines: list[str] = []
        table_count = 0

        for page in pages:
            page_no = int(page["page"])
            if page_no > 1:
                md_lines.extend(["", "---", f"<!-- 第{page_no}页 -->", ""])

            page_lines = [line for line in page["lines"] if not _is_pdf_noise_line(line, repeated_noise)]
            page_markdown = _plain_lines_to_markdown(page_lines)
            if page_markdown:
                md_lines.extend(page_markdown.splitlines())

            for table in page["tables"]:  # type: ignore[assignment]
                table_md = _table_rows_to_markdown(table)
                if not table_md:
                    continue
                table_count += 1
                md_lines.extend(["", f"### 表格 {page_no}-{table_count}", table_md, ""])

        markdown = "\n".join(md_lines).strip()
        if _effective_length(markdown) < SCANNED_EFFECTIVE_TEXT_THRESHOLD:
            fallback = _convert_pdf_with_pypdf2(file_path)
            if fallback and _effective_length(fallback) > _effective_length(markdown):
                markdown = fallback
        if not markdown:
            raise BidParseScannedError("检测到文件为图片/扫描版，无法自动提取文本，请使用粘贴模式或上传可编辑的文档版本")
        return markdown
    except BidParseScannedError:
        raise
    except Exception as exc:  # noqa: BLE001
        return f"[错误] PDF解析失败: {exc}"


def _convert_pdf_with_pypdf2(file_path: str) -> str:
    try:
        import PyPDF2
    except ImportError:
        return ""

    text_parts: list[str] = []
    try:
        with open(file_path, "rb") as file:
            reader = PyPDF2.PdfReader(file)
            for index, page in enumerate(reader.pages, 1):
                text = fix_encoding(page.extract_text() or "")
                if _effective_length(text) > 30:
                    text_parts.append(f"<!-- 第{index}页 -->\n" + _plain_lines_to_markdown(text.splitlines()))
    except Exception:
        return ""
    return "\n\n".join(text_parts).strip()


def _iter_docx_blocks(document) -> Iterable[object]:
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def _docx_paragraph_to_markdown(paragraph) -> str | None:
    text = _normalize_inline_space(paragraph.text)
    if not text:
        return None

    style_name = (getattr(paragraph.style, "name", "") or "").lower()
    if "heading 1" in style_name or "标题 1" in style_name:
        return f"# {text}"
    if "heading 2" in style_name or "标题 2" in style_name:
        return f"## {text}"
    if "heading 3" in style_name or "标题 3" in style_name:
        return f"### {text}"
    if "heading 4" in style_name or "标题 4" in style_name:
        return f"#### {text}"
    if "list" in style_name or "列表" in style_name:
        return _list_line_to_markdown(text)
    return _line_to_markdown(text)


def _docx_table_rows(table) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.rows:
        values = [_clean_table_cell(cell.text) for cell in row.cells]
        if any(values):
            rows.append(values)
    return rows


def _table_rows_to_markdown(rows: object) -> str:
    values: list[list[str]] = []
    for raw_row in rows or []:  # type: ignore[assignment]
        row = [_clean_table_cell(str(cell or "")) for cell in raw_row or []]
        if any(row):
            values.append(row)
    if not values:
        return ""

    width = max(len(row) for row in values)
    normalized = [row + [""] * (width - len(row)) for row in values]
    headers = normalized[0]
    if not any(headers):
        headers = [f"列{index + 1}" for index in range(width)]

    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _clean_table_cell(value: str) -> str:
    text = fix_encoding(value or "")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("|", " / ")
    return text[:MAX_MARKDOWN_CELL_LENGTH]


def _plain_lines_to_markdown(lines: Iterable[str]) -> str:
    output: list[str] = []
    for raw in lines:
        line = _normalize_inline_space(fix_encoding(raw))
        if not line:
            if output and output[-1] != "":
                output.append("")
            continue
        output.append(_line_to_markdown(line))
    return "\n".join(output)


def _merge_fragmented_pdf_lines(lines: list[str]) -> list[str]:
    """Join PDF text runs that were extracted as one glyph per line."""

    merged: list[str] = []
    fragments: list[str] = []

    def flush() -> None:
        if not fragments:
            return
        if len(fragments) >= 6:
            merged.append("".join(fragments))
        else:
            merged.extend(fragments)
        fragments.clear()

    for line in lines:
        compact = re.sub(r"\s+", "", line)
        if compact and len(compact) <= 3 and not re.match(r"^[-—_=]{2,}$", compact):
            fragments.append(compact)
            continue
        flush()
        merged.append(line)
    flush()
    return merged


def _line_to_markdown(line: str) -> str:
    if re.match(r"^第[一二三四五六七八九十百千万\d]+章", line):
        return f"# {line}"
    if re.match(r"^[一二三四五六七八九十]、", line) and len(line) < 80:
        return f"## {line}"
    if re.match(r"^\d+\.\d+\.\d+\s*", line) and len(line) < 120:
        return f"#### {line}"
    if re.match(r"^\d+\.\d+\s*", line) and len(line) < 100:
        return f"### {line}"
    if re.match(r"^\d+[\.、]\s*", line) and len(line) < 90:
        return f"## {line}"
    return _list_line_to_markdown(line)


def _list_line_to_markdown(line: str) -> str:
    if re.match(r"^[\-•●]\s*", line):
        return re.sub(r"^[\-•●]\s*", "- ", line)
    if re.match(r"^[（(]\d+[）)]", line):
        return f"- {line}"
    return line


def _pdf_table_settings() -> dict[str, object]:
    return {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "edge_min_length": 3,
        "min_words_vertical": 2,
        "min_words_horizontal": 1,
        "intersection_tolerance": 5,
        "text_tolerance": 3,
    }


def _detect_repeated_pdf_noise(pages: list[dict[str, object]]) -> set[str]:
    counts: dict[str, int] = {}
    for page in pages:
        lines = list(page.get("lines") or [])
        for line in [*lines[:3], *lines[-3:]]:
            normalized = _normalize_inline_space(str(line))
            if 0 < len(normalized) <= 80:
                counts[normalized] = counts.get(normalized, 0) + 1
    threshold = max(3, int(len(pages) * 0.45))
    return {line for line, count in counts.items() if count >= threshold}


def _is_pdf_noise_line(line: str, repeated_noise: set[str]) -> bool:
    value = _normalize_inline_space(line)
    if not value:
        return True
    if value in repeated_noise:
        return True
    if re.match(r"^(第\s*)?\d+\s*(页)?$", value, re.I):
        return True
    if re.match(r"^page\s+\d+(\s+of\s+\d+)?$", value, re.I):
        return True
    return False


def markdown_to_plain_text(markdown: str) -> str:
    lines: list[str] = []
    for raw in (markdown or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("<!--"):
            continue
        if re.match(r"^\|\s*[-: ]+(\|\s*[-: ]+)+\|?$", line):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        if line.startswith("|") and line.endswith("|"):
            parts = [part.strip() for part in line.strip("|").split("|") if part.strip()]
            line = " ".join(parts)
        lines.append(line)
    return _clean_text("\n".join(lines))


def build_chapter_blocks(markdown: str) -> list[dict[str, Any]]:
    """Split Markdown into AI-friendly chapter blocks with page hints."""

    lines = (markdown or "").splitlines()
    if not lines:
        return []

    blocks: list[dict[str, Any]] = []
    current_title = "全文概览"
    current_level = 1
    current_page = 1
    start_page = 1
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer, current_title, current_level, start_page
        content = _collapse_blank_lines([line.rstrip() for line in buffer])
        if not content:
            buffer = []
            return
        block_id = f"B{len(blocks) + 1:03d}"
        end_page = _last_page_in_content(content, current_page)
        blocks.append(
            {
                "block_id": block_id,
                "chapter_title": current_title[:180],
                "level": current_level,
                "page_range": [start_page, end_page],
                "content": content[:18000],
                "block_type": _detect_block_type(content),
                "char_count": len(content),
            }
        )
        buffer = []

    for raw in lines:
        line = raw.rstrip()
        page = _extract_page_marker(line)
        if page:
            current_page = page
        heading = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if heading and buffer:
            flush()
            current_level = len(heading.group(1))
            current_title = heading.group(2).strip() or current_title
            start_page = current_page
            buffer.append(line)
            continue
        if heading and not buffer:
            current_level = len(heading.group(1))
            current_title = heading.group(2).strip() or current_title
            start_page = current_page
        buffer.append(line)

        if sum(len(item) for item in buffer) > 12000 and not _inside_markdown_table(buffer):
            flush()
            current_title = f"{current_title}（续）"
            start_page = current_page

    flush()

    if blocks:
        return blocks
    return _fallback_chunk_blocks(markdown)


def _fallback_chunk_blocks(markdown: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    text = markdown or ""
    for start in range(0, len(text), 10000):
        content = text[start : start + 10000].strip()
        if not content:
            continue
        chunks.append(
            {
                "block_id": f"B{len(chunks) + 1:03d}",
                "chapter_title": "全文分块",
                "level": 1,
                "page_range": [1, 1],
                "content": content,
                "block_type": _detect_block_type(content),
                "char_count": len(content),
            }
        )
    return chunks


def _extract_page_marker(line: str) -> int | None:
    match = re.search(r"第\s*(\d+)\s*页|page\s+(\d+)", line, re.I)
    if not match:
        return None
    value = match.group(1) or match.group(2)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _last_page_in_content(content: str, default_page: int) -> int:
    pages = [_extract_page_marker(line) for line in content.splitlines()]
    pages = [page for page in pages if page]
    return int(pages[-1]) if pages else default_page


def _detect_block_type(content: str) -> str:
    table_lines = [line for line in content.splitlines() if line.strip().startswith("|") and line.strip().endswith("|")]
    non_table_lines = [line for line in content.splitlines() if line.strip() and not line.strip().startswith(("|", "---", "<!--"))]
    if table_lines and non_table_lines:
        return "mixed"
    if table_lines:
        return "table"
    return "text"


def _inside_markdown_table(lines: list[str]) -> bool:
    tail = [line.strip() for line in lines[-4:]]
    return any(line.startswith("|") and line.endswith("|") for line in tail)


def fix_encoding(text: str) -> str:
    """BiaoShu-SKILL style mojibake repair for UTF-8 mis-decoded as GBK/Latin-1."""

    if not text:
        return text

    garbled_count = sum(1 for char in text if char in _BIAOSHU_GARBLED_CHARS)
    latin1_mojibake_count = sum(1 for char in text if char in _BIAOSHU_LATIN1_MOJIBAKE_CHARS)
    cjk_ratio = len(_CJK_RE.findall(text)) / len(text) if text else 0
    garbled_ratio = garbled_count / len(text) if text else 0
    latin1_mojibake_ratio = latin1_mojibake_count / len(text) if text else 0
    if garbled_ratio > 0.1 or (latin1_mojibake_ratio > 0.05 and cjk_ratio < 0.1):
        for source_encoding in ("latin-1", "gbk"):
            try:
                return text.encode(source_encoding).decode("utf-8")
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
    return text


def _is_biaoshu_error(text: str) -> bool:
    value = (text or "").lstrip()
    return value.startswith("[错误]") or value.startswith("[ERROR]") or value.startswith("[閿欒")


def _strip_biaoshu_error_prefix(text: str) -> str:
    return re.sub(r"^\s*\[[^\]]+\]\s*", "", text or "").strip() or "文件解析失败"


def _clean_markdown(markdown: str) -> str:
    text = markdown.replace("\x00", "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u200b\u200c\u200d\u2060]", "", text)
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    lines = [_normalize_inline_space(line) if not line.startswith("|") else _normalize_table_line(line) for line in text.split("\n")]
    return _collapse_blank_lines(lines)


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u200b\u200c\u200d\u2060]", "", text)
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    lines = [_normalize_inline_space(line) for line in text.split("\n")]
    return _collapse_blank_lines(lines)


def _collapse_blank_lines(lines: list[str]) -> str:
    cleaned_lines: list[str] = []
    blank_seen = False
    for line in lines:
        if not line:
            if not blank_seen and cleaned_lines:
                cleaned_lines.append("")
            blank_seen = True
            continue
        cleaned_lines.append(line)
        blank_seen = False
    return "\n".join(cleaned_lines).strip()


def _normalize_table_line(text: str) -> str:
    return re.sub(r"[ \t\f\v]+", " ", text).strip()


def _normalize_inline_space(text: str) -> str:
    text = str(text or "").replace("\u3000", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text).strip()
    cjk_or_punctuation = r"\u4e00-\u9fff：:，。；、！？（）《》【】"
    text = re.sub(rf"(?<=[{cjk_or_punctuation}])\s+(?=[{cjk_or_punctuation}])", "", text)
    return text


def _effective_length(text: str) -> int:
    return len(_CJK_OR_ALNUM_RE.findall(text or ""))
