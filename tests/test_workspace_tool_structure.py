from __future__ import annotations

from inspect import Parameter, signature
import unittest

from sisyphus_harness import tools
from sisyphus_harness.workspace_tool_contracts import ToolError, ToolOutcome


class WorkspaceToolStructureTests(unittest.TestCase):
    def test_public_facade_reexports_contract_identities(self) -> None:
        self.assertIs(tools.ToolError, ToolError)
        self.assertIs(tools.ToolOutcome, ToolOutcome)

    def test_public_facade_signatures_remain_compatible(self) -> None:
        constructor = signature(tools.WorkspaceTools).parameters
        self.assertEqual(
            list(constructor),
            [
                "workspace",
                "max_file_bytes",
                "max_output_chars",
                "protected_write_paths",
                "allowed_write_paths",
                "deadline",
            ],
        )
        self.assertEqual(constructor["workspace"].kind, Parameter.POSITIONAL_OR_KEYWORD)
        for name in (
            "max_file_bytes",
            "max_output_chars",
            "protected_write_paths",
            "allowed_write_paths",
            "deadline",
        ):
            with self.subTest(parameter=name):
                self.assertEqual(constructor[name].kind, Parameter.KEYWORD_ONLY)
        self.assertEqual(constructor["protected_write_paths"].default, ())
        self.assertIsNone(constructor["allowed_write_paths"].default)
        self.assertIsNone(constructor["deadline"].default)

        execute = signature(tools.WorkspaceTools.execute).parameters
        self.assertEqual(list(execute), ["self", "tool", "arguments"])


if __name__ == "__main__":
    unittest.main()
