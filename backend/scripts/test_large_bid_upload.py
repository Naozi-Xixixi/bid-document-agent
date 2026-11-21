"""Offline regression checks for bounded bid uploads and media-free DOCX parsing.

Run from backend: python -m unittest discover -s scripts -p test_large_bid_upload.py -v
No model calls or application database writes are performed.
"""
from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import tracemalloc
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from app.services import bid_parse_service as service
from app.utils.bid_parser import parser


class GeneratedUpload:
    """Seekable generated stream: fail if the application requests an unbounded read."""
    def __init__(self, size: int, delay: float = 0):
        self.size = size
        self.offset = 0
        self.requests: list[int] = []
        self.delay = delay
        self.started = threading.Event()

    def seek(self, offset: int):
        self.offset = offset

    def read(self, size: int):
        if not 0 < size <= service.UPLOAD_COPY_CHUNK_SIZE:
            raise AssertionError(f"unbounded upload read: {size}")
        self.requests.append(size)
        self.started.set()
        if self.delay:
            time.sleep(self.delay)
        count = min(size, self.size - self.offset)
        self.offset += count
        return b"x" * count


class UploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache = Path(__file__).resolve().parents[2] / ".cache"
        cache.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="bid-upload-test-", dir=cache)
        self.root = Path(self.temp.name)
        self.data = patch.object(service.settings, "data_dir", self.root)
        self.limit = patch.object(service.settings, "bid_max_upload_size_mb", 32)
        self.data.start()
        self.limit.start()

    def tearDown(self):
        self.limit.stop()
        self.data.stop()
        self.temp.cleanup()

    async def test_large_upload_uses_bounded_reads_and_memory(self):
        stream = GeneratedUpload(16 * 1024 * 1024 + 7)
        upload = UploadFile(stream, filename="large.docx")
        tracemalloc.start()
        try:
            saved = await service._save_upload(1, "tender", upload, service.TENDER_EXTENSIONS)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        target = self.root / saved["file_path"]
        self.assertEqual(target.stat().st_size, stream.size)
        self.assertEqual(saved["file_size"], stream.size)
        self.assertLessEqual(max(stream.requests), 1024 * 1024)
        self.assertLess(peak, 6 * 1024 * 1024, f"upload allocated {peak} bytes")
        self.assertFalse(list(self.root.rglob("*.part")))

    async def test_unknown_size_over_limit_has_no_file_or_database_record(self):
        stream = GeneratedUpload(1024 * 1024 + 1)
        upload = UploadFile(stream, filename="too-large.docx")
        with patch.object(service.settings, "bid_max_upload_size_mb", 1), patch.object(service, "_ensure_bid"), patch.object(service.storage, "connect") as connect:
            with self.assertRaises(service.BidParseServiceError) as caught:
                await service.upload_and_start_parse(1, upload)
            self.assertEqual(caught.exception.status_code, 413)
            self.assertEqual(caught.exception.code, 40002)
            connect.assert_not_called()
        self.assertFalse([p for p in self.root.rglob("*") if p.is_file()])

    async def test_known_size_rejected_without_reading(self):
        stream = GeneratedUpload(1024 * 1024 + 1)
        upload = UploadFile(stream, filename="too-large.docx", size=stream.size)
        with patch.object(service.settings, "bid_max_upload_size_mb", 1):
            with self.assertRaises(service.BidParseServiceError):
                await service._save_upload(1, "tender", upload)
        self.assertEqual(stream.requests, [])
        self.assertFalse([p for p in self.root.rglob("*") if p.is_file()])

    async def test_empty_upload_has_no_file(self):
        with self.assertRaises(service.BidParseServiceError):
            await service._save_upload(1, "tender", UploadFile(GeneratedUpload(0), filename="empty.docx"))
        self.assertFalse([p for p in self.root.rglob("*") if p.is_file()])

    async def test_copy_does_not_block_event_loop_and_cancellation_cleans_up(self):
        stream = GeneratedUpload(8 * 1024 * 1024, delay=0.05)
        task = asyncio.create_task(service._save_upload(1, "tender", UploadFile(stream, filename="cancel.docx")))
        self.assertTrue(await asyncio.to_thread(stream.started.wait, 5))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse([p for p in self.root.rglob("*") if p.is_file()])
        self.assertLess(stream.offset, stream.size)


class DocxTests(unittest.TestCase):
    def test_preserves_headings_tables_and_order_without_opening_media(self):
        cache = Path(__file__).resolve().parents[2] / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="bid-docx-test-", dir=cache) as folder:
            target = Path(folder) / "fixture.docx"
            document = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:pPr><w:pStyle w:val="CustomHeading"/></w:pPr><w:r><w:t>Delivery requirements</w:t></w:r></w:p>
<w:p><w:r><w:t>Arrive before 07:00.</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Metric</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>Response</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>30 minutes</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:p><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Final checklist</w:t></w:r></w:p>
</w:body></w:document>'''
            styles = '''<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:styleId="Heading2"><w:name w:val="heading 2"/></w:style><w:style w:styleId="CustomHeading"><w:basedOn w:val="Heading2"/></w:style></w:styles>'''
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("word/document.xml", document)
                archive.writestr("word/styles.xml", styles)
                archive.writestr("word/media/unread-image.bin", b"media must not be read")
                archive.writestr("word/embeddings/unread-object.bin", b"embedding must not be read")
            opened: list[str] = []
            original_open = zipfile.ZipFile.open

            def checked_open(archive, name, *args, **kwargs):
                key = name.filename if isinstance(name, zipfile.ZipInfo) else name
                self.assertIn(key, {"word/document.xml", "word/styles.xml"})
                opened.append(key)
                return original_open(archive, name, *args, **kwargs)

            with patch.object(zipfile.ZipFile, "open", checked_open):
                result = parser.parse_bid_document(str(target))
            markdown = result.full_markdown
            self.assertIn("## Delivery requirements", markdown)
            self.assertIn("| Response | 30 minutes |", markdown)
            self.assertIn("- Final checklist", markdown)
            self.assertLess(markdown.index("Arrive before"), markdown.index("| Metric |"))
            self.assertLess(markdown.index("| Response |"), markdown.index("Final checklist"))
            self.assertEqual(set(opened), {"word/document.xml", "word/styles.xml"})
            self.assertTrue(result.chapter_blocks)

    def test_merged_cells_keep_column_positions(self):
        cache = Path(__file__).resolve().parents[2] / ".cache"
        with tempfile.TemporaryDirectory(prefix="bid-docx-merge-", dir=cache) as folder:
            target = Path(folder) / "merged.docx"
            xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>
<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Merged</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl></w:body></w:document>'''
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("word/document.xml", xml)
            markdown = parser.convert_docx_to_markdown(str(target))
            self.assertIn("| Merged | Merged | A |", markdown)
            self.assertIn("| Merged | Merged | B |", markdown)

    def test_corrupt_docx_reports_parse_error(self):
        cache = Path(__file__).resolve().parents[2] / ".cache"
        with tempfile.TemporaryDirectory(prefix="bid-docx-bad-", dir=cache) as folder:
            target = Path(folder) / "bad.docx"
            target.write_bytes(b"not a zip")
            with self.assertRaises(parser.BidParseError):
                parser.parse_bid_document(str(target))


if __name__ == "__main__":
    unittest.main()
