"""Offline progress contracts; all databases and model responses are disposable."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, closing
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.services import bid_parse_service as service, storage
from app.utils.bid_extractor import ai_extractor as ai


class AIProgressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(ai.settings, "enable_llm_parse_optimize", True))
        self.stack.enter_context(patch.object(ai.settings, "qwen_api_key", "offline-test"))
        self.kwargs = dict(full_markdown="PRIVATE_DOCUMENT", chapter_blocks=[], located_blocks={}, rule_result={"project_overview": "概况", "score_requirements": "评分"}, bid_type="service")
        self.progress = service._new_extraction_progress()
        service._advance_extraction_progress(self.progress, "prepare", "completed", 1)
        self.events, self.snapshots = [], []

    async def report(self, event):
        self.events.append(event)
        service._advance_extraction_progress(self.progress, **event)
        self.snapshots.append(json.loads(json.dumps(self.progress)))

    def result(self, text="{}", used=True):
        return SimpleNamespace(content=text, usedApi=used, error=None)

    async def test_retry_does_not_increase_completed_and_summary_is_below_100(self):
        response = AsyncMock(side_effect=[self.result("bad JSON"), *[self.result() for _ in range(6)]])
        with patch.object(ai.qwen_client, "chat_completion", response):
            await ai.extract_bid_info_with_ai(**self.kwargs, progress_callback=self.report)
        category = [(event["status"], event["attempt"]) for event in self.events if event["key"] == "project_overview"]
        self.assertEqual(category, [("running", 1), ("running", 2), ("completed", 2)])
        self.assertEqual([snapshot["completed"] for snapshot in self.snapshots[:3]], [1, 1, 2])
        self.assertEqual((self.progress["status"], self.progress["completed"], self.progress["percent"]), ("running", 7, 87))
        self.assertNotIn("PRIVATE_DOCUMENT", json.dumps(self.events))
        self.assertTrue(all(set(event) == {"key", "status", "attempt"} for event in self.events))

    async def test_exhausted_retry_and_disabled_ai_report_fallback(self):
        with patch.object(ai.qwen_client, "chat_completion", AsyncMock(side_effect=[self.result("bad"), self.result("bad"), *[self.result() for _ in range(5)]])):
            result = await ai.extract_bid_info_with_ai(**self.kwargs, progress_callback=self.report)
        self.assertIn("project_overview", result["degraded_categories"])
        self.assertEqual(self.events[2], {"key": "project_overview", "status": "fallback", "attempt": 2})
        self.events.clear()
        with patch.object(ai.settings, "enable_llm_parse_optimize", False), patch.object(ai.qwen_client, "chat_completion", AsyncMock()) as model:
            result = await ai.extract_bid_info_with_ai(**self.kwargs, progress_callback=self.report)
        model.assert_not_called()
        self.assertFalse(result["used_api"])
        self.assertEqual(len(self.events), 6)
        self.assertTrue(all(event["status"] == "fallback" and event["attempt"] == 0 for event in self.events))

    async def test_callback_failure_does_not_discard_extraction(self):
        async def broken(event):
            raise RuntimeError("telemetry unavailable")
        with patch.object(ai.qwen_client, "chat_completion", AsyncMock(return_value=self.result())):
            result = await ai.extract_bid_info_with_ai(**self.kwargs, progress_callback=broken)
        self.assertTrue(result["used_api"])

    def test_save_cannot_publish_completed_with_pending_items(self):
        service._advance_extraction_progress(self.progress, "save", "completed", 1)
        self.assertNotEqual(self.progress["status"], "completed")
        self.assertLess(self.progress["percent"], 100)


class PersistedProgressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(service._ACTIVE_PARSE_TASKS.clear)
        self.company = "company_a"
        self.connections = []
        self.addCleanup(lambda: [connection.close() for connection in self.connections])
        self.paths = {company: Path(self.temp.name) / (company + ".sqlite") for company in ("company_a", "company_b")}
        for path in self.paths.values():
            with closing(sqlite3.connect(path)) as conn:
                storage.init_db(conn)
                now = service._now_text()
                conn.execute("INSERT INTO projects (id, state_json, created_at, updated_at) VALUES ('p', '{}', ?, ?)", (now, now))
                conn.execute("INSERT INTO bids (bid_id, project_id, create_time, update_time) VALUES (1, 'p', ?, ?)", (now, now))
                conn.execute("INSERT INTO bid_parse_info (bid_id, parse_mode, tender_file_path, parse_status, create_time, update_time) VALUES (1, 'auto_upload', 'offline.docx', 'processing', ?, ?)", (now, now))
                conn.commit()
        self.stack.enter_context(patch.object(service.storage, "connect", side_effect=self.connect))
        self.stack.enter_context(patch.object(service, "get_current_tenant", side_effect=lambda: SimpleNamespace(company_id=self.company)))
        self.stack.enter_context(patch.object(service, "_ensure_bid", return_value={"current_step": 2}))
        self.stack.enter_context(patch.object(service.settings, "db_backend", "sqlite"))
        self.stack.enter_context(patch.object(service.settings, "enable_llm_parse_optimize", False))
        self.stack.enter_context(patch.object(service, "_absolute_data_path", return_value=Path("offline.docx")))
        self.stack.enter_context(patch.object(service, "parse_bid_document", return_value=SimpleNamespace(raw_text="原文" * 300, raw_markdown="原文", full_markdown="原文", chapter_blocks=[])))
        self.rules = {"project_overview": "概况", "score_requirements": "评分要求"}
        self.stack.enter_context(patch.object(service, "extract_bid_info", return_value=self.rules))
        self.stack.enter_context(patch.object(service, "locate_core_blocks", return_value={"categories": {}}))

    def connect(self):
        conn = sqlite3.connect(self.paths[self.company], check_same_thread=False)
        self.connections.append(conn)
        conn.row_factory = sqlite3.Row
        return conn

    def row(self):
        with closing(self.connect()) as conn:
            return dict(conn.execute("SELECT * FROM bid_parse_info WHERE bid_id = 1").fetchone())

    async def test_real_pipeline_persists_progress_and_only_committed_save_reaches_100(self):
        snapshots = []
        original = service._write_extraction_progress
        def write(bid, path, serialized, expected):
            snapshot = json.loads(serialized)
            snapshots.append(snapshot)
            if snapshot["status"] == "completed":
                self.assertEqual(self.row()["parse_status"], "completed")
            original(bid, path, serialized, expected)
        with patch.object(service, "_write_extraction_progress", write):
            await service.run_auto_parse_task(1, "offline.docx")
        self.assertTrue(all(item["percent"] < 100 for item in snapshots[:-1]))
        self.assertEqual((snapshots[-1]["completed"], snapshots[-1]["total"], snapshots[-1]["percent"]), (8, 8, 100))
        self.assertEqual(snapshots[-1]["status"], "completed")
        self.assertTrue(snapshots[0]["started_at"].endswith("+00:00"))
        self.assertEqual(service.get_parse_status(1)["extraction_progress"], snapshots[-1])
        self.assertEqual(service.get_parse_info(1)["extraction_progress"], snapshots[-1])
        self.assertFalse(service._ACTIVE_PARSE_TASKS)

    async def test_progress_write_failure_preserves_main_result(self):
        with patch.object(service, "_write_extraction_progress", side_effect=sqlite3.OperationalError("test progress failure")):
            await service.run_auto_parse_task(1, "offline.docx")
        self.assertEqual(self.row()["parse_status"], "completed")
        self.assertEqual(self.row()["parsed_project_overview"], "概况")

    async def test_save_failure_never_publishes_100_and_persists_failed_progress(self):
        class FailResultSave:
            def __init__(self, conn):
                self.conn = conn
            def __getattr__(self, name):
                return getattr(self.conn, name)
            def __enter__(self):
                self.conn.__enter__()
                return self
            def __exit__(self, *args):
                return self.conn.__exit__(*args)
            def execute(self, query, params=()):
                if "SET raw_full_text = ?" in query:
                    raise sqlite3.OperationalError("test result commit failure")
                return self.conn.execute(query, params)
        with patch.object(service.storage, "connect", side_effect=lambda: FailResultSave(self.connect())):
            await service.run_auto_parse_task(1, "offline.docx")
        progress = service.get_parse_status(1)["extraction_progress"]
        self.assertEqual(self.row()["parse_status"], "failed")
        self.assertEqual(progress["status"], "failed")
        self.assertEqual(progress["current_key"], "save")
        self.assertEqual(progress["percent"], 87)

    async def test_cancel_during_ai_persists_failed_progress(self):
        started = asyncio.Event()
        async def wait_for_ai(**kwargs):
            await kwargs["progress_callback"]({"key": "scoring", "status": "running", "attempt": 1})
            started.set()
            await asyncio.Event().wait()
        with patch.object(service, "extract_bid_info_with_ai", wait_for_ai):
            task = asyncio.create_task(service.run_auto_parse_task(1, "offline.docx"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        progress = service.get_parse_status(1)["extraction_progress"]
        self.assertEqual(progress["status"], "failed")
        self.assertEqual(progress["current_key"], "scoring")
        self.assertLess(progress["percent"], 100)

    async def test_tenant_file_isolation_upload_and_reparse_reset(self):
        progress = service._new_extraction_progress()
        await service._report_extraction_progress(1, "offline.docx", progress, {"key": "prepare", "status": "completed"})
        self.assertIsNotNone(self.row()["extraction_progress"])
        self.company = "company_b"
        self.assertIsNone(service.get_parse_status(1)["extraction_progress"])
        self.company = "company_a"
        service.prepare_reparse(1)
        self.assertIsNone(self.row()["extraction_progress"])
        await service._report_extraction_progress(1, "offline.docx", progress, {"key": "prepare", "status": "completed"})
        service._ACTIVE_PARSE_TASKS[(self.company, 1, "offline.docx")] = {object()}
        with self.assertRaises(service.BidParseServiceError) as error:
            service.prepare_reparse(1)
        self.assertEqual(error.exception.code, 40901)
        service._ACTIVE_PARSE_TASKS.clear()
        saved = {"file_path": "new.docx", "file_name": "new.docx", "file_size": 123}
        with patch.object(service, "_save_upload", AsyncMock(return_value=saved)), patch.object(service, "_delete_data_file"):
            await service.upload_and_start_parse(1, object())
        self.assertIsNone(self.row()["extraction_progress"])
        await service._report_extraction_progress(1, "offline.docx", progress, {"key": "scoring", "status": "running", "attempt": 1})
        self.assertIsNone(self.row()["extraction_progress"])

    def test_polling_uses_scalar_projection_and_tolerates_old_invalid_json(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE bid_parse_info SET raw_full_text=?, chapter_blocks=?, parsed_project_overview=?, parsed_score_requirements=? WHERE bid_id=1", ("文" * 10000, "{bad", "概" * 200, "评分"))
            conn.commit()
        with patch.object(service, "get_parse_info", side_effect=AssertionError("poll must not load detail")):
            result = service.get_parse_status(1)
        self.assertEqual(result["raw_full_text_length"], 10000)
        self.assertEqual(result["chapter_block_count"], 0)
        self.assertEqual(len(result["project_overview_preview"]), 160)
        self.assertNotIn("raw_full_text", result)
        self.assertTrue(result["has_extracted_result"])

    def test_schema_upgrade_keeps_old_parse_records(self):
        with closing(self.connect()) as conn:
            conn.execute("ALTER TABLE bid_parse_info DROP COLUMN extraction_progress")
            conn.commit()
            with patch.object(storage, "_migrate_projects_to_bids"):
                storage.init_db(conn)
            row = conn.execute("SELECT tender_file_path, extraction_progress FROM bid_parse_info WHERE bid_id=1").fetchone()
        self.assertEqual(row["tender_file_path"], "offline.docx")
        self.assertIsNone(row["extraction_progress"])


if __name__ == "__main__":
    unittest.main()
