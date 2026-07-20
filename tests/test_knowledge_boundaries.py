from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator
import unittest
from unittest.mock import patch

from sisyphus_harness.contracts.codec import canonical_json_bytes, sha256_digest
from sisyphus_harness.contracts.knowledge import (
    CandidateTaskStatus,
    DependencyInspection,
    DependencyState,
    GraphPathStep,
    KnowledgeEdge,
    KnowledgeEdgeType,
    KnowledgeNode,
    KnowledgeNodeType,
    KnowledgeProvenance,
    NextStepCandidate,
    knowledge_graph_score,
    next_step_candidate_explanation,
    validate_max_depth,
)
from sisyphus_harness.infra.knowledge_index import (
    KnowledgeIndexError,
    SQLiteKnowledgeIndex,
)
from sisyphus_harness.knowledge_graph import KnowledgeGraph, KnowledgeGraphError


class _EscalatingString(str):
    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = str.__hash__


class _AdversarialInt(int):
    def __le__(self, other: object) -> bool:
        return True


@dataclass(frozen=True)
class _FakeProvenance:
    source_id: str = "source:fake"
    source_kind: str = "test"
    source_digest: str = "sha256:" + "a" * 64
    producer: str = "fake-producer"
    revision: str = "1"
    schema_version: str = "sisyphus_harness.knowledge_provenance.v1"

    @property
    def provenance_digest(self) -> str:
        return "sha256:" + "f" * 64


class _KnowledgeNodeSubclass(KnowledgeNode):
    pass


class _KnowledgeEdgeSubclass(KnowledgeEdge):
    pass


