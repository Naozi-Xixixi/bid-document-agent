"""Regression tests using disposable SQLite databases and mocked model calls."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, closing
from contextvars import ContextVar
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from app.modules.knowledge import router, service
from app.services import storage
from app.services.qwen_client import LLMResult


class KnowledgeInterpretationTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / ".cache" / "knowledge-reliability-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.connections = []
        self.addCleanup(lambda: [conn.close() for conn in self.connections])
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.stack.enter_context(patch.object(service.settings, "database_path", self.path))
        self.stack.enter_context(patch.object(service.settings, "db_backend", "sqlite"))
        self.stack.enter_context(patch.object(service.storage, "connect", side_effect=self.connect))
        self.stack.enter_context(patch.object(service, "_QWEN_DOCUMENT_SEMAPHORE", asyncio.Semaphore(1)))
        self.model = self.stack.enter_context(patch.object(service.qwen_client, "chat_completion", new_callable=AsyncMock))
        with closing(self.connect()) as conn:
            storage.init_db(conn)
        service.ensure_schema()
        with closing(self.connect()) as conn:
            conn.execute("INSERT INTO knowledge_item (item_id,title,file_type,source_name,create_time,update_time) VALUES (1,'test','txt','test.txt','2026-01-01','2026-01-01')")
            conn.execute("INSERT INTO knowledge_chunk (item_id,chunk_index,content,create_time) VALUES (1,0,'测试文档要求每日配送','2026-01-01')")
            conn.commit()

    def connect(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self.connections.append(conn)
        return conn

    def saved(self):
        with closing(self.connect()) as conn:
            return dict(conn.execute("SELECT * FROM knowledge_item_interpretation WHERE item_id=1").fetchone())

    def test_invalid_or_truncated_model_json_finishes_as_failed(self):
        for content in ("not json", '{"summary":', "[]", "null", "{}", '{"unexpected":123}', '{"summary":[]}', '{"requirements":"wrong"}', '{"project_info":null}'):
            with self.subTest(content=content):
                self.model.return_value = LLMResult(content, "test", "test", True)
                result = asyncio.run(service.interpret_knowledge_item(1, role="tender"))
                self.assertEqual(result["status"], "failed")
                self.assertEqual(self.saved()["status"], "failed")
                self.assertTrue(self.saved()["error"])
                self.assertEqual(json.loads(self.saved()["analysis_json"]), {})

    def test_model_exception_is_persisted_instead_of_staying_processing(self):
        self.model.side_effect = TimeoutError("offline test timeout")
        result = asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("offline test timeout", self.saved()["error"])

    def test_cancellation_records_interruption_and_propagates(self):
        self.model.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        self.assertEqual(self.saved()["status"], "pending")
        self.assertIn("中断", self.saved()["error"])

    def test_api_fallback_is_never_saved_as_success(self):
        self.model.return_value = LLMResult('{"summary":"fallback"}', "local", "test", False)
        result = asyncio.run(service.interpret_knowledge_item(1, role="bid"))
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["error"])
        self.assertEqual(self.saved()["summary"], "")

    def test_valid_fenced_json_keeps_summary_and_results(self):
        self.model.return_value = LLMResult('```json\n{"summary":"配送要求","requirements":[{"title":"每日送达"}],"chapters":[]}\n```', "test", "test-model", True)
        result = asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        self.assertEqual((result["status"], result["result_count"]), ("completed", 1))
        self.assertEqual((self.saved()["summary"], self.saved()["model"]), ("配送要求", "test-model"))
        self.assertIsNone(self.saved()["error"])

    def test_empty_document_fails_without_calling_model(self):
        with closing(self.connect()) as conn:
            conn.execute("DELETE FROM knowledge_chunk")
            conn.commit()
        result = asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        self.assertEqual(result["status"], "failed")
        self.model.assert_not_awaited()


class KnowledgeSourcePageTests(unittest.TestCase):
    def test_each_chunk_keeps_its_actual_source_page(self):
        chunks = service._split_chunks("【第1页】\n第一页正文\n【第2页】\n第二页正文", [])
        self.assertEqual([(c["page_number"], c["content"]) for c in chunks], [(1, "第一页正文"), (2, "第二页正文")])

    def test_inline_page_text_and_cross_page_heading_are_preserved(self):
        chunks = service._split_chunks("【第4页】一、配送方案\n前页内容\n【第5页】次页内容", [])
        self.assertEqual([(c["page_number"], c["section_title"], c["content"]) for c in chunks], [(4, "一、配送方案", "前页内容"), (5, "一、配送方案", "次页内容")])

    def test_long_windows_do_not_borrow_the_next_page_number(self):
        chunks = service._split_chunks("【第1页】\n" + "甲" * 2100 + "\n【第2页】\n" + "乙" * 1200, [])
        self.assertTrue(all(c["page_number"] == (1 if "甲" in c["content"] else 2) for c in chunks))
        self.assertTrue(all(not ("甲" in c["content"] and "乙" in c["content"]) for c in chunks))

    def test_document_without_page_markers_does_not_invent_page_numbers(self):
        chunks = service._split_chunks("没有页码的正文", [])
        self.assertEqual(chunks[0]["page_number"], None)


class KnowledgeNonblockingRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_and_detail_keep_tenant_context_in_worker_thread(self):
        marker = ContextVar("test_tenant", default="missing")
        token = marker.set("test-company")
        self.addCleanup(marker.reset, token)
        caller = threading.get_ident()
        observed = []
        def worker(*args):
            observed.append((threading.get_ident(), marker.get()))
            return {"results": []}
        with patch.object(service, "search", side_effect=worker), patch.object(service, "get_item", side_effect=worker):
            await router.search(router.KnowledgeSearchRequest(query="测试"))
            await router.get_item(1)
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(tid != caller and tenant == "test-company" for tid, tenant in observed))

    async def test_slow_search_does_not_block_status_requests(self):
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        def slow_search(payload):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(2):
                raise TimeoutError("status request could not run")
            return {"results": []}
        with patch.object(service, "search", side_effect=slow_search), patch.object(service, "item_status", return_value={"item_id": 1}):
            task = asyncio.create_task(router.search(router.KnowledgeSearchRequest(query="测试")))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                status = await asyncio.wait_for(router.item_status(1), 1)
                self.assertEqual(status["data"]["item_id"], 1)
                self.assertFalse(task.done())
            finally:
                release.set()
                await task

    async def test_search_error_keeps_its_existing_http_contract(self):
        with patch.object(service, "search", side_effect=service.KnowledgeModuleError(40001, "bad query", 400)):
            response = await router.search(router.KnowledgeSearchRequest(query="test"))
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
