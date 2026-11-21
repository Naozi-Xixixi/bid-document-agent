from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.core.tenant_context import TenantContext, get_current_tenant, reset_current_tenant, set_current_tenant
from app.services import model_preferences, qwen_client, storage


class ModelPreferencesTests(unittest.TestCase):
    def test_scenes_are_independent_and_company_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = {company: Path(temp) / f"{company}.db" for company in ("a", "b")}

            def connect():
                return sqlite3.connect(paths[get_current_tenant().company_id])

            with patch.object(storage, "connect", side_effect=connect):
                for company, expected in (("a", "qwen-plus"), ("b", "qwen-max")):
                    token = set_current_tenant(TenantContext(1, "admin", "admin", company, company, paths[company], Path(temp)))
                    try:
                        self.assertEqual(model_preferences.save_selection("catalog", expected), expected)
                        self.assertEqual(model_preferences.selected_model("catalog"), expected)
                        self.assertEqual(model_preferences.selected_model("bid_content"), model_preferences.default_model("bid_content"))
                    finally:
                        reset_current_tenant(token)
                token = set_current_tenant(TenantContext(1, "admin", "admin", "a", "a", paths["a"], Path(temp)))
                try:
                    self.assertEqual(model_preferences.selected_model("catalog"), "qwen-plus")
                    self.assertEqual(model_preferences.save_selection("catalog", None), model_preferences.default_model("catalog"))
                    self.assertEqual(model_preferences.selected_model("catalog"), model_preferences.default_model("catalog"))
                    with self.assertRaises(ValueError):
                        model_preferences.save_selection("catalog", "invalid model id")
                finally:
                    reset_current_tenant(token)


class ModelRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_request_uses_selected_scene_model(self):
        captured = []

        def handle(request: httpx.Request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "完成"}, "finish_reason": "length"}]})

        real_client = httpx.AsyncClient
        factory = lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs)
        with patch.object(qwen_client, "selected_model", return_value="qwen-plus") as selected, \
             patch.object(qwen_client, "qwen_api_key", return_value="offline-test"), \
             patch.object(qwen_client.httpx, "AsyncClient", factory):
            result = await qwen_client.chat_completion([{"role": "user", "content": "测试"}], scene="catalog")
        self.assertEqual(result.content, "完成")
        self.assertEqual(result.finish_reason, "length")
        self.assertEqual(captured[0]["model"], "qwen-plus")
        selected.assert_called_once_with("catalog")


if __name__ == "__main__":
    unittest.main()
