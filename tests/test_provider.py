from __future__ import annotations

from io import BytesIO
import json
import os
import unittest
from unittest.mock import patch
import urllib.error

from sisyphus_harness.config import ProviderSettings
from sisyphus_harness.provider import (
    ChatMessage,
    OpenAICompatibleProvider,
    ProviderError,
)
from sisyphus_harness.protocol import AGENT_DECISION_RESPONSE_FORMAT


class _Response:
    def __init__(self, payload: object) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


class ProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = ProviderSettings(
            base_url="http://127.0.0.1:8080/v1",
            model="local",
        )
        self.provider = OpenAICompatibleProvider(self.settings)

    def test_openai_compatible_completion(self) -> None:
        payload = {
            "choices": [
                {"message": {"content": '{"type":"finish","summary":"done"}'}}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
        with patch(
            "urllib.request.urlopen",
            return_value=_Response(payload),
        ) as urlopen:
            response = self.provider.complete(
                (ChatMessage(role="user", content="task"),)
            )

        self.assertEqual(response.content, '{"type":"finish","summary":"done"}')
        self.assertEqual(response.prompt_tokens, 10)
        self.assertEqual(response.completion_tokens, 4)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8080/v1/chat/completions")
        request_payload = json.loads(request.data)
        self.assertEqual(request_payload["model"], "local")
        self.assertFalse(request_payload["stream"])
        self.assertEqual(
            request_payload["response_format"],
            AGENT_DECISION_RESPONSE_FORMAT,
        )

    def test_plain_text_mode_omits_json_response_constraint(self) -> None:
        provider = OpenAICompatibleProvider(self.settings, json_mode=False)
        payload = {"choices": [{"message": {"content": "revised guidance"}}]}
        with patch(
            "urllib.request.urlopen",
            return_value=_Response(payload),
        ) as urlopen:
            provider.complete((ChatMessage(role="user", content="reflect"),))

        request = urlopen.call_args.args[0]
        self.assertNotIn("response_format", json.loads(request.data))

    def test_http_and_invalid_payload_fail_closed(self) -> None:
        error = urllib.error.HTTPError(
            url=self.settings.base_url,
            code=500,
            msg="failed",
            hdrs=None,
            fp=BytesIO(b'{"error":"failed"}'),
        )
        try:
            with patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaisesRegex(ProviderError, "HTTP 500"):
                    self.provider.complete((ChatMessage(role="user", content="task"),))
        finally:
            error.close()

        with patch(
            "urllib.request.urlopen",
            return_value=_Response({"choices": []}),
        ):
            with self.assertRaisesRegex(ProviderError, "invalid chat completion"):
                self.provider.complete((ChatMessage(role="user", content="task"),))

    def test_http_error_detail_is_bounded(self) -> None:
        error = urllib.error.HTTPError(
            url=self.settings.base_url,
            code=500,
            msg="failed",
            hdrs=None,
            fp=BytesIO(b"internal reasoning " * 200),
        )
        try:
            with patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(ProviderError) as raised:
                    self.provider.complete((ChatMessage(role="user", content="task"),))
        finally:
            error.close()

        self.assertLess(len(str(raised.exception)), 560)
        self.assertTrue(str(raised.exception).endswith("..."))

    def test_oversized_response_is_rejected_before_json_parsing(self) -> None:
        response = _Response({"choices": [{"message": {"content": "x" * 100}}]})
        with (
            patch("sisyphus_harness.provider.MAX_RESPONSE_BYTES", 32),
            patch("urllib.request.urlopen", return_value=response),
        ):
            with self.assertRaisesRegex(ProviderError, "exceeds 32 byte limit"):
                self.provider.complete((ChatMessage(role="user", content="task"),))

    def test_missing_api_key_fails_before_request(self) -> None:
        provider = OpenAICompatibleProvider(
            ProviderSettings(
                base_url=self.settings.base_url,
                model="local",
                api_key_env="SISYPHUS_TEST_MISSING_KEY",
            )
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key != "SISYPHUS_TEST_MISSING_KEY"
        }

        with patch.dict(os.environ, environment, clear=True):
            with patch("urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(
                    ProviderError,
                    "environment variable is unset",
                ):
                    provider.complete((ChatMessage(role="user", content="task"),))
                urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
