from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import random
from threading import Barrier
import tempfile
import unittest

from sisyphus_harness.contracts.codec import sha256_digest
from sisyphus_harness.contracts.knowledge import (
    CandidateTaskStatus,
    DERIVED_CANDIDATE_AUTHORITY,
    KnowledgeEdge,
    KnowledgeEdgeType,
    KnowledgeNode,
    KnowledgeNodeType,
    KnowledgeProvenance,
    normalized_terms,
)
from sisyphus_harness.infra.knowledge_index import (
    KNOWLEDGE_INDEX_SCHEMA_VERSION,
    KnowledgeIndexConflict,
    KnowledgeIndexError,
    SQLiteKnowledgeIndex,
)
from sisyphus_harness.knowledge_graph import KnowledgeGraph, KnowledgeGraphError


_DOCUMENT_DIGEST = "sha256:" + "a" * 64


class KnowledgeGraphTests(unittest.TestCase):
    def test_contract_digests_are_canonical_and_authority_is_candidate_only(self) -> None:
        first = _knowledge_node(
            "chunk-body",
            "Body result",
            "The full body requires refresh token rotation.",
            metadata={"section_kind": "body", "chunk_ordinal": "1"},
        )
        second = _knowledge_node(
            "chunk-body",
            "Body result",
            "The full body requires refresh token rotation.",
            metadata={"chunk_ordinal": "1", "section_kind": "body"},
        )

        self.assertEqual(first.metadata_digest, second.metadata_digest)
        self.assertEqual(first.node_digest, second.node_digest)
        self.assertEqual(KnowledgeNode.from_dict(first.to_dict()), first)
        self.assertEqual(first.authority, DERIVED_CANDIDATE_AUTHORITY)
        with self.assertRaisesRegex(ValueError, "derived candidates only"):
            _knowledge_node(
                "invalid-authority",
                "Invalid",
                "This must fail closed.",
                authority="task_authority",
            )

    def test_seeded_depth_three_graphrag_context_is_deterministic(self) -> None:
        """A body chunk, not an abstract summary, grounds the depth-three path.

        Exact evidence spans are intentionally not admitted by this foundation. Page and
        offsets remain provenance metadata until a separate evidence-admission contract
        validates them.
        """

        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "knowledge-index.sqlite3")
            index.initialize()
            graph = KnowledgeGraph(index)
            nodes, edges = _seeded_depth_three_dataset(seed=20260720)
            for node in nodes:
                graph.add_node(node)
            for edge in edges:
                graph.add_edge(edge)

            abstract_hits = graph.search(
                "document-auth-paper",
                "cleanup sufficient",
                max_depth=1,
            )
            self.assertEqual(abstract_hits[0].node.node_id, "chunk-abstract")
            self.assertEqual(
                abstract_hits[0].node.metadata["section_kind"],
                "abstract",
            )

            shallow = graph.search(
                "document-auth-paper",
                "refresh rotation implementation",
                max_depth=2,
            )
            deep = graph.search(
                "document-auth-paper",
                "refresh rotation implementation",
                max_depth=3,
            )
            self.assertNotIn("task-rotate", {hit.node.node_id for hit in shallow})
            ready_hit = next(hit for hit in deep if hit.node.node_id == "task-rotate")
            self.assertEqual(ready_hit.depth, 3)
            self.assertEqual(
                ready_hit.path_node_ids,
                (
                    "document-auth-paper",
                    "chunk-body-results",
                    "entity-refresh-token",
                    "task-rotate",
                ),
            )
            self.assertEqual(
                tuple(step.edge_type for step in ready_hit.path_steps),
                (
                    KnowledgeEdgeType.DERIVED_FROM,
                    KnowledgeEdgeType.MENTIONS,
                    KnowledgeEdgeType.SUPPORTS,
                ),
            )
            self.assertFalse(ready_hit.path_steps[0].traversed_forward)
            body = index.get_node("chunk-body-results")
            assert body is not None
            self.assertEqual(body.metadata["section_kind"], "body")
            self.assertEqual(body.metadata["section_path"], "Results/Token rotation")
            self.assertEqual(body.metadata["chunk_ordinal"], "7")
            self.assertEqual(body.metadata["document_digest"], _DOCUMENT_DIGEST)
            self.assertTrue(body.metadata["content_digest"].startswith("sha256:"))

            dependencies = graph.inspect_dependencies("task-rotate")
            self.assertTrue(dependencies.all_satisfied)
            self.assertEqual(
                tuple(
                    (item.node_id, item.task_status, item.satisfied)
                    for item in dependencies.dependencies
                ),
                (("task-schema", CandidateTaskStatus.COMPLETED, True),),
            )

            first = graph.next_step_context(
                "document-auth-paper",
                "refresh rotation shortcut",
                max_depth=3,
            )
            second = graph.next_step_context(
                "document-auth-paper",
                "refresh rotation shortcut",
                max_depth=3,
            )
            self.assertEqual(first.to_dict(), second.to_dict())
            self.assertEqual(first.authority, DERIVED_CANDIDATE_AUTHORITY)
            self.assertEqual(first.candidate_max_depth, 3)
            self.assertEqual(first.dependency_max_depth, 3)
            self.assertEqual(first.candidates[0].task.node_id, "task-rotate")
            self.assertEqual(first.candidates[0].rank, 1)
            self.assertTrue(first.candidates[0].eligible)
            self.assertEqual(first.candidates[0].depth, 3)
            self.assertGreater(first.candidates[0].lexical_score, 0)
            self.assertGreater(first.candidates[0].graph_score, 0)
            self.assertEqual(
                first.candidates[0].total_score,
                first.candidates[0].lexical_score * 100
                + first.candidates[0].graph_score,
            )
            blocked = next(
                candidate
                for candidate in first.candidates
                if candidate.task.node_id == "task-shortcut"
            )
            self.assertGreater(
                blocked.total_score,
                first.candidates[0].total_score,
            )
            self.assertFalse(blocked.eligible)
            self.assertIn("task status is blocked", blocked.unmet_dependency_reasons)
            self.assertIn(
                "dependency task-rotate is ready",
                blocked.unmet_dependency_reasons,
            )
            self.assertIn("authority=derived_candidate_only", blocked.explanation)

            reopened = KnowledgeGraph(
                SQLiteKnowledgeIndex(Path(directory) / "knowledge-index.sqlite3")
            ).next_step_context(
                "document-auth-paper",
                "refresh rotation shortcut",
                max_depth=3,
            )
            self.assertEqual(first.to_dict(), reopened.to_dict())

    def test_index_is_immutable_and_dependency_cycles_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "knowledge-index.sqlite3")
            index.initialize()
            graph = KnowledgeGraph(index)
            first = _task_node("task-a", CandidateTaskStatus.READY, "First task")
            second = _task_node("task-b", CandidateTaskStatus.BLOCKED, "Second task")
            self.assertTrue(graph.add_node(first))
            self.assertFalse(graph.add_node(first))
            with self.assertRaises(KnowledgeIndexConflict):
                graph.add_node(
                    _task_node(
                        "task-a",
                        CandidateTaskStatus.READY,
                        "Changed immutable task",
                    )
                )
            graph.add_node(second)
            graph.add_edge(
                _edge("task-a", "task-b", KnowledgeEdgeType.DEPENDS_ON)
            )
            with self.assertRaisesRegex(KnowledgeGraphError, "cycle"):
                graph.add_edge(
                    _edge("task-b", "task-a", KnowledgeEdgeType.DEPENDS_ON)
                )
            with self.assertRaisesRegex(ValueError, "between 0 and 3"):
                graph.search("task-a", "task", max_depth=4)

    def test_concurrent_opposite_dependency_writes_admit_exactly_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "knowledge-index.sqlite3"
            index = SQLiteKnowledgeIndex(path)
            index.initialize()
            graph = KnowledgeGraph(index)
            graph.add_node(
                _task_node("task-a", CandidateTaskStatus.COMPLETED, "Task A")
            )
            graph.add_node(
                _task_node("task-b", CandidateTaskStatus.COMPLETED, "Task B")
            )
            barrier = Barrier(2)

            def add(edge: KnowledgeEdge) -> bool:
                barrier.wait()
                return KnowledgeGraph(SQLiteKnowledgeIndex(path)).add_edge(edge)

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = (
                    executor.submit(
                        add,
                        _edge("task-a", "task-b", KnowledgeEdgeType.DEPENDS_ON),
                    ),
                    executor.submit(
                        add,
                        _edge("task-b", "task-a", KnowledgeEdgeType.DEPENDS_ON),
                    ),
                )
                successes: list[bool] = []
                failures: list[BaseException] = []
                for future in futures:
                    try:
                        successes.append(future.result())
                    except BaseException as exc:  # surfaced below with exact type
                        failures.append(exc)

            self.assertEqual(successes, [True])
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], KnowledgeGraphError)
            self.assertIn("cycle", str(failures[0]))
            dependency_edges = tuple(
                edge
                for edge in index.edges_for("task-a")
                if edge.edge_type is KnowledgeEdgeType.DEPENDS_ON
            )
            self.assertEqual(len(dependency_edges), 1)
            self.assertTrue(index.revision_digest().startswith("sha256:"))

    def test_next_step_exposes_independent_candidate_and_dependency_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "knowledge-index.sqlite3")
            index.initialize()
            graph = KnowledgeGraph(index)
            graph.add_node(_knowledge_node("anchor", "Anchor", "Candidate work"))
            for ordinal, status in (
                (0, CandidateTaskStatus.READY),
                (1, CandidateTaskStatus.COMPLETED),
                (2, CandidateTaskStatus.COMPLETED),
                (3, CandidateTaskStatus.COMPLETED),
            ):
                graph.add_node(
                    _task_node(
                        f"task-{ordinal}",
                        status,
                        f"Candidate work dependency {ordinal}",
                    )
                )
            graph.add_edge(
                _edge("anchor", "task-0", KnowledgeEdgeType.SUPPORTS)
            )
            for ordinal in range(3):
                graph.add_edge(
                    _edge(
                        f"task-{ordinal}",
                        f"task-{ordinal + 1}",
                        KnowledgeEdgeType.DEPENDS_ON,
                    )
                )

            shallow = graph.next_step_context(
                "anchor",
                "candidate work",
                max_depth=1,
                dependency_max_depth=1,
            )
            candidate = shallow.candidates[0]
            self.assertEqual(shallow.candidate_max_depth, 1)
            self.assertEqual(shallow.dependency_max_depth, 1)
            self.assertEqual(len(candidate.dependency_inspection.dependencies), 1)
            self.assertTrue(candidate.dependency_inspection.truncated)
            self.assertFalse(candidate.eligible)

            deep_dependencies = graph.next_step_context(
                "anchor",
                "candidate work",
                max_depth=1,
                dependency_max_depth=3,
            )
            candidate = deep_dependencies.candidates[0]
            self.assertEqual(len(candidate.dependency_inspection.dependencies), 3)
            self.assertFalse(candidate.dependency_inspection.truncated)
            self.assertTrue(candidate.eligible)

    def test_contract_and_index_inputs_fail_closed(self) -> None:
        provenance = _provenance("strict-source")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            KnowledgeProvenance(
                "source",
                "fixture",
                "not-a-digest",
                "producer",
            )
        with self.assertRaisesRegex(ValueError, "requires a candidate task status"):
            KnowledgeNode(
                "task-no-status",
                KnowledgeNodeType.TASK,
                "Task",
                "Task body",
                provenance,
            )
        with self.assertRaisesRegex(ValueError, "cannot have a task status"):
            KnowledgeNode(
                "knowledge-with-status",
                KnowledgeNodeType.KNOWLEDGE,
                "Knowledge",
                "Knowledge body",
                provenance,
                CandidateTaskStatus.READY,
            )
        with self.assertRaisesRegex(ValueError, "invalid key"):
            _knowledge_node(
                "bad-metadata",
                "Bad metadata",
                "Content",
                metadata={" bad": "value"},
            )
        with self.assertRaisesRegex(ValueError, "self-edge"):
            _edge("same", "same", KnowledgeEdgeType.RELATES_TO)
        with self.assertRaisesRegex(TypeError, "term source"):
            normalized_terms(1)  # type: ignore[arg-type]

        node = _knowledge_node("strict-node", "Strict", "Strict body")
        tampered_node = node.to_dict()
        tampered_node["node_digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match content"):
            KnowledgeNode.from_dict(tampered_node)
        edge = _edge("strict-node", "other-node", KnowledgeEdgeType.RELATES_TO)
        self.assertEqual(KnowledgeEdge.from_dict(edge.to_dict()), edge)
        tampered_edge = edge.to_dict()
        tampered_edge["edge_digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match content"):
            KnowledgeEdge.from_dict(tampered_edge)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "knowledge-index.sqlite3"
            index = SQLiteKnowledgeIndex(path)
            index.initialize()
            index.initialize()
            graph = KnowledgeGraph(index)
            graph.add_node(node)
            other = _knowledge_node("other-node", "Other", "Other body")
            graph.add_node(other)
            self.assertEqual(
                tuple(item.node_id for item in index.list_nodes()),
                ("other-node", "strict-node"),
            )
            self.assertIsNone(index.get_node("missing"))
            self.assertEqual(index.term_scores((), ("strict-node",)), {})
            with self.assertRaisesRegex(ValueError, "normalized"):
                index.term_scores(("Not-Normalized",), ("strict-node",))

            stored_edge = _edge(
                "strict-node",
                "other-node",
                KnowledgeEdgeType.RELATES_TO,
            )
            self.assertTrue(graph.add_edge(stored_edge))
            self.assertFalse(graph.add_edge(stored_edge))
            with self.assertRaises(KnowledgeIndexConflict):
                graph.add_edge(
                    KnowledgeEdge(
                        "strict-node",
                        "other-node",
                        KnowledgeEdgeType.RELATES_TO,
                        _provenance("changed-edge"),
                        metadata={"changed": "true"},
                    )
                )
            with self.assertRaisesRegex(KnowledgeIndexError, "endpoints"):
                index.put_edge(
                    _edge(
                        "strict-node",
                        "missing-node",
                        KnowledgeEdgeType.SUPPORTS,
                    )
                )
            with self.assertRaisesRegex(KnowledgeGraphError, "does not exist"):
                graph.search("missing", "query")
            with self.assertRaisesRegex(ValueError, "at least one term"):
                graph.search("strict-node", "---")
            with self.assertRaisesRegex(KnowledgeGraphError, "requires a task"):
                graph.inspect_dependencies("strict-node")
            with self.assertRaisesRegex(ValueError, "between 1 and 1000"):
                graph.search("strict-node", "strict", limit=0)

            with index.transaction() as connection:
                connection.execute(
                    """
                    UPDATE knowledge_index_metadata
                    SET value = ?
                    WHERE key = 'schema_version'
                    """,
                    (str(KNOWLEDGE_INDEX_SCHEMA_VERSION + 1),),
                )
            with self.assertRaisesRegex(KnowledgeIndexError, "newer than supported"):
                index.initialize()

    def test_dependency_inspection_reports_depth_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "knowledge-index.sqlite3")
            index.initialize()
            graph = KnowledgeGraph(index)
            tasks = [
                _task_node(
                    f"chain-{ordinal}",
                    CandidateTaskStatus.READY
                    if ordinal == 0
                    else CandidateTaskStatus.COMPLETED,
                    f"Dependency chain task {ordinal}",
                )
                for ordinal in range(5)
            ]
            for task in tasks:
                graph.add_node(task)
            for ordinal in range(4):
                graph.add_edge(
                    _edge(
                        f"chain-{ordinal}",
                        f"chain-{ordinal + 1}",
                        KnowledgeEdgeType.DEPENDS_ON,
                    )
                )

            inspection = graph.inspect_dependencies("chain-0", max_depth=3)

            self.assertTrue(inspection.truncated)
            self.assertFalse(inspection.all_satisfied)
            self.assertEqual(len(inspection.dependencies), 3)
            self.assertIn(
                "dependency traversal truncated at depth 3",
                inspection.unmet_reasons,
            )


