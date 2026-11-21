"""Word body tables retain cell alignment and half-line trailing space."""
from io import BytesIO
import unittest

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from docx.oxml.ns import qn

from app.services.bid_graphic_service import _append_markdown_table, _append_markdown_to_doc, _apply_doc_template


class GraphicTableLayoutTests(unittest.TestCase):
    def test_native_export_sets_chinese_fonts_and_two_character_body_indent(self):
        doc = Document()
        _apply_doc_template(doc, "default-business")
        _append_markdown_to_doc(doc, "正文段落。\n\n# 小标题", bid_id=1)
        saved = BytesIO()
        doc.save(saved)
        saved.seek(0)
        reopened = Document(saved)

        body, heading = reopened.paragraphs
        indent = body._p.pPr.find(qn("w:ind"))
        self.assertEqual(indent.get(qn("w:firstLineChars")), "200")
        self.assertIsNone(heading._p.pPr.find(qn("w:ind")))
        self.assertEqual(heading.style.font.color.rgb, RGBColor(0, 0, 0))
        for style, expected in ((body.style, "宋体"), (heading.style, "黑体")):
            fonts = style.element.find(qn("w:rPr")).find(qn("w:rFonts"))
            self.assertEqual(fonts.get(qn("w:eastAsia")), expected)
            self.assertIsNone(fonts.get(qn("w:eastAsiaTheme")))

    def test_signature_fields_align_left_and_explicit_emphasis_survives(self):
        doc = Document()
        _append_markdown_to_doc(
            doc,
            "请核对**证明材料**。\n**投标人名称（加盖电子签章）**：[待核实]\n"
            "法定代表人或其授权代表（签字或电子签章）：[待核实]\n日期：[待核实]",
            bid_id=1,
        )
        body, *fields = doc.paragraphs
        self.assertEqual(body._p.pPr.ind.get(qn("w:firstLineChars")), "200")
        self.assertEqual([(run.text, run.bold) for run in body.runs], [
            ("请核对", None), ("证明材料", True), ("。", None),
        ])
        self.assertEqual(len(fields), 3)
        for field in fields:
            self.assertEqual(field.alignment, WD_ALIGN_PARAGRAPH.LEFT)
            self.assertEqual(field.paragraph_format.left_indent, Pt(0))
            self.assertEqual(field._p.pPr.ind.get(qn("w:firstLineChars")), "0")
        self.assertTrue(fields[0].runs[0].bold)

    def test_cells_center_both_ways_and_table_has_half_line_after(self):
        doc = Document()
        _append_markdown_table(doc, [["资源类别", "拟配置情况"], ["车辆", "两辆冷藏车"]])
        doc.add_paragraph("后续正文")
        saved = BytesIO()
        doc.save(saved)
        saved.seek(0)
        reopened = Document(saved)

        for row in reopened.tables[0].rows:
            self.assertIsNotNone(row._tr.trPr.find(qn("w:cantSplit")))
            for cell in row.cells:
                self.assertEqual(cell.vertical_alignment, WD_CELL_VERTICAL_ALIGNMENT.CENTER)
                for paragraph in cell.paragraphs:
                    self.assertEqual(paragraph.alignment, WD_ALIGN_PARAGRAPH.CENTER)
                    self.assertEqual(paragraph.paragraph_format.first_line_indent, Pt(0))
                    self.assertEqual(paragraph.paragraph_format.space_before, Pt(0))
                    self.assertEqual(paragraph.paragraph_format.space_after, Pt(0))

        spacer, following = reopened.paragraphs
        self.assertEqual(spacer.text, "")
        self.assertEqual(spacer.paragraph_format.line_spacing, Pt(6))
        self.assertTrue(spacer.paragraph_format.keep_with_next)
        self.assertEqual(following.text, "后续正文")
