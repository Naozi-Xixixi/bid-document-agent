import asyncio
import unittest
from unittest.mock import patch

from app.modules.knowledge import service


class KnowledgeTaskThrottlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_deep_recognition_jobs_run_one_at_a_time(self):
        entered = []
        started = asyncio.Event()
        gate = asyncio.Event()

        async def recognize(item_id):
            entered.append(item_id)
            started.set()
            await gate.wait()

        with patch.object(service, '_DEEP_SEMAPHORE', asyncio.Semaphore(1)), \
             patch.object(service, 'deep_extract_item', side_effect=recognize):
            self.assertTrue(service._queue_deep_extract(900001))
            self.assertTrue(service._queue_deep_extract(900002))
            try:
                await asyncio.wait_for(started.wait(), 2)
                await asyncio.sleep(0.05)
                self.assertEqual(entered, [900001])
            finally:
                tasks = list(service._DEEP_TASKS)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(0)
            self.assertNotIn(service._tenant_item_key(900001), service._DEEP_ITEM_IDS)
            self.assertNotIn(service._tenant_item_key(900002), service._DEEP_ITEM_IDS)


class CatalogTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_timeout_returns_actionable_error(self):
        from fastapi import HTTPException
        from app.api.routes import models

        class TimedOut:
            async def __aenter__(self):
                raise TimeoutError()
            async def __aexit__(self, *args):
                pass

        with patch.object(models, 'qwen_api_key', return_value='test'), \
             patch.object(models.settings, 'qwen_base_url', 'https://example.invalid'), \
             patch.object(models.asyncio, 'timeout', return_value=TimedOut()):
            with self.assertRaises(HTTPException) as caught:
                await models.get_qwen_catalog()
        self.assertEqual(caught.exception.status_code, 504)
        self.assertIn('超时', caught.exception.detail)


class TextIngestionResponsivenessTests(unittest.TestCase):
    from test_knowledge_reliability import KnowledgeInterpretationTests as fixture
    setUp = fixture.setUp
    connect = fixture.connect
    saved = fixture.saved

    def test_slow_chunking_keeps_event_loop_responsive(self):
        import time
        from pathlib import Path
        from unittest.mock import AsyncMock
        source = Path(self.temp.name) / 'input.txt'
        source.write_text('项目测试内容', encoding='utf8')
        original_split = service._split_chunks
        def slow_split(*args, **kwargs):
            time.sleep(0.4)
            return original_split(*args, **kwargs)
        extracted = {'text': '项目测试内容', 'pages': [], 'sections': [], 'total_pages': 1}
        async def run():
            with patch.object(service, '_resolve_stored_path', return_value=source), \
                 patch.object(service, '_extract_document', return_value=extracted), \
                 patch.object(service, '_split_chunks', side_effect=slow_split), \
                 patch.object(service, '_semantic_confirm_assets', new_callable=AsyncMock), \
                 patch.object(service, '_queue_item_vector_sync'), \
                 patch.object(service, '_delete_item_vectors'), \
                 patch.object(service.vector_store, 'enabled', return_value=False), \
                 patch.object(service, '_maybe_finalize_project_for_item'):
                task = asyncio.create_task(service._process_item_once(1))
                ticks = 0
                while not task.done():
                    await asyncio.sleep(0.05)
                    ticks += 1
                await task
                self.assertGreaterEqual(ticks, 4)
        asyncio.run(run())
        with self.connect() as conn:
            item = conn.execute('SELECT status, process_error FROM knowledge_item WHERE item_id=1').fetchone()
            chunks = conn.execute('SELECT COUNT(*) FROM knowledge_chunk WHERE item_id=1').fetchone()[0]
        self.assertEqual(item['status'], 'completed', item['process_error'])
        self.assertGreater(chunks, 0)
