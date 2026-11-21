"""Build editable presentation slides from a company's saved bid chapters."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

from app.services import storage


class PptServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


THEMES = {
    "green": {"dark": "16483A", "accent": "28896C", "pale": "EAF4EE", "ink": "203D36"},
    "blue": {"dark": "17385E", "accent": "2772B4", "pale": "EAF2F9", "ink": "23394E"},
}
IMAGE_MARKER = re.compile(r"\[\[(?:KNOWLEDGE_IMAGE|MERMAID_ASSET):(\d+)\]\]", re.I)
KNOWLEDGE_MARKER = re.compile(r"\[\[KNOWLEDGE_IMAGE:(\d+)\]\]", re.I)
CHART_MARKER = re.compile(r"\[\[MERMAID_ASSET:(\d+)\]\]", re.I)


def _rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value)


def _ordered_chapters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_parent: dict[int, list[dict[str, Any]]] = defaultdict(list)
    ids = {int(row["chapter_id"]) for row in rows}
    for row in rows:
        parent = int(row.get("parent_id") or 0)
        by_parent[parent if parent in ids else 0].append(row)
    for siblings in by_parent.values():
        siblings.sort(key=lambda row: (int(row.get("sort_order") or 0), int(row["chapter_id"])))
    ordered: list[dict[str, Any]] = []
    visited: set[int] = set()

    def visit(parent: int) -> None:
        for row in by_parent.get(parent, []):
            chapter_id = int(row["chapter_id"])
            if chapter_id in visited:
                continue
            visited.add(chapter_id)
            ordered.append(row)
            visit(chapter_id)

    visit(0)
    for row in rows:
        if int(row["chapter_id"]) not in visited:
            ordered.append(row)
    return ordered


def _load_source(bid_id: int, version_id: int | None = None) -> dict[str, Any]:
    try:
        project = storage.load_project_by_bid_id(bid_id)
    except FileNotFoundError as exc:
        raise PptServiceError("项目不存在或无权访问", 404) from exc
    with storage.connect() as db:
        versions = [dict(row) for row in db.execute(
            "SELECT version_id, version_name, is_final FROM bid_catalog_versions WHERE bid_id=? "
            "ORDER BY is_final DESC, version_id DESC", (bid_id,),
        ).fetchall()]
        if not versions:
            raise PptServiceError("该项目还没有目录，请先生成并保存目录")
        chosen = next((row for row in versions if int(row["version_id"]) == version_id), None) if version_id else versions[0]
        if chosen is None:
            raise PptServiceError("目录版本不存在", 404)
        rows = [dict(row) for row in db.execute(
            "SELECT c.chapter_id, c.parent_id, c.chapter_title, c.level, c.sort_order, "
            "c.graphic_markdown, c.ref_chart_ids, c.ref_image_asset_ids, bc.content_text "
            "FROM bid_catalog_chapters c LEFT JOIN bid_chapter_contents bc "
            "ON bc.bid_id=c.bid_id AND bc.version_id=c.version_id AND bc.chapter_id=c.chapter_id "
            "WHERE c.bid_id=? AND c.version_id=?",
            (bid_id, int(chosen["version_id"])),
        ).fetchall()]
    chapters = _ordered_chapters(rows)
    for chapter in chapters:
        graphic = str(chapter.get("graphic_markdown") or "")
        body = str(chapter.get("content_text") or "")
        chapter["available"] = bool(graphic.strip() or body.strip())
        chapter["source_kind"] = "图文正文" if graphic.strip() else "章节正文" if body.strip() else "未生成"
        chapter["image_count"] = len(IMAGE_MARKER.findall(graphic))
        chapter["preview"] = re.sub(r"\s+", " ", body or graphic)[:110]
    return {
        "bid_id": bid_id,
        "project_title": str(project.settings.bidTitle or "项目汇报"),
        "version_id": int(chosen["version_id"]),
        "versions": versions,
        "chapters": chapters,
    }


def get_source(bid_id: int, version_id: int | None = None) -> dict[str, Any]:
    source = _load_source(bid_id, version_id)
    return {**source, "chapters": [
        {key: row[key] for key in ("chapter_id", "parent_id", "chapter_title", "level", "available", "source_kind", "image_count", "preview")}
        for row in source["chapters"]
    ]}


def _add_text(slide, text: str, left: float, top: float, width: float, height: float, *,
              size: int = 22, color: str = "203D36", bold: bool = False,
              align: PP_ALIGN = PP_ALIGN.LEFT) -> None:
    shape = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(0.03)
    frame.margin_top = frame.margin_bottom = Inches(0.03)
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = text
    run.font.name = "Microsoft YaHei"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = _rgb(color)


def _add_rect(slide, left: float, top: float, width: float, height: float, color: str) -> None:
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(color)
    shape.line.fill.background()


def _new_slide(prs: Presentation, theme: dict[str, str], title: str, section: str) -> Any:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb("FFFFFF")
    _add_rect(slide, 0, 0, 13.333, 0.16, theme["accent"])
    _add_text(slide, section.upper(), 0.75, 0.43, 5.5, 0.3, size=12, color=theme["accent"], bold=True)
    _add_text(slide, title[:52], 0.75, 0.86, 11.8, 0.8, size=29, color=theme["dark"], bold=True)
    _add_rect(slide, 0.75, 1.73, 11.8, 0.025, theme["pale"])
    _add_text(slide, "基于已保存标书内容 · 可编辑PPT", 0.75, 7.05, 8.4, 0.22, size=10, color="778A83")
    _add_text(slide, f"{len(prs.slides):02d}", 11.75, 7.02, 0.75, 0.25, size=10, color="778A83", align=PP_ALIGN.RIGHT)
    return slide


def _cover(prs: Presentation, theme: dict[str, str], title: str, subtitle: str) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(theme["dark"])
    _add_rect(slide, 0, 0, 0.22, 7.5, theme["accent"])
    _add_rect(slide, 0.9, 1.18, 1.08, 0.1, theme["accent"])
    _add_text(slide, "项目汇报", 0.9, 1.55, 5, 0.45, size=18, color="CBE2D8")
    _add_text(slide, title[:76], 0.9, 2.2, 11.5, 1.85, size=39, color="FFFFFF", bold=True)
    _add_text(slide, subtitle[:110] or "依据标书目录与正文整理", 0.93, 5.02, 10.8, 0.7, size=18, color="D7E5DF")
    _add_text(slide, "内置版式 · 非示例公司官方模板", 0.93, 6.85, 8.0, 0.3, size=11, color="B7CCC2")


def _agenda(prs: Presentation, theme: dict[str, str], chapters: list[dict[str, Any]]) -> None:
    for start in range(0, len(chapters), 8):
        slide = _new_slide(prs, theme, "汇报目录", "AGENDA")
        for index, chapter in enumerate(chapters[start:start + 8], start + 1):
            column = (index - start - 1) // 4
            row = (index - start - 1) % 4
            x = 0.8 + column * 6.1
            y = 2.05 + row * 1.1
            _add_text(slide, f"{index:02d}", x, y, 0.7, 0.4, size=17, color=theme["accent"], bold=True)
            _add_text(slide, str(chapter["chapter_title"])[:45], x + 0.74, y - 0.04, 4.9, 0.68, size=20, color=theme["ink"])


def _plain_lines(markdown: str) -> list[str]:
    lines: list[str] = []
    for raw in (markdown or "").splitlines():
        line = raw.strip()
        if not line or IMAGE_MARKER.search(line) or line.startswith("|") or re.fullmatch(r"[-| :]+", line):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*•\d.、]+\s*", "", line)
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"[*_`]+", "", line).strip()
        if not line:
            continue
        for sentence in re.split(r"(?<=[。；;!?！？])", line):
            sentence = sentence.strip()
            while len(sentence) > 100:
                cut = sentence.rfind("，", 36, 100)
                if cut < 36:
                    cut = 90
                lines.append(sentence[:cut + 1].strip())
                sentence = sentence[cut + 1:].strip()
            if sentence:
                lines.append(sentence)
    unique: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = re.sub(r"\W+", "", line)
        if key and key not in seen:
            unique.append(line)
            seen.add(key)
    return unique


def _first_table(markdown: str) -> list[list[str]]:
    group: list[list[str]] = []
    for line in (markdown or "").splitlines():
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
                continue
            if 2 <= len(cells) <= 5:
                group.append(cells)
                continue
        if len(group) >= 2:
            return group[:7]
        group = []
    return group[:7] if len(group) >= 2 else []


def _content_slide(prs: Presentation, theme: dict[str, str], chapter: dict[str, Any], bullets: list[str], part: int) -> None:
    title = str(chapter["chapter_title"])
    slide = _new_slide(prs, theme, title + (f"（{part}）" if part > 1 else ""), "KEY POINTS")
    for index, bullet in enumerate(bullets):
        y = 2.05 + index * 1.12
        _add_rect(slide, 0.82, y + 0.19, 0.11, 0.11, theme["accent"])
        _add_text(slide, bullet, 1.13, y, 11.1, 0.9, size=22 if len(bullet) < 65 else 19, color=theme["ink"])
    try:
        slide.notes_slide.notes_text_frame.text = str(chapter.get("content_text") or chapter.get("graphic_markdown") or "")[:20000]
    except Exception:
        pass


def _table_slide(prs: Presentation, theme: dict[str, str], chapter: dict[str, Any], rows: list[list[str]]) -> None:
    slide = _new_slide(prs, theme, str(chapter["chapter_title"]) + " · 关键表格", "TABLE")
    columns = max(len(row) for row in rows)
    shape = slide.shapes.add_table(len(rows), columns, Inches(0.85), Inches(2.1), Inches(11.6), Inches(min(4.45, 0.61 * len(rows))))
    table = shape.table
    for col in table.columns:
        col.width = Inches(11.6 / columns)
    for row_index, values in enumerate(rows):
        for col_index in range(columns):
            cell = table.cell(row_index, col_index)
            cell.text = (values[col_index] if col_index < len(values) else "")[:80]
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(theme["dark"] if row_index == 0 else "FFFFFF" if row_index % 2 else theme["pale"])
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.name = "Microsoft YaHei"
                    run.font.size = Pt(14 if row_index == 0 else 12)
                    run.font.color.rgb = _rgb("FFFFFF" if row_index == 0 else theme["ink"])


def _decode_image_data(data: str) -> bytes | None:
    if not data.startswith("data:image/") or "," not in data:
        return None
    try:
        raw = base64.b64decode(data.split(",", 1)[1], validate=True)
        return raw if len(raw) <= 15 * 1024 * 1024 else None
    except (ValueError, binascii.Error):
        return None


def _picture_bytes(bid_id: int, kind: str, asset_id: int) -> tuple[BytesIO, str] | None:
    raw: bytes | None = None
    caption = "业务图片"
    if kind == "chart":
        with storage.connect() as db:
            row = db.execute("SELECT chart_title, chart_image_data FROM bid_mermaid_assets WHERE bid_id=? AND id=?", (bid_id, asset_id)).fetchone()
        if row:
            raw = _decode_image_data(str(row["chart_image_data"] or ""))
            caption = str(row["chart_title"] or "业务图表")
    else:
        from app.modules.knowledge import service as knowledge_service
        try:
            asset = knowledge_service.get_asset(asset_id)
            resource = str(asset.get("resource_path") or "")
            caption = str(asset.get("title") or asset.get("description") or "业务图片")
            if resource.startswith("data:image/"):
                raw = _decode_image_data(resource)
            else:
                path = knowledge_service._resolve_stored_path(resource)  # type: ignore[attr-defined]
                if path and Path(path).is_file() and Path(path).stat().st_size <= 15 * 1024 * 1024:
                    raw = Path(path).read_bytes()
        except Exception:
            return None
    if not raw:
        return None
    try:
        with Image.open(BytesIO(raw)) as image:
            image = image.convert("RGB")
            image.thumbnail((2200, 1400))
            output = BytesIO()
            image.save(output, "JPEG", quality=86, optimize=True)
            output.seek(0)
            return output, caption[:90]
    except Exception:
        return None


def _image_refs(chapter: dict[str, Any]) -> list[tuple[str, int]]:
    markdown = str(chapter.get("graphic_markdown") or "")
    refs = [("knowledge", int(value)) for value in KNOWLEDGE_MARKER.findall(markdown)]
    refs += [("chart", int(value)) for value in CHART_MARKER.findall(markdown)]
    for column, kind in (("ref_image_asset_ids", "knowledge"), ("ref_chart_ids", "chart")):
        try:
            values = json.loads(chapter.get(column) or "[]")
            refs.extend((kind, int(value)) for value in values if str(value).isdigit())
        except (ValueError, TypeError):
            pass
    return list(dict.fromkeys(refs))[:3]


def _image_slide(prs: Presentation, theme: dict[str, str], chapter: dict[str, Any], picture: BytesIO, caption: str) -> None:
    slide = _new_slide(prs, theme, str(chapter["chapter_title"]) + " · 图文资料", "VISUAL")
    with Image.open(picture) as image:
        ratio = image.width / image.height
    width = min(10.9, 4.65 * ratio)
    height = min(4.65, width / ratio)
    width = height * ratio
    x = (13.333 - width) / 2
    y = 1.95 + (4.75 - height) / 2
    picture.seek(0)
    slide.shapes.add_picture(picture, Inches(x), Inches(y), Inches(width), Inches(height))
    _add_text(slide, caption, 1.15, 6.72, 11.0, 0.3, size=12, color="60786E", align=PP_ALIGN.CENTER)


def _ending(prs: Presentation, theme: dict[str, str]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(theme["dark"])
    _add_rect(slide, 0, 0, 0.22, 7.5, theme["accent"])
    _add_text(slide, "汇报完毕", 1.1, 2.75, 11.1, 1.0, size=45, color="FFFFFF", bold=True, align=PP_ALIGN.CENTER)
    _add_text(slide, "感谢聆听", 1.1, 4.12, 11.1, 0.5, size=20, color="D9E8E1", align=PP_ALIGN.CENTER)


def generate_pptx(
    bid_id: int,
    *,
    version_id: int | None,
    chapter_ids: list[int],
    title: str,
    subtitle: str,
    theme_name: str = "green",
    max_slides_per_chapter: int = 3,
    include_images: bool = True,
) -> tuple[bytes, str]:
    if theme_name not in THEMES:
        raise PptServiceError("不支持的版式")
    if not 1 <= max_slides_per_chapter <= 4:
        raise PptServiceError("每章节页数应为 1 到 4")
    if not chapter_ids or len(set(chapter_ids)) > 40:
        raise PptServiceError("请选择 1 到 40 个已生成正文的章节")
    source = _load_source(bid_id, version_id)
    by_id = {int(row["chapter_id"]): row for row in source["chapters"]}
    selected = set(chapter_ids)
    if any(chapter_id not in by_id or not by_id[chapter_id]["available"] for chapter_id in selected):
        raise PptServiceError("所选章节包含未生成正文的项目，请刷新后重新选择")
    chapters = [row for row in source["chapters"] if int(row["chapter_id"]) in selected]
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    prs.core_properties.title = (title.strip() or source["project_title"])[:250]
    prs.core_properties.subject = "基于已保存标书正文生成的可编辑项目汇报"
    theme = THEMES[theme_name]
    final_title = title.strip() or source["project_title"]
    _cover(prs, theme, final_title, subtitle.strip())
    _agenda(prs, theme, chapters)
    for chapter in chapters:
        markdown = str(chapter.get("graphic_markdown") or "")
        body = str(chapter.get("content_text") or "") or markdown
        bullets = _plain_lines(body)
        table = _first_table(markdown or body)
        content_chunks = [bullets[index:index + 4] for index in range(0, min(len(bullets), 16), 4)]
        used = 0
        if content_chunks:
            _content_slide(prs, theme, chapter, content_chunks[0], 1)
            used += 1
        if table and used < max_slides_per_chapter:
            _table_slide(prs, theme, chapter, table)
            used += 1
        if include_images and used < max_slides_per_chapter:
            for kind, asset_id in _image_refs(chapter):
                picture = _picture_bytes(bid_id, kind, asset_id)
                if picture:
                    _image_slide(prs, theme, chapter, *picture)
                    used += 1
                    if used >= max_slides_per_chapter:
                        break
        for part, chunk in enumerate(content_chunks[1:], 2):
            if used >= max_slides_per_chapter:
                break
            _content_slide(prs, theme, chapter, chunk, part)
            used += 1
        if used == 0:
            raise PptServiceError(f"章节“{chapter['chapter_title']}”没有可排入幻灯片的内容")
    _ending(prs, theme)
    output = BytesIO()
    prs.save(output)
    safe_title = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", final_title).strip(" ._")[:70] or "项目汇报"
    return output.getvalue(), f"{safe_title}-项目汇报.pptx"
