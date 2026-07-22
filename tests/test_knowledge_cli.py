from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sisyphus_harness.authority import knowledge_index_path
from sisyphus_harness.cli import main
from sisyphus_harness.contracts.codec import sha256_digest
from sisyphus_harness.contracts.knowledge import (
    CandidateTaskStatus,
    DERIVED_CANDIDATE_AUTHORITY,
    KnowledgeEdge,
    KnowledgeEdgeType,
    KnowledgeNode,
    KnowledgeNodeType,
    KnowledgeProvenance,
)

from .helpers import create_git_repo


def _invoke(arguments: list[str]) -> tuple[int, object, object]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, output, error


class KnowledgeCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository = create_git_repo(
            Path(self.temporary_directory.name) / "repository"
        )

    def test_index_search_dependencies_and_next_step_are_inspectable(self) -> None:
        code, initialized, error = _invoke(
            ["graph-init", "--repo", str(self.repository)]
        )
        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertEqual(initialized["status"], "initialized")
        self.assertEqual(
            initialized["authority"],
            DERIVED_CANDIDATE_AUTHORITY,
        )
        self.assertTrue(knowledge_index_path(self.repository).is_file())

        nodes = (
            _knowledge_node(
                "document-auth",
                "Authentication document",
                "Versioned full text source.",
            ),
            _knowledge_node(
                "chunk-rotation",
                "Rotation body chunk",
                "The body requires refresh token rotation.",
            ),
            _task_node(
                "task-schema",
                CandidateTaskStatus.COMPLETED,
                "Persist the token schema.",
            ),
            _task_node(
                "task-rotate",
                CandidateTaskStatus.READY,
                "Implement refresh token rotation.",
            ),
        )
        for node in nodes:
            code, payload, error = _invoke(
                [
                    "graph-put-node",
                    "--repo",
                    str(self.repository),
                    "--node-json",
                    json.dumps(node.to_dict()),
                ]
            )
            self.assertEqual(code, 0)
            self.assertIsNone(error)
            self.assertTrue(payload["indexed"])

        edges = (
            _edge(
                "chunk-rotation",
                "document-auth",
                KnowledgeEdgeType.DERIVED_FROM,
            ),
            _edge(
                "chunk-rotation",
                "task-rotate",
                KnowledgeEdgeType.SUPPORTS,
            ),
            _edge(
                "task-rotate",
                "task-schema",
                KnowledgeEdgeType.DEPENDS_ON,
            ),
        )
        for edge in edges:
            code, payload, error = _invoke(
                [
                    "graph-put-edge",
                    "--repo",
                    str(self.repository),
                    "--edge-json",
                    json.dumps(edge.to_dict()),
                ]
            )
            self.assertEqual(code, 0)
            self.assertIsNone(error)
            self.assertTrue(payload["indexed"])

        code, search, error = _invoke(
            [
                "graph-search",
                "--repo",
                str(self.repository),
                "--anchor-id",
                "document-auth",
                "--query",
                "refresh rotation",
                "--max-depth",
                "2",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertEqual(search["authority"], DERIVED_CANDIDATE_AUTHORITY)
        self.assertIn(
            "task-rotate",
            {hit["node"]["node_id"] for hit in search["hits"]},
        )
        revision = search["index_revision_digest"]

        code, dependency, error = _invoke(
            [
                "graph-dependencies",
                "--repo",
                str(self.repository),
                "--task-id",
                "task-rotate",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertTrue(dependency["inspection"]["all_satisfied"])
        self.assertEqual(
            dependency["inspection"]["dependencies"][0]["node_id"],
            "task-schema",
        )
        self.assertEqual(dependency["index_revision_digest"], revision)

        code, context, error = _invoke(
            [
                "graph-next",
                "--repo",
                str(self.repository),
                "--anchor-id",
                "document-auth",
                "--query",
                "refresh rotation",
                "--max-depth",
                "3",
                "--dependency-max-depth",
                "2",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertEqual(context["authority"], DERIVED_CANDIDATE_AUTHORITY)
        self.assertEqual(context["candidate_max_depth"], 3)
        self.assertEqual(context["dependency_max_depth"], 2)
        self.assertEqual(context["index_revision_digest"], revision)
        self.assertEqual(context["candidates"][0]["task"]["node_id"], "task-rotate")
        self.assertTrue(context["candidates"][0]["eligible"])
        self.assertEqual(context["candidates"][0]["rank"], 1)

    def test_queries_fail_closed_before_initialization_and_above_depth_three(self) -> None:
        code, output, error = _invoke(
            [
                "graph-search",
                "--repo",
                str(self.repository),
                "--anchor-id",
                "missing",
                "--query",
                "evidence",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIsNone(output)
        self.assertIn("graph-init", error["error"])

        _invoke(["graph-init", "--repo", str(self.repository)])
        code, output, error = _invoke(
            [
                "graph-search",
                "--repo",
                str(self.repository),
                "--anchor-id",
                "missing",
                "--query",
                "evidence",
                "--max-depth",
                "4",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIsNone(output)
        self.assertIn("between 0 and 3", error["error"])

    def test_existing_empty_index_file_fails_as_structured_cli_error(self) -> None:
        path = knowledge_index_path(self.repository)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

        code, output, error = _invoke(
            [
                "graph-search",
                "--repo",
                str(self.repository),
                "--anchor-id",
                "anchor",
                "--query",
                "evidence",
            ]
        )

        self.assertEqual(code, 2)
        self.assertIsNone(output)
        self.assertIn("integrity validation", error["error"])

    def test_graph_initialization_database_failure_is_structured(self) -> None:
        with patch(
            "sisyphus_harness.interfaces.cli.handlers.knowledge."
            "SQLiteKnowledgeIndex.initialize",
            side_effect=sqlite3.DatabaseError("invalid database"),
        ):
            code, output, error = _invoke(
                ["graph-init", "--repo", str(self.repository)]
            )

        self.assertEqual(code, 2)
        self.assertIsNone(output)
        self.assertEqual(error["error_type"], "DatabaseError")
        self.assertIn("invalid database", error["error"])


def _provenance(source_id: str) -> KnowledgeProvenance:
    return KnowledgeProvenance(
        source_id=source_id,
        source_kind="cli-fixture",
        source_digest=sha256_digest({"source_id": source_id}),
        producer="tests.test_knowledge_cli",
    )


def _knowledge_node(node_id: str, title: str, content: str) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=node_id,
        node_type=KnowledgeNodeType.KNOWLEDGE,
        title=title,
        content=content,
        provenance=_provenance(node_id),
        metadata={"section_kind": "body", "chunk_ordinal": "1"},
    )


def _task_node(
    node_id: str,
    status: CandidateTaskStatus,
    content: str,
) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=node_id,
        node_type=KnowledgeNodeType.TASK,
        title=node_id,
        content=content,
        provenance=_provenance(node_id),
        task_status=status,
    )


def _edge(
    source_node_id: str,
    target_node_id: str,
    edge_type: KnowledgeEdgeType,
) -> KnowledgeEdge:
    return KnowledgeEdge(
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        edge_type=edge_type,
        provenance=_provenance(
            f"{source_node_id}-{edge_type.value}-{target_node_id}"
        ),
    )


if __name__ == "__main__":
    unittest.main()
