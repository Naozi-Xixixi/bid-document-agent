from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "src" / "templates"


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
    rfonts.set(qn("w:eastAsia"), name)


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run("第 ")
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


def shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def build_template(filename: str, *, body_font: str, body_size: float, accent: str, subtitle: str) -> None:
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.85)
    section.bottom_margin = Inches(0.75)
    section.left_margin = Inches(0.9)
    section.right_margin = Inches(0.9)

    set_style_font(doc.styles["Normal"], body_font, body_size)
    set_style_font(doc.styles["Title"], body_font, 24, bold=True, color=accent)
    set_style_font(doc.styles["Heading 1"], body_font, 16, bold=True, color=accent)
    set_style_font(doc.styles["Heading 2"], body_font, 14, bold=True, color=accent)
    set_style_font(doc.styles["Heading 3"], body_font, 12, bold=True, color=accent)

    footer = section.footer.paragraphs[0]
    add_page_number(footer)

    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.add_run("{bid_title}")
    subtitle_run = doc.add_paragraph()
    subtitle_run.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle_run.add_run(subtitle)
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run("项目编号：{bid_id}    模板：{template_name}")
    doc.add_paragraph()
    cover_hint = doc.add_paragraph()
    cover_hint.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cover_hint.add_run("智能标书生成系统 · 图文标书导出")
    doc.add_page_break()

    doc.add_heading("目录", level=1)
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    headers = ["编号", "章节标题", "目标字数", "状态"]
    for cell, value in zip(table.rows[0].cells, headers):
        shade_cell(cell, accent)
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(value)
        run.bold = True
        run.font.color.rgb = RGBColor(255, 255, 255)
    row = table.add_row()
    row.cells[0].text = "{#chapter_rows}{number}"
    row.cells[1].text = "{title}"
    row.cells[2].text = "{word_count}"
    row.cells[3].text = "{status}{/chapter_rows}"
    doc.add_page_break()

    doc.add_heading("图文标书正文", level=1)
    doc.add_paragraph("{#chapters}")
    doc.add_paragraph("{chapter_heading}", style="Heading 2")
    doc.add_paragraph("{chapter_content}")
    doc.add_paragraph("{#images}")
    image_paragraph = doc.add_paragraph()
    image_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    image_paragraph.add_run("{%image}")
    doc.add_paragraph("{/images}")
    doc.add_paragraph("{/chapters}")

    doc.core_properties.title = "智能标书生成系统"
    doc.core_properties.subject = "图文标书正文"
    doc.core_properties.author = "智能标书生成系统"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_DIR / filename)


def main() -> None:
    build_template("default-business.docx", body_font="宋体", body_size=11, accent="1F4E79", subtitle="默认商务标书模板")
    build_template("compact-technical.docx", body_font="等线", body_size=10.5, accent="2F5597", subtitle="技术方案紧凑模板")
    build_template("formal-government.docx", body_font="宋体", body_size=12, accent="7F1D1D", subtitle="政采正式模板")


if __name__ == "__main__":
    main()
