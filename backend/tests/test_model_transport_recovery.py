import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.services import qwen_client


class ModelTransportRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_disconnect_is_classified_as_network_failure(self):
        client = AsyncMock()
        client.__aenter__.return_value.post.side_effect = httpx.RemoteProtocolError(
            'Server disconnected without sending a response.'
        )
        with patch.object(qwen_client, '_generation_target', return_value=('qwen', 'test', 'dummy', 'https://example.invalid')), patch.object(qwen_client.httpx, 'AsyncClient', return_value=client):
            result = await qwen_client.chat_completion([{'role': 'user', 'content': 'test'}], model='test')
        self.assertFalse(result.usedApi)
        self.assertEqual(result.error_kind, 'network')

    async def test_http_error_is_not_misclassified_as_network_failure(self):
        client = AsyncMock()
        response = httpx.Response(401, text='unauthorized')
        client.__aenter__.return_value.post.return_value = response
        with patch.object(qwen_client, '_generation_target', return_value=('qwen', 'test', 'dummy', 'https://example.invalid')), patch.object(qwen_client.httpx, 'AsyncClient', return_value=client):
            result = await qwen_client.chat_completion([{'role': 'user', 'content': 'test'}], model='test')
        self.assertFalse(result.usedApi)
        self.assertIsNone(result.error_kind)
