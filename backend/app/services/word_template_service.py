from __future__ import annotations

import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services import storage


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _stored_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(settings.backend_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _resolve_path(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text)
    return path if path.is_absolute() else (settings.backend_root / path).resolve()


def _template_dir() -> Path:
    directory = settings.upload_dir / "knowledge" / "templates"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _is_generated_template_path(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        path.resolve().relative_to(_template_dir().resolve())
        return True
    except ValueError:
        return False


def remove_template_file(path: Path | None) -> None:
    """Remove only files generated inside the knowledge template directory."""
    if not _is_generated_template_path(path):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[KnowledgeTemplate] cleanup failed path={path} error={exc}", flush=True)


def project_template_path(project_id: int) -> Path | None:
    with storage.connect() as conn:
        row = conn.execute("SELECT format_template_path FROM bid_project WHERE project_id = ?", (project_id,)).fetchone()
    return _resolve_path(row["format_template_path"]) if row and row["format_template_path"] else None


def _xml_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _w_text(value: str) -> str:
    escaped = _xml_escape(value)
    preserve = ' xml:space="preserve"' if value.startswith(" ") or value.endswith(" ") else ""
    return f"<w:r><w:t{preserve}>{escaped}</w:t></w:r>"


def _w_paragraph(value: str = "", style: str | None = None, *, align: str | None = None, page_break: bool = False) -> str:
    properties: list[str] = []
    if style:
        properties.append(f'<w:pStyle w:val="{_xml_escape(style)}"/>')
    if align:
        properties.append(f'<w:jc w:val="{_xml_escape(align)}"/>')
    ppr = f"<w:pPr>{''.join(properties)}</w:pPr>" if properties else ""
    runs = ""
    if value:
        runs += _w_text(value)
    if page_break:
        runs += '<w:r><w:br w:type="page"/></w:r>'
    return f"<w:p>{ppr}{runs}</w:p>"


def _w_brand_cover_line(value: str, *, half_points: int, after: int = 0, before: int = 0, underline: bool = False) -> str:
    text = _xml_escape(value)
    return (
        '<w:p><w:pPr><w:jc w:val="center"/>'
        f'<w:spacing w:before="{before}" w:after="{after}"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:ascii="黑体" w:hAnsi="黑体" w:eastAsia="黑体"/>'
        '<w:b/><w:color w:val="588E31"/>'
        f'<w:sz w:val="{half_points}"/><w:szCs w:val="{half_points}"/>'
        + ('<w:u w:val="single"/>' if underline else '') +
        f'</w:rPr><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
    )


def _w_cell(value: str, *, width: int = 1800, align: str | None = None) -> str:
    paragraph = _w_paragraph(value, align=align)
    return f'<w:tc><w:tcPr><w:tcW w:type="dxa" w:w="{width}"/><w:vAlign w:val="center"/></w:tcPr>{paragraph}</w:tc>'


def _w_cell_paragraphs(paragraphs: list[str], *, width: int = 1800) -> str:
    content = "".join(paragraphs) or _w_paragraph()
    return f'<w:tc><w:tcPr><w:tcW w:type="dxa" w:w="{width}"/><w:vAlign w:val="center"/></w:tcPr>{content}</w:tc>'


def _w_table(headers: list[str], fields: list[str], loop_name: str, *, widths: list[int] | None = None, table_style: str = "TableGrid") -> str:
    widths = widths or [1800] * len(headers)
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    header_cells = "".join(
        _w_cell(header, width=width, align="center")
        for header, width in zip(headers, widths, strict=False)
    )
    row_cells: list[str] = []
    for index, (field, width) in enumerate(zip(fields, widths, strict=False)):
        prefix = f"{{#{loop_name}}}" if index == 0 else ""
        suffix = f"{{/{loop_name}}}" if index == len(fields) - 1 else ""
        row_cells.append(_w_cell(f"{prefix}{{{field}}}{suffix}", width=width, align="center"))
    row = f"<w:tr>{''.join(row_cells)}</w:tr>"
    header_row = f"<w:tr>{header_cells}</w:tr>"
    properties = f'<w:tblPr><w:tblStyle w:val="{_xml_escape(table_style)}"/><w:tblW w:type="auto" w:w="0"/></w:tblPr>'
    return f"<w:tbl>{properties}<w:tblGrid>{grid}</w:tblGrid>{header_row}{row}</w:tbl>"


def _w_image_table(loop_name: str, fields: list[str], headers: list[str], *, widths: list[int], table_style: str = "TableGrid") -> str:
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    header_row = f"<w:tr>{''.join(_w_cell(header, width=width, align='center') for header, width in zip(headers, widths, strict=False))}</w:tr>"
    cells: list[str] = []
    for index, (field, width) in enumerate(zip(fields, widths, strict=False)):
        prefix = f"{{#{loop_name}}}" if index == 0 else ""
        suffix = f"{{/{loop_name}}}" if index == len(fields) - 1 else ""
        if field == "image":
            paragraphs = []
            if prefix:
                paragraphs.append(_w_paragraph(prefix))
            paragraphs.extend([_w_paragraph("{#image}"), _w_paragraph("{%image}", align="center"), _w_paragraph("{/image}")])
            if suffix:
                paragraphs.append(_w_paragraph(suffix))
            cells.append(_w_cell_paragraphs(paragraphs, width=width))
            continue
        else:
            text = f"{prefix}{{{field}}}{suffix}"
        cells.append(_w_cell(text, width=width, align="center"))
    properties = f'<w:tblPr><w:tblStyle w:val="{_xml_escape(table_style)}"/><w:tblW w:type="auto" w:w="0"/></w:tblPr>'
    return f"<w:tbl>{properties}<w:tblGrid>{grid}</w:tblGrid>{header_row}<w:tr>{''.join(cells)}</w:tr></w:tbl>"


def _template_body(section_xml: str, *, branded_cover: bool = False, table_style: str = "TableGrid") -> str:
    """Build a small reusable body with clean labels and stable tags."""
    cover = (
        [
            _w_brand_cover_line("{bid_title}", half_points=44, before=400, after=1300),
            _w_brand_cover_line("投 标 文 件", half_points=112, after=4800),
            _w_brand_cover_line("投标人名称：{bidder_name}", half_points=36, after=120, underline=True),
            _w_brand_cover_line("{bid_date}", half_points=36, after=220, underline=True),
        ]
        if branded_cover else [
            _w_paragraph("{bid_title}", "Title", align="center"),
            _w_paragraph("{template_name}", align="center"),
            _w_paragraph("项目编号：{bid_id}", align="center"),
        ]
    )
    blocks = [
        *cover,
        _w_paragraph(page_break=True), _w_paragraph("目 录" if branded_cover else "目录", "Heading1", align="center"),
    ]
    if branded_cover:
        blocks.extend((
            _w_paragraph("{#chapter_rows}"),
            _w_paragraph("{number} {title}"),
            _w_paragraph("{/chapter_rows}"),
        ))
    else:
        blocks.append(_w_table(["序号", "章节名称", "字数", "状态"], ["number", "title", "word_count", "status"], "chapter_rows", widths=[1400, 4200, 1400, 1800], table_style=table_style))
    blocks.extend([
        _w_paragraph(page_break=True),
        *([] if branded_cover else [_w_paragraph("图文标书正文", "Heading1")]),
        '<w:p><w:r><w:t>{#chapters}</w:t></w:r></w:p>', _w_paragraph("{chapter_heading}", "Heading2"),
        '<w:p><w:r><w:t>{#content_blocks}</w:t></w:r></w:p>',
        '<w:p><w:r><w:t>{#heading1}</w:t></w:r></w:p>', _w_paragraph("{text}", "Heading1"), '<w:p><w:r><w:t>{/heading1}</w:t></w:r></w:p>',
        '<w:p><w:r><w:t>{#heading2}</w:t></w:r></w:p>', _w_paragraph("{text}", "Heading2"), '<w:p><w:r><w:t>{/heading2}</w:t></w:r></w:p>',
        '<w:p><w:r><w:t>{#heading3}</w:t></w:r></w:p>', _w_paragraph("{text}", "Heading3"), '<w:p><w:r><w:t>{/heading3}</w:t></w:r></w:p>',
        '<w:p><w:r><w:t>{#body}</w:t></w:r></w:p>', _w_paragraph("{text}"), '<w:p><w:r><w:t>{/body}</w:t></w:r></w:p>',
        '<w:p><w:r><w:t>{#image}</w:t></w:r></w:p>', _w_paragraph("{%image}", align="center"), _w_paragraph("{caption}", align="center"), '<w:p><w:r><w:t>{/image}</w:t></w:r></w:p>',
        '<w:p><w:r><w:t>{/content_blocks}</w:t></w:r></w:p>', '<w:p><w:r><w:t>{/chapters}</w:t></w:r></w:p>',
    ])
    table_specs = [
        ("资质证明表", "qualification_rows", ["certificate_name", "issuing_authority", "certificate_number", "valid_until", "remark", "image"], ["证书名称", "发证机关", "证书编号", "有效期", "备注", "图片"], [1900, 1800, 1600, 1500, 2400, 1300]),
        ("人员信息表", "personnel_rows", ["name", "role", "certificate", "certificate_number", "valid_until", "years_experience", "image"], ["姓名", "岗位", "证件名称", "证书编号", "有效期", "从业年限", "图片"], [1500, 1600, 1900, 1600, 1500, 1500, 1100]),
        ("车辆信息表", "vehicle_rows", ["vehicle_type", "plate_number", "approved_load", "quantity", "inspection_date", "cold_chain_equipment", "remark", "image"], ["车辆类型", "车牌号", "核定载重", "数量", "年检有效期", "冷链配置", "备注", "图片"], [1500, 1300, 1300, 800, 1400, 2100, 1900, 1100]),
        ("设备信息表", "equipment_rows", ["name", "model", "quantity", "condition", "image"], ["设备名称", "型号", "数量", "状态", "图片"], [2500, 1800, 1000, 3000, 1300]),
        ("业绩案例表", "performance_rows", ["project_name", "client_name", "project_date", "project_scale", "remark", "image"], ["项目名称", "甲方单位", "项目时间", "项目规模", "备注", "图片"], [2300, 1900, 1400, 1700, 2300, 1300]),
        ("财务审计表", "financial_rows", ["year", "revenue", "total_assets", "net_profit", "audit_firm", "verification", "image"], ["年度", "营业收入", "资产总额", "净利润", "审计机构", "核验结果", "图片"], [1000, 1700, 1700, 1500, 2200, 1900, 1300]),
    ]
    for title, loop_name, fields, headers, widths in table_specs:
        condition = f"has_{loop_name}"
        blocks.extend((_w_paragraph(f"{{#{condition}}}"), _w_paragraph(title, "Heading1"), _w_image_table(loop_name, fields, headers, widths=widths, table_style=table_style), _w_paragraph(f"{{/{condition}}}")))
    return f"<w:body>{''.join(blocks)}{section_xml}</w:body>"


def _replace_body(document_xml: str) -> str:
    # Re-extraction should be idempotent. This matters for projects whose
    # uploaded bid file is already one of our prepared format templates.
    if "{bid_title}" in document_xml and "{#chapters}" in document_xml:
        return document_xml
    section_match = re.search(r"<w:sectPr\b[\s\S]*?</w:sectPr>", document_xml)
    section_xml = section_match.group(0) if section_match else '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
    body_match = re.search(r"<w:body\b[^>]*>[\s\S]*?</w:body>", document_xml)
    if body_match is None:
        raise ValueError("DOCX 缺少可编辑的正文结构")
    # This brand treatment is present on the 示例公司 company sample's cover;
    # other customer files keep the neutral layout.
    branded_cover = 'w:val="588E31"' in document_xml[:180000]
    style_counts = Counter(re.findall(r'<w:tblStyle\b[^>]*\bw:val="([^"]+)"', document_xml))
    table_style = style_counts.most_common(1)[0][0] if style_counts else "TableGrid"
    body = _template_body(section_xml, branded_cover=branded_cover, table_style=table_style)
    return document_xml[:body_match.start()] + body + document_xml[body_match.end():]


def _style_summary(styles: Any, style_name: str, label: str) -> str:
    try:
        style = styles[style_name]
        font = style.font
        font_name = font.name or "inherit"
        size = f"{font.size.pt:g} pt" if font.size else "inherit"
        spacing = style.paragraph_format.line_spacing
        line = f", line spacing {spacing:g}" if isinstance(spacing, (int, float)) else ""
        return f"{label}: {font_name}, {size}{line}"
    except Exception:
        return f"{label}: inherit"


def _format_rules(source_path: Path, document_xml: str, entries: dict[str, bytes]) -> dict[str, Any]:
    # Inspect package XML directly. python-docx opens every related image part,
    # which can exhaust memory for the 300–600 MB customer examples.
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    style_names: dict[str, str] = {}
    style_summaries: dict[str, str] = {}
    header_paragraphs: list[str] = []
    footer_paragraphs: list[str] = []
    with zipfile.ZipFile(source_path) as source_zip:
        if "word/styles.xml" in source_zip.namelist():
            styles_root = ET.fromstring(source_zip.read("word/styles.xml"))
            for style in styles_root.findall(f"{ns}style"):
                style_id = style.get(f"{ns}styleId") or ""
                name = style.find(f"{ns}name")
                style_names[style_id] = (name.get(f"{ns}val") if name is not None else None) or style_id
                rpr = style.find(f"{ns}rPr")
                fonts = rpr.find(f"{ns}rFonts") if rpr is not None else None
                size = rpr.find(f"{ns}sz") if rpr is not None else None
                font_name = (fonts.get(f"{ns}eastAsia") or fonts.get(f"{ns}ascii")) if fonts is not None else None
                size_value = f"{int(size.get(f'{ns}val')) / 2:g} pt" if size is not None and (size.get(f"{ns}val") or "").isdigit() else "inherit"
                style_summaries[style_id] = f"{style_names[style_id]}: {font_name or 'inherit'}, {size_value}"
        for name in source_zip.namelist():
            if not re.fullmatch(r"word/(?:header|footer)\d+\.xml", name):
                continue
            root = ET.fromstring(source_zip.read(name))
            content = " ".join((node.text or "").strip() for node in root.iter(f"{ns}t") if (node.text or "").strip())
            (header_paragraphs if "/header" in name else footer_paragraphs).append(content)
    media = [name for name in entries if name.startswith("word/media/")]
    header_text = " ".join(header_paragraphs)[:240]
    footer_text = " ".join(footer_paragraphs)[:240]
    sections_xml = re.search(r"<w:sectPr\b[\s\S]*?</w:sectPr>", document_xml)
    section_text = sections_xml.group(0) if sections_xml else ""
    has_header = "<w:headerReference" in section_text
    has_footer = "<w:footerReference" in section_text
    brand_terms = [term for term in ("示例公司", "BIDAGENT", "食材配送") if term.casefold() in (header_text + " " + footer_text).casefold()]
    page = {
        "paper": "unknown",
        "orientation": "portrait",
        "margins": {},
        "header_footer": {"header": has_header, "footer": has_footer, "header_text": header_text, "footer_text": footer_text},
    }
    def attribute(tag: str, key: str) -> str | None:
        match = re.search(rf"<w:{tag}\b([^>]*)", section_text)
        value = re.search(rf'\bw:{key}="([^"]+)"', match.group(1)) if match else None
        return value.group(1) if value else None

    width, height = attribute("pgSz", "w"), attribute("pgSz", "h")
    if width and height and width.isdigit() and height.isdigit():
        page["paper"] = {"width_inches": round(int(width) / 1440, 3), "height_inches": round(int(height) / 1440, 3)}
    page["orientation"] = "landscape" if attribute("pgSz", "orient") == "landscape" else "portrait"
    page["margins"] = {
        f"{side}_inches": round(int(value) / 1440, 3)
        for side in ("top", "right", "bottom", "left")
        if (value := attribute("pgMar", side)) and value.isdigit()
    }
    table_styles = sorted({style_names.get(value, value) for value in re.findall(r'<w:tblStyle\b[^>]*\bw:val="([^"]+)"', document_xml)})
    def summary(name: str) -> str:
        key = next((key for key, value in style_names.items() if value.casefold() == name.casefold()), name.replace(" ", ""))
        return style_summaries.get(key, f"{name}: inherit")
    return {
        "page": page,
        "styles": {
            "body": summary("Normal"),
            "heading1": summary("heading 1"),
            "heading2": summary("heading 2"),
            "heading3": summary("heading 3"),
        },
        "table": {"count": len(re.findall(r"<w:tbl(?:\s|>)", document_xml)), "has_borders": "tblBorders" in document_xml, "styles": table_styles},
        "lists": {"has_numbering": "<w:numPr" in document_xml},
        "fixed_elements": {"header": has_header, "footer": has_footer, "logo_or_images": len(media), "brand_terms": brand_terms},
        "source": {"file_name": source_path.name, "paragraph_count": len(re.findall(r"<w:p(?:\s|>)", document_xml)), "table_count": len(re.findall(r"<w:tbl(?:\s|>)", document_xml)), "media_count": len(media)},
    }


def _required_placeholders() -> list[str]:
    return [
        "bid_title", "bidder_name", "bid_date", "bid_id", "template_name", "chapter_rows", "chapters", "content_blocks",
        "heading1", "heading2", "heading3", "body", "image", "qualification_rows", "personnel_rows",
        "vehicle_rows", "equipment_rows", "performance_rows", "financial_rows",
        "has_qualification_rows", "has_personnel_rows", "has_vehicle_rows",
        "has_equipment_rows", "has_performance_rows", "has_financial_rows",
    ]


def _kept_media_names(source_zip: zipfile.ZipFile, document_xml: str) -> set[str]:
    """Keep only media referenced by preserved headers and footers."""
    names = set(source_zip.namelist())
    header_footer_names = [name for name in names if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)]
    xml_parts = [document_xml]
    for name in header_footer_names:
        try:
            xml_parts.append(source_zip.read(name).decode("utf-8"))
        except Exception:
            continue
    referenced_rids: set[str] = set()
    for xml in xml_parts:
        referenced_rids.update(re.findall(r"(?:r:id|r:embed)=\"([^\"]+)\"", xml))
    rel_names = ["word/_rels/document.xml.rels"] + [f"word/_rels/{Path(name).name}.rels" for name in header_footer_names]
    kept: set[str] = set()
    for rel_name in rel_names:
        if rel_name not in names:
            continue
        try:
            root = ET.fromstring(source_zip.read(rel_name))
        except ET.ParseError:
            continue
        for rel in root:
            if rel.attrib.get("Id") not in referenced_rids:
                continue
            target = rel.attrib.get("Target", "")
            target_name = (Path(rel_name).parent.parent / target).as_posix()
            while "/../" in target_name:
                target_name = target_name.replace("/../", "/")
            if target_name.startswith("word/media/") and target_name in names:
                kept.add(target_name)
    return kept


def _filter_document_relationships(xml: bytes, kept_media: set[str]) -> bytes:
    """Remove dangling relationships to body images removed from the template."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return xml
    for rel in list(root):
        target = rel.attrib.get("Target", "")
        if not target.startswith("media/"):
            continue
        target_name = (Path("word") / target).as_posix()
        if target_name not in kept_media:
            root.remove(rel)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _write_template(source_path: Path, target_path: Path) -> dict[str, Any]:
    """Build a lightweight template without copying historical body images."""
    with zipfile.ZipFile(source_path, "r") as source_zip:
        document_bytes = source_zip.read("word/document.xml")
        document_xml = document_bytes.decode("utf-8")
        prepared_document = _replace_body(document_xml).encode("utf-8")
        kept_media = _kept_media_names(source_zip, prepared_document.decode("utf-8"))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = target_path.with_suffix(".tmp.docx")
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as output:
                for info in source_zip.infolist():
                    name = info.filename
                    if name.endswith("/") or (name.startswith("word/media/") and name not in kept_media):
                        continue
                    if name == "word/document.xml":
                        output.writestr(info, prepared_document)
                        continue
                    if name == "word/_rels/document.xml.rels":
                        output.writestr(info, _filter_document_relationships(source_zip.read(name), kept_media))
                        continue
                    with source_zip.open(info, "r") as source_stream:
                        output.writestr(info, source_stream.read())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        temporary.replace(target_path)
        media_names = [name for name in source_zip.namelist() if name in kept_media]
        source_media_count = sum(1 for name in source_zip.namelist() if name.startswith("word/media/"))
    rules = _format_rules(source_path, document_xml, {name: b"" for name in media_names})
    rules["optimization"] = {
        "body_images_removed": True,
        "preserved_media_count": len(media_names),
        "source_media_count": source_media_count,
    }
    return rules


def _project_item_path(item_id: int) -> tuple[Path | None, str]:
    with storage.connect() as conn:
        row = conn.execute("SELECT source_local_path, original_file_path, source_name FROM knowledge_item WHERE item_id = ?", (item_id,)).fetchone()
    if row is None:
        return None, ""
    for key in ("source_local_path", "original_file_path"):
        path = _resolve_path(row[key])
        if path and path.exists():
            return path, str(row["source_name"] or path.name)
    return None, str(row["source_name"] or "")


def _project_exists(project_id: int) -> bool:
    with storage.connect() as conn:
        return conn.execute("SELECT 1 FROM bid_project WHERE project_id = ?", (project_id,)).fetchone() is not None


def mark_project_template_pending(project_id: int) -> dict[str, Any]:
    if not _project_exists(project_id):
        raise FileNotFoundError("project not found")
    now = _now()
    with storage.connect() as conn:
        conn.execute("UPDATE bid_project SET format_template_status = 'pending', format_template_error = '', update_time = ? WHERE project_id = ?", (now, project_id))
        conn.commit()
    return {"project_id": project_id, "status": "pending"}


def extract_project_template(project_id: int) -> dict[str, Any]:
    if not _project_exists(project_id):
        raise FileNotFoundError("project not found")
    now = _now()
    with storage.connect() as conn:
        row = conn.execute("SELECT bid_doc_id, format_template_path FROM bid_project WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            raise FileNotFoundError("project not found")
        conn.execute("UPDATE bid_project SET format_template_status = 'processing', format_template_error = '', update_time = ? WHERE project_id = ?", (now, project_id))
        conn.commit()
    previous_template_path = _resolve_path(row["format_template_path"]) if row["format_template_path"] else None
    source_path, source_name = _project_item_path(int(row["bid_doc_id"] or 0))
    if source_path is None:
        message = "找不到对应投标文件的本地副本"
        with storage.connect() as conn:
            conn.execute("UPDATE bid_project SET format_template_status = 'failed', format_template_error = ?, update_time = ? WHERE project_id = ?", (message, _now(), project_id))
            conn.commit()
        return {"project_id": project_id, "status": "failed", "error": message}
    if source_path.suffix.lower() != ".docx":
        message = "格式模板提取暂时只支持 DOCX 投标文件，请将 DOC 或 PDF 另存为 DOCX 后重新提取"
        with storage.connect() as conn:
            conn.execute("UPDATE bid_project SET format_template_status = 'failed', format_template_error = ?, format_template_source_file = ?, update_time = ? WHERE project_id = ?", (message, source_name, _now(), project_id))
            conn.commit()
        return {"project_id": project_id, "status": "failed", "error": message}
    target_path = _template_dir() / f"project_{project_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.docx"
    try:
        rules = _write_template(source_path, target_path)
        template_id = f"project:{project_id}:{target_path.stem.rsplit('_', 1)[-1]}"
        with storage.connect() as conn:
            current_default = conn.execute(
                "SELECT project_id FROM bid_project WHERE format_template_status = 'completed' AND format_template_default = 1 ORDER BY format_template_updated_at DESC, project_id DESC LIMIT 1"
            ).fetchone()
            make_default = current_default is None or int(current_default["project_id"]) == project_id
            if make_default:
                conn.execute("UPDATE bid_project SET format_template_default = 0 WHERE format_template_status = 'completed'")
            conn.execute(
                """
                UPDATE bid_project
                SET format_template_id = ?, format_template_path = ?, format_template_name = ?,
                    format_template_status = 'completed', format_template_error = '',
                    format_template_rules = ?, format_template_source_file = ?,
                    format_template_updated_at = ?, format_template_default = ?, update_time = ?
                WHERE project_id = ?
                """,
                (template_id, _stored_path(target_path), f"{source_path.stem} 格式模板", _json(rules), source_name, _now(), 1 if make_default else 0, _now(), project_id),
            )
            conn.commit()
        if previous_template_path and previous_template_path.resolve() != target_path.resolve():
            remove_template_file(previous_template_path)
        return {"project_id": project_id, "status": "completed", "template_id": template_id, "path": _stored_path(target_path), "rules": rules}
    except Exception as exc:  # noqa: BLE001 - template extraction must not fail document parsing.
        target_path.unlink(missing_ok=True)
        message = str(exc) or "格式模板提取失败"
        with storage.connect() as conn:
            conn.execute("UPDATE bid_project SET format_template_status = 'failed', format_template_error = ?, format_template_source_file = ?, update_time = ? WHERE project_id = ?", (message[:1000], source_name, _now(), project_id))
            conn.commit()
        print(f"[KnowledgeTemplate] extraction failed project_id={project_id} error={message}", flush=True)
        return {"project_id": project_id, "status": "failed", "error": message}


def get_project_template(project_id: int) -> dict[str, Any]:
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT project_id, format_template_id, format_template_name, format_template_status, format_template_error, format_template_rules, format_template_source_file, format_template_updated_at, format_template_default FROM bid_project WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    if row is None:
        raise FileNotFoundError("project not found")
    try:
        rules = json.loads(row["format_template_rules"] or "{}")
    except Exception:
        rules = {}
    return {
        "project_id": int(row["project_id"]),
        "id": row["format_template_id"],
        "name": row["format_template_name"],
        "status": row["format_template_status"] or "not_started",
        "error": row["format_template_error"],
        "rules": rules,
        "source_file_name": row["format_template_source_file"],
        "updated_at": row["format_template_updated_at"],
        "is_default": bool(row["format_template_default"]),
    }


def activate_project_template(project_id: int) -> dict[str, Any]:
    template = get_project_template(project_id)
    if template["status"] != "completed":
        raise ValueError("模板尚未提取完成")
    with storage.connect() as conn:
        conn.execute("UPDATE bid_project SET format_template_default = 0")
        conn.execute("UPDATE bid_project SET format_template_default = 1, update_time = ? WHERE project_id = ?", (_now(), project_id))
        conn.commit()
    return get_project_template(project_id)


def active_template_path() -> Path | None:
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT format_template_path FROM bid_project WHERE format_template_status = 'completed' AND format_template_default = 1 AND format_template_path IS NOT NULL ORDER BY format_template_updated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT format_template_path FROM bid_project WHERE format_template_status = 'completed' AND format_template_path IS NOT NULL ORDER BY format_template_updated_at DESC, project_id DESC LIMIT 1"
            ).fetchone()
    path = _resolve_path(row["format_template_path"]) if row else None
    return path if path and path.exists() else None


def template_download_path(project_id: int) -> tuple[Path, str]:
    with storage.connect() as conn:
        row = conn.execute("SELECT format_template_path, format_template_name FROM bid_project WHERE project_id = ? AND format_template_status = 'completed'", (project_id,)).fetchone()
    if row is None:
        raise FileNotFoundError("模板尚未提取完成")
    path = _resolve_path(row["format_template_path"])
    if path is None or not path.exists():
        raise FileNotFoundError("模板文件不存在，请重新提取")
    return path, f"{row['format_template_name'] or '项目格式模板'}.docx"
