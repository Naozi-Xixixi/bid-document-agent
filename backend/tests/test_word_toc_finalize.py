from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

from lxml import etree

from app.services.word_toc_finalize import _freeze_export_layout, _toc_layout_table, _write_toc_pages, _stabilize_toc_pages, TocFinalizationError


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


class WordTocFinalizeTests(unittest.TestCase):
    def test_tab_leader_reaches_end_of_title_column_for_long_and_indented_titles(self) -> None:
        rows = [("TOC1", "1 短标题", "1"), ("TOC3", "1.1.1 采购验收与食品质量控制", "9999")]
        table = _toc_layout_table(rows, 9878)
        columns = table.xpath('./w:tblGrid/w:gridCol/@w:w', namespaces=NS)
        self.assertGreaterEqual(int(columns[1]), 4 * 120 + 60)
        self.assertEqual(
            table.xpath('./w:tr/w:tc[1]/w:p/w:pPr/w:tabs/w:tab/@w:pos', namespaces=NS),
            [str(int(columns[0]) - 40)] * 2,
        )
        self.assertEqual(table.xpath('./w:tr/w:tc[1]/w:p/w:pPr/w:tabs/w:tab/@w:leader', namespaces=NS), ["dot", "dot"])

    def test_export_keeps_page_number_but_removes_viewer_dependent_hyperlink(self) -> None:
        styles = f'''<w:styles xmlns:w="{W}"><w:style w:styleId="aa">
            <w:name w:val="Title"/></w:style></w:styles>'''
        document = f'''<w:document xmlns:w="{W}"><w:body>
            <w:p><w:pPr><w:pStyle w:val="aa"/></w:pPr><w:r><w:t>封面标题</w:t></w:r></w:p>
            <w:p><w:pPr><w:pStyle w:val="TOC1"/><w:tabs>
              <w:tab w:val="right" w:leader="dot" w:pos="9878"/>
            </w:tabs></w:pPr><w:r><w:fldChar w:fldCharType="begin"/></w:r>
              <w:hyperlink w:anchor="chapter"><w:r><w:t>1 第一章</w:t></w:r>
                <w:r><w:tab/></w:r><w:r><w:t>3</w:t></w:r></w:hyperlink>
              <w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
            </w:body></w:document>'''
        workspace = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=workspace) as folder:
            target = Path(folder) / "test.docx"
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("word/styles.xml", styles)
                archive.writestr("word/document.xml", document)
            self.assertEqual(_freeze_export_layout(target), ["chapter"])
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                root = etree.fromstring(archive.read("word/document.xml"))
            toc = root.xpath('//w:body/w:tbl', namespaces=NS)[0]
            texts = toc.xpath('./w:tr/w:tc/w:p/w:r/w:t/text()', namespaces=NS)
            self.assertEqual(texts[0], "1 第一章")
            self.assertEqual(texts[-1], "3")
            self.assertEqual(toc.xpath('./w:tr/w:tc[1]/w:p/w:pPr/w:tabs/w:tab/@w:leader', namespaces=NS), ["dot"])
            self.assertEqual(toc.xpath('./w:tr/w:tc[1]/w:p/w:pPr/w:tabs/w:tab/@w:val', namespaces=NS), ["right"])
            self.assertEqual(len(toc.xpath('./w:tr/w:tc[1]/w:p/w:r/w:tab', namespaces=NS)), 1)
            self.assertEqual(toc.xpath('./w:tblPr/w:tblW/@w:w', namespaces=NS), ["9878"])
            self.assertEqual(toc.xpath('./w:tr/w:tc[2]/w:p/w:pPr/w:jc/@w:val', namespaces=NS), ["right"])
            self.assertEqual(len(toc.xpath('./w:tr/w:tc', namespaces=NS)), 2)
            self.assertFalse(root.xpath('//w:hyperlink | //w:fldChar', namespaces=NS))
            with patch("app.services.word_toc_finalize._read_bookmark_pages", side_effect=[["4"], ["4"]]) as read_pages:
                _stabilize_toc_pages(target, ["chapter"])
                self.assertEqual(read_pages.call_count, 2)
            with zipfile.ZipFile(target) as archive:
                corrected = etree.fromstring(archive.read("word/document.xml"))
            self.assertEqual(corrected.xpath("//w:tbl/w:tr/w:tc[2]//w:t/text()", namespaces=NS), ["4"])
            self.assertFalse(_write_toc_pages(target, ["4"]))
            with self.assertRaises(TocFinalizationError):
                _write_toc_pages(target, [])
            with patch("app.services.word_toc_finalize._read_bookmark_pages", side_effect=[["5"], ["6"], ["7"]]):
                with self.assertRaisesRegex(TocFinalizationError, "稳定"):
                    _stabilize_toc_pages(target, ["chapter"])
            title = root.xpath('//w:p[w:pPr/w:pStyle[@w:val="aa"]]', namespaces=NS)[0]
            self.assertEqual(title.xpath('./w:r/w:rPr/w:color/@w:val', namespaces=NS), ["000000"])
            self.assertEqual(title.xpath('./w:r/w:rPr/w:sz/@w:val', namespaces=NS), ["48"])


if __name__ == "__main__":
    unittest.main()
