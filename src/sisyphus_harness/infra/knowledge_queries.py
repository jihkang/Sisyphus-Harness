from __future__ import annotations

import sqlite3
from typing import Iterator

from ..contracts.knowledge import (
    KnowledgeEdge,
    KnowledgeNode,
    normalized_terms,
    weighted_node_terms,
)
from .knowledge_database import (
    KNOWLEDGE_INDEX_SCHEMA_VERSION,
    SQLiteKnowledgeDatabase,
)
from .knowledge_index_errors import KnowledgeIndexError
from .knowledge_integrity import (
    edge_from_row,
    node_from_row,
    revision_digest_from_connection,
)


_SQLITE_SAFE_PARAMETER_LIMIT = 900


class SQLiteKnowledgeQueries:
    """Read-only projection queries and integrity-bound revision reads."""

    def __init__(self, database: SQLiteKnowledgeDatabase) -> None:
        self.database = database

    def get_node(self, node_id: str) -> KnowledgeNode | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_nodes WHERE node_id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        return node_from_row(row)

    def list_nodes(self) -> tuple[KnowledgeNode, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_nodes ORDER BY node_id"
            ).fetchall()
        return tuple(node_from_row(row) for row in rows)

    def edges_for(self, node_id: str) -> tuple[KnowledgeEdge, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM knowledge_edges
                WHERE source_node_id = ? OR target_node_id = ?
                ORDER BY source_node_id, target_node_id, edge_type, edge_digest
                """,
                (node_id, node_id),
            ).fetchall()
        return tuple(edge_from_row(row) for row in rows)

    def term_scores(
        self,
        terms: tuple[str, ...],
        node_ids: tuple[str, ...],
    ) -> dict[str, tuple[tuple[str, int], ...]]:
        if not terms or not node_ids:
            return {}
        if len(terms) > 128:
            raise ValueError("knowledge search supports at most 128 unique terms")
        for term in terms:
            if normalized_terms(term) != (term,):
                raise ValueError("knowledge search terms must be normalized")
        allowed_node_ids = tuple(sorted(set(node_ids)))
        chunk_size = _SQLITE_SAFE_PARAMETER_LIMIT - len(terms)
        term_placeholders = ",".join("?" for _ in terms)
        rows: list[sqlite3.Row] = []
        node_rows: list[sqlite3.Row] = []
        with self.database.connection() as connection:
            for chunk in _chunks(allowed_node_ids, chunk_size):
                node_placeholders = ",".join("?" for _ in chunk)
                term_query = (
                    "SELECT node_id, term, weight FROM knowledge_terms "
                    f"WHERE term IN ({term_placeholders}) "  # nosec B608
                    f"AND node_id IN ({node_placeholders}) "
                    "ORDER BY node_id, term"
                )
                node_query = (
                    "SELECT * FROM knowledge_nodes "
                    f"WHERE node_id IN ({node_placeholders}) "  # nosec B608
                    "ORDER BY node_id"
                )
                rows.extend(
                    connection.execute(
                        term_query,
                        terms + chunk,
                    ).fetchall()
                )
                node_rows.extend(
                    connection.execute(
                        node_query,
                        chunk,
                    ).fetchall()
                )
        allowed = frozenset(allowed_node_ids)
        scores: dict[str, list[tuple[str, int]]] = {}
        for row in rows:
            node_id = row["node_id"]
            if node_id in allowed:
                scores.setdefault(node_id, []).append((row["term"], row["weight"]))
        result = {
            node_id: tuple(items)
            for node_id, items in sorted(scores.items())
        }
        expected: dict[str, tuple[tuple[str, int], ...]] = {}
        requested = frozenset(terms)
        for node in (node_from_row(row) for row in node_rows):
            node_id = node.node_id
            matches = tuple(
                (term, weight)
                for term, weight in weighted_node_terms(node).items()
                if term in requested
            )
            if matches:
                expected[node_id] = matches
        if result != expected:
            raise KnowledgeIndexError(
                "knowledge term index does not match stored node payloads"
            )
        return result

    def revision_digest(self) -> str:
        with self.database.read_transaction() as connection:
            return revision_digest_from_connection(
                connection,
                schema_version=KNOWLEDGE_INDEX_SCHEMA_VERSION,
            )


def _chunks(values: tuple[str, ...], size: int) -> Iterator[tuple[str, ...]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
