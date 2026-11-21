"""Offline catalog streaming regressions; no application data or model API calls."""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from anyio import CancelScope
from fastapi import BackgroundTasks
from app.api.routes import bid_catalog as routes
from app.services import bid_catalog_service as service


class CatalogStreamingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.company = "company_a"
        self.saved = []
        service.CATALOG_TASKS.clear()
        self.patchers = [
            patch.object(service, "get_current_tenant", side_effect=lambda: SimpleNamespace(company_id=self.company)),
            patch.object(service, "VERSION_STYLES", [("方案一", "标准", "standard")]),
            patch.object(service, "_catalog_context", return_value={}),
            patch.object(service, "get_catalog", return_value={"versions": []}),
            patch.object(service, "_build_rule_catalog", return_value=[]),
            patch.object(service, "_catalog_messages", return_value=[{"role": "user", "content": "offline test"}]),
            patch.object(service, "_store_catalog_version", side_effect=self.store_version),
            patch.object(service, "_sync_project_directory_for_version"),
            patch.object(service, "_clear_catalog_versions"),
        ]
        self.mocks = [patcher.start() for patcher in self.patchers]
        self.addCleanup(lambda: [patcher.stop() for patcher in reversed(self.patchers)])
        self.addCleanup(service.CATALOG_TASKS.clear)

    def store_version(self, bid_id, name, style, version_type, catalog):
        self.saved.append(catalog)
        return {"version_id": 11, "version_name": name, "catalog_content": catalog}

    async def collect(self, upstream):
        with patch.object(service, "stream_chat_completion", upstream):
            return [event async for event in service.stream_catalog_generation_events(1, force=True)]

    async def test_delta_is_visible_before_upstream_finishes_and_before_save(self):
        release = asyncio.Event()

        async def upstream(messages, **options):
            self.assertTrue(options["strict"])
            self.assertTrue(options["json_mode"])
            yield '{"catalog":[{"title":"供应'
            await release.wait()
            yield '保障","children":[{"title":"配送","children":["验收"]}]}]}'

        with patch.object(service, "stream_chat_completion", upstream):
            stream = service.stream_catalog_generation_events(1, force=True)
            first = await anext(stream)
            while first["type"] != "delta":
                first = await anext(stream)
            self.assertIn("供应", first["preview"])
            self.assertEqual(self.saved, [])
            pending = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            self.assertFalse(pending.done())
            release.set()
            second = await pending
            self.assertEqual(second["type"], "delta")
            self.assertIn("    验收", second["preview"])
            remaining = [event async for event in stream]
        self.assertEqual(remaining[-1]["type"], "done")
        self.assertEqual(self.saved[0][0]["title"], "供应保障")
        self.assertEqual(service.CATALOG_TASKS[("company_a", 1)]["status"], "completed")
        self.mocks[-1].assert_not_called()

    async def test_flat_model_result_keeps_tender_order_and_adds_plan_subsections(self):
        self.mocks[2].return_value = {
            "required_format_sections": ["投标函", "供货安装（调试）方案"],
            "project_overview": "食材配送服务",
        }

        async def upstream(*args, **kwargs):
            yield '{"catalog":[{"title":"供货配送方案","children":[]},{"title":"投标函","children":[]}]}'

        events = await self.collect(upstream)
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual([item["title"] for item in self.saved[0]], ["投标函", "供货安装（调试）方案"])
        self.assertEqual(self.saved[0][0]["children"], [])
        self.assertTrue(self.saved[0][1]["children"][0]["children"])

    async def test_truncated_outer_json_never_saves_inner_complete_array(self):
        async def upstream(*args, **kwargs):
            yield '{"catalog":[{"title":"供应保障","children":["配送"]}]'
        events = await self.collect(upstream)
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.saved, [])
        self.assertNotIn("done", [event["type"] for event in events])
        self.assertEqual(service.CATALOG_TASKS[("company_a", 1)]["status"], "failed")
        self.mocks[-1].assert_not_called()

    async def test_upstream_failure_does_not_save_even_if_accumulated_json_is_valid(self):
        async def upstream(*args, **kwargs):
            yield '{"catalog":[{"title":"供应保障","children":[]}]}'
            raise RuntimeError("upstream terminated without completion")
        events = await self.collect(upstream)
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.saved, [])

    async def test_invalid_structure_is_not_replaced_by_rule_fallback(self):
        async def upstream(*args, **kwargs):
            yield '{"catalog":[{"title":"","children":[]}]}'
        events = await self.collect(upstream)
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.saved, [])

    async def test_closing_stream_marks_cancelled_and_leaves_previous_version(self):
        async def upstream(*args, **kwargs):
            yield '{"catalog":[{"title":"供应'
            yield '保障","children":[]}]}'
        with patch.object(service, "stream_chat_completion", upstream):
            stream = service.stream_catalog_generation_events(1, force=True)
            event = await anext(stream)
            while event["type"] != "delta":
                event = await anext(stream)
            await stream.aclose()
        self.assertEqual(service.CATALOG_TASKS[("company_a", 1)]["status"], "cancelled")
        self.assertEqual(self.saved, [])

    async def test_same_bid_id_is_separate_for_two_companies_and_deduplicated_within_one(self):
        first = service.start_catalog_generation(1, force=True)
        duplicate = service.start_catalog_generation(1, force=True)
        self.assertTrue(first["newly_created"])
        self.assertFalse(duplicate["newly_created"])
        self.assertEqual(first["task_id"], duplicate["task_id"])
        self.company = "company_b"
        other = service.start_catalog_generation(1, force=True)
        self.assertTrue(other["newly_created"])
        self.assertNotEqual(first["task_id"], other["task_id"])
        self.assertEqual(set(service.CATALOG_TASKS), {("company_a", 1), ("company_b", 1)})

    async def test_duplicate_stream_replays_preview_and_subscribes_without_second_model_call(self):
        calls = 0
        release = asyncio.Event()
        async def upstream(*args, **kwargs):
            nonlocal calls
            calls += 1
            yield '{"catalog":[{"title":"供应'
            yield '保障","children":['
            await release.wait()
            yield '"配送"]}]}'
        with patch.object(service, "stream_chat_completion", upstream):
            owner = service.stream_catalog_generation_events(1, force=True)
            for _ in range(2):
                event = await anext(owner)
                while event["type"] != "delta":
                    event = await anext(owner)
            subscriber = service.stream_catalog_generation_events(1, force=False)
            replay = []
            while not replay or replay[-1]["type"] != "delta":
                replay.append(await anext(subscriber))
            self.assertEqual(sum(item["type"] == "delta" for item in replay), 1)
            self.assertEqual(replay[-1]["preview"], "供应保障")
            async def collect(stream):
                return [item async for item in stream]
            following = asyncio.create_task(collect(subscriber))
            release.set()
            remaining = [item async for item in owner]
            received = await asyncio.wait_for(following, timeout=2)
        self.assertEqual(calls, 1)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(remaining[-1]["type"], "done")
        self.assertEqual(received[-1]["type"], "done")
        self.assertEqual([item["type"] for item in received], [item["type"] for item in remaining])
        self.assertFalse(service.CATALOG_TASKS[("company_a", 1)]["subscribers"])

    async def test_closing_subscriber_leaves_owner_running(self):
        async def upstream(*args, **kwargs):
            yield '{"catalog":[{"title":"供应'
            yield '保障","children":[]}]}'
        with patch.object(service, "stream_chat_completion", upstream):
            owner = service.stream_catalog_generation_events(1, force=True)
            event = await anext(owner)
            while event["type"] != "delta":
                event = await anext(owner)
            subscriber = service.stream_catalog_generation_events(1, force=False)
            await anext(subscriber)
            await subscriber.aclose()
            self.assertEqual(service.CATALOG_TASKS[("company_a", 1)]["status"], "processing")
            remaining = [item async for item in owner]
        self.assertEqual(remaining[-1]["type"], "done")
        self.assertEqual(len(self.saved), 1)

    async def test_owner_cancellation_ends_subscriber_and_closes_upstream(self):
        upstream_closed = asyncio.Event()
        wait_for_more = asyncio.Event()
        async def upstream(*args, **kwargs):
            try:
                yield '{"catalog":[{"title":"供应'
                await wait_for_more.wait()
            finally:
                upstream_closed.set()
        with patch.object(service, "stream_chat_completion", upstream):
            owner = service.stream_catalog_generation_events(1, force=True)
            event = await anext(owner)
            while event["type"] != "delta":
                event = await anext(owner)
            subscriber = service.stream_catalog_generation_events(1, force=False)
            await anext(subscriber)
            pending = asyncio.create_task(anext(owner))
            await asyncio.sleep(0)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            following = [item async for item in subscriber]
        self.assertTrue(upstream_closed.is_set())
        self.assertEqual(following[-1]["type"], "error")
        self.assertEqual(following[-1]["code"], 49901)
        self.assertEqual(service.CATALOG_TASKS[("company_a", 1)]["status"], "cancelled")
        self.assertEqual(self.saved, [])

    async def test_route_close_propagates_to_generation(self):
        async def upstream(*args, **kwargs):
            yield '{"catalog":[{"title":"供应'
            yield '保障","children":[]}]}'
        with patch.object(service, "stream_chat_completion", upstream):
            response = await routes.stream_generate_bid_catalog(1, force=True, style=None)
            self.assertEqual(response.headers["x-accel-buffering"], "no")
            stream = response.body_iterator
            chunk = await anext(stream)
            while 'event: delta' not in chunk:
                chunk = await anext(stream)
            await stream.aclose()
        self.assertEqual(service.CATALOG_TASKS[("company_a", 1)]["status"], "cancelled")
        self.assertEqual(self.saved, [])

    async def test_cancel_during_store_or_sync_waits_for_commit_before_allowing_retry(self):
        for phase in ("store", "sync"):
            with self.subTest(phase=phase):
                service.CATALOG_TASKS.clear()
                entered, release = threading.Event(), threading.Event()
                stored, synced = [], []
                calls = 0

                async def upstream(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    title = "old" if calls == 1 else "new"
                    yield json.dumps({"catalog": [{"title": title, "children": []}]})

                def store(bid_id, name, style, kind, catalog):
                    title = catalog[0]["title"]
                    if title == "old" and phase == "store":
                        entered.set()
                        if not release.wait(3):
                            raise RuntimeError("test gate timed out")
                    stored.append(title)
                    return {"version_id": 11, "version_name": name, "catalog_content": catalog}

                def sync(bid_id, version_id, catalog):
                    title = catalog[0]["title"]
                    if title == "old" and phase == "sync":
                        entered.set()
                        if not release.wait(3):
                            raise RuntimeError("test gate timed out")
                    synced.append(title)

                async def consume():
                    return [event async for event in service.stream_catalog_generation_events(1, force=True)]

                with patch.object(service, "stream_chat_completion", upstream), patch.object(service, "_store_catalog_version", store), patch.object(service, "_sync_project_directory_for_version", sync):
                    owner = asyncio.create_task(consume())
                    try:
                        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                        owner.cancel()
                        await asyncio.sleep(0.02)
                        owner.cancel()  # Repeated cancellation cannot abandon a write.
                        await asyncio.sleep(0.02)
                        self.assertFalse(owner.done())
                        self.assertEqual(service.CATALOG_TASKS[("company_a", 1)]["status"], "processing")
                        duplicate = service.start_catalog_generation(1, force=True)
                        self.assertFalse(duplicate["newly_created"])
                        release.set()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(owner, 2)
                        self.assertEqual(synced, ["old"])
                        self.assertEqual(service.CATALOG_TASKS[("company_a", 1)]["status"], "cancelled")
                        result = await consume()
                        self.assertEqual(result[-1]["type"], "done")
                        self.assertEqual(stored, ["old", "new"])
                        self.assertEqual(synced, ["old", "new"])
                    finally:
                        release.set()
                        if not owner.done():
                            await asyncio.gather(owner, return_exceptions=True)

    async def test_anyio_disconnect_keeps_lease_until_commit_finishes(self):
        entered, release = threading.Event(), threading.Event()
        scopes = []

        async def upstream(*args, **kwargs):
            yield '{"catalog":[{"title":"供应保障","children":[]}]}'

        def store(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test gate timed out")
            return self.store_version(*args)

        async def consume():
            with CancelScope() as scope:
                scopes.append(scope)
                return [event async for event in service.stream_catalog_generation_events(1, force=True)]

        with patch.object(service, "stream_chat_completion", upstream), patch.object(service, "_store_catalog_version", store):
            owner = asyncio.create_task(consume())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                scopes[0].cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(owner.done())
                self.assertFalse(service.start_catalog_generation(1, force=True)["newly_created"])
                release.set()
                await asyncio.wait_for(owner, 2)
                self.assertEqual(len(self.saved), 1)
                self.mocks[-2].assert_called_once()
                self.assertEqual(service.CATALOG_TASKS[("company_a", 1)]["status"], "cancelled")
            finally:
                release.set()
                if not owner.done():
                    await asyncio.gather(owner, return_exceptions=True)

    async def test_http_background_routes_queue_only_new_tasks(self):
        first, duplicate = BackgroundTasks(), BackgroundTasks()
        await routes.generate_bid_catalog(1, first)
        await routes.generate_bid_catalog(1, duplicate)
        self.assertEqual(len(first.tasks), 1)
        self.assertEqual(len(duplicate.tasks), 0)
        duplicate_regeneration = BackgroundTasks()
        await routes.regenerate_bid_catalog(1, duplicate_regeneration)
        self.assertEqual(len(duplicate_regeneration.tasks), 0)

    def test_preview_handles_split_escapes_and_never_exposes_json_field_names(self):
        raw = json.dumps({"catalog": [{"title": "食品配送", "children": [{"title": "组织", "children": ["验收"]}]}]}, ensure_ascii=True)
        for end in range(1, len(raw) + 1):
            preview = service._catalog_preview(raw[:end])
            self.assertNotIn("catalog", preview)
            self.assertNotIn("children", preview)
            self.assertNotIn("title", preview)
        self.assertEqual(service._catalog_preview(raw), "食品配送\n  组织\n    验收")


if __name__ == "__main__":
    unittest.main()
