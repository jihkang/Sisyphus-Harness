from __future__ import annotations

from collections import Counter
import json
from typing import Any


def transcript_size(events: list[dict[str, Any]]) -> int:
    return len(json.dumps(events, sort_keys=True, separators=(",", ":")))


def compact_events(
    previous_summary: dict[str, Any] | None,
    events: list[dict[str, Any]],
    *,
    keep_recent: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    compacted = events[:-keep_recent]
    recent = events[-keep_recent:]
    tools = Counter()
    files_read: set[str] = set()
    files_mutated: set[str] = set()
    errors: list[str] = []
    verification: dict[str, Any] | None = None
    if previous_summary is not None:
        tools.update(previous_summary.get("tool_counts", {}))
        files_read.update(previous_summary.get("files_read", []))
        files_mutated.update(previous_summary.get("files_mutated", []))
        errors.extend(previous_summary.get("recent_errors", []))
        verification = previous_summary.get("latest_verification")
    for event in compacted:
        tool = event.get("tool")
        if isinstance(tool, str):
            tools[tool] += 1
        output = event.get("output")
        if isinstance(output, dict):
            path = output.get("path")
            if isinstance(path, str):
                if event.get("mutated"):
                    files_mutated.add(path)
                else:
                    files_read.add(path)
        error = event.get("error")
        if isinstance(error, str):
            errors.append(error)
        if event.get("kind") == "verification":
            verification = {
                "passed": event.get("passed"),
                "run_id": event.get("run_id"),
                "criteria": event.get("criteria"),
            }
    summary = {
        "compacted_event_count": (
            int(previous_summary.get("compacted_event_count", 0))
            if previous_summary
            else 0
        )
        + len(compacted),
        "tool_counts": dict(sorted(tools.items())),
        "files_read": sorted(files_read),
        "files_mutated": sorted(files_mutated),
        "recent_errors": errors[-5:],
        "latest_verification": verification,
    }
    return summary, recent
