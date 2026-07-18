from __future__ import annotations

import unittest

from sisyphus_harness.compaction import compact_events, transcript_size


class CompactionTests(unittest.TestCase):
    def test_compaction_is_deterministic_and_preserves_recent_events(self) -> None:
        events = [
            {
                "kind": "tool",
                "tool": "read_file",
                "output": {"path": "a.py"},
                "mutated": False,
            },
            {
                "kind": "tool",
                "tool": "replace_text",
                "output": {"path": "a.py"},
                "mutated": True,
            },
            {
                "kind": "verification",
                "run_id": "verify-1",
                "passed": False,
                "criteria": [{"criterion": "tests", "passed": False}],
            },
            {"kind": "tool_error", "error": "stale hash"},
        ]

        first = compact_events(None, events, keep_recent=1)
        second = compact_events(None, events, keep_recent=1)

        self.assertEqual(first, second)
        summary, recent = first
        self.assertEqual(recent, [events[-1]])
        self.assertEqual(summary["files_read"], ["a.py"])
        self.assertEqual(summary["files_mutated"], ["a.py"])
        self.assertEqual(summary["latest_verification"]["run_id"], "verify-1")
        self.assertGreater(transcript_size(events), 0)

    def test_repeated_compaction_accumulates_counts(self) -> None:
        first_summary, _ = compact_events(
            None,
            [
                {"kind": "tool", "tool": "read_file", "output": {"path": "a.py"}},
                {"kind": "tool", "tool": "list_files", "output": {}},
            ],
            keep_recent=1,
        )
        second_summary, recent = compact_events(
            first_summary,
            [
                {"kind": "tool", "tool": "replace_text", "output": {"path": "a.py"}},
                {"kind": "tool", "tool": "read_file", "output": {"path": "b.py"}},
            ],
            keep_recent=1,
        )

        self.assertEqual(second_summary["compacted_event_count"], 2)
        self.assertEqual(second_summary["tool_counts"]["read_file"], 1)
        self.assertEqual(second_summary["tool_counts"]["replace_text"], 1)
        self.assertEqual(recent[0]["output"]["path"], "b.py")


if __name__ == "__main__":
    unittest.main()