class KnowledgeContractBoundaryTests(unittest.TestCase):
    def test_provenance_and_node_boundaries_reject_ambiguous_inputs(self) -> None:
        valid = _provenance("source")
        for overrides, message in (
            ({"producer": ""}, "producer"),
            ({"revision": ""}, "revision"),
            ({"schema_version": "future"}, "schema"),
            ({"producer": _EscalatingString("producer")}, "producer"),
        ):
            values = {
                "source_id": valid.source_id,
                "source_kind": valid.source_kind,
                "source_digest": valid.source_digest,
                "producer": valid.producer,
                "revision": valid.revision,
                "schema_version": valid.schema_version,
            }
            values.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, message):
                    KnowledgeProvenance(**values)

        tampered = valid.to_dict()
        tampered["provenance_digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            KnowledgeProvenance.from_dict(tampered)

        base = {
            "node_id": "node",
            "node_type": KnowledgeNodeType.KNOWLEDGE,
            "title": "Title",
            "content": "Content",
            "provenance": valid,
        }
        for overrides, message in (
            ({"node_id": "bad id"}, "node ID"),
            ({"node_type": "unsupported"}, "node type"),
            ({"title": ""}, "title"),
            ({"content": ""}, "content"),
            ({"content": "x" * 1_000_001}, "supported size"),
            ({"metadata": {"key": 1}}, "invalid value"),
            ({"metadata": {"key": _EscalatingString("value")}}, "invalid value"),
            ({"provenance": _FakeProvenance()}, "exact KnowledgeProvenance"),
            ({"authority": _EscalatingString("task_authority")}, "derived candidates"),
            ({"schema_version": "future"}, "schema"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, message):
                    KnowledgeNode(**(base | overrides))

        node = KnowledgeNode(**base)
        payload = node.to_dict()
        payload["metadata"] = []
        with self.assertRaisesRegex(ValueError, "metadata must be an object"):
            KnowledgeNode.from_dict(payload)
        payload = node.to_dict()
        payload["task_status"] = 1
        with self.assertRaisesRegex(ValueError, "status must be"):
            KnowledgeNode.from_dict(payload)

    def test_edge_and_depth_boundaries_fail_closed(self) -> None:
        provenance = _provenance("edge")
        base = {
            "source_node_id": "source",
            "target_node_id": "target",
            "edge_type": KnowledgeEdgeType.SUPPORTS,
            "provenance": provenance,
        }
        for overrides, message in (
            ({"edge_type": "unsupported"}, "edge type"),
            ({"provenance": _FakeProvenance()}, "exact KnowledgeProvenance"),
            ({"authority": "authority"}, "derived candidates"),
            ({"authority": _EscalatingString("task_authority")}, "derived candidates"),
            ({"schema_version": "future"}, "schema"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, message):
                    KnowledgeEdge(**(base | overrides))
        edge = KnowledgeEdge(**base)
        payload = edge.to_dict()
        payload["metadata"] = []
        with self.assertRaisesRegex(ValueError, "metadata must be an object"):
            KnowledgeEdge.from_dict(payload)

        for value in (True, "3", -1, 4, _AdversarialInt(3)):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "between 0 and 3"):
                    validate_max_depth(value)  # type: ignore[arg-type]

    def test_public_graph_projections_reject_forged_decision_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
            index.initialize()
            graph = KnowledgeGraph(index)
            anchor = _node("anchor")
            task = _node("task", status=CandidateTaskStatus.READY)
            dependency = _node("dependency", status=CandidateTaskStatus.COMPLETED)
            for node in (anchor, task, dependency):
                graph.add_node(node)
            graph.add_edge(
                KnowledgeEdge(
                    "anchor",
                    "task",
                    KnowledgeEdgeType.SUPPORTS,
                    _provenance("projection-path"),
                )
            )
            graph.add_edge(
                KnowledgeEdge(
                    "task",
                    "dependency",
                    KnowledgeEdgeType.DEPENDS_ON,
                    _provenance("projection-dependency"),
                )
            )
            hit = next(
                item
                for item in graph.search("anchor", "task", max_depth=2)
                if item.node.node_id == "task"
            )
            context = graph.next_step_context(
                "anchor",
                "task",
                max_depth=2,
                dependency_max_depth=2,
            )
            candidate = context.candidates[0]
            inspection = candidate.dependency_inspection
            state = inspection.dependencies[0]

            invalid_hit_mutations = (
                {"authority": "task_authority"},
                {"index_revision_digest": "not-a-digest"},
                {"lexical_score": hit.lexical_score + 1},
                {"path_node_ids": list(hit.path_node_ids)},
                {"explanation": "forged explanation"},
            )
            for mutation in invalid_hit_mutations:
                with self.subTest(hit=mutation), self.assertRaises(ValueError):
                    replace(hit, **mutation)

            with self.assertRaisesRegex(ValueError, "boolean"):
                replace(hit.path_steps[0], traversed_forward=1)
            with self.assertRaisesRegex(ValueError, "satisfaction"):
                replace(state, satisfied=False)
            for mutation in (
                {"dependencies": list(inspection.dependencies)},
                {"all_satisfied": False},
                {"max_depth": 0},
                {"index_revision_digest": "not-a-digest"},
            ):
                with self.subTest(inspection=mutation), self.assertRaises(ValueError):
                    replace(inspection, **mutation)

            for mutation in (
                {"rank": 0},
                {"eligible": False},
                {"unmet_dependency_reasons": ("forged",)},
                {"task": anchor},
                {"total_score": candidate.total_score + 1},
            ):
                with self.subTest(candidate=mutation), self.assertRaises(ValueError):
                    replace(candidate, **mutation)

            for mutation in (
                {"candidates": list(context.candidates)},
                {"candidate_max_depth": 0},
                {"dependency_max_depth": 0},
                {"authority": "task_authority"},
                {"authority": _EscalatingString("task_authority")},
                {"schema_version": "sisyphus_harness.next_step_context.v1"},
                {"index_revision_digest": "not-a-digest"},
            ):
                with self.subTest(context=mutation), self.assertRaises(ValueError):
                    replace(context, **mutation)

    def test_blocked_candidate_accepts_every_bounded_dependency_reason(self) -> None:
        revision = "sha256:" + "0" * 64
        dependencies = tuple(
            DependencyState(
                node_id=f"dependency-{ordinal:04d}",
                task_status=CandidateTaskStatus.READY,
                depth=1,
                path_node_ids=("task", f"dependency-{ordinal:04d}"),
                path_steps=(
                    GraphPathStep(
                        source_node_id="task",
                        target_node_id=f"dependency-{ordinal:04d}",
                        edge_type=KnowledgeEdgeType.DEPENDS_ON,
                        edge_digest=sha256_digest({"dependency": ordinal}),
                        traversed_forward=True,
                    ),
                ),
                satisfied=False,
            )
            for ordinal in range(1000)
        )
        inspection_reasons = tuple(
            f"dependency {dependency.node_id} is ready"
            for dependency in dependencies
        ) + ("dependency traversal truncated at depth 1",)
        inspection = DependencyInspection(
            task_id="task",
            max_depth=1,
            index_revision_digest=revision,
            dependencies=dependencies,
            all_satisfied=False,
            truncated=True,
            unmet_reasons=inspection_reasons,
        )
        task = _node("task", status=CandidateTaskStatus.BLOCKED)
        path_step = GraphPathStep(
            source_node_id="anchor",
            target_node_id="task",
            edge_type=KnowledgeEdgeType.SUPPORTS,
            edge_digest=sha256_digest({"path": "anchor-task"}),
            traversed_forward=True,
        )
        reasons = ("task status is blocked",) + inspection.unmet_reasons
        graph_score = knowledge_graph_score(1)

        candidate = NextStepCandidate(
            rank=1,
            anchor_id="anchor",
            task=task,
            depth=1,
            path_node_ids=("anchor", "task"),
            path_steps=(path_step,),
            matched_terms=(),
            lexical_score=0,
            graph_score=graph_score,
            total_score=graph_score,
            dependency_inspection=inspection,
            eligible=False,
            unmet_dependency_reasons=reasons,
            explanation=next_step_candidate_explanation(
                task=task,
                depth=1,
                path_steps=(path_step,),
                matched_terms=(),
                lexical_score=0,
                graph_score=graph_score,
                total_score=graph_score,
                eligible=False,
                unmet_reasons=reasons,
            ),
        )

        self.assertEqual(len(candidate.unmet_dependency_reasons), 1002)


class KnowledgeRuntimeBoundaryTests(unittest.TestCase):
    def test_graph_and_index_reject_model_subclasses(self) -> None:
        provenance = _provenance("subclass")
        node = _KnowledgeNodeSubclass(
            node_id="subclass-node",
            node_type=KnowledgeNodeType.KNOWLEDGE,
            title="Subclass",
            content="Subclass content",
            provenance=provenance,
        )
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
            index.initialize()
            with self.assertRaisesRegex(TypeError, "exact KnowledgeNode"):
                index.put_node(node)
            with self.assertRaisesRegex(TypeError, "exact KnowledgeNode"):
                KnowledgeGraph(index).add_node(node)
            self.assertEqual(index.list_nodes(), ())

            for valid in (_node("source"), _node("target")):
                index.put_node(valid)
            edge = _KnowledgeEdgeSubclass(
                "source",
                "target",
                KnowledgeEdgeType.SUPPORTS,
                provenance,
            )
            with self.assertRaisesRegex(TypeError, "exact KnowledgeEdge"):
                index.put_edge(edge)
            with self.assertRaisesRegex(TypeError, "exact KnowledgeEdge"):
                KnowledgeGraph(index).add_edge(edge)
            self.assertEqual(index.edges_for("source"), ())

    def test_write_postflight_rolls_back_trigger_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
            index.initialize()
            stable_revision = index.revision_digest()
            with index.transaction() as connection:
                connection.execute(
                    """
                    CREATE TRIGGER corrupt_new_term
                    AFTER INSERT ON knowledge_terms
                    BEGIN
                        UPDATE knowledge_terms
                        SET weight = weight + 1
                        WHERE node_id = NEW.node_id AND term = NEW.term;
                    END
                    """
                )

            with self.assertRaisesRegex(KnowledgeIndexError, "term index"):
                index.put_node(_node("triggered-node"))

            self.assertEqual(index.list_nodes(), ())
            self.assertEqual(index.revision_digest(), stable_revision)

        for edge_type in (
            KnowledgeEdgeType.SUPPORTS,
            KnowledgeEdgeType.DEPENDS_ON,
        ):
            with self.subTest(edge_type=edge_type), tempfile.TemporaryDirectory() as directory:
                index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
                index.initialize()
                status = (
                    CandidateTaskStatus.READY
                    if edge_type is KnowledgeEdgeType.DEPENDS_ON
                    else None
                )
                index.put_node(_node("source", status=status))
                index.put_node(_node("target", status=status))
                stable_revision = index.revision_digest()
                with index.transaction() as connection:
                    connection.execute(
                        """
                        CREATE TRIGGER corrupt_new_edge
                        AFTER INSERT ON knowledge_edges
                        BEGIN
                            UPDATE knowledge_edges
                            SET metadata_digest = 'sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
                            WHERE source_node_id = NEW.source_node_id
                              AND target_node_id = NEW.target_node_id
                              AND edge_type = NEW.edge_type;
                        END
                        """
                    )
                edge = KnowledgeEdge(
                    "source",
                    "target",
                    edge_type,
                    _provenance(f"trigger-{edge_type.value}"),
                )

                with self.assertRaisesRegex(KnowledgeIndexError, "columns"):
                    if edge_type is KnowledgeEdgeType.DEPENDS_ON:
                        index.put_dependency_edge(edge)
                    else:
                        index.put_edge(edge)

                self.assertEqual(index.edges_for("source"), ())
                self.assertEqual(index.revision_digest(), stable_revision)

    def test_valid_payload_swaps_and_term_tampering_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
            index.initialize()
            first = _node("alpha-node")
            second = _node("beta-node")
            third = _node("gamma-node")
            for node in (first, second, third):
                index.put_node(node)
            first_edge = KnowledgeEdge(
                "alpha-node",
                "beta-node",
                KnowledgeEdgeType.RELATES_TO,
                _provenance("first-edge"),
            )
            second_edge = KnowledgeEdge(
                "beta-node",
                "gamma-node",
                KnowledgeEdgeType.SUPPORTS,
                _provenance("second-edge"),
            )
            index.put_edge(first_edge)
            index.put_edge(second_edge)
            stable_revision = index.revision_digest()

            with index.transaction() as connection:
                first_payload = connection.execute(
                    "SELECT payload_json FROM knowledge_nodes WHERE node_id = ?",
                    ("alpha-node",),
                ).fetchone()["payload_json"]
                second_payload = connection.execute(
                    "SELECT payload_json FROM knowledge_nodes WHERE node_id = ?",
                    ("beta-node",),
                ).fetchone()["payload_json"]
                connection.execute(
                    "UPDATE knowledge_nodes SET payload_json = ? WHERE node_id = ?",
                    (second_payload, "alpha-node"),
                )
            with self.assertRaisesRegex(KnowledgeIndexError, "columns.*payload"):
                index.get_node("alpha-node")
            with self.assertRaisesRegex(KnowledgeIndexError, "columns.*payload"):
                index.revision_digest()
            with index.transaction() as connection:
                connection.execute(
                    "UPDATE knowledge_nodes SET payload_json = ? WHERE node_id = ?",
                    (first_payload, "alpha-node"),
                )

            with index.transaction() as connection:
                wrong_edge_payload = connection.execute(
                    "SELECT payload_json FROM knowledge_edges "
                    "WHERE source_node_id = ? AND target_node_id = ?",
                    ("beta-node", "gamma-node"),
                ).fetchone()["payload_json"]
                connection.execute(
                    "UPDATE knowledge_edges SET payload_json = ? "
                    "WHERE source_node_id = ? AND target_node_id = ?",
                    (wrong_edge_payload, "alpha-node", "beta-node"),
                )
            with self.assertRaisesRegex(KnowledgeIndexError, "columns.*payload"):
                index.edges_for("alpha-node")
            with index.transaction() as connection:
                connection.execute(
                    "UPDATE knowledge_edges SET payload_json = ? "
                    "WHERE source_node_id = ? AND target_node_id = ?",
                    (
                        canonical_json_bytes(first_edge.to_dict()).decode("utf-8"),
                        "alpha-node",
                        "beta-node",
                    ),
                )

            with index.transaction() as connection:
                connection.execute(
                    "UPDATE knowledge_terms SET weight = weight + 1 "
                    "WHERE node_id = ? AND term = ?",
                    ("alpha-node", "alpha"),
                )
            with self.assertRaisesRegex(KnowledgeIndexError, "term index"):
                index.term_scores(("alpha",), ("alpha-node",))
            with self.assertRaisesRegex(KnowledgeIndexError, "term index"):
                index.revision_digest()
            self.assertNotEqual(stable_revision, "")

    def test_index_rejects_bad_schema_terms_and_corrupt_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            index = SQLiteKnowledgeIndex(path)
            index.initialize()
            node = _node("node")
            index.put_node(node)
            with self.assertRaisesRegex(ValueError, "at most 128"):
                index.term_scores(tuple(f"term{item}" for item in range(129)), ("node",))

            with index.transaction() as connection:
                connection.execute(
                    "UPDATE knowledge_nodes SET payload_json = ? WHERE node_id = ?",
                    ("{", "node"),
                )
            with self.assertRaises(KnowledgeIndexError):
                index.get_node("node")

        for value, message in (("invalid", "invalid"), ("-1", "invalid")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
                index.initialize()
                with index.transaction() as connection:
                    connection.execute(
                        "UPDATE knowledge_index_metadata SET value = ? "
                        "WHERE key = 'schema_version'",
                        (value,),
                    )
                with self.assertRaisesRegex(KnowledgeIndexError, message):
                    index.initialize()

    def test_corrupt_index_rejects_writes_without_mutation_or_metadata_repair(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
            index.initialize()
            for node in (
                _node("anchor"),
                _node("other"),
                _node("task-a", status=CandidateTaskStatus.READY),
                _node("task-b", status=CandidateTaskStatus.COMPLETED),
            ):
                index.put_node(node)
            with index.transaction() as connection:
                connection.execute(
                    "UPDATE knowledge_terms SET weight = weight + 1 "
                    "WHERE node_id = ?",
                    ("anchor",),
                )
            with index.connection() as connection:
                before = (
                    connection.execute(
                        "SELECT count(*) FROM knowledge_nodes"
                    ).fetchone()[0],
                    connection.execute(
                        "SELECT count(*) FROM knowledge_edges"
                    ).fetchone()[0],
                )

            writes = (
                ("node", lambda: index.put_node(_node("new-node"))),
                (
                    "edge",
                    lambda: index.put_edge(
                        KnowledgeEdge(
                            "anchor",
                            "other",
                            KnowledgeEdgeType.SUPPORTS,
                            _provenance("new-edge"),
                        )
                    ),
                ),
                (
                    "dependency",
                    lambda: index.put_dependency_edge(
                        KnowledgeEdge(
                            "task-a",
                            "task-b",
                            KnowledgeEdgeType.DEPENDS_ON,
                            _provenance("new-dependency"),
                        )
                    ),
                ),
            )
            for label, write in writes:
                with self.subTest(write=label):
                    with self.assertRaisesRegex(KnowledgeIndexError, "term index"):
                        write()
                    with index.connection() as connection:
                        after = (
                            connection.execute(
                                "SELECT count(*) FROM knowledge_nodes"
                            ).fetchone()[0],
                            connection.execute(
                                "SELECT count(*) FROM knowledge_edges"
                            ).fetchone()[0],
                        )
                    self.assertEqual(after, before)

        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
            index.initialize()
            with index.transaction() as connection:
                connection.execute(
                    "UPDATE knowledge_index_metadata SET value = ? "
                    "WHERE key = 'authority'",
                    ("task_authority",),
                )

            with self.assertRaisesRegex(KnowledgeIndexError, "metadata"):
                index.initialize()
            with index.connection() as connection:
                authority = connection.execute(
                    "SELECT value FROM knowledge_index_metadata "
                    "WHERE key = 'authority'"
                ).fetchone()[0]
            self.assertEqual(authority, "task_authority")

    def test_term_score_sql_is_restricted_to_candidate_node_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = _TracingSQLiteKnowledgeIndex(
                Path(directory) / "index.sqlite3"
            )
            index.initialize()
            index.put_node(_node("shared-allowed"))
            index.put_node(_node("shared-distractor"))
            index.statements.clear()

            scores = index.term_scores(("shared",), ("shared-allowed",))

            self.assertEqual(tuple(scores), ("shared-allowed",))
            term_query = next(
                statement
                for statement in index.statements
                if "FROM knowledge_terms" in statement
            )
            self.assertIn("AND node_id IN", term_query)
            self.assertIn("'shared-allowed'", term_query)
            self.assertNotIn("'shared-distractor'", term_query)

    def test_revision_rejects_metadata_dangling_edges_and_dependency_cycles(
        self,
    ) -> None:
        for key, value in (
            ("schema_version", "999"),
            ("authority", "task_authority"),
        ):
            with self.subTest(metadata=key), tempfile.TemporaryDirectory() as directory:
                index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
                index.initialize()
                with index.transaction() as connection:
                    connection.execute(
                        "UPDATE knowledge_index_metadata SET value = ? WHERE key = ?",
                        (value, key),
                    )
                with self.assertRaisesRegex(KnowledgeIndexError, "metadata"):
                    index.revision_digest()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            index = SQLiteKnowledgeIndex(path)
            index.initialize()
            index.put_node(_node("anchor"))
            _insert_raw_edge(
                path,
                KnowledgeEdge(
                    "anchor",
                    "missing",
                    KnowledgeEdgeType.SUPPORTS,
                    _provenance("dangling-edge"),
                ),
            )
            with self.assertRaisesRegex(KnowledgeIndexError, "endpoints"):
                index.revision_digest()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            index = SQLiteKnowledgeIndex(path)
            index.initialize()
            for node_id in ("task-a", "task-b"):
                index.put_node(_node(node_id, status=CandidateTaskStatus.COMPLETED))
            first = KnowledgeEdge(
                "task-a",
                "task-b",
                KnowledgeEdgeType.DEPENDS_ON,
                _provenance("dependency-a-b"),
            )
            second = KnowledgeEdge(
                "task-b",
                "task-a",
                KnowledgeEdgeType.DEPENDS_ON,
                _provenance("dependency-b-a"),
            )
            index.put_dependency_edge(first)
            _insert_raw_edge(path, second)
            with self.assertRaisesRegex(KnowledgeIndexError, "cycle"):
                index.revision_digest()

    def test_graph_queries_fail_closed_when_revision_changes_mid_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
            index.initialize()
            graph = KnowledgeGraph(index)
            graph.add_node(_node("anchor"))
            graph.add_node(_node("task", status=CandidateTaskStatus.READY))
            graph.add_edge(
                KnowledgeEdge(
                    "anchor",
                    "task",
                    KnowledgeEdgeType.SUPPORTS,
                    _provenance("anchor-task"),
                )
            )

            operations = (
                lambda changing: KnowledgeGraph(changing).search(
                    "anchor", "task", max_depth=1
                ),
                lambda changing: KnowledgeGraph(changing).inspect_dependencies(
                    "task", max_depth=1
                ),
                lambda changing: KnowledgeGraph(changing).next_step_context(
                    "anchor",
                    "task",
                    max_depth=1,
                    dependency_max_depth=1,
                ),
            )
            for operation in operations:
                with self.subTest(operation=operation):
                    changing = _RevisionChangingIndex(index)
                    with self.assertRaisesRegex(KnowledgeGraphError, "changed"):
                        operation(changing)

    def test_next_step_reuses_one_revision_and_each_edge_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
            index.initialize()
            graph = KnowledgeGraph(index)
            graph.add_node(_node("anchor"))
            graph.add_node(
                _node("shared-dependency", status=CandidateTaskStatus.COMPLETED)
            )
            for ordinal in range(3):
                task_id = f"task-{ordinal}"
                graph.add_node(_node(task_id, status=CandidateTaskStatus.READY))
                graph.add_edge(
                    KnowledgeEdge(
                        "anchor",
                        task_id,
                        KnowledgeEdgeType.SUPPORTS,
                        _provenance(f"anchor-{task_id}"),
                    )
                )
                graph.add_edge(
                    KnowledgeEdge(
                        task_id,
                        "shared-dependency",
                        KnowledgeEdgeType.DEPENDS_ON,
                        _provenance(f"{task_id}-dependency"),
                    )
                )
            counting = _CountingIndex(index)

            context = KnowledgeGraph(counting).next_step_context(
                "anchor",
                "task",
                max_depth=1,
                dependency_max_depth=2,
            )

            self.assertEqual(len(context.candidates), 3)
            self.assertEqual(counting.revision_calls, 2)
            self.assertEqual(
                counting.edge_calls,
                {
                    "anchor": 1,
                    "shared-dependency": 1,
                    "task-0": 1,
                    "task-1": 1,
                    "task-2": 1,
                },
            )

    def test_graph_candidate_and_dependency_expansions_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
            index.initialize()
            graph = KnowledgeGraph(index)
            graph.add_node(_node("anchor"))
            for ordinal in range(3):
                graph.add_node(
                    _node(f"task-{ordinal}", status=CandidateTaskStatus.READY)
                )
            for ordinal in range(2):
                graph.add_edge(
                    KnowledgeEdge(
                        "anchor",
                        f"task-{ordinal}",
                        KnowledgeEdgeType.SUPPORTS,
                        _provenance(f"bounded-anchor-{ordinal}"),
                    )
                )
            for ordinal in (1, 2):
                graph.add_edge(
                    KnowledgeEdge(
                        "task-0",
                        f"task-{ordinal}",
                        KnowledgeEdgeType.DEPENDS_ON,
                        _provenance(f"bounded-dependency-{ordinal}"),
                    )
                )

            with patch(
                "sisyphus_harness.knowledge_graph._MAX_GRAPH_EXPANSION_NODES",
                2,
            ):
                with self.assertRaisesRegex(KnowledgeGraphError, "graph traversal"):
                    graph.search("anchor", "task", max_depth=1)
            with patch(
                "sisyphus_harness.knowledge_graph._MAX_NEXT_STEP_CANDIDATES",
                1,
            ):
                with self.assertRaisesRegex(KnowledgeGraphError, "candidate expansion"):
                    graph.next_step_context("anchor", "task", max_depth=1)
            with patch(
                "sisyphus_harness.knowledge_graph._MAX_DEPENDENCY_STATES",
                1,
            ):
                with self.assertRaisesRegex(
                    KnowledgeGraphError,
                    "dependency expansion",
                ):
                    graph.inspect_dependencies("task-0", max_depth=1)

    def test_graph_rejects_non_task_dependencies_and_reports_empty_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SQLiteKnowledgeIndex(Path(directory) / "index.sqlite3")
            index.initialize()
            graph = KnowledgeGraph(index)
            knowledge = _node("knowledge")
            task = _node("task", status=CandidateTaskStatus.READY)
            graph.add_node(knowledge)
            graph.add_node(task)
            with self.assertRaisesRegex(KnowledgeGraphError, "connect task"):
                graph.add_edge(
                    KnowledgeEdge(
                        "task",
                        "knowledge",
                        KnowledgeEdgeType.DEPENDS_ON,
                        _provenance("bad-dependency"),
                    )
                )

            # Every index entry point enforces the task-only, atomic dependency
            # boundary; callers cannot bypass KnowledgeGraph's preflight check.
            bypass = KnowledgeEdge(
                "task",
                "knowledge",
                KnowledgeEdgeType.DEPENDS_ON,
                _provenance("bypassed-dependency"),
            )
            with self.assertRaisesRegex(KnowledgeIndexError, "atomic"):
                index.put_edge(bypass)
            with self.assertRaisesRegex(KnowledgeIndexError, "task nodes"):
                index.put_dependency_edge(bypass)

            context = graph.next_step_context("knowledge", query=None, max_depth=0)
            self.assertEqual(context.query_terms, ("content", "knowledge"))
            self.assertEqual(context.candidates, ())
            with self.assertRaisesRegex(ValueError, "between 1 and 1000"):
                graph.next_step_context("knowledge", limit=True)


def _provenance(source_id: str) -> KnowledgeProvenance:
    return KnowledgeProvenance(
        source_id=source_id,
        source_kind="boundary-test",
        source_digest=sha256_digest({"source_id": source_id}),
        producer="tests.test_knowledge_boundaries",
    )


class _RevisionChangingIndex:
    def __init__(self, delegate: SQLiteKnowledgeIndex) -> None:
        self.delegate = delegate
        self.revision_calls = 0

    def revision_digest(self) -> str:
        self.revision_calls += 1
        revision = self.delegate.revision_digest()
        if self.revision_calls == 1:
            return revision
        return "sha256:" + "f" * 64

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)


class _CountingIndex:
    def __init__(self, delegate: SQLiteKnowledgeIndex) -> None:
        self.delegate = delegate
        self.revision_calls = 0
        self.edge_calls: dict[str, int] = {}

    def revision_digest(self) -> str:
        self.revision_calls += 1
        return self.delegate.revision_digest()

    def edges_for(self, node_id: str) -> tuple[KnowledgeEdge, ...]:
        self.edge_calls[node_id] = self.edge_calls.get(node_id, 0) + 1
        return self.delegate.edges_for(node_id)

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)


class _TracingSQLiteKnowledgeIndex(SQLiteKnowledgeIndex):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.statements: list[str] = []

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with super().connection() as connection:
            connection.set_trace_callback(self.statements.append)
            yield connection


def _insert_raw_edge(path: Path, edge: KnowledgeEdge) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO knowledge_edges(
                source_node_id,
                target_node_id,
                edge_type,
                metadata_digest,
                provenance_digest,
                edge_digest,
                authority,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.source_node_id,
                edge.target_node_id,
                edge.edge_type.value,
                edge.metadata_digest,
                edge.provenance_digest,
                edge.edge_digest,
                edge.authority,
                canonical_json_bytes(edge.to_dict()).decode("utf-8"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _node(
    node_id: str,
    *,
    status: CandidateTaskStatus | None = None,
) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=node_id,
        node_type=(
            KnowledgeNodeType.KNOWLEDGE if status is None else KnowledgeNodeType.TASK
        ),
        title=node_id.title(),
        content=f"{node_id} content",
        provenance=_provenance(node_id),
        task_status=status,
    )


if __name__ == "__main__":
    unittest.main()
