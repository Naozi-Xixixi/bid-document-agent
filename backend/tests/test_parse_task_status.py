"""Offline checks for long-running parse status and orphan detection."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services import bid_parse_service as service


class ParseTaskStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(service._ACTIVE_PARSE_TASKS.clear)
        service._ACTIVE_PARSE_TASKS.clear()
        self.company = "company_a"
        self.now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.info = {
            "parse_status": "processing", "parse_current_step": 3,
            "tender_file_path": "offline.docx",
            "update_time": (self.now - timedelta(seconds=service.PARSE_TIMEOUT_SECONDS + 1)).isoformat(),
        }
        self.stack.enter_context(patch.object(service, "get_current_tenant", side_effect=lambda: SimpleNamespace(company_id=self.company)))
        self.stack.enter_context(patch.object(service.storage, "now_utc", return_value=self.now))
        self.stack.enter_context(patch.object(service, "_get_parse_status_info", side_effect=lambda bid: dict(self.info)))
        self.failed = self.stack.enter_context(patch.object(service, "_mark_parse_failed", side_effect=self.mark_failed))

    def mark_failed(self, bid_id, file_path, message, code):
        self.info.update(parse_status="failed", parse_error=str(code), parse_error_msg=message)

    async def test_active_extraction_with_old_update_time_is_not_failed(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def work(*args):
            started.set()
            await release.wait()

        with patch.object(service, "_run_auto_parse_task_impl", work):
            task = asyncio.create_task(service.run_auto_parse_task(1, "offline.docx"))
            try:
                await started.wait()
                result = service.get_parse_status(1)
                self.assertEqual(result["parse_status"], "processing")
                self.failed.assert_not_called()
                self.assertIn(("company_a", 1, "offline.docx"), service._ACTIVE_PARSE_TASKS)
            finally:
                release.set()
                await task
        self.assertEqual(service._ACTIVE_PARSE_TASKS, {})

    async def test_orphan_with_old_update_time_is_still_failed(self):
        result = service.get_parse_status(1)
        self.failed.assert_called_once_with(1, "offline.docx", "解析超时，请重新上传", 50002)
        self.assertEqual(result["parse_status"], "failed")
        self.assertEqual(result["parse_error_code"], 50002)

    async def test_cancelled_task_releases_activity_and_is_detected_as_orphan(self):
        started = asyncio.Event()

        async def work(*args):
            started.set()
            await asyncio.Event().wait()

        with patch.object(service, "_run_auto_parse_task_impl", work):
            task = asyncio.create_task(service.run_auto_parse_task(1, "offline.docx"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(service._ACTIVE_PARSE_TASKS)
        self.assertEqual(service.get_parse_status(1)["parse_status"], "failed")

    async def test_other_company_or_replaced_file_is_not_protected_by_old_task(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def work(*args):
            started.set()
            await release.wait()

        with patch.object(service, "_run_auto_parse_task_impl", work):
            task = asyncio.create_task(service.run_auto_parse_task(1, "offline.docx"))
            try:
                await started.wait()
                self.company = "company_b"
                self.assertEqual(service.get_parse_status(1)["parse_status"], "failed")
                self.company = "company_a"
                self.info.update(parse_status="processing", tender_file_path="replacement.docx")
                self.assertEqual(service.get_parse_status(1)["parse_status"], "failed")
                self.assertEqual(self.failed.call_count, 2)
            finally:
                release.set()
                await task
        self.assertFalse(service._ACTIVE_PARSE_TASKS)


if __name__ == "__main__":
    unittest.main()