def _seeded_depth_three_dataset(
    *, seed: int
) -> tuple[list[KnowledgeNode], list[KnowledgeEdge]]:
    abstract_text = (
        "The abstract suggests cache cleanup is sufficient for session safety."
    )
    body_text = (
        "The full body results contradict the shortcut: refresh token rotation is "
        "required for the implementation."
    )
    nodes = [
        _knowledge_node(
            "document-auth-paper",
            "Authentication paper",
            "A versioned full-text paper with abstract and body sections.",
            metadata={
                "document_id": "auth-paper",
                "document_digest": _DOCUMENT_DIGEST,
                "source_digest": _DOCUMENT_DIGEST,
                "document_version": "1",
                "full_text_available": "true",
            },
        ),
        _knowledge_node(
            "chunk-abstract",
            "Abstract summary",
            abstract_text,
            metadata=_chunk_metadata(
                section_kind="abstract",
                section_path="Abstract",
                chunk_ordinal=0,
                start=0,
                end=len(abstract_text),
                content=abstract_text,
            ),
        ),
        _knowledge_node(
            "chunk-body-results",
            "Body result on rotation",
            body_text,
            metadata=_chunk_metadata(
                section_kind="body",
                section_path="Results/Token rotation",
                chunk_ordinal=7,
                start=812,
                end=812 + len(body_text),
                content=body_text,
            ),
        ),
        _knowledge_node(
            "entity-refresh-token",
            "Refresh token entity",
            "Canonical entity candidate mentioned by the full body result.",
            metadata={
                "entity_type": "security_control",
                "ontology_version": "auth-v1",
                "source_chunk_id": "chunk-body-results",
                "source_digest": _DOCUMENT_DIGEST,
            },
        ),
        _task_node(
            "task-schema",
            CandidateTaskStatus.COMPLETED,
            "Persist the refresh token schema migration.",
        ),
        _task_node(
            "task-rotate",
            CandidateTaskStatus.READY,
            "Implement refresh token rotation using the body evidence.",
        ),
        _task_node(
            "task-shortcut",
            CandidateTaskStatus.BLOCKED,
            "Implement refresh rotation shortcut shortcut shortcut.",
        ),
    ]
    for ordinal in range(3):
        token = f"noise-{random.Random(seed + ordinal).randrange(1000, 9999)}"
        nodes.append(
            _knowledge_node(
                f"chunk-distractor-{ordinal}",
                f"Distractor {ordinal}",
                f"Unrelated appendix observation {token}.",
                metadata=_chunk_metadata(
                    section_kind="appendix",
                    section_path=f"Appendix/{ordinal}",
                    chunk_ordinal=20 + ordinal,
                    start=2000 + ordinal * 100,
                    end=2050 + ordinal * 100,
                    content=token,
                ),
            )
        )

    edges = [
        _edge(
            "chunk-abstract",
            "document-auth-paper",
            KnowledgeEdgeType.DERIVED_FROM,
        ),
        _edge(
            "chunk-body-results",
            "document-auth-paper",
            KnowledgeEdgeType.DERIVED_FROM,
        ),
        _edge(
            "chunk-body-results",
            "entity-refresh-token",
            KnowledgeEdgeType.MENTIONS,
        ),
        _edge(
            "entity-refresh-token",
            "task-rotate",
            KnowledgeEdgeType.SUPPORTS,
        ),
        _edge(
            "entity-refresh-token",
            "task-shortcut",
            KnowledgeEdgeType.SUPPORTS,
        ),
        _edge("task-rotate", "task-schema", KnowledgeEdgeType.DEPENDS_ON),
        _edge("task-shortcut", "task-rotate", KnowledgeEdgeType.DEPENDS_ON),
    ]
    edges.extend(
        _edge(
            f"chunk-distractor-{ordinal}",
            "document-auth-paper",
            KnowledgeEdgeType.DERIVED_FROM,
        )
        for ordinal in range(3)
    )

    generator = random.Random(seed)
    generator.shuffle(nodes)
    generator.shuffle(edges)
    return nodes, edges


