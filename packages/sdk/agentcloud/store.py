"""Local SQLite-backed event store (WAL).

Stores the agent's local events and last-known remote event_id (sync cursor).
"""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS local_events (
    client_event_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0,
    remote_event_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_local_events_synced ON local_events(synced);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass
class LocalEvent:
    client_event_id: str
    type: str
    payload: Dict[str, Any]
    created_at: str
    synced: bool = False
    remote_event_id: Optional[int] = None

    def to_remote_dict(self) -> Dict[str, Any]:
        """Format for the cloud /v1/events POST."""
        return {
            "type": self.type,
            "payload": self.payload,
            "client_ts": self.created_at,
            "client_event_id": self.client_event_id,
        }


class LocalStore:
    """SQLite-backed local WAL."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ===== events =====

    def append(
        self,
        type: str,
        payload: Dict[str, Any],
        client_event_id: Optional[str] = None,
    ) -> LocalEvent:
        """Append an event to the local WAL. Returns the event."""
        ceid = client_event_id or uuid.uuid4().hex
        ev = LocalEvent(
            client_event_id=ceid,
            type=type,
            payload=payload,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO local_events
                (client_event_id, type, payload, created_at, synced)
                VALUES (?, ?, ?, ?, 0)
                """,
                (ev.client_event_id, ev.type, json.dumps(ev.payload), ev.created_at),
            )
        return ev

    def unsynced(self, limit: int = 200) -> List[LocalEvent]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT client_event_id, type, payload, created_at, synced, remote_event_id
                FROM local_events
                WHERE synced = 0
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            LocalEvent(
                client_event_id=r["client_event_id"],
                type=r["type"],
                payload=json.loads(r["payload"]),
                created_at=r["created_at"],
                synced=bool(r["synced"]),
                remote_event_id=r["remote_event_id"],
            )
            for r in rows
        ]

    def mark_synced(self, updates: List[tuple]) -> None:
        """Bulk mark events as synced.

        updates: list of (remote_event_id, client_event_id).
        """
        with self._conn() as conn:
            conn.executemany(
                """
                UPDATE local_events
                SET synced = 1, remote_event_id = ?
                WHERE client_event_id = ?
                """,
                updates,
            )

    # ===== sync cursor =====

    def get_cursor(self) -> int:
        """Last known remote event_id we've pulled."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM sync_state WHERE key = 'last_remote_event_id'"
            ).fetchone()
        return int(row["value"]) if row else 0

    def set_cursor(self, event_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (key, value) VALUES ('last_remote_event_id', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(event_id),),
            )

    def stats(self) -> Dict[str, int]:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM local_events").fetchone()["c"]
            unsynced = conn.execute(
                "SELECT COUNT(*) AS c FROM local_events WHERE synced = 0"
            ).fetchone()["c"]
        return {
            "total_local_events": total,
            "unsynced": unsynced,
            "last_remote_event_id": self.get_cursor(),
        }