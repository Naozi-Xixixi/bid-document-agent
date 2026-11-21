from pathlib import Path
import runpy

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "src" / "assets" / "bid-templates"


def set_style_font(style, name: str, size: float, *, bold: bool = False, color: str | None = None) -> None:
    style.font.name = name
    style.font.size = Pt(size)
    style.font.bold = bold
    if color:
        style.font.color.rgb = RGBColor.from_string(color)
    rpr = style._element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for key in list(rfonts.attrib):
        if key.rsplit("}", 1)[-1].lower().endswith("theme"):
            del rfonts.attrib[key]
    rfonts.set(qn("w:eastAsia"), name)


def shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def set_cell_text(cell, text: str, *, bold: bool = False, color: str | None = None) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(text)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run("第 " )
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run = paragraph.add_run()
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(separate)
    run._r.append(end)
    paragraph.add_run(" 页")


def add_toc_field(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    for kind in ("begin", "separate", "end"):
        run = paragraph.add_run()
        if kind == "separate":
            instruction = OxmlElement("w:instrText")
            instruction.set(qn("xml:space"), "preserve")
            instruction.text = ' TOC \\o "1-3" \\h \\z \\u '
            run._r.append(instruction)
        marker = OxmlElement("w:fldChar")
        marker.set(qn("w:fldCharType"), kind)
        run._r.append(marker)


def add_header_footer(doc: Document, header_text: str) -> None:
    section = doc.sections[0]
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    header.add_run(header_text)
    footer = section.footer.paragraphs[0]
    add_page_number(footer)


def configure_document(doc: Document, *, font_name: str, body_size: float, accent: str, line_spacing: float) -> None:
    section = doc.sections[0]
    section.top_margin = Inches(0.82)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.82)
    section.right_margin = Inches(0.82)

    for style_name, size, bold, color in [
        ("Normal", body_size, False, None),
        ("Title", 24, True, "000000"),
        ("Heading 1", 16, True, "000000"),
        ("Heading 2", 14, True, "000000"),
        ("Heading 3", 12, True, "000000"),
    ]:
        style = doc.styles[style_name]
        set_style_font(style, font_name, size, bold=bold, color=color)
        style.paragraph_format.line_spacing = line_spacing
        style.paragraph_format.space_after = Pt(6)

    for name, size in (("Bid TOC Title", 16), ("Bid Section Title", 16), ("Bid Subheading", 11)):
        style = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = doc.styles["Normal"]
        set_style_font(style, font_name, size, bold=True, color="000000")
        style.paragraph_format.first_line_indent = Pt(0)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.keep_with_next = True

    update = doc.settings.element.find(qn("w:updateFields"))
    if update is None:
        update = OxmlElement("w:updateFields")
        doc.settings.element.append(update)
    update.set(qn("w:val"), "true")


def add_cover(doc: Document, subtitle: str, *, show_project_meta: bool = True) -> None:
    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.add_run("{bid_title}")

    subtitle_paragraph = doc.add_paragraph()
    subtitle_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle_paragraph.add_run(subtitle)

    if show_project_meta:
        meta = doc.add_paragraph()
        meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
        meta.add_run("项目编号：{bid_id}    模板：{template_name}")

    doc.add_paragraph()
    hint = doc.add_paragraph()
    hint.alignment = WD_ALIGN_PARAGRAPH.CENTER
    hint.add_run("智能标书生成系统 · 本地模板导出")
    doc.add_page_break()


