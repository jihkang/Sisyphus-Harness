from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


ALLOWED_TOOLS = {
    "list_files",
    "read_file",
    "search_text",
    "write_file",
    "replace_text",
    "delete_file",
}


def _arguments_schema(
    properties: dict[str, object],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
    }


_RELATIVE_PATH = {"type": "string", "minLength": 1}
_SHA256 = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
TOOL_ARGUMENT_SCHEMAS: dict[str, dict[str, object]] = {
    "list_files": _arguments_schema(
        {"prefix": {"type": "string"}},
    ),
    "read_file": _arguments_schema(
        {
            "path": _RELATIVE_PATH,
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        },
        required=("path",),
    ),
    "search_text": _arguments_schema(
        {
            "query": {"type": "string", "minLength": 1, "maxLength": 512},
            "path": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        required=("query",),
    ),
    "write_file": {
        "oneOf": [
            _arguments_schema(
                {
                    "path": _RELATIVE_PATH,
                    "content": {"type": "string"},
                    "expected_sha256": {"oneOf": [_SHA256, {"type": "null"}]},
                },
                required=("path", "content", "expected_sha256"),
            ),
            _arguments_schema(
                {
                    "path": _RELATIVE_PATH,
                    "content_lines": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "expected_sha256": {"oneOf": [_SHA256, {"type": "null"}]},
                },
                required=("path", "content_lines", "expected_sha256"),
            ),
        ]
    },
    "replace_text": {
        "oneOf": [
            _arguments_schema(
                {
                    "path": _RELATIVE_PATH,
                    "old": {"type": "string", "minLength": 1},
                    "new": {"type": "string"},
                    "expected_sha256": _SHA256,
                },
                required=("path", "old", "new", "expected_sha256"),
            ),
            _arguments_schema(
                {
                    "path": _RELATIVE_PATH,
                    "old_lines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "new_lines": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "expected_sha256": _SHA256,
                },
                required=("path", "old_lines", "new_lines", "expected_sha256"),
            ),
        ]
    },
    "delete_file": _arguments_schema(
        {
            "path": _RELATIVE_PATH,
            "expected_sha256": _SHA256,
        },
        required=("path", "expected_sha256"),
    ),
}


def _tool_decision_schema(tool: str) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "type": {"const": "tool"},
            "tool": {"const": tool},
            "arguments": TOOL_ARGUMENT_SCHEMAS[tool],
            "reason": {"type": "string"},
        },
        "required": ["type", "tool", "arguments", "reason"],
    }


AGENT_DECISION_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "agent_decision",
        "strict": True,
        "schema": {
            "oneOf": [
                *(
                    _tool_decision_schema(tool)
                    for tool in sorted(ALLOWED_TOOLS)
                ),
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "type": {"const": "finish"},
                        "summary": {"type": "string", "minLength": 1},
                    },
                    "required": ["type", "summary"],
                },
            ]
        },
    },
}


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AgentDecision:
    kind: str
    tool: str | None
    arguments: dict[str, Any]
    reason: str
    summary: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.kind,
            "tool": self.tool,
            "arguments": self.arguments,
            "reason": self.reason,
            "summary": self.summary,
        }


def parse_agent_decision(content: str) -> AgentDecision:
    payload = _parse_json_object(content)
    kind = payload.get("type")
    if kind == "tool":
        _reject_unknown(payload, {"type", "tool", "arguments", "reason"})
        tool = payload.get("tool")
        arguments = payload.get("arguments")
        reason = payload.get("reason", "")
        if not isinstance(tool, str) or tool not in ALLOWED_TOOLS:
            raise ProtocolError(f"unsupported tool: {tool}")
        if not isinstance(arguments, dict):
            raise ProtocolError("tool arguments must be an object")
        if not isinstance(reason, str):
            raise ProtocolError("tool reason must be a string")
        return AgentDecision(
            kind="tool",
            tool=tool,
            arguments=arguments,
            reason=reason.strip(),
            summary=None,
        )
    if kind == "finish":
        _reject_unknown(payload, {"type", "summary"})
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ProtocolError("finish summary must be a non-empty string")
        return AgentDecision(
            kind="finish",
            tool=None,
            arguments={},
            reason="",
            summary=summary.strip(),
        )
    raise ProtocolError("response type must be 'tool' or 'finish'")


def _parse_json_object(content: str) -> dict[str, Any]:
    stripped = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if fenced is not None:
        stripped = fenced.group(1)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"response is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("response JSON must be an object")
    return payload


def _reject_unknown(payload: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(payload).difference(allowed))
    if unknown:
        raise ProtocolError(f"response contains unknown fields: {', '.join(unknown)}")
