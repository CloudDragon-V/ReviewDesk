"""Offline transport and accounting tests; never use the user's API key."""
import json
import io
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from reviewdesk.api import ApiError, DeepSeekClient, estimate_cost, parse_usage


class PricingTests(unittest.TestCase):
    def test_peak_and_weekend(self):
        monday = datetime(2026, 9, 14, 2, tzinfo=timezone.utc)
        saturday = datetime(2026, 9, 12, 2, tzinfo=timezone.utc)
        self.assertEqual(estimate_cost("deepseek-flash", 1000, 100, 200, monday), Decimal("0.00240800"))
        self.assertEqual(estimate_cost("deepseek-flash", 1000, 100, 200, saturday), Decimal("0.00120400"))

    def test_usage_not_fabricated(self):
        for data in (None, {}, {"prompt_tokens": True, "completion_tokens": 1},
                     {"prompt_tokens": 2, "completion_tokens": 1, "prompt_cache_hit_tokens": 3},
                     {"prompt_tokens": 10**100, "completion_tokens": 1}):
            self.assertFalse(parse_usage(data, "deepseek-flash", datetime.now(timezone.utc)).actual)


class TransportTests(unittest.TestCase):
    def test_unicode_escaped_credential_is_redacted_after_decoding(self):
        key = "test-secret-credential"
        escaped = "".join("\\u%04x" % ord(char) for char in key)
        raw = ('{"nested":[{"value":"' + escaped + '"}]}').encode()
        with patch("urllib.request.OpenerDirector.open", return_value=io.BytesIO(raw)):
            result = DeepSeekClient(key).check_connection()
        self.assertNotIn(key, str(result))
        self.assertIn("[REDACTED]", str(result))

    def test_wrapped_timeout_is_classified(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=URLError(TimeoutError())):
            with self.assertRaises(ApiError) as raised:
                DeepSeekClient("test-only-credential").check_connection()
        self.assertEqual(raised.exception.kind, "timeout")

    def test_connection_reset_during_read_is_classified(self):
        class BrokenResponse(io.BytesIO):
            def read(self, *args):
                raise ConnectionResetError(10054, "private socket detail")
        with patch("urllib.request.OpenerDirector.open", return_value=BrokenResponse()):
            with self.assertRaises(ApiError) as raised:
                DeepSeekClient("test-only-credential").check_connection()
        self.assertEqual(raised.exception.kind, "network")
        self.assertNotIn("private socket detail", str(raised.exception))

    def test_malformed_choices_preserve_usage(self):
        for choices in ([None], [123], ["text"], {}, None, [{"finish_reason": "stop", "message": None}]):
            response = {"choices": choices, "usage": {"prompt_tokens": 30, "completion_tokens": 10}}
            with patch.object(DeepSeekClient, "_request", return_value=response):
                with self.assertRaises(ApiError) as raised:
                    DeepSeekClient("test-only-credential").complete("json", "source")
                self.assertTrue(raised.exception.usage.actual)
                self.assertEqual(raised.exception.usage.prompt_tokens, 30)

    def test_http_classification_does_not_echo_body(self):
        for code, kind in [(401, "authentication"), (402, "balance"), (429, "rate_limit"), (503, "server"), (400, "request")]:
            with patch("urllib.request.OpenerDirector.open", side_effect=HTTPError("https://api.deepseek.com", code, "private body", {}, None)):
                with self.assertRaises(ApiError) as raised:
                    DeepSeekClient("test-only-credential").check_connection()
                self.assertEqual(raised.exception.kind, kind)
                self.assertNotIn("private", str(raised.exception))

    def test_network(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=URLError("secret detail")):
            with self.assertRaises(ApiError) as raised:
                DeepSeekClient("test-only-credential").check_connection()
        self.assertEqual(raised.exception.kind, "network")
        self.assertNotIn("secret detail", str(raised.exception))

    def test_truncated_json_preserves_usage(self):
        response = {"choices": [{"finish_reason": "length", "message": {"content": "{"}}],
                    "usage": {"prompt_tokens": 123, "completion_tokens": 25}}
        with patch.object(DeepSeekClient, "_request", return_value=response):
            with self.assertRaises(ApiError) as raised:
                DeepSeekClient("test-only-credential").complete("json", "source")
        self.assertTrue(raised.exception.usage.actual)
        self.assertEqual(raised.exception.usage.prompt_tokens, 123)

    def test_valid_response(self):
        response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"findings": []})}}],
                    "usage": {"prompt_tokens": 123, "completion_tokens": 25}}
        with patch.object(DeepSeekClient, "_request", return_value=response) as request:
            result, usage = DeepSeekClient("test-only-credential").complete("json", "source")
        self.assertEqual(result, {"findings": []})
        self.assertTrue(usage.actual)
        self.assertEqual(request.call_args.args[1]["thinking"], {"type": "disabled"})

    def test_reading_reasoning_mode_keeps_only_final_answer_and_full_usage(self):
        response={"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"findings":[]}),"reasoning_content":"internal"}}],
                  "usage":{"prompt_tokens":130,"completion_tokens":300,"completion_tokens_details":{"reasoning_tokens":220}}}
        with patch.object(DeepSeekClient,"_request",return_value=response) as request:
            result,usage=DeepSeekClient("test-only-credential").complete("json","source",max_tokens=12000,reasoning=True)
        self.assertEqual(request.call_args.args[1]["thinking"],{"type":"enabled"})
        self.assertEqual(request.call_args.args[1]["reasoning_effort"],"high")
        self.assertEqual(request.call_args.args[1]["max_tokens"],12000)
        self.assertNotIn("internal",str(result))
        self.assertEqual(usage.completion_tokens,300)


if __name__ == "__main__":
    unittest.main()
