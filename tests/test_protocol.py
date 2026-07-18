from __future__ import annotations

import unittest

from sisyphus_harness.protocol import (
    AGENT_DECISION_RESPONSE_FORMAT,
    ProtocolError,
    TOOL_ARGUMENT_SCHEMAS,
    parse_agent_decision,
)


class ProtocolTests(unittest.TestCase):
    def test_parses_raw_and_single_fenced_json(self) -> None:
        raw = (
            '{"type":"tool","tool":"read_file",'
            '"arguments":{"path":"a.py"},"reason":"inspect"}'
        )
        decision = parse_agent_decision(raw)
        fenced = parse_agent_decision(f"```json\n{raw}\n```")

        self.assertEqual(decision, fenced)
        self.assertEqual(decision.tool, "read_file")
        self.assertEqual(decision.arguments, {"path": "a.py"})

    def test_parses_finish(self) -> None:
        decision = parse_agent_decision(
            '{"type":"finish","summary":"implemented and verified"}'
        )

        self.assertEqual(decision.kind, "finish")
        self.assertEqual(decision.summary, "implemented and verified")

    def test_rejects_unknown_tool_and_fields(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "unsupported tool"):
            parse_agent_decision(
                '{"type":"tool","tool":"shell","arguments":{},"reason":""}'
            )
        with self.assertRaisesRegex(ProtocolError, "unknown fields"):
            parse_agent_decision(
                '{"type":"finish","summary":"done","approved":true}'
            )

    def test_rejects_prose_around_json(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "not valid JSON"):
            parse_agent_decision(
                'Here is the action: {"type":"finish","summary":"done"}'
            )

    def test_rejects_non_object_and_missing_arguments(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "must be an object"):
            parse_agent_decision("[]")
        with self.assertRaisesRegex(ProtocolError, "arguments must be an object"):
            parse_agent_decision('{"type":"tool","tool":"list_files"}')

    def test_response_schema_enforces_each_tool_argument_contract(self) -> None:
        schema = AGENT_DECISION_RESPONSE_FORMAT["json_schema"]["schema"]
        variants = schema["oneOf"]
        tool_variants = {
            variant["properties"]["tool"]["const"]: variant
            for variant in variants
            if "tool" in variant["properties"]
        }

        self.assertEqual(set(tool_variants), set(TOOL_ARGUMENT_SCHEMAS))
        replace_variants = tool_variants["replace_text"]["properties"][
            "arguments"
        ]["oneOf"]
        self.assertEqual(
            {frozenset(variant["required"]) for variant in replace_variants},
            {
                frozenset({"path", "old", "new", "expected_sha256"}),
                frozenset(
                    {"path", "old_lines", "new_lines", "expected_sha256"}
                ),
            },
        )
        self.assertTrue(
            all(not variant["additionalProperties"] for variant in replace_variants)
        )


if __name__ == "__main__":
    unittest.main()
