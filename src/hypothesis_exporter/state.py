from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from .models import EntryState, SourceState


SCHEMA_VERSION = 1


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"State database schema {version} is newer than supported schema {SCHEMA_VERSION}")
        if version == 0:
            with self.connection:
                self.connection.executescript("""
                    CREATE TABLE source_sections (
                        source_key TEXT PRIMARY KEY,
                        local_date TEXT NOT NULL,
                        uri TEXT NOT NULL,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('managed', 'detached')),
                        path TEXT
                    );
                    CREATE TABLE annotation_entries (
                        annotation_id TEXT PRIMARY KEY,
                        source_key TEXT NOT NULL REFERENCES source_sections(source_key),
                        created_at TEXT NOT NULL,
                        base_text TEXT NOT NULL,
                        remote_updated TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('managed', 'detached', 'remote_missing')),
                        path TEXT
                    );
                    CREATE INDEX entries_source_key ON annotation_entries(source_key);
                    PRAGMA user_version = 1;
                """)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self.connection.execute("BEGIN")
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def entries(self, *, include_detached: bool = True) -> dict[str, EntryState]:
        sql = "SELECT * FROM annotation_entries"
        if not include_detached:
            sql += " WHERE status = 'managed'"
        return {
            row["annotation_id"]: EntryState(
                annotation_id=row["annotation_id"], source_key=row["source_key"],
                created_at=row["created_at"], base_text=row["base_text"],
                remote_updated=row["remote_updated"], status=row["status"], path=row["path"],
            )
            for row in self.connection.execute(sql)
        }

    def sources(self) -> dict[str, SourceState]:
        return {
            row["source_key"]: SourceState(
                source_key=row["source_key"], local_date=row["local_date"], uri=row["uri"],
                title=row["title"], status=row["status"], path=row["path"],
            )
            for row in self.connection.execute("SELECT * FROM source_sections")
        }

    def upsert_source(self, source: SourceState) -> None:
        self.connection.execute("""
            INSERT INTO source_sections(source_key, local_date, uri, title, status, path)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                local_date=excluded.local_date, uri=excluded.uri, title=excluded.title,
                status=excluded.status, path=excluded.path
        """, (source.source_key, source.local_date, source.uri, source.title, source.status, source.path))

    def upsert_entry(self, entry: EntryState) -> None:
        self.connection.execute("""
            INSERT INTO annotation_entries(annotation_id, source_key, created_at, base_text,
                                           remote_updated, status, path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(annotation_id) DO UPDATE SET
                source_key=excluded.source_key, created_at=excluded.created_at,
                base_text=excluded.base_text, remote_updated=excluded.remote_updated,
                status=excluded.status, path=excluded.path
        """, (entry.annotation_id, entry.source_key, entry.created_at, entry.base_text,
              entry.remote_updated, entry.status, entry.path))

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for row in self.connection.execute("SELECT status, count(*) AS count FROM annotation_entries GROUP BY status"):
            result[row["status"]] = row["count"]
        return result
