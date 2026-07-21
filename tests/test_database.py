from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sisyphus_harness.database import Database, SCHEMA_VERSION


class DatabaseTests(unittest.TestCase):
    def test_initialization_is_idempotent_and_records_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authority.sqlite3"
            database = Database(path)

            database.initialize()
            database.initialize()

            with database.connection() as connection:
                version = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()["value"]
                jobs = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
                ).fetchone()
            self.assertEqual(version, str(SCHEMA_VERSION))
            self.assertIsNotNone(jobs)

    def test_newer_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authority.sqlite3"
            database = Database(path)
            database.initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
                    (str(SCHEMA_VERSION + 1),),
                )
                connection.commit()

            with self.assertRaisesRegex(RuntimeError, "newer than supported"):
                database.initialize()

    def test_version_one_database_migrates_without_replacing_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authority.sqlite3"
            database = Database(path)
            database.initialize()
            with database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO jobs(
                        job_id, idempotency_key, kind, payload_json, status,
                        created_at, updated_at
                    )
                    VALUES('job-1', 'request-1', 'coding-agent', '{}', 'queued',
                           'now', 'now')
                    """
                )
                connection.execute("DROP TABLE task_outcomes")
                connection.execute("DROP TABLE attempt_finished")
                connection.execute(
                    "UPDATE metadata SET value = '1' WHERE key = 'schema_version'"
                )

            database.initialize()

            with database.connection() as connection:
                version = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()["value"]
                job = connection.execute(
                    "SELECT job_id FROM jobs WHERE job_id = 'job-1'"
                ).fetchone()
                tables = {
                    row["name"]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table'
                          AND name IN ('attempt_finished', 'task_outcomes')
                        """
                    )
                }
            self.assertEqual(version, str(SCHEMA_VERSION))
            self.assertEqual(job["job_id"], "job-1")
            self.assertEqual(tables, {"attempt_finished", "task_outcomes"})

    def test_invalid_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authority.sqlite3"
            database = Database(path)
            database.initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE metadata SET value = 'invalid' WHERE key = 'schema_version'"
                )
                connection.commit()

            with self.assertRaisesRegex(RuntimeError, "invalid database schema"):
                database.initialize()


if __name__ == "__main__":
    unittest.main()
