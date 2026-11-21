"""Offline ASGI lifecycle checks; never touch a project database or real files."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse

from app.core.tenant_context import TenantContext, reset_current_tenant, set_current_tenant
from app.services import project_activity as activity


def tenant(company: str) -> TenantContext:
    return TenantContext(1, 'offline-test', 'user', company, company, Path('unused.sqlite3'), Path('unused-uploads'))


def scope(path: str, method: str = 'POST') -> dict:
    return {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.0'},
            'http_version': '1.1', 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
            'query_string': b'', 'method': method, 'headers': [], 'server': ('test', 80), 'client': ('test', 1)}


class ProjectActivityLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.token = set_current_tenant(tenant('activity-company-a'))
        self.addCleanup(reset_current_tenant, self.token)
        self.addCleanup(activity._ACTIVE.clear)
        self.addCleanup(activity._DELETING.clear)
        activity._ACTIVE.clear()
        activity._DELETING.clear()
        self.messages = []
        self.receive_queue = asyncio.Queue()

    async def receive(self):
        return await self.receive_queue.get()

    async def send(self, message):
        self.messages.append(message)

    def assert_busy(self, project_id='project-a', bid_id=1):
        with self.assertRaises(activity.ActivityConflict):
            with activity.begin_deletion(project_id, bid_id):
                pass

    def assert_idle(self, project_id='project-a', bid_id=1):
        with activity.begin_deletion(project_id, bid_id):
            pass
        self.assertFalse(activity._ACTIVE)
        self.assertFalse(activity._DELETING)

    async def test_stream_disconnect_releases_lease_and_closes_generator(self):
        first_chunk = asyncio.Event()
        closed = asyncio.Event()

        async def chunks():
            try:
                yield b'data: first\n\n'
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def send(message):
            await self.send(message)
            if message['type'] == 'http.response.body' and message.get('body'):
                first_chunk.set()

        app = activity.ProjectActivityMiddleware(StreamingResponse(chunks(), media_type='text/event-stream'))
        task = asyncio.create_task(app(scope('/api/bid/1/graphic-section/generate-stream', 'GET'), self.receive, send))
        try:
            await asyncio.wait_for(first_chunk.wait(), 1)
            self.assert_busy()
            await self.receive_queue.put({'type': 'http.disconnect'})
            await asyncio.wait_for(task, 1)
            self.assertTrue(closed.is_set())
            self.assert_idle()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_background_work_holds_lease_after_final_response_body(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def background():
            entered.set()
            await release.wait()

        app = activity.ProjectActivityMiddleware(Response(b'accepted', background=BackgroundTask(background)))
        task = asyncio.create_task(app(scope('/api/bid/1/upload-and-parse'), self.receive, self.send))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assertTrue(any(m['type'] == 'http.response.body' and not m.get('more_body', False) for m in self.messages))
            self.assert_busy()
        finally:
            release.set()
            await task
        self.assert_idle()

    async def test_upload_request_is_protected_before_body_finishes(self):
        entered = asyncio.Event()

        async def app(current_scope, receive, send):
            entered.set()
            body = await receive()
            self.assertEqual(body['body'], b'fixture')
            await Response(b'ok')(current_scope, receive, send)

        task = asyncio.create_task(activity.ProjectActivityMiddleware(app)(scope('/api/projects/project-a/files'), self.receive, self.send))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assert_busy()
        finally:
            await self.receive_queue.put({'type': 'http.request', 'body': b'fixture', 'more_body': False})
            await task
        self.assert_idle()

    async def test_cancelled_request_does_not_leak_lease(self):
        entered = asyncio.Event()

        async def app(*args):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(activity.ProjectActivityMiddleware(app)(scope('/api/bid/1/reparse'), self.receive, self.send))
        await asyncio.wait_for(entered.wait(), 1)
        self.assert_busy()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_idle()

    async def test_application_error_releases_lease(self):
        async def app(*args):
            self.assert_busy()
            raise RuntimeError('offline fixture failure')

        with self.assertRaisesRegex(RuntimeError, 'offline fixture failure'):
            await activity.ProjectActivityMiddleware(app)(scope('/api/bid/1/parse-info', 'GET'), self.receive, self.send)
        self.assert_idle()

    async def test_delete_request_does_not_lock_itself(self):
        async def app(current_scope, receive, send):
            with activity.begin_deletion('project-a', 1):
                await Response(b'deleted')(current_scope, receive, send)

        await activity.ProjectActivityMiddleware(app)(scope('/api/projects/project-a', 'DELETE'), self.receive, self.send)
        self.assertEqual(self.messages[0]['status'], 200)
        self.assert_idle()

    async def test_deletion_blocks_new_bid_and_project_requests(self):
        calls = []

        async def app(*args):
            calls.append(True)

        with activity.begin_deletion('project-a', 1):
            for path in ['/api/bid/1/parse-info', '/api/projects/project-a/settings']:
                self.messages.clear()
                await activity.ProjectActivityMiddleware(app)(scope(path, 'GET'), self.receive, self.send)
                self.assertEqual(self.messages[0]['status'], 409)
        self.assertEqual(calls, [])
        self.assert_idle()

    async def test_same_ids_in_different_companies_do_not_conflict(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def app(*args):
            entered.set()
            await release.wait()

        task = asyncio.create_task(activity.ProjectActivityMiddleware(app)(scope('/api/bid/1/parse-info', 'GET'), self.receive, self.send))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assert_busy()
            other = set_current_tenant(tenant('activity-company-b'))
            try:
                with activity.begin_deletion('project-a', 1):
                    pass
            finally:
                reset_current_tenant(other)
            self.assert_busy()
        finally:
            release.set()
            await task
        self.assert_idle()

    async def test_numeric_bid_aliases_share_the_canonical_lease(self):
        for identifier in ['01', '+1', '001']:
            with self.subTest(identifier=identifier):
                async def app(current_scope, receive, send):
                    self.assert_busy()
                    await Response(b'ok')(current_scope, receive, send)

                await activity.ProjectActivityMiddleware(app)(scope('/api/bid/' + identifier + '/parse-info', 'GET'), self.receive, self.send)
                self.assert_idle()

    async def test_one_of_two_finished_requests_does_not_release_the_other(self):
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        calls = 0

        async def app(*args):
            nonlocal calls
            index = calls
            calls += 1
            entered[index].set()
            await release[index].wait()

        middleware = activity.ProjectActivityMiddleware(app)
        tasks = [asyncio.create_task(middleware(scope('/api/bid/1/parse-info', 'GET'), self.receive, self.send)) for _ in range(2)]
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 1)
            self.assert_busy()
            release[0].set()
            await tasks[0]
            self.assert_busy()
        finally:
            for event in release:
                event.set()
            await asyncio.gather(*tasks)
        self.assert_idle()

    async def test_merge_holds_both_target_and_source_bid_leases(self):
        async def app(current_scope, receive, send):
            self.assert_busy('target-project', 1)
            self.assert_busy('source-project', 2)
            await Response(b'ok')(current_scope, receive, send)

        await activity.ProjectActivityMiddleware(app)(scope('/api/bid/1/merge-parse/02'), self.receive, self.send)
        self.assert_idle()

    async def test_deleting_merge_source_rejects_request_without_leaking_target_lease(self):
        called = []

        async def app(*args):
            called.append(True)

        with activity.begin_deletion('source-project', 2):
            await activity.ProjectActivityMiddleware(app)(scope('/api/bid/1/merge-parse/2'), self.receive, self.send)
            self.assertEqual(self.messages[0]['status'], 409)
            self.assertFalse(activity._ACTIVE)
        self.assertFalse(called)
        self.assert_idle()

    async def test_public_project_request_protects_body_addressed_work(self):
        with activity.project_request('project-a'):
            await asyncio.sleep(0)
            self.assert_busy()
        self.assert_idle()
        with activity.begin_deletion('project-a', 1):
            with self.assertRaises(activity.ActivityConflict):
                with activity.project_request('project-a'):
                    self.fail('deleting project should reject new body-addressed work')
        self.assert_idle()

    async def test_public_project_request_releases_after_application_error(self):
        with self.assertRaisesRegex(RuntimeError, 'offline fixture'):
            with activity.project_request('project-a'):
                raise RuntimeError('offline fixture')
        self.assert_idle()

    async def test_agent_stream_disconnect_during_send_releases_body_addressed_lease(self):
        from app.api.routes import agent

        sending = asyncio.Event()
        generator_closed = asyncio.Event()

        async def chunks(*args):
            try:
                yield {"type": "delta", "content": "offline fixture"}
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                generator_closed.set()

        async def blocked_send(message):
            await self.send(message)
            if message['type'] == 'http.response.body' and message.get('body'):
                sending.set()
                await asyncio.Event().wait()

        payload = SimpleNamespace(projectId='project-a', message='offline', mode='agent')
        with patch.object(agent, '_load_project', return_value=object()), patch.object(agent.tender_agent, 'stream_chat', chunks):
            response = await agent.chat_stream(payload)
            task = asyncio.create_task(response(scope('/api/agent/chat/stream'), self.receive, blocked_send))
            try:
                await asyncio.wait_for(sending.wait(), 1)
                self.assert_busy()
                await self.receive_queue.put({'type': 'http.disconnect'})
                await asyncio.wait_for(task, 1)
                self.assertTrue(generator_closed.is_set(), 'disconnect must close the model stream even while socket send is suspended')
                self.assert_idle()
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await response.body_iterator.aclose()


    async def test_agent_stream_socket_error_closes_provider_and_releases_lease(self):
        from app.api.routes import agent
        from starlette.requests import ClientDisconnect

        closed = asyncio.Event()

        async def chunks(*args):
            try:
                yield {"type": "delta", "content": "offline fixture"}
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                closed.set()

        async def failed_send(message):
            await self.send(message)
            if message['type'] == 'http.response.body' and message.get('body'):
                self.assert_busy()
                raise OSError('offline disconnected socket')

        payload = SimpleNamespace(projectId='project-a', message='offline', mode='agent')
        current_scope = scope('/api/agent/chat/stream')
        current_scope['asgi']['spec_version'] = '2.4'
        with patch.object(agent, '_load_project', return_value=object()), patch.object(agent.tender_agent, 'stream_chat', chunks):
            response = await agent.chat_stream(payload)
            with self.assertRaises(ClientDisconnect):
                await response(current_scope, self.receive, failed_send)
        self.assertTrue(closed.is_set())
        self.assert_idle()


if __name__ == '__main__':
    unittest.main()