def _chunk_metadata(
    *,
    section_kind: str,
    section_path: str,
    chunk_ordinal: int,
    start: int,
    end: int,
    content: str,
) -> dict[str, str]:
    return {
        "document_id": "auth-paper",
        "document_digest": _DOCUMENT_DIGEST,
        "source_digest": _DOCUMENT_DIGEST,
        "parser_id": "fixture-structural-parser",
        "parser_version": "1",
        "section_kind": section_kind,
        "section_path": section_path,
        "chunk_ordinal": str(chunk_ordinal),
        "canonical_char_start": str(start),
        "canonical_char_end": str(end),
        "content_digest": sha256_digest({"text": content}),
    }


def _knowledge_node(
    node_id: str,
    title: str,
    content: str,
    *,
    metadata: dict[str, str] | None = None,
    authority: str = DERIVED_CANDIDATE_AUTHORITY,
) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=node_id,
        node_type=KnowledgeNodeType.KNOWLEDGE,
        title=title,
        content=content,
        provenance=_provenance(node_id),
        metadata=metadata or {},
        authority=authority,
    )


def _task_node(
    node_id: str,
    status: CandidateTaskStatus,
    content: str,
) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=node_id,
        node_type=KnowledgeNodeType.TASK,
        title=node_id.replace("-", " ").title(),
        content=content,
        provenance=_provenance(node_id),
        task_status=status,
        metadata={
            "candidate_status_source": "seeded-fixture",
            "evidence_authority": DERIVED_CANDIDATE_AUTHORITY,
        },
    )


def _edge(
    source_id: str,
    target_id: str,
    edge_type: KnowledgeEdgeType,
) -> KnowledgeEdge:
    return KnowledgeEdge(
        source_node_id=source_id,
        target_node_id=target_id,
        edge_type=edge_type,
        provenance=_provenance(f"{source_id}-{edge_type.value}-{target_id}"),
        metadata={"extraction_run": "seeded-depth-three-v1"},
    )


def _provenance(source_id: str) -> KnowledgeProvenance:
    return KnowledgeProvenance(
        source_id=source_id,
        source_kind="seeded-fixture",
        source_digest=sha256_digest({"source_id": source_id}),
        producer="tests.test_knowledge_graph",
    )


if __name__ == "__main__":
    unittest.main()
