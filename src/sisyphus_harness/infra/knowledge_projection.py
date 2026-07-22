from __future__ import annotations

import sqlite3

from ..contracts.codec import canonical_json_bytes
from ..contracts.knowledge import (
    DEPENDENCY_EDGE_TYPES,
    DependencyEdgeWriteResult,
    KnowledgeEdge,
    KnowledgeNode,
    weighted_node_terms,
)
from .knowledge_database import (
    KNOWLEDGE_INDEX_SCHEMA_VERSION,
    SQLiteKnowledgeDatabase,
)
from .knowledge_index_errors import KnowledgeIndexConflict, KnowledgeIndexError
from .knowledge_integrity import (
    canonical_edge,
    canonical_node,
    revision_digest_from_connection,
)


class SQLiteKnowledgeProjection:
    """Transactional node and edge projection writes."""

    def __init__(self, database: SQLiteKnowledgeDatabase) -> None:
        self.database = database

    def put_node(self, node: KnowledgeNode) -> bool:
        node = canonical_node(node)
        payload = canonical_json_bytes(node.to_dict()).decode("utf-8")
        terms = tuple(weighted_node_terms(node).items())
        record = (
            node.node_id,
            node.node_type.value,
            None if node.task_status is None else node.task_status.value,
            node.title,
            node.content_digest,
            node.metadata_digest,
            node.provenance_digest,
            node.node_digest,
            node.authority,
            payload,
        )
        with self.database.transaction() as connection:
            self._require_integrity(connection)
            existing = connection.execute(
                "SELECT node_digest FROM knowledge_nodes WHERE node_id = ?",
                (node.node_id,),
            ).fetchone()
            if existing is not None:
                if existing["node_digest"] == node.node_digest:
                    return False
                raise KnowledgeIndexConflict(
                    f"knowledge node {node.node_id!r} already has different content"
                )
            connection.execute(
                """
                INSERT INTO knowledge_nodes(
                    node_id,
                    node_type,
                    task_status,
                    title,
                    content_digest,
                    metadata_digest,
                    provenance_digest,
                    node_digest,
                    authority,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                record,
            )
            connection.executemany(
                """
                INSERT INTO knowledge_terms(node_id, term, weight)
                VALUES (?, ?, ?)
                """,
                (
                    (node.node_id, term, weight)
                    for term, weight in terms
                ),
            )
            self._require_integrity(connection)
        return True

    def put_edge(self, edge: KnowledgeEdge) -> bool:
        edge = canonical_edge(edge)
        if edge.edge_type in DEPENDENCY_EDGE_TYPES:
            raise KnowledgeIndexError(
                "dependency edges must use the atomic dependency writer"
            )
        payload = canonical_json_bytes(edge.to_dict()).decode("utf-8")
        with self.database.transaction() as connection:
            self._require_integrity(connection)
            existing = self._existing_edge(connection, edge)
            if existing is not None:
                return existing
            self._insert_edge(connection, edge, payload)
            self._require_integrity(connection)
            return True

    def put_dependency_edge(
        self,
        edge: KnowledgeEdge,
    ) -> DependencyEdgeWriteResult:
        edge = canonical_edge(edge)
        if edge.edge_type not in DEPENDENCY_EDGE_TYPES:
            raise ValueError("atomic dependency write requires a dependency edge")
        payload = canonical_json_bytes(edge.to_dict()).decode("utf-8")
        with self.database.transaction() as connection:
            self._require_integrity(connection)
            self._require_task_endpoints(connection, edge)
            existing = self._existing_edge(connection, edge)
            if existing is not None:
                return DependencyEdgeWriteResult.UNCHANGED
            if self._dependency_reaches(
                connection,
                start_id=edge.target_node_id,
                target_id=edge.source_node_id,
            ):
                return DependencyEdgeWriteResult.CYCLE
            self._insert_edge(connection, edge, payload)
            self._require_integrity(connection)
            return DependencyEdgeWriteResult.INSERTED

    def _require_integrity(self, connection: sqlite3.Connection) -> str:
        return revision_digest_from_connection(
            connection,
            schema_version=KNOWLEDGE_INDEX_SCHEMA_VERSION,
        )

    @staticmethod
    def _require_task_endpoints(
        connection: sqlite3.Connection,
        edge: KnowledgeEdge,
    ) -> None:
        rows = connection.execute(
            """
            SELECT node_id, node_type
            FROM knowledge_nodes
            WHERE node_id IN (?, ?)
            ORDER BY node_id
            """,
            (edge.source_node_id, edge.target_node_id),
        ).fetchall()
        types = {row["node_id"]: row["node_type"] for row in rows}
        if (
            types.get(edge.source_node_id) != "task"
            or types.get(edge.target_node_id) != "task"
        ):
            raise KnowledgeIndexError(
                "dependency edge endpoints must reference stored task nodes"
            )

    @staticmethod
    def _existing_edge(
        connection: sqlite3.Connection,
        edge: KnowledgeEdge,
    ) -> bool | None:
        existing = connection.execute(
            """
            SELECT edge_digest
            FROM knowledge_edges
            WHERE source_node_id = ?
              AND target_node_id = ?
              AND edge_type = ?
            """,
            (
                edge.source_node_id,
                edge.target_node_id,
                edge.edge_type.value,
            ),
        ).fetchone()
        if existing is None:
            return None
        if existing["edge_digest"] == edge.edge_digest:
            return False
        raise KnowledgeIndexConflict(
            "knowledge edge already has different metadata or provenance"
        )

    @staticmethod
    def _insert_edge(
        connection: sqlite3.Connection,
        edge: KnowledgeEdge,
        payload: str,
    ) -> None:
        try:
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
                    payload,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise KnowledgeIndexError(
                "knowledge edge endpoints must reference stored nodes"
            ) from exc

    @staticmethod
    def _dependency_reaches(
        connection: sqlite3.Connection,
        *,
        start_id: str,
        target_id: str,
    ) -> bool:
        values = tuple(edge_type.value for edge_type in DEPENDENCY_EDGE_TYPES)
        rows = connection.execute(
            """
            SELECT source_node_id, target_node_id
            FROM knowledge_edges
            WHERE edge_type IN (?, ?)
            ORDER BY source_node_id, target_node_id
            """,
            values,
        ).fetchall()
        outgoing: dict[str, list[str]] = {}
        for row in rows:
            outgoing.setdefault(row["source_node_id"], []).append(
                row["target_node_id"]
            )
        pending = [start_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop(0)
            if current == target_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(
                node_id
                for node_id in outgoing.get(current, ())
                if node_id not in visited
            )
        return False
