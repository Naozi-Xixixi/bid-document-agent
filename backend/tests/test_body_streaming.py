"""Offline streaming contracts: first output, completion and cancellation."""

import asyncio
import unittest

import httpx
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from app.schemas.bid_graphic import GraphicSectionGenerateRequest
from generation_fixtures import seed_generation_db
from app.api.routes import bid_content as content_routes
from app.services import bid_content_service as content
from app.services import bid_graphic_service as graphic


class GraphicStreamingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        seed_generation_db(self.stack)
        self.generation = {
            "messages": [], "max_tokens": 100, "length_controlled": False,
            "existing_markdown": "", "images": [], "chapter_path": "配送安排",
            "chapter": {"chapter_title": "配送安排"}, "target_words": 100,
        }
        self.stack.enter_context(patch.object(graphic, "_build_graphic_generation", return_value=self.generation))
        self.stack.enter_context(patch.object(graphic, "_normalize_graphic_markdown", side_effect=lambda text, *_: text))
        self.saved = self.stack.enter_context(patch.object(
            graphic, "save_graphic_section",
            side_effect=lambda bid, payload: {
                "bid_id": bid, "chapter_id": payload.chapter_id,
                "chapter_title": "配送安排", "markdown": payload.markdown,
                "charts": [], "images": [], "ref_chart_ids": [],
            },
        ))
        self.payload = GraphicSectionGenerateRequest(chapter_id=1, rag_enable=False)

    async def test_first_delta_arrives_before_completion_and_save(self):
        release = asyncio.Event()

        async def fake_stream(*args, **kwargs):
            self.assertTrue(kwargs["strict"])
            yield "首段正文。"
            await release.wait()
            yield "第二段正文。"

        self.stack.enter_context(patch.object(graphic, "stream_chat_completion", fake_stream))
        events = graphic.stream_graphic_section(1, self.payload)
        first = await anext(events)
        self.assertEqual(first["content"], "首段正文。")
        self.saved.assert_not_called()
        next_event = asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        self.assertFalse(next_event.done())
        release.set()
        self.assertEqual((await next_event)["content"], "第二段正文。")
        remaining = [event async for event in events]
        self.assertEqual(remaining[-1]["type"], "done")
        self.assertEqual(remaining[-1]["result"]["markdown"], "首段正文。第二段正文。")
        self.saved.assert_called_once()

    async def test_partial_failure_does_not_save_or_report_done(self):
        async def fake_stream(*args, **kwargs):
            yield "尚未完成"
            raise RuntimeError("模拟上游连接中断")

        self.stack.enter_context(patch.object(graphic, "stream_chat_completion", fake_stream))
        events = graphic.stream_graphic_section(1, self.payload)
        self.assertEqual((await anext(events))["type"], "chunk")
        with self.assertRaisesRegex(RuntimeError, "连接中断"):
            await anext(events)
        self.saved.assert_not_called()

    async def test_transient_disconnect_and_empty_output_retry_before_saving(self):
        calls = 0

        async def fake_stream(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield "不应保存的半截文字"
                raise httpx.ReadTimeout("上游超时")
            if calls == 2:
                yield " \n"
                return
            yield "完整的配送安排。"

        with patch.object(graphic, "stream_chat_completion", fake_stream), patch.object(graphic.asyncio, "sleep", AsyncMock()):
            events = [event async for event in graphic.stream_graphic_section(1, self.payload)]
        self.assertEqual([event["attempt"] for event in events if event["type"] == "retrying"], [2, 3])
        self.assertEqual(events[-1]["result"]["markdown"], "完整的配送安排。")
        self.saved.assert_called_once()

    async def test_length_adjustment_does_not_discard_new_valid_draft(self):
        self.generation["length_controlled"] = True
        self.generation["target_words"] = 100
        payload = GraphicSectionGenerateRequest(chapter_id=1, length_target=100)
        draft = "配送到场后逐批核对并登记。"

        async def fake_stream(*args, **kwargs):
            yield draft

        with patch.object(graphic, "stream_chat_completion", fake_stream):
            events = [event async for event in graphic.stream_graphic_section(1, payload)]
        self.assertEqual(events[-1]["result"]["markdown"], draft)
        self.assertFalse(events[-1]["result"]["length_control"]["within_tolerance"])
        self.assertTrue(events[-1]["result"]["length_control"]["review_reason"])
        self.saved.assert_called_once()

    async def test_cancel_closes_upstream_and_keeps_existing_section(self):
        closed = asyncio.Event()

        async def fake_stream(*args, **kwargs):
            try:
                yield "未完成内容"
                await asyncio.Event().wait()
            finally:
                closed.set()

        self.stack.enter_context(patch.object(graphic, "stream_chat_completion", fake_stream))
        events = graphic.stream_graphic_section(1, self.payload)
        await anext(events)
        await events.aclose()
        self.assertTrue(closed.is_set())
        self.saved.assert_not_called()

    async def test_batch_forwards_deltas_and_final_structured_section(self):
        self.stack.enter_context(patch.object(graphic, "_batch_chapter_ids", return_value=[1, 2]))
        self.stack.enter_context(patch.object(graphic, "_targets_for_batch", return_value={}))
        self.stack.enter_context(patch.object(graphic, "_chapter_row", return_value={"chapter_title": "配送安排"}))
        calls = 0

        async def fake_stream(*args, **kwargs):
            nonlocal calls
            calls += 1
            yield f"第{calls}章"
            if calls == 2:
                raise RuntimeError("第二章失败")

        self.stack.enter_context(patch.object(graphic, "stream_chat_completion", fake_stream))
        events = [event async for event in graphic.stream_graphic_section_batch(1, self.payload)]
        first_chunk = next(index for index, event in enumerate(events) if event["type"] == "chunk")
        first_done = next(index for index, event in enumerate(events) if event["type"] == "chapter_done")
        self.assertLess(first_chunk, first_done)
        self.assertEqual(events[first_done]["result"]["markdown"], "第1章")
        self.assertEqual(events[-1]["type"], "batch_done")
        self.assertEqual((events[-1]["success_count"], events[-1]["failure_count"]), (1, 1))
        self.assertEqual(events[-1]["incomplete_chapter_ids"], [2])
        self.saved.assert_called_once()

    async def test_length_revision_is_streamed_before_save(self):
        self.generation["length_controlled"] = True
        self.generation["target_words"] = 10
        self.generation["context"] = {}
        self.payload = GraphicSectionGenerateRequest(chapter_id=1, length_target=10)
        calls = 0

        async def fake_stream(*args, **kwargs):
            nonlocal calls
            calls += 1
            yield "短文" if calls == 1 else "一二三四五"
            if calls == 2:
                self.saved.assert_not_called()
                yield "六七八九十"

        self.stack.enter_context(patch.object(graphic, "stream_chat_completion", fake_stream))
        events = [event async for event in graphic.stream_graphic_section(1, self.payload)]
        self.assertTrue(any(event["type"] == "adjusting" for event in events))
        revisions = [event["content"] for event in events if event.get("phase") == "adjusting"]
        self.assertEqual(revisions, ["一二三四五", "六七八九十"])
        self.assertEqual(events[-1]["result"]["markdown"], "一二三四五六七八九十")


class ContentStreamingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        seed_generation_db(self.stack)
        chapter = {"chapter_id": 1, "version_id": 2, "chapter_title": "配送安排"}
        mocks = {
            "_content_context": {"bid_type": "open", "score_requirements": "", "project_overview": ""},
            "_get_chapter": chapter, "_has_children": False,
            "_select_version": {"version_id": 2}, "_chapter_path": ["配送安排"],
            "_chapter_children": [], "_target_words": 100,
            "_quality_word_bounds": (80, 120, 1000), "_biaoshu_paragraph_plan": "",
        }
        for name, value in mocks.items():
            self.stack.enter_context(patch.object(content, name, return_value=value))
        self.stack.enter_context(patch.object(content.bid_reference_service, "retrieve_reference_context", return_value={}))
        self.stack.enter_context(patch.object(content.bid_reference_service, "format_reference_prompt", return_value=""))
        self.stack.enter_context(patch.object(content.bid_case_reference_service, "retrieve_case_context", return_value=None))
        self.stack.enter_context(patch.object(content.bid_case_reference_service, "retrieve_knowledge_evidence", return_value={"excerpts": []}))
        self.stack.enter_context(patch.object(content.rag_case_service, "retrieve_scoring_context", return_value={}))
        self.stack.enter_context(patch.object(content.rag_case_service, "format_scoring_context_prompt", return_value=""))
        self.stack.enter_context(patch.object(content, "_strip_generated_headings", side_effect=lambda text, _: text))
        self.stack.enter_context(patch.object(content, "_humanize_bid_text", side_effect=lambda text: text))
        self.stack.enter_context(patch.object(content, "_fit_content_to_target", side_effect=lambda text, *_: (text, {"deduplication": {"repeat_ratio": 0}})))
        self.status = self.stack.enter_context(patch.object(content, "_set_content_status", wraps=content._set_content_status))
        self.sync = self.stack.enter_context(patch.object(content, "_sync_project_section"))

    async def test_content_first_delta_before_done_preserves_real_deltas(self):
        release = asyncio.Event()

        async def fake_stream(*args, **kwargs):
            self.assertTrue(kwargs["strict"])
            yield "责任"
            await release.wait()
            yield "责任明确。"

        self.stack.enter_context(patch.object(content, "stream_chat_completion", fake_stream))
        events = content.generate_chapter_events(1, 1)
        self.assertEqual((await anext(events))["type"], "start")
        self.assertEqual((await anext(events))["content"], "责任")
        self.sync.assert_not_called()
        pending = asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        release.set()
        self.assertEqual((await pending)["content"], "责任明确。")
        result = [event async for event in events]
        self.assertEqual(result[-1]["type"], "done")
        self.assertEqual(result[-1]["content_text"], "责任责任明确。")
        self.assertEqual(self.status.call_args.args[4], "completed")

    async def test_ocr_evidence_reaches_plain_body_and_saved_provenance(self):
        evidence={"excerpts":[{"chunk_id":321,"title":"扫描资料","page_number":8,"source_asset_id":42,"chunk_kind":"ocr_text","content":"扫描独有配送温控记录"}]}
        self.stack.enter_context(patch.object(content.bid_case_reference_service,"retrieve_knowledge_evidence",return_value=evidence))
        async def fake_stream(messages,**kwargs):
            prompt='\n'.join(message['content'] for message in messages)
            self.assertIn('扫描独有配送温控记录',prompt)
            self.assertIn('图片OCR#42',prompt)
            yield '依据温控记录安排配送交接。'
        self.stack.enter_context(patch.object(content,'stream_chat_completion',fake_stream))
        events=[event async for event in content.generate_chapter_events(1,1)]
        self.assertEqual(events[-1]['type'],'done')
        self.assertEqual(events[-1]['word_control']['knowledge_chunk_ids'],[321])

    async def test_content_disconnect_is_failed_not_completed(self):
        async def fake_stream(*args, **kwargs):
            yield "尚未完成"
            await asyncio.Event().wait()

        self.stack.enter_context(patch.object(content, "stream_chat_completion", fake_stream))
        events = content.generate_chapter_events(1, 1)
        await anext(events)
        await anext(events)
        await events.aclose()
        self.assertEqual(self.status.call_args.args[4], "failed")
        self.assertTrue(self.status.call_args.args[7]["interrupted"])
        self.sync.assert_not_called()

    async def test_content_upstream_error_cannot_be_done(self):
        async def fake_stream(*args, **kwargs):
            yield "部分正文"
            raise RuntimeError("模拟中断")

        self.stack.enter_context(patch.object(content, "stream_chat_completion", fake_stream))
        events = [event async for event in content.generate_chapter_events(1, 1)]
        self.assertEqual(events[-1]["type"], "error")
        self.assertFalse(any(event["type"] == "done" for event in events))
        self.assertEqual(self.status.call_args.args[4], "failed")
        self.sync.assert_not_called()

    async def test_response_iterator_close_marks_partial_content_failed(self):
        closed = asyncio.Event()

        async def fake_stream(*args, **kwargs):
            try:
                yield "响应中断前的正文"
                await asyncio.Event().wait()
            finally:
                closed.set()

        self.stack.enter_context(patch.object(content, "stream_chat_completion", fake_stream))
        response = await content_routes.generate_bid_chapter_content(1, 1)
        events = response.body_iterator
        self.assertIn("event: start", await anext(events))
        self.assertIn("响应中断前的正文", await anext(events))
        await events.aclose()
        self.assertTrue(closed.is_set())
        self.assertEqual(self.status.call_args.args[4], "failed")
        self.assertEqual(response.headers["x-accel-buffering"], "no")

    async def test_concurrent_rewrite_rejected_then_retry_preserves_completed_content(self):
        calls = 0

        async def fake_stream(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield "旧任务半截"
                await asyncio.Event().wait()
            else:
                yield "新任务完成的正文"

        self.stack.enter_context(patch.object(content, "stream_chat_completion", fake_stream))
        old = content.generate_chapter_events(1, 1)
        self.addAsyncCleanup(old.aclose)
        await anext(old)
        await anext(old)
        before_conflict = self.status.call_count
        conflict = [event async for event in content.generate_chapter_events(1, 1, rewrite=True)]
        self.assertEqual(conflict, [{
            "type": "error", "chapter_id": 1, "code": 40901,
            "message": "本章节正在生成，请等待完成或停止后重试",
        }])
        self.assertEqual(self.status.call_count, before_conflict)
        self.assertEqual(calls, 1)

        await old.aclose()
        completed = [event async for event in content.generate_chapter_events(1, 1, rewrite=True)]
        self.assertEqual(completed[-1]["type"], "done")
        self.assertEqual(self.status.call_args.args[4:6], ("completed", "新任务完成的正文"))
        writes = self.status.call_count
        await old.aclose()
        self.assertEqual(self.status.call_count, writes)
        self.assertFalse(content._ACTIVE_CONTENT_GENERATIONS)

    async def test_cancelled_task_releases_chapter_for_retry(self):
        reached_wait = asyncio.Event()

        async def fake_stream(*args, **kwargs):
            yield "取消前的内容"
            reached_wait.set()
            await asyncio.Event().wait()

        self.stack.enter_context(patch.object(content, "stream_chat_completion", fake_stream))
        events = content.generate_chapter_events(1, 1)
        self.addAsyncCleanup(events.aclose)
        await anext(events)
        await anext(events)
        pending = asyncio.create_task(anext(events))
        await reached_wait.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(self.status.call_args.args[4], "failed")
        self.assertFalse(content._ACTIVE_CONTENT_GENERATIONS)

    async def test_setup_failure_releases_owner_without_overwriting_existing_content(self):
        with patch.object(content, "_target_words", side_effect=RuntimeError("准备失败")):
            events = [event async for event in content.generate_chapter_events(1, 1)]
        self.assertEqual(events[-1]["type"], "error")
        self.status.assert_not_called()
        self.assertFalse(content._ACTIVE_CONTENT_GENERATIONS)

    async def test_same_chapter_in_other_tenant_is_not_blocked(self):
        tenant = self.stack.enter_context(patch.object(content, "get_current_tenant"))
        tenant.return_value = SimpleNamespace(company_id="company-a")
        first = content.generate_chapter_events(1, 1)
        second = content.generate_chapter_events(1, 1)
        self.addAsyncCleanup(first.aclose)
        self.addAsyncCleanup(second.aclose)
        self.assertEqual((await anext(first))["type"], "start")
        tenant.return_value = SimpleNamespace(company_id="company-b")
        self.assertEqual((await anext(second))["type"], "start")
        await first.aclose()
        await second.aclose()
        self.assertFalse(content._ACTIVE_CONTENT_GENERATIONS)


if __name__ == "__main__":
    unittest.main()
