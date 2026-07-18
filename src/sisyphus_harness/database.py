from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


SCHEMA_VERSION = 1

_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('queued', 'running', 'completed', 'failed')
            ),
            lease_owner TEXT,
            lease_expires_at REAL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            result_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (status = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
                OR
                (status != 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)
            )
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS jobs_claimable
        ON jobs(status, lease_expires_at, created_at)
        """,
    ),
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            current = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            current_version = 0 if current is None else _schema_version(current["value"])
            if current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema version {current_version} is newer than "
                    f"supported version {SCHEMA_VERSION}"
                )
            for version in range(current_version + 1, SCHEMA_VERSION + 1):
                statements = _MIGRATIONS.get(version)
                if statements is None:
                    raise RuntimeError(f"missing database migration {version}")
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO metadata(key, value)
                    VALUES('schema_version', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(version),),
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


def _schema_version(raw: object) -> int:
    try:
        version = int(str(raw))
    except ValueError as exc:
        raise RuntimeError(f"invalid database schema version: {raw}") from exc
    if version < 0:
        raise RuntimeError(f"invalid database schema version: {raw}")
    return version
