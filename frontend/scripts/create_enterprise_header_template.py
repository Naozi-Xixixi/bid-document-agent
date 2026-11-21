from pathlib import Path
import shutil

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "assets" / "bid-templates" / "standard-public.docx"
TARGET = ROOT / "src" / "assets" / "bid-templates" / "enterprise-header.docx"
GREEN = "3F8E2F"
LIGHT_GREEN = "EAF4E6"
GRAY = "777777"


def set_cell_shading(cell, fill: str):
    properties = cell._tc.get_or_add_tcPr()
    shading = properties.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        properties.append(shading)
    shading.set(qn("w:fill"), fill)


def remove_table_borders(table):
    properties = table._tbl.tblPr
    borders = properties.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        properties.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "nil")


def set_cell_margins(cell, top=60, start=90, bottom=60, end=90):
    properties = cell._tc.get_or_add_tcPr()
    margins = properties.first_child_found_in("w:tcMar")
    if margins is None:
        margins = OxmlElement("w:tcMar")
        properties.append(margins)
    for key, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = margins.find(qn(f"w:{key}"))
        if node is None:
            node = OxmlElement(f"w:{key}")
            margins.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_cell_border(cell, **kwargs):
    properties = cell._tc.get_or_add_tcPr()
    borders = properties.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        properties.append(borders)
    for edge, data in kwargs.items():
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        for key, value in data.items():
            element.set(qn(f"w:{key}"), str(value))


def set_run_font(run, size=9, color=GREEN, bold=False):
    run.font.name = "Microsoft YaHei"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def add_page_field(paragraph):
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend((begin, instruction, separate, text, end))
    set_run_font(run, size=8, color=GREEN, bold=True)


def clear_container(container):
    for paragraph in list(container.paragraphs):
        element = paragraph._element
        element.getparent().remove(element)
    for table in list(container.tables):
        element = table._element
        element.getparent().remove(element)


def build_header(section):
    header = section.header
    clear_container(header)
    table = header.add_table(rows=1, cols=3, width=Inches(6.7))
    table.autofit = False
    remove_table_borders(table)
    widths = (1.95, 3.75, 1.0)
    for cell, width in zip(table.rows[0].cells, widths):
        cell.width = Inches(width)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_margins(cell, top=70, start=45, bottom=70, end=45)
    left, middle, right = table.rows[0].cells
    paragraph = left.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    leaf = paragraph.add_run("◆ ")
    set_run_font(leaf, size=12, color=GREEN, bold=True)
    text = paragraph.add_run("农业产业化龙头企业")
    set_run_font(text, size=8, color=GREEN, bold=True)
    set_cell_shading(middle, GREEN)
    middle_paragraph = middle.paragraphs[0]
    middle_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    marker = middle_paragraph.add_run("示例公司标书编排中心")
    set_run_font(marker, size=8, color="FFFFFF", bold=True)
    right_paragraph = right.paragraphs[0]
    right_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    brand = right_paragraph.add_run("示例配送")
    set_run_font(brand, size=9, color=GREEN, bold=True)
    rule = header.add_paragraph()
    rule.paragraph_format.space_before = Pt(0)
    rule.paragraph_format.space_after = Pt(0)
    rule.paragraph_format.line_spacing = Pt(1)
    run = rule.add_run(" ")
    run.font.size = Pt(1)
    run.font.color.rgb = RGBColor.from_string(GREEN)
    ppr = rule._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "8")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), GREEN)
    borders.append(bottom)
    ppr.append(borders)


def build_footer(section):
    footer = section.footer
    clear_container(footer)
    table = footer.add_table(rows=1, cols=3, width=Inches(6.7))
    table.autofit = False
    remove_table_borders(table)
    widths = (3.0, 0.7, 3.0)
    for cell, width in zip(table.rows[0].cells, widths):
        cell.width = Inches(width)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_margins(cell, top=70, start=45, bottom=50, end=45)
    left, center, right = table.rows[0].cells
    for cell in table.rows[0].cells:
        set_cell_border(cell, top={"val": "single", "sz": "8", "color": GREEN, "space": "1"})
    left_paragraph = left.paragraphs[0]
    left_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    left_run = left_paragraph.add_run("汇集四方绿色  配送万家新鲜")
    set_run_font(left_run, size=8, color=GREEN, bold=True)
    center_paragraph = center.paragraphs[0]
    center_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_page_field(center_paragraph)
    right_paragraph = right.paragraphs[0]
    right_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    arrows = right_paragraph.add_run(">>>")
    set_run_font(arrows, size=14, color=GREEN, bold=True)


def main():
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, TARGET)
    document = Document(str(TARGET))
    for section in document.sections:
        section.top_margin = Inches(0.85)
        section.bottom_margin = Inches(0.72)
        section.header_distance = Inches(0.28)
        section.footer_distance = Inches(0.28)
        build_header(section)
        build_footer(section)
    document.save(str(TARGET))
    print(TARGET)


if __name__ == "__main__":
    main()
