from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from typing import Protocol, runtime_checkable
import urllib.error
import urllib.request

from .config import ProviderSettings
from .contracts.codec import WireModel
from .protocol import AGENT_DECISION_RESPONSE_FORMAT


class ProviderError(RuntimeError):
    pass


MAX_RESPONSE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ChatMessage(WireModel):
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ChatProvider(Protocol):
    def complete(self, messages: tuple[ChatMessage, ...]) -> ChatResponse:
        ...


@runtime_checkable
class DeadlineChatProvider(Protocol):
    def complete_with_timeout(
        self,
        messages: tuple[ChatMessage, ...],
        *,
        timeout_seconds: float,
    ) -> ChatResponse:
        ...


class OpenAICompatibleProvider:
    def __init__(
        self,
        settings: ProviderSettings,
        *,
        json_mode: bool = True,
    ) -> None:
        self.settings = settings
        self.response_format = settings.response_format if json_mode else "none"

    def complete(self, messages: tuple[ChatMessage, ...]) -> ChatResponse:
        return self.complete_with_timeout(
            messages,
            timeout_seconds=self.settings.timeout_seconds,
        )

    def complete_with_timeout(
        self,
        messages: tuple[ChatMessage, ...],
        *,
        timeout_seconds: float,
    ) -> ChatResponse:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ProviderError("provider deadline timeout must be positive and finite")
        endpoint = f"{self.settings.base_url.rstrip('/')}/chat/completions"
        request_payload: dict[str, object] = {
            "model": self.settings.model,
            "messages": [message.to_dict() for message in messages],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        if self.response_format == "json_schema":
            request_payload["response_format"] = AGENT_DECISION_RESPONSE_FORMAT
        elif self.response_format == "json_object":
            request_payload["response_format"] = {"type": "json_object"}
        payload = json.dumps(request_payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        authorization: str | None = None
        if self.settings.api_key_env is not None:
            api_key = os.environ.get(self.settings.api_key_env)
            if not api_key:
                raise ProviderError(
                    f"provider API key environment variable is unset: "
                    f"{self.settings.api_key_env}"
                )
            authorization = f"Bearer {api_key}"
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers=headers,
            method="POST",
        )
        if authorization is not None:
            request.add_unredirected_header("Authorization", authorization)
        try:
            # ProviderSettings rejects non-HTTP(S) schemes before this boundary.
            with urllib.request.urlopen(  # nosec B310
                request,
                timeout=min(self.settings.timeout_seconds, timeout_seconds),
            ) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = " ".join(
                exc.read(1025).decode("utf-8", errors="replace").split()
            )
            if len(detail) > 512:
                detail = f"{detail[:512]}..."
            if exc.code == 400 and self.response_format == "json_schema":
                detail = (
                    f"{detail} Provider may not support strict json_schema; set "
                    "provider.response_format = 'json_object' or 'none' only after "
                    "confirming the endpoint capability."
                ).strip()
            raise ProviderError(f"provider returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise ProviderError(
                f"provider response exceeds {MAX_RESPONSE_BYTES} byte limit"
            )
        try:
            raw = json.loads(body)
            content = raw["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("provider returned an invalid chat completion") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("provider returned empty message content")
        usage = raw.get("usage", {})
        return ChatResponse(
            content=content,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
        )


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