def add_chapter_catalog(doc: Document) -> None:
    doc.add_paragraph("{#include_toc}")
    title = doc.add_paragraph("目 录", style="Bid TOC Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_toc_field(doc.add_paragraph())
    doc.add_page_break()
    doc.add_paragraph("{/include_toc}")


def add_body_placeholders(doc: Document, title: str) -> None:
    doc.add_paragraph(title, style="Bid Section Title")
    doc.add_paragraph("{#chapters}")
    for level in (1, 2, 3):
        doc.add_paragraph("{#chapter_heading" + str(level) + "}")
        doc.add_paragraph("{text}", style="Heading " + str(level))
        doc.add_paragraph("{/chapter_heading" + str(level) + "}")
    doc.add_paragraph("{#content_blocks}")
    doc.add_paragraph("{#heading1}")
    doc.add_paragraph("{text}", style="Bid Subheading")
    doc.add_paragraph("{/heading1}")
    doc.add_paragraph("{#heading2}")
    doc.add_paragraph("{text}", style="Bid Subheading")
    doc.add_paragraph("{/heading2}")
    doc.add_paragraph("{#heading3}")
    doc.add_paragraph("{text}", style="Bid Subheading")
    doc.add_paragraph("{/heading3}")
    doc.add_paragraph("{#body}")
    doc.add_paragraph("{text}")
    doc.add_paragraph("{/body}")
    doc.add_paragraph("{#image}")
    image_paragraph = doc.add_paragraph()
    image_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    image_paragraph.add_run("{%image}")
    doc.add_paragraph("{caption}")
    doc.add_paragraph("{/image}")
    doc.add_paragraph("{/content_blocks}")
    doc.add_paragraph("{/chapters}")


def add_loop_table(doc: Document, title: str, headers: list[str], row_values: list[str], accent: str) -> None:
    doc.add_heading(title, level=2)
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for cell, value in zip(table.rows[0].cells, headers):
        shade_cell(cell, accent)
        set_cell_text(cell, value, bold=True, color="FFFFFF")
    row = table.add_row()
    for cell, value in zip(row.cells, row_values):
        set_cell_text(cell, value)


def add_media_loop_table(doc: Document, title: str, headers: list[str], row_values: list[str], accent: str) -> None:
    doc.add_heading(title, level=2)
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for cell, value in zip(table.rows[0].cells, headers):
        shade_cell(cell, accent)
        set_cell_text(cell, value, bold=True, color="FFFFFF")
    loop_key = "rows"
    if row_values and row_values[0].startswith("{#") and "}" in row_values[0]:
        loop_key = row_values[0][2:row_values[0].find("}")]

    row = table.add_row()
    for index, value in enumerate(row_values):
        if index == 0:
            cell = row.cells[index]
            cell.text = ""
            start_paragraph = cell.paragraphs[0]
            start_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            start_paragraph.add_run("{#" + loop_key + "}")
            value_without_loop = value.split("}", 1)[1] if value.startswith("{#") and "}" in value else value
            value_paragraph = cell.add_paragraph()
            value_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            value_paragraph.add_run(value_without_loop)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            continue

        if index == len(row_values) - 1:
            # The image module requires {%image} to be the only raw tag in
            # its Word paragraph. Keep the image loop and row loop separate.
            cell = row.cells[index]
            cell.text = ""
            for paragraph_index, paragraph_text in enumerate([
                "{#image}",
                "{%image}",
                "{/image}",
                "{/" + loop_key + "}",
            ]):
                paragraph = cell.paragraphs[0] if paragraph_index == 0 else cell.add_paragraph()
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                paragraph.add_run(paragraph_text)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            continue

        set_cell_text(row.cells[index], value)


def build_standard_template() -> None:
    doc = Document()
    configure_document(doc, font_name="宋体", body_size=11, accent="1F4E79", line_spacing=1.5)
    add_header_footer(doc, "{bid_title} · 标准明标版")
    add_cover(doc, "标准明标版 · 公开招标项目")
    add_chapter_catalog(doc)
    add_body_placeholders(doc, "投标文件正文")
    doc.core_properties.title = "标准明标版"
    doc.core_properties.subject = "公开招标项目标准模板"
    doc.core_properties.author = "智能标书生成系统"
    doc.save(OUTPUT_DIR / "standard-public.docx")


def build_anonymous_template() -> None:
    doc = Document()
    configure_document(doc, font_name="等线", body_size=10.5, accent="5B6573", line_spacing=1.15)
    add_header_footer(doc, "技术方案 · 精简暗标版")
    add_cover(doc, "精简暗标版 · 隐去企业信息", show_project_meta=False)
    add_chapter_catalog(doc)
    add_body_placeholders(doc, "技术方案正文")
    doc.core_properties.title = "精简暗标版"
    doc.core_properties.subject = "暗标评审紧凑模板"
    doc.core_properties.author = "智能标书生成系统"
    doc.save(OUTPUT_DIR / "compact-anonymous.docx")


def build_food_delivery_template() -> None:
    doc = Document()
    configure_document(doc, font_name="宋体", body_size=11, accent="2F6B57", line_spacing=1.35)
    add_header_footer(doc, "{bid_title} · 食材配送专项版")
    add_cover(doc, "食材配送专项版 · 资质与履约资料模板")
    add_chapter_catalog(doc)
    doc.add_heading("食材配送专项响应表", level=1)
    add_media_loop_table(doc, "资质表", ["资质名称", "证书编号", "有效期", "备注", "材料图片"], ["{#qualification_rows}{certificate_name}", "{certificate_number}", "{valid_until}", "{remark}", "{#image}{%image}{/image}{/qualification_rows}"], "2F6B57")
    add_media_loop_table(doc, "人员表", ["姓名", "岗位", "证书/资质", "有效期", "材料图片"], ["{#personnel_rows}{name}", "{role}", "{certificate}", "{valid_until}", "{#image}{%image}{/image}{/personnel_rows}"], "2F6B57")
    add_media_loop_table(doc, "车辆表", ["车辆类型", "车牌号", "数量", "年检/保险", "材料图片"], ["{#vehicle_rows}{vehicle_type}", "{plate_number}", "{quantity}", "{inspection_date}", "{#image}{%image}{/image}{/vehicle_rows}"], "2F6B57")
    add_media_loop_table(doc, "设备表", ["设备名称", "型号", "数量", "状态", "材料图片"], ["{#equipment_rows}{name}", "{model}", "{quantity}", "{condition}", "{#image}{%image}{/image}{/equipment_rows}"], "2F6B57")
    doc.add_page_break()
    add_body_placeholders(doc, "图文标书正文")
    doc.core_properties.title = "食材配送专项版"
    doc.core_properties.subject = "食材配送投标专用模板"
    doc.core_properties.author = "智能标书生成系统"
    doc.save(OUTPUT_DIR / "food-delivery.docx")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_standard_template()
    build_anonymous_template()
    # The food-delivery template has its own maintained generator.
    runpy.run_path(str(ROOT / "scripts" / "build_food_delivery_template.py"))


if __name__ == "__main__":
    main()
