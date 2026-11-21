"""Full-document and restart regression tests; no real AI, OCR or business data."""
import asyncio
from contextlib import closing
import json
import threading
import unittest
from unittest.mock import AsyncMock, patch

import test_knowledge_reliability as fixture
from app.modules.knowledge import document_analysis as analysis, page_repair, recovery, service
from app.services.qwen_client import LLMResult


class ContinuationTests(unittest.TestCase):
    setUp = fixture.KnowledgeInterpretationTests.setUp
    connect = fixture.KnowledgeInterpretationTests.connect
    saved = fixture.KnowledgeInterpretationTests.saved

    def chunks(self, count=130, width=1000):
        with closing(self.connect()) as conn:
            conn.execute("DELETE FROM knowledge_chunk")
            for index in range(count):
                conn.execute(
                    "INSERT INTO knowledge_chunk (item_id,chunk_index,content,create_time) VALUES (1,?,?,?)",
                    (index, f"TAIL_{index:04}_" + "甲" * width, "2026-01-01"),
                )
            conn.execute("UPDATE knowledge_item SET status='completed',vector_status='completed',deep_status='completed'")
            conn.commit()

    def ok(self, value="成功"):
        return LLMResult(json.dumps({"summary": value, "requirements": [{"title": value}]}), "test", "test", True)

    def test_all_chunks_and_tail_after_70000_chars_are_sent(self):
        self.chunks()
        self.model.return_value = self.ok()
        result = asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        prompts = "\n".join(c.args[0][1]["content"] for c in self.model.await_args_list)
        self.assertIn("TAIL_0129_", prompts)
        self.assertGreater(self.model.await_count, 1)
        self.assertEqual(result["coverage"]["total_chunks"], 130)
        self.assertGreater(result["coverage"]["total_chars"], 70000)
        self.assertEqual(result["coverage"]["percent"], 100)
        self.assertEqual(result["coverage"]["covered_chunks"], 130)
        self.assertEqual(result["result_count"], 1)  # duplicates merged

    def test_oversized_single_chunk_is_split_without_truncation(self):
        rows = [{"chunk_id": 1, "content": "A" * 25001, "section_title": "", "section_path": ""}]
        parts = list(analysis.batches(rows))
        self.assertEqual(sum(p["chars"] for p in parts), 25001)
        self.assertEqual(len(parts), 3)

    def test_split_preserves_text_offsets_and_ids(self):
        rows = [{"chunk_id": 7, "content": "甲" * 1700 + "乙" * 900, "section_title": "章", "section_path": ""},
                {"chunk_id": 8, "content": "丙" * 501, "section_title": "", "section_path": ""}]
        parent = list(analysis.batches(rows))[0]
        children = analysis.split_batch(parent)
        self.assertEqual(sum(p["chars"] for p in children), parent["chars"])
        self.assertEqual("".join(s["content"] for c in children for s in c["segments"]), "甲" * 1700 + "乙" * 900 + "丙" * 501)
        self.assertEqual(children[1]["segments"][0]["offset"], parent["chars"] // 2)
        self.assertEqual(children[0]["ids"], [7])
        self.assertEqual(children[1]["ids"], [7, 8])

    def test_truncated_batch_splits_and_reuses_successful_child_on_retry(self):
        self.chunks(2, 3000)
        broken = LLMResult('{"summary":', "test", "test", True, finish_reason="length")
        self.model.side_effect = [broken, self.ok("左"), TimeoutError("timeout")]
        first = asyncio.run(service.interpret_knowledge_item(1, role="bid"))
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["coverage"]["percent"], 0)
        with closing(self.connect()) as conn:
            child = conn.execute("SELECT status FROM knowledge_analysis_part WHERE part_path='0'").fetchone()
            self.assertEqual(child["status"], "completed")
        self.model.reset_mock()
        self.model.side_effect = [self.ok("右")]
        second = asyncio.run(service.interpret_knowledge_item(1, role="bid"))
        self.assertEqual(second["coverage"]["percent"], 100)
        self.assertEqual(self.model.await_count, 1)
        self.assertIn("左", second["analysis"]["summary"])
        self.assertIn("右", second["analysis"]["summary"])

    def test_length_finish_is_not_success_even_with_valid_json(self):
        self.chunks(1, 2000)
        truncated = LLMResult('{"summary":"不完整"}', "test", "test", True, finish_reason="length")
        self.model.side_effect = [truncated, self.ok("一"), self.ok("二")]
        result = asyncio.run(service.interpret_knowledge_item(1, role="bid"))
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("不完整", result["analysis"]["summary"])
        self.assertEqual(self.model.await_count, 3)

    def test_repeated_invalid_output_is_bounded_and_never_reports_complete(self):
        self.chunks(1, 9000)
        self.model.return_value = LLMResult("invalid", "test", "test", True)
        result = asyncio.run(service.interpret_knowledge_item(1, role="bid"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["coverage"]["percent"], 0)
        self.assertEqual(self.model.await_count, 15)  # max binary tree depth 3
        self.assertIn("自动拆分下限", result["error"])

    def test_cancelled_subpart_resumes_without_repeating_completed_sibling(self):
        self.chunks(1, 3000)
        self.model.side_effect = [LLMResult("invalid", "test", "test", True), self.ok("左"), asyncio.CancelledError()]
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(service.interpret_knowledge_item(1, role="bid"))
        self.assertEqual(self.saved()["status"], "pending")
        self.model.reset_mock()
        self.model.side_effect = [self.ok("右")]
        result = asyncio.run(service.interpret_knowledge_item(1, role="bid"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.model.await_count, 1)

    def test_network_failure_stops_remaining_batches_and_retry_reuses_success(self):
        self.chunks(3, 9000)
        failure = LLMResult('{}', 'qwen', 'test', False, error='连接失败', error_kind='network')
        self.model.side_effect = [self.ok('第一批'), failure, failure, failure]
        with patch.object(analysis.asyncio, 'sleep', new_callable=AsyncMock):
            first = asyncio.run(service.interpret_knowledge_item(1, role='bid'))
        self.assertEqual(first['status'], 'failed')
        self.assertIn('暂停后续批次', first['error'])
        self.assertEqual(self.model.await_count, 4)
        with closing(self.connect()) as conn:
            statuses = [r['status'] for r in conn.execute('SELECT status FROM knowledge_analysis_batch WHERE item_id=1 ORDER BY batch_index')]
        self.assertEqual(statuses, ['completed', 'failed', 'pending'])
        self.model.reset_mock()
        self.model.side_effect = None
        self.model.return_value = self.ok('重试成功')
        second = asyncio.run(service.interpret_knowledge_item(1, role='bid'))
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(self.model.await_count, 2)

    def test_network_recovers_on_same_batch_then_continues(self):
        self.chunks(2, 9000)
        failure = LLMResult('{}', 'qwen', 'test', False, error='连接失败', error_kind='network')
        self.model.side_effect = [failure, self.ok('第一批'), self.ok('第二批')]
        with patch.object(analysis.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
            result = asyncio.run(service.interpret_knowledge_item(1, role='bid'))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.model.await_count, 3)
        sleep.assert_awaited_once_with(5)

    def test_failed_batch_retry_reuses_successful_batches(self):
        self.chunks(3, 9000)
        self.model.side_effect = [self.ok("一"), TimeoutError("timeout"), self.ok("三")]
        first = asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["coverage"]["completed_batches"], 2)
        self.assertEqual(first["coverage"]["failed_batches"], 1)
        self.model.reset_mock()
        self.model.side_effect = None
        self.model.return_value = self.ok("二")
        second = asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        self.assertEqual(second["coverage"]["percent"], 100)
        self.assertEqual(self.model.await_count, 1)
        self.assertEqual(second["result_count"], 3)

    def test_restart_continues_after_committed_batch(self):
        self.chunks(3, 9000)
        self.model.side_effect = [self.ok("一"), asyncio.CancelledError()]
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        self.assertEqual(analysis.coverage(1)["completed_batches"], 1)
        self.assertFalse(analysis._running)
        self.model.reset_mock()
        self.model.side_effect = None
        self.model.return_value = self.ok("后续")
        async def restart():
            with patch.object(service, "resume_pending_vector_sync_tasks", new_callable=AsyncMock, return_value=0):
                queued = await recovery.resume_pending_tasks()
                self.assertEqual(queued, 1)
                await asyncio.gather(*list(recovery._tasks.values()))
        asyncio.run(restart())
        self.assertEqual(self.model.await_count, 2)
        self.assertEqual(self.saved()["status"], "completed")

    def test_content_change_invalidates_completed_cache(self):
        self.model.return_value = self.ok()
        asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        self.assertEqual(self.model.await_count, 1)
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_chunk SET content='新版内容'")
            conn.commit()
        asyncio.run(service.interpret_knowledge_item(1, role="tender"))
        self.assertEqual(self.model.await_count, 2)

    def test_concurrent_duplicate_requests_share_model_call(self):
        self.model.return_value = self.ok()
        async def duplicate():
            return await asyncio.gather(*(service.interpret_knowledge_item(1, role="tender") for _ in range(3)))
        results = asyncio.run(duplicate())
        self.assertEqual(self.model.await_count, 1)
        self.assertTrue(all(r["status"] == "completed" for r in results))

    def test_recovery_runs_without_vector_backend_and_skips_terminal_items(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET status='processing'")
            for item_id, status, deep in [(2, "completed", "processing"), (3, "failed", "failed"), (4, "completed", "completed")]:
                conn.execute(
                    "INSERT INTO knowledge_item (item_id,title,file_type,source_name,status,deep_status,vector_status,create_time,update_time) VALUES (?,'test','txt','test.txt',?,?,'completed','x','x')",
                    (item_id, status, deep),
                )
            conn.commit()
        async def resume():
            with patch.object(service, "process_item", new_callable=AsyncMock) as parse, \
                 patch.object(service, "_queue_deep_extract", return_value=True) as deep, \
                 patch.object(service, "_queue_item_vector_sync", return_value=False), \
                 patch.object(service, "resume_pending_vector_sync_tasks", new_callable=AsyncMock, return_value=0):
                await recovery.resume_pending_tasks()
                await asyncio.gather(*list(recovery._tasks.values()))
                parse.assert_awaited_once_with(1)
                deep.assert_called_once_with(2)
        asyncio.run(resume())

    def test_parse_checkpoint_does_not_clear_assets_or_read_original(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET status='processing',process_progress=78,content_text='已保存正文',file_type='docx'")
            conn.commit()
        with patch.object(service, "_clear_assets_for_item") as clear, \
             patch.object(service, "_extract_document") as extract, \
             patch.object(service, "_refresh_asset_annotation_chunks"), \
             patch.object(service, "_build_graph_for_item"), \
             patch.object(service, "_queue_item_vector_sync"), \
             patch.object(service, "_queue_deep_extract") as deep, \
             patch.object(service, "_maybe_finalize_project_for_item"):
            asyncio.run(service._process_item_once(1))
            clear.assert_not_called()
            extract.assert_not_called()
            deep.assert_called_once_with(1)
        self.assertEqual(service.item_status(1)["status"], "completed")

    def test_page_repair_preserves_chunk_ids_content_and_is_idempotent(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET file_type='pdf',status='completed',content_text='【第1页】\n甲方正文\n【第2页】\n乙方正文'")
            conn.execute("UPDATE knowledge_chunk SET content='甲方正文',page_number=2")
            conn.commit()
            before = dict(conn.execute("SELECT * FROM knowledge_chunk").fetchone())
        result = page_repair.apply(1)
        self.assertEqual(result["applied"], 1)
        with closing(self.connect()) as conn:
            after = dict(conn.execute("SELECT * FROM knowledge_chunk").fetchone())
        self.assertEqual(after["page_number"], 1)
        for key in before:
            if key != "page_number":
                self.assertEqual(before[key], after[key])
        self.assertEqual(page_repair.apply(1)["applied"], 0)
        self.model.assert_not_awaited()

    def test_cross_page_chunk_uses_start_page_and_ambiguous_text_has_no_guess(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET file_type='pdf',status='completed',content_text='【第1页】\n甲方正文\n【第2页】\n乙方正文\n【第3页】\n乙方正文'")
            conn.execute("UPDATE knowledge_chunk SET content='甲方正文\n乙方正文',page_number=3")
            conn.commit()
        self.assertEqual(page_repair.plan(1)["changes"][0]["after"], 1)
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_chunk SET content='乙方正文',page_number=3")
            conn.commit()
        result = page_repair.plan(1)
        self.assertIsNone(result["changes"][0]["after"])
        self.assertEqual(result["unresolved"], 1)

    def test_word_does_not_invent_physical_pages(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET file_type='docx',status='completed'")
            conn.execute("UPDATE knowledge_chunk SET page_number=12")
            conn.commit()
        self.assertIsNone(page_repair.plan(1)["changes"][0]["after"])

    def test_project_resume_skips_completed_parsing_and_matching_checkpoint(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET status='completed'")
            conn.execute("INSERT INTO knowledge_item (item_id,title,file_type,source_name,status,create_time,update_time) VALUES (2,'bid','txt','bid.txt','completed','x','x')")
            conn.execute("INSERT INTO bid_project (project_id,project_name,tender_doc_id,bid_doc_id,status,create_time,update_time) VALUES (1,'pair',1,2,'completed','x','x')")
            recovery.prepare_project(1, conn)
            conn.execute("UPDATE knowledge_project_workflow SET phase='template',status='processing'")
            conn.commit()
        with patch.object(service, "process_item", new_callable=AsyncMock) as parse, \
             patch.object(service, "interpret_knowledge_item", new_callable=AsyncMock) as interpret, \
             patch.object(service, "match_project") as match, \
             patch.object(service, "extract_project_template") as template:
            asyncio.run(recovery.run_project(1, 1, 2))
            parse.assert_not_awaited()
            interpret.assert_not_awaited()
            match.assert_not_called()
            template.assert_called_once_with(1)
        self.assertEqual(recovery.workflow_state(1)["status"], "completed")

    def test_deep_resume_skips_completed_assets_and_keeps_total_progress(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE knowledge_item SET status='completed'")
            for index, status in enumerate(("completed", "pending"), 1):
                conn.execute(
                    "INSERT INTO knowledge_asset (asset_id,item_id,asset_type,deep_candidate,deep_status,create_time) VALUES (?,1,'图片',1,?,'x')",
                    (index, status),
                )
            conn.commit()
        with patch.object(service, "_materialize_docx_images", return_value=0), \
             patch.object(service, "_materialize_scan_page_assets", return_value=0), \
             patch.object(service, "_refresh_asset_annotation_chunks"), \
             patch.object(service, "_queue_item_vector_sync"), \
             patch.object(service, "_reextract_one_asset", return_value={"status": "updated"}) as extract:
            result = asyncio.run(service.deep_extract_item(1))
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(extract.call_args.args[0]["asset_id"], 2)
            self.assertEqual(result["total"], 2)
            state = service.item_status(1)
            self.assertEqual((state["deep_total"], state["deep_progress"]), (2, 100))

    def test_new_ocr_chunks_trigger_followup_if_vector_pass_is_running(self):
        async def scenario():
            entered, release = asyncio.Event(), threading.Event()
            loop = asyncio.get_running_loop()
            count = 0
            def vector_pass(*args):
                nonlocal count
                count += 1
                if count == 1:
                    loop.call_soon_threadsafe(entered.set)
                    if not release.wait(2):
                        raise TimeoutError("test vector pass blocked")
                return {"complete": True}
            with patch.object(service.vector_store, "enabled", return_value=True), \
                 patch.object(service, "_persist_vector_sync_task", return_value=1), \
                 patch.object(service, "_run_vector_sync_task", side_effect=vector_pass), \
                 patch.object(service, "_maybe_finalize_project_for_item"):
                self.assertTrue(service._queue_item_vector_sync(1))
                await asyncio.wait_for(entered.wait(), 2)
                self.assertFalse(service._queue_item_vector_sync(1))
                release.set()
                while service._VECTOR_TASKS:
                    await asyncio.gather(*list(service._VECTOR_TASKS))
            self.assertEqual(count, 2)
        asyncio.run(scenario())
