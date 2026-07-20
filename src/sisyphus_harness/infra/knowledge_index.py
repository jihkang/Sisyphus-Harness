from __future__ import annotations

from contextlib import contextmanager
import heapq
from pathlib import Path
import sqlite3
from typing import Iterator

from ..contracts.codec import canonical_json_bytes, loads_strict_json, sha256_digest
from ..contracts.knowledge import (
    DEPENDENCY_EDGE_TYPES,
    DERIVED_CANDIDATE_AUTHORITY,
    DependencyEdgeWriteResult,
    KnowledgeEdge,
    KnowledgeNode,
    normalized_terms,
    weighted_node_terms,
)


KNOWLEDGE_INDEX_SCHEMA_VERSION = 1
_SQLITE_SAFE_PARAMETER_LIMIT = 900


class KnowledgeIndexError(RuntimeError):
    pass


class KnowledgeIndexConflict(KnowledgeIndexError):
    pass


class SQLiteKnowledgeIndex:
    """Rebuildable lexical/graph projection with no task authority."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            existing_tables = {
                row["name"]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name IN (
                          'knowledge_index_metadata',
                          'knowledge_nodes',
                          'knowledge_terms',
                          'knowledge_edges'
                      )
                    """
                ).fetchall()
            }
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_index_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            current = connection.execute(
                """
                SELECT value
                FROM knowledge_index_metadata
                WHERE key = 'schema_version'
                """
            ).fetchone()
            current_version = (
                0 if current is None else _schema_version(current["value"])
            )
            if current_version > KNOWLEDGE_INDEX_SCHEMA_VERSION:
                raise KnowledgeIndexError(
                    f"knowledge index schema version {current_version} is newer than "
                    f"supported version {KNOWLEDGE_INDEX_SCHEMA_VERSION}"
                )
            metadata_rows = connection.execute(
                """
                SELECT key, value
                FROM knowledge_index_metadata
                ORDER BY key
                """
            ).fetchall()
            if current is None:
                if metadata_rows or existing_tables:
                    raise KnowledgeIndexError(
                        "knowledge index metadata is incomplete or corrupt"
                    )
                self._create_schema(connection)
                connection.executemany(
                    """
                    INSERT INTO knowledge_index_metadata(key, value)
                    VALUES(?, ?)
                    """,
                    (
                        ("authority", DERIVED_CANDIDATE_AUTHORITY),
                        ("schema_version", str(KNOWLEDGE_INDEX_SCHEMA_VERSION)),
                    ),
                )
            elif current_version < 1:
                raise KnowledgeIndexError(
                    f"unsupported knowledge index schema version {current_version}"
                )
            _revision_digest_from_connection(connection)

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_nodes (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL CHECK (node_type IN ('task', 'knowledge')),
                task_status TEXT CHECK (
                    task_status IS NULL
                    OR task_status IN ('completed', 'ready', 'blocked')
                ),
                title TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                metadata_digest TEXT NOT NULL,
                provenance_digest TEXT NOT NULL,
                node_digest TEXT NOT NULL UNIQUE,
                authority TEXT NOT NULL CHECK (
                    authority = '{DERIVED_CANDIDATE_AUTHORITY}'
                ),
                payload_json TEXT NOT NULL,
                CHECK (
                    (node_type = 'task' AND task_status IS NOT NULL)
                    OR
                    (node_type = 'knowledge' AND task_status IS NULL)
                )
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_terms (
                node_id TEXT NOT NULL REFERENCES knowledge_nodes(node_id)
                    ON DELETE CASCADE,
                term TEXT NOT NULL,
                weight INTEGER NOT NULL CHECK (weight > 0),
                PRIMARY KEY(node_id, term)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS knowledge_terms_lookup
            ON knowledge_terms(term, node_id)
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_edges (
                source_node_id TEXT NOT NULL REFERENCES knowledge_nodes(node_id),
                target_node_id TEXT NOT NULL REFERENCES knowledge_nodes(node_id),
                edge_type TEXT NOT NULL,
                metadata_digest TEXT NOT NULL,
                provenance_digest TEXT NOT NULL,
                edge_digest TEXT NOT NULL UNIQUE,
                authority TEXT NOT NULL CHECK (
                    authority = '{DERIVED_CANDIDATE_AUTHORITY}'
                ),
                payload_json TEXT NOT NULL,
                PRIMARY KEY(source_node_id, target_node_id, edge_type),
                CHECK(source_node_id != target_node_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS knowledge_edges_target
            ON knowledge_edges(target_node_id, edge_type, source_node_id)
            """
        )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    @contextmanager
    def read_transaction(self) -> Iterator[sqlite3.Connection]:
        """Expose one stable WAL snapshot for a multi-table integrity read."""

        with self.connection() as connection:
            connection.execute("BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def put_node(self, node: KnowledgeNode) -> bool:
        node = _canonical_node(node)
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
        with self.transaction() as connection:
            _revision_digest_from_connection(connection)
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
            _revision_digest_from_connection(connection)
        return True

    def put_edge(self, edge: KnowledgeEdge) -> bool:
        edge = _canonical_edge(edge)
        if edge.edge_type in DEPENDENCY_EDGE_TYPES:
            raise KnowledgeIndexError(
                "dependency edges must use the atomic dependency writer"
            )
        payload = canonical_json_bytes(edge.to_dict()).decode("utf-8")
        with self.transaction() as connection:
            _revision_digest_from_connection(connection)
            existing = self._existing_edge(connection, edge)
            if existing is not None:
                return existing
            self._insert_edge(connection, edge, payload)
            _revision_digest_from_connection(connection)
            return True

    def put_dependency_edge(
        self,
        edge: KnowledgeEdge,
    ) -> DependencyEdgeWriteResult:
        edge = _canonical_edge(edge)
        if edge.edge_type not in DEPENDENCY_EDGE_TYPES:
            raise ValueError("atomic dependency write requires a dependency edge")
        payload = canonical_json_bytes(edge.to_dict()).decode("utf-8")
        with self.transaction() as connection:
            _revision_digest_from_connection(connection)
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
            _revision_digest_from_connection(connection)
            return DependencyEdgeWriteResult.INSERTED

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

    def get_node(self, node_id: str) -> KnowledgeNode | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_nodes WHERE node_id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        return _node_from_row(row)

    def list_nodes(self) -> tuple[KnowledgeNode, ...]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_nodes ORDER BY node_id"
            ).fetchall()
        return tuple(_node_from_row(row) for row in rows)

    def edges_for(self, node_id: str) -> tuple[KnowledgeEdge, ...]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM knowledge_edges
                WHERE source_node_id = ? OR target_node_id = ?
                ORDER BY source_node_id, target_node_id, edge_type, edge_digest
                """,
                (node_id, node_id),
            ).fetchall()
        return tuple(_edge_from_row(row) for row in rows)

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
        with self.connection() as connection:
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
        for node in (_node_from_row(row) for row in node_rows):
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
        with self.read_transaction() as connection:
            return _revision_digest_from_connection(connection)


def _revision_digest_from_connection(connection: sqlite3.Connection) -> str:
    metadata_rows = connection.execute(
        """
        SELECT key, value
        FROM knowledge_index_metadata
        ORDER BY key
        """
    ).fetchall()
    node_rows = connection.execute(
        "SELECT * FROM knowledge_nodes ORDER BY node_id"
    ).fetchall()
    edge_rows = connection.execute(
        """
        SELECT *
        FROM knowledge_edges
        ORDER BY source_node_id, target_node_id, edge_type
        """
    ).fetchall()
    term_rows = connection.execute(
        """
        SELECT node_id, term, weight
        FROM knowledge_terms
        ORDER BY node_id, term
        """
    ).fetchall()
    stored_metadata = tuple((row["key"], row["value"]) for row in metadata_rows)
    expected_metadata = (
        ("authority", DERIVED_CANDIDATE_AUTHORITY),
        ("schema_version", str(KNOWLEDGE_INDEX_SCHEMA_VERSION)),
    )
    if stored_metadata != expected_metadata:
        raise KnowledgeIndexError(
            "knowledge index metadata does not match the supported schema"
        )
    nodes = tuple(_node_from_row(row) for row in node_rows)
    edges = tuple(_edge_from_row(row) for row in edge_rows)
    nodes_by_id = {node.node_id: node for node in nodes}
    for edge in edges:
        source = nodes_by_id.get(edge.source_node_id)
        target = nodes_by_id.get(edge.target_node_id)
        if source is None or target is None:
            raise KnowledgeIndexError(
                "knowledge edge endpoints do not reference stored nodes"
            )
        if edge.edge_type in DEPENDENCY_EDGE_TYPES and (
            source.node_type.value != "task" or target.node_type.value != "task"
        ):
            raise KnowledgeIndexError(
                "dependency edge endpoints are not stored task nodes"
            )
    if _dependency_edges_contain_cycle(edges):
        raise KnowledgeIndexError("knowledge dependency edges contain a cycle")
    expected_terms = tuple(
        (node.node_id, term, weight)
        for node in nodes
        for term, weight in weighted_node_terms(node).items()
    )
    stored_terms = tuple(
        (row["node_id"], row["term"], row["weight"]) for row in term_rows
    )
    if stored_terms != expected_terms:
        raise KnowledgeIndexError(
            "knowledge term index does not match stored node payloads"
        )
    return sha256_digest(
        {
            "metadata": [list(item) for item in stored_metadata],
            "nodes": [[node.node_id, node.node_digest] for node in nodes],
            "edges": [
                [
                    edge.source_node_id,
                    edge.target_node_id,
                    edge.edge_type.value,
                    edge.edge_digest,
                ]
                for edge in edges
            ],
            "terms": [list(item) for item in stored_terms],
        }
    )


def _canonical_node(node: KnowledgeNode) -> KnowledgeNode:
    if type(node) is not KnowledgeNode:
        raise TypeError("knowledge index requires an exact KnowledgeNode")
    return KnowledgeNode.from_dict(node.to_dict())


def _canonical_edge(edge: KnowledgeEdge) -> KnowledgeEdge:
    if type(edge) is not KnowledgeEdge:
        raise TypeError("knowledge index requires an exact KnowledgeEdge")
    return KnowledgeEdge.from_dict(edge.to_dict())


def _chunks(values: tuple[str, ...], size: int) -> Iterator[tuple[str, ...]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _dependency_edges_contain_cycle(edges: tuple[KnowledgeEdge, ...]) -> bool:
    dependency_edges = tuple(
        edge for edge in edges if edge.edge_type in DEPENDENCY_EDGE_TYPES
    )
    if not dependency_edges:
        return False
    node_ids = {
        node_id
        for edge in dependency_edges
        for node_id in (edge.source_node_id, edge.target_node_id)
    }
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    incoming_count = {node_id: 0 for node_id in node_ids}
    for edge in dependency_edges:
        outgoing[edge.source_node_id].append(edge.target_node_id)
        incoming_count[edge.target_node_id] += 1
    ready = [
        node_id for node_id, count in incoming_count.items() if count == 0
    ]
    heapq.heapify(ready)
    visited = 0
    while ready:
        current = heapq.heappop(ready)
        visited += 1
        for target in sorted(outgoing[current]):
            incoming_count[target] -= 1
            if incoming_count[target] == 0:
                heapq.heappush(ready, target)
    return visited != len(node_ids)


def _parse_node(payload: str) -> KnowledgeNode:
    try:
        return KnowledgeNode.from_dict(
            loads_strict_json(payload, label="stored knowledge node")
        )
    except ValueError as exc:
        raise KnowledgeIndexError(str(exc)) from exc


def _parse_edge(payload: str) -> KnowledgeEdge:
    try:
        return KnowledgeEdge.from_dict(
            loads_strict_json(payload, label="stored knowledge edge")
        )
    except ValueError as exc:
        raise KnowledgeIndexError(str(exc)) from exc


def _node_from_row(row: sqlite3.Row) -> KnowledgeNode:
    node = _parse_node(row["payload_json"])
    expected = {
        "node_id": node.node_id,
        "node_type": node.node_type.value,
        "task_status": (
            None if node.task_status is None else node.task_status.value
        ),
        "title": node.title,
        "content_digest": node.content_digest,
        "metadata_digest": node.metadata_digest,
        "provenance_digest": node.provenance_digest,
        "node_digest": node.node_digest,
        "authority": node.authority,
    }
    if any(row[key] != value for key, value in expected.items()):
        raise KnowledgeIndexError(
            "stored knowledge node columns do not match its payload"
        )
    return node


def _edge_from_row(row: sqlite3.Row) -> KnowledgeEdge:
    edge = _parse_edge(row["payload_json"])
    expected = {
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
        "edge_type": edge.edge_type.value,
        "metadata_digest": edge.metadata_digest,
        "provenance_digest": edge.provenance_digest,
        "edge_digest": edge.edge_digest,
        "authority": edge.authority,
    }
    if any(row[key] != value for key, value in expected.items()):
        raise KnowledgeIndexError(
            "stored knowledge edge columns do not match its payload"
        )
    return edge


def _schema_version(raw: object) -> int:
    try:
        version = int(str(raw))
    except ValueError as exc:
        raise KnowledgeIndexError(
            f"invalid knowledge index schema version: {raw}"
        ) from exc
    if version < 0:
        raise KnowledgeIndexError(f"invalid knowledge index schema version: {raw}")
    return version
