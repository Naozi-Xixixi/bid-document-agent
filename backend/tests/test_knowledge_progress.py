"""Offline knowledge progress tests; no real documents, DBs or model calls."""
from __future__ import annotations

from contextlib import ExitStack, closing
from contextvars import ContextVar
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.modules.knowledge import service, router
from app.services import storage


class KnowledgeProgressTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[2] / ".cache" / "knowledge-progress-tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.connections = []
        self.addCleanup(lambda: [conn.close() for conn in self.connections])
        self.path = Path(self.temp.name) / "isolated.sqlite3"
        self.stack.enter_context(patch.object(service.settings, "database_path", self.path))
        self.stack.enter_context(patch.object(service.settings, "db_backend", "sqlite"))
        self.stack.enter_context(patch.object(service.storage, "connect", side_effect=self.connect))
        with closing(self.connect()) as conn:
            storage.init_db(conn)
        service.ensure_schema()
        now = "2026-10-20T00:00:00+00:00"
        with closing(self.connect()) as conn:
            conn.execute("""
                INSERT INTO knowledge_item (
                    item_id, title, file_type, source_name, content_text,
                    status, process_progress, deep_status, deep_progress, deep_total,
                    extracted_image_count, text_char_count, vector_status, vector_indexed_chunks,
                    create_time, update_time
                ) VALUES (1, 'offline', 'docx', 'offline.docx', ?, 'completed', 100,
                          'processing', 66, 3, 0, 20000, 'indexing', 1, ?, ?)
            """, ("PRIVATE_TEXT" * 2000, now, now))
            for index, (candidate, status) in enumerate(((1, "completed"), (1, "failed"), (1, "pending"), (0, "completed")), start=1):
                conn.execute("INSERT INTO knowledge_asset (asset_id, item_id, asset_type, deep_candidate, deep_status, ocr_text, create_time) VALUES (?, 1, '图片', ?, ?, ?, ?)", (index, candidate, status, "PRIVATE_OCR" * 1000, now))
            conn.execute("INSERT INTO knowledge_item_version (item_id, title, version_no, content_text, create_time) VALUES (1, 'offline', 1, ?, ?)", ("PRIVATE_VERSION" * 2000, now))
            for index in range(3):
                conn.execute("INSERT INTO knowledge_chunk (item_id, chunk_index, content, create_time) VALUES (1, ?, 'offline chunk', ?)", (index, now))
            conn.commit()

    def connect(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self.connections.append(conn)
        return conn

    def test_poll_reads_only_scalars_and_live_counts_not_cached_zero(self):
        denied = {("knowledge_item", "content_text"), ("knowledge_asset", "ocr_text"), ("knowledge_item_version", "content_text"), ("knowledge_chunk", "content")}
        def progress_connection():
            conn = self.connect()
            conn.set_authorizer(lambda action, table, column, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and (table, column) in denied else sqlite3.SQLITE_OK)
            return conn
        with patch.object(service, "get_item", side_effect=AssertionError("polling must not load detail")), patch.object(service.storage, "connect", side_effect=progress_connection):
            result = service.item_status(1)
        self.assertEqual((result["status"], result["process_progress"]), ("completed", 100))
        self.assertEqual((result["deep_status"], result["deep_progress"]), ("processing", 66))
        self.assertEqual((result["asset_count"], result["extracted_image_count"]), (4, 4))
        self.assertEqual((result["deep_completed_count"], result["deep_failed_count"], result["deep_processed_count"]), (1, 1, 2))
        self.assertEqual((result["vector_status"], result["vector_indexed_chunks"], result["chunk_count"]), ("indexing", 1, 3))
        self.assertTrue({"content_text", "assets", "versions", "chunks"}.isdisjoint(result))

    def test_list_detail_and_poll_share_live_asset_counts(self):
        values = [service.list_items()["items"][0], service.get_item(1), service.item_status(1)]
        for result in values:
            self.assertEqual((result["extracted_image_count"], result["deep_completed_count"], result["deep_failed_count"], result["deep_processed_count"]), (4, 1, 1, 2))
            self.assertEqual(result["chunk_count"], 3)

    def test_failed_deep_and_vector_are_not_hidden_by_text_completion(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET deep_status='failed', deep_progress=100, deep_error='1 asset failed', vector_status='failed', vector_error='offline embedding unavailable' WHERE item_id=1")
            conn.commit()
        result = service.item_status(1)
        self.assertEqual((result["status"], result["process_progress"]), ("completed", 100))
        self.assertEqual((result["deep_status"], result["deep_failed_count"], result["deep_error"]), ("failed", 1, "1 asset failed"))
        self.assertEqual((result["vector_status"], result["vector_error"]), ("failed", "offline embedding unavailable"))

    def test_no_assets_does_not_reuse_cached_count(self):
        with closing(self.connect()) as conn:
            conn.execute("DELETE FROM knowledge_asset WHERE item_id=1")
            conn.execute("UPDATE knowledge_item SET extracted_image_count=99, deep_total=0, deep_progress=100, deep_status='completed', vector_status='not_configured' WHERE item_id=1")
            conn.commit()
        result = service.item_status(1)
        self.assertEqual((result["asset_count"], result["extracted_image_count"], result["deep_processed_count"]), (0, 0, 0))
        self.assertEqual(result["vector_status"], "not_configured")

    def test_missing_item_is_404(self):
        with self.assertRaises(service.KnowledgeModuleError) as error:
            service.item_status(999)
        self.assertEqual(error.exception.status_code, 404)


class KnowledgeProgressRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_query_runs_off_event_loop_and_keeps_context(self):
        marker = ContextVar("knowledge_progress_tenant", default="missing")
        token = marker.set("tenant_a")
        self.addCleanup(marker.reset, token)
        caller = threading.get_ident()
        observed = []
        def status(item_id):
            observed.append((threading.get_ident(), marker.get(), item_id))
            return {"item_id": item_id, "vector_status": "indexing"}
        with patch.object(service, "item_status", side_effect=status):
            response = await router.item_status(7)
        self.assertEqual(response["data"]["vector_status"], "indexing")
        self.assertNotEqual(observed[0][0], caller)
        self.assertEqual(observed[0][1:], ("tenant_a", 7))

    async def test_status_query_keeps_not_found_response(self):
        with patch.object(service, "item_status", side_effect=service.KnowledgeModuleError(40401, "not found", 404)):
            response = await router.item_status(999)
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
