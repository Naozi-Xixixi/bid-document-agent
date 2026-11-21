import asyncio
import json
import unittest
from unittest.mock import patch
import httpx
from app.services import qwen_client as q

def frame(text="", reason=None):
    return ("data: " + json.dumps({"choices":[{"delta":{"content":text}, "finish_reason":reason}]}, ensure_ascii=False) + "\n\n").encode()

class Body(httpx.AsyncByteStream):
    def __init__(self, parts, gate=None):
        self.parts, self.gate = parts, gate
    async def __aiter__(self):
        for index, part in enumerate(self.parts):
            if index == 1 and self.gate:
                await self.gate.wait()
            yield part

class StreamingTests(unittest.IsolatedAsyncioTestCase):
    def client(self, parts, gate=None, capture=None):
        real = httpx.AsyncClient
        def handle(request):
            if capture is not None:
                capture.append(json.loads(request.content))
            return httpx.Response(200, stream=Body(parts, gate))
        return lambda **kwargs: real(transport=httpx.MockTransport(handle), **kwargs)

    async def consume(self, parts, **kwargs):
        with patch.object(q, "qwen_api_key", return_value="offline-test"), patch.object(q, "_apply_qwen_generation_options"), patch.object(q.httpx, "AsyncClient", self.client(parts)):
            return [x async for x in q.stream_chat_completion([], strict=True, **kwargs)]

    async def test_delta_arrives_before_completion(self):
        gate = asyncio.Event()
        capture = []
        with patch.object(q, "qwen_api_key", return_value="offline-test"), patch.object(q, "_apply_qwen_generation_options"), patch.object(q.httpx, "AsyncClient", self.client([frame("第一段"), frame("第二段"), frame(reason="stop"), b"data: [DONE]\n\n"], gate, capture)):
            stream = q.stream_chat_completion([], strict=True, json_mode=True)
            self.assertEqual(await asyncio.wait_for(anext(stream), 1), "第一段")
            self.assertFalse(gate.is_set())
            gate.set()
            self.assertEqual("".join([x async for x in stream]), "第二段")
        self.assertTrue(capture[0]["stream"])
        self.assertEqual(capture[0]["response_format"], {"type":"json_object"})

    async def test_broken_stream_never_appends_fallback(self):
        with patch.object(q, "qwen_api_key", return_value="offline-test"), patch.object(q, "_apply_qwen_generation_options"), patch.object(q.httpx, "AsyncClient", self.client([frame("半截正文")])):
            stream = q.stream_chat_completion([], strict=True, fallback="错误的替代正文")
            self.assertEqual(await anext(stream), "半截正文")
            with self.assertRaisesRegex(RuntimeError, "提前中断"):
                await anext(stream)

    async def test_output_limit_is_failure(self):
        with self.assertRaisesRegex(RuntimeError, "输出长度"):
            await self.consume([frame("未完成"), frame(reason="length"), b"data: [DONE]\n\n"])

    async def test_empty_response_is_failure(self):
        with self.assertRaisesRegex(RuntimeError, "未返回正文"):
            await self.consume([frame(reason="stop"), b"data: [DONE]\n\n"])

    async def test_invalid_json_is_failure(self):
        with self.assertRaisesRegex(RuntimeError, "无效"):
            await self.consume([b"data: {broken\n\n"])

    async def test_missing_key_is_failure(self):
        with patch.object(q, "qwen_api_key", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "未配置"):
                await anext(q.stream_chat_completion([], strict=True))

    async def test_legacy_missing_key_still_works(self):
        with patch.object(q, "qwen_api_key", return_value=""):
            self.assertEqual("".join([x async for x in q.stream_chat_completion([], fallback="本地兼容内容")]), "本地兼容内容")

if __name__ == "__main__":
    unittest.main()
