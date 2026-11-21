"""A customer DOCX becomes a small editable template without old bid facts."""

from pathlib import Path
import tempfile
import unittest
import zipfile

from docx import Document

from app.services.word_template_service import _write_template


class WordTemplateExtractionTests(unittest.TestCase):
    def test_preserves_header_and_removes_historical_body(self):
        parent = Path(__file__).resolve().parents[2] / ".cache" / "word-template-tests"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            source = Path(folder) / "customer.docx"
            target = Path(folder) / "template.docx"
            document = Document()
            document.sections[0].header.paragraphs[0].text = "示例公司页眉"
            document.add_paragraph("旧项目名称和旧客户金额必须移除")
            document.add_table(rows=1, cols=2).style = "Table Grid"
            document.save(source)

            rules = _write_template(source, target)
            with zipfile.ZipFile(target) as package:
                body = package.read("word/document.xml").decode("utf-8")
                self.assertIsNone(package.testzip())
                self.assertIn("{bid_title}", body)
                self.assertIn("{#chapters}", body)
                self.assertNotIn("旧项目名称", body)
                self.assertTrue(any(name.startswith("word/header") for name in package.namelist()))
            self.assertTrue(rules["page"]["header_footer"]["header"])
            self.assertEqual(rules["source"]["file_name"], "customer.docx")
