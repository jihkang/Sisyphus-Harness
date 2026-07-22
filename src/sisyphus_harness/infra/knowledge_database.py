from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from ..contracts.knowledge import DERIVED_CANDIDATE_AUTHORITY
from .knowledge_index_errors import KnowledgeIndexError
from .knowledge_integrity import (
    parse_schema_version,
    revision_digest_from_connection,
)


KNOWLEDGE_INDEX_SCHEMA_VERSION = 1


class SQLiteKnowledgeDatabase:
    """SQLite schema and transaction lifecycle for the derived index."""

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
                0 if current is None else parse_schema_version(current["value"])
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
                        (
                            "schema_version",
                            str(KNOWLEDGE_INDEX_SCHEMA_VERSION),
                        ),
                    ),
                )
            elif current_version < 1:
                raise KnowledgeIndexError(
                    f"unsupported knowledge index schema version {current_version}"
                )
            revision_digest_from_connection(
                connection,
                schema_version=KNOWLEDGE_INDEX_SCHEMA_VERSION,
            )

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
