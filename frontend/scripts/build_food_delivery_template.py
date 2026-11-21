"""Build the formal food-delivery DOCX template used by the browser exporter.

The tender response uses the source sample's A4 page geometry and restrained
Chinese typography. Evidence images belong in the relevant chapter body,
never in a narrow summary table ahead of the response.
"""

from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


OUTPUT = Path(__file__).resolve().parents[1] / "src/assets/bid-templates/food-delivery.docx"


def font(style, family: str, size: int, *, bold: bool = False):
    style.font.name = family
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor(0, 0, 0)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    # Built-in Heading styles retain theme font attributes from the default
    # document. Word prefers those over explicit family names, which can mix
    # fallback fonts inside a single Chinese heading.
    for key in list(rfonts.attrib):
        if key.rsplit("}", 1)[-1].lower().endswith("theme"):
            del rfonts.attrib[key]
    rfonts.set(qn("w:eastAsia"), family)


def field(paragraph, instruction: str):
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    code = OxmlElement("w:instrText")
    code.set(qn("xml:space"), "preserve")
    code.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, code, separate, end):
        paragraph.add_run()._r.append(element)


def loop(doc, name: str, style: str, value: str):
    doc.add_paragraph("{#" + name + "}")
    doc.add_paragraph(value, style)
    doc.add_paragraph("{/" + name + "}")


doc = Document()
section = doc.sections[0]
section.page_width = Cm(21)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.54)
section.bottom_margin = Cm(2.54)
section.left_margin = Cm(1.91)
section.right_margin = Cm(1.91)
section.header_distance = Cm(1.2)
section.footer_distance = Cm(1.2)
section.different_first_page_header_footer = True

styles = doc.styles
normal = styles["Normal"]
font(normal, "宋体", 11)
normal.paragraph_format.line_spacing = 1.5
normal.paragraph_format.space_after = Pt(0)
normal.paragraph_format.first_line_indent = Pt(22)

title = styles["Title"]
font(title, "黑体", 22, bold=True)
title.paragraph_format.first_line_indent = Pt(0)
title.paragraph_format.space_after = Pt(0)

for level, size in ((1, 16), (2, 14), (3, 12)):
    style = styles[f"Heading {level}"]
    font(style, "宋体" if level == 3 else "黑体", size, bold=True)
    style.paragraph_format.first_line_indent = Pt(0)
    style.paragraph_format.space_before = Pt(16 if level == 1 else 10)
    style.paragraph_format.space_after = Pt(8 if level == 1 else 5)
    style.paragraph_format.keep_with_next = True

subheading = styles.add_style("Bid Subheading", WD_STYLE_TYPE.PARAGRAPH)
subheading.base_style = normal
font(subheading, "宋体", 11, bold=True)
subheading.paragraph_format.first_line_indent = Pt(0)
subheading.paragraph_format.space_before = Pt(8)
subheading.paragraph_format.space_after = Pt(3)
subheading.paragraph_format.keep_with_next = True

toc_title = styles.add_style("Bid TOC Title", WD_STYLE_TYPE.PARAGRAPH)
toc_title.base_style = normal
font(toc_title, "宋体", 16, bold=True)
toc_title.paragraph_format.first_line_indent = Pt(0)
toc_title.paragraph_format.space_after = Pt(12)

p = doc.add_paragraph(style="Title")
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.add_run("{bid_title}")
doc.add_paragraph("\n\n\n")
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("投 标 文 件")
run.bold = True
run.font.name = "黑体"
run.font.size = Pt(36)
run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "黑体")
doc.add_paragraph("\n\n\n\n")
p = doc.add_paragraph("投标人名称：{bidder_name}")
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.first_line_indent = Pt(0)
p = doc.add_paragraph("{bid_date}")
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.first_line_indent = Pt(0)

doc.add_page_break()
doc.add_paragraph("{#include_toc}")
p = doc.add_paragraph("目 录", style="Bid TOC Title")
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
field(doc.add_paragraph(), 'TOC \\o "1-3" \\h \\z \\u')
doc.add_page_break()
doc.add_paragraph("{/include_toc}")

doc.add_paragraph("{#chapters}")
loop(doc, "chapter_heading1", "Heading 1", "{text}")
loop(doc, "chapter_heading2", "Heading 2", "{text}")
loop(doc, "chapter_heading3", "Heading 3", "{text}")
doc.add_paragraph("{#content_blocks}")
loop(doc, "heading1", "Bid Subheading", "{text}")
loop(doc, "heading2", "Bid Subheading", "{text}")
loop(doc, "heading3", "Bid Subheading", "{text}")
loop(doc, "body", "Normal", "{text}")
doc.add_paragraph("{#image}")
pic = doc.add_paragraph("{%image}")
pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
pic.paragraph_format.first_line_indent = Pt(0)
pic.paragraph_format.keep_with_next = True
caption = doc.add_paragraph("{caption}")
caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
caption.paragraph_format.first_line_indent = Pt(0)
caption.paragraph_format.space_after = Pt(8)
doc.add_paragraph("{/image}")
doc.add_paragraph("{/content_blocks}")
doc.add_paragraph("{/chapters}")

footer = section.footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
footer.add_run("第 ")
field(footer, "PAGE")
footer.add_run(" 页")

settings = doc.settings.element
update = settings.find(qn("w:updateFields"))
if update is None:
    update = OxmlElement("w:updateFields")
    settings.append(update)
update.set(qn("w:val"), "true")

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(OUTPUT)
print(OUTPUT)
