import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from app.api.routes import models
from app.services import model_preferences, qwen_client


class DeepseekTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_maps_ids_and_keeps_key_server_side(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={'data': [{'id': 'deepseek-flash', 'name': 'Flash'}]})
        client = httpx.AsyncClient
        with patch.object(models, 'deepseek_api_key', return_value='dummy-secret'), \
             patch.object(models.settings, 'deepseek_base_url', 'https://api.deepseek.com'), \
             patch.object(models.httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(handler), **kw)):
            result = await models.get_deepseek_catalog()
        self.assertEqual(str(seen[0].url), 'https://api.deepseek.com/models')
        self.assertEqual(result['models'][0]['id'], 'deepseek/deepseek-flash')
        self.assertNotIn('dummy-secret', str(result))

    async def test_catalog_missing_key_and_timeout(self):
        with patch.object(models, 'deepseek_api_key', return_value=''):
            with self.assertRaises(HTTPException) as caught:
                await models.get_deepseek_catalog()
            self.assertEqual(caught.exception.status_code, 503)
        class TimedOut:
            async def __aenter__(self): raise TimeoutError()
            async def __aexit__(self, *args): pass
        with patch.object(models, 'deepseek_api_key', return_value='dummy'), \
             patch.object(models.asyncio, 'timeout', return_value=TimedOut()):
            with self.assertRaises(HTTPException) as caught:
                await models.get_deepseek_catalog()
            self.assertEqual(caught.exception.status_code, 504)

    async def test_catalog_auth_error_does_not_echo_key(self):
        client = httpx.AsyncClient
        with patch.object(models, 'deepseek_api_key', return_value='dummy-secret'), \
             patch.object(models.httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(lambda req: httpx.Response(401, text='dummy-secret')), **kw)):
            with self.assertRaises(HTTPException) as caught:
                await models.get_deepseek_catalog()
        self.assertIn('密钥', caught.exception.detail)
        self.assertNotIn('dummy-secret', caught.exception.detail)

    async def test_deepseek_chat_and_stream_use_own_endpoint_and_key(self):
        seen = []
        def handler(request):
            body = json.loads(request.content)
            seen.append((request, body))
            if body['stream']:
                return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"正文"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            return httpx.Response(200, json={'choices':[{'message':{'content':'正文'},'finish_reason':'stop'}]})
        client = httpx.AsyncClient
        with patch.object(qwen_client, 'selected_model', return_value='deepseek/deepseek-flash'), \
             patch.object(qwen_client, 'deepseek_api_key', return_value='deepseek-dummy'), \
             patch.object(qwen_client.settings, 'deepseek_base_url', 'https://api.deepseek.com'), \
             patch.object(qwen_client.httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(handler), **kw)):
            result = await qwen_client.chat_completion([{'role':'user','content':'hello'}], scene='bid_content')
            stream = [part async for part in qwen_client.stream_chat_completion([{'role':'user','content':'hello'}], scene='bid_content', strict=True)]
        self.assertEqual((result.provider, result.usedApi, result.content), ('deepseek', True, '正文'))
        self.assertEqual(''.join(stream), '正文')
        for request, body in seen:
            self.assertEqual(str(request.url), 'https://api.deepseek.com/chat/completions')
            self.assertEqual(request.headers['authorization'], 'Bearer deepseek-dummy')
            self.assertEqual(body['model'], 'deepseek-flash')
            self.assertNotIn('enable_thinking', body)

    async def test_transport_failure_is_classified_for_recovery(self):
        def handler(request):
            raise httpx.ConnectError('offline', request=request)
        client = httpx.AsyncClient
        with patch.object(qwen_client, 'selected_model', return_value='deepseek/deepseek-flash'), \
             patch.object(qwen_client, 'deepseek_api_key', return_value='test-key'), \
             patch.object(qwen_client.httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(handler), **kw)):
            result = await qwen_client.chat_completion([{'role': 'user', 'content': 'test'}])
        self.assertFalse(result.usedApi)
        self.assertEqual(result.error_kind, 'network')

    async def test_reject_deepseek_in_embedding_or_ocr(self):
        for scene in ('embedding', 'knowledge_ocr'):
            with self.assertRaises(ValueError):
                model_preferences.save_selection(scene, 'deepseek/deepseek-flash')
