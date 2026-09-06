import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from stock_alpha.ai_config import AISettings, check_connection, load_ai_settings


class StockAlphaAIConfigTests(unittest.TestCase):
    def test_legacy_strategy_environment_is_not_used(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = load_ai_settings(Path(directory) / "missing.json", environ={
                "STOCK_AI_PRIMARY_MODEL": "legacy-model", "MINIMAX_API_KEY": "legacy-secret",
                "STOCK_AI_PRIMARY_API_KEY": "legacy-secret",
            })
        self.assertEqual(settings.model, "deepseek-v4-flash")
        self.assertEqual(settings.api_key, "")

    def test_local_file_and_scoped_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ai_config.json"
            path.write_text(json.dumps({"model": "file-model", "base_url": "https://example.test/v1/",
                                        "api_key": "private-test-value"}))
            settings = load_ai_settings(path, environ={"STOCK_ALPHA_AI_MODEL": "scoped-model"})
        self.assertEqual(settings.model, "scoped-model")
        self.assertEqual(settings.endpoint, "https://example.test/v1/chat/completions")
        self.assertNotIn("private-test-value", repr(settings))
        self.assertNotIn("private-test-value", json.dumps(settings.public_config()))

    def test_invalid_configuration_fails_without_exposing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ai_config.json"
            for value in ("private-test-value", '["private-test-value"]', '{"unexpected":"private-test-value"}'):
                path.write_text(value)
                with self.assertRaises(ValueError) as caught:
                    load_ai_settings(path, environ={})
                self.assertNotIn("private-test-value", str(caught.exception))

    def test_rejects_unsafe_urls(self):
        for url in ("http://example.test/v1", "https://secret@example.test/v1", "https://example.test/v1?key=x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                AISettings(base_url=url)

    @patch("stock_alpha.ai_config.requests.post")
    def test_missing_key_does_not_call_provider(self, post):
        self.assertEqual(check_connection(AISettings())["error"], "missing_api_key")
        post.assert_not_called()

    @patch("stock_alpha.ai_config.requests.post")
    def test_success_is_a_bounded_non_trading_probe(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"model": "deepseek-v4-flash", "choices": [{"message": {"content": "OK"}}]}
        result = check_connection(AISettings(api_key="private-test-value"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["no_orders"])
        self.assertTrue(result["response_model_matches"])
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://tbtk.asia/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "deepseek-v4-flash")
        self.assertEqual(kwargs["json"]["max_tokens"], 128)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("private-test-value", json.dumps(result))

    @patch("stock_alpha.ai_config.requests.post")
    def test_failure_redacts_response_and_exception(self, post):
        post.return_value = Mock(status_code=401, text="private-test-value")
        result = check_connection(AISettings(api_key="private-test-value"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["http_status"], 401)
        self.assertNotIn("private-test-value", json.dumps(result))
        post.side_effect = requests.ConnectionError("private-test-value")
        self.assertEqual(check_connection(AISettings(api_key="private-test-value"))["error"], "network_error")

    @patch("stock_alpha.ai_config.requests.post")
    def test_model_alias_is_visible_but_embedded_key_is_redacted(self, post):
        post.return_value = Mock(status_code=200)
        for name in ("provider-alias", "private-test-value"):
            post.return_value.json.return_value = {"model": name, "choices": [{"message": {"content": "OK"}}]}
            result = check_connection(AISettings(api_key="private-test-value"))
            self.assertTrue(result["ok"])
            self.assertFalse(result["response_model_matches"])
            self.assertNotIn("private-test-value", json.dumps(result))
            self.assertEqual(result["response_model"], None if name == "private-test-value" else name)

    @patch("stock_alpha.ai_config.requests.post")
    def test_malformed_and_empty_response_do_not_pass(self, post):
        for payload in ({}, {"choices": []}, {"choices": [{"message": {"content": ""}}]}):
            post.return_value = Mock(status_code=200)
            post.return_value.json.return_value = payload
            self.assertFalse(check_connection(AISettings(api_key="private-test-value"))["ok"])


if __name__ == "__main__":
    unittest.main()
