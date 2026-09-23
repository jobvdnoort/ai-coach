"""SQLite storage for AI Coach: chat history, weight and training data per HA user."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3
import threading
from typing import Any, TypeVar

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_T = TypeVar("_T")

SCHEMA_VERSION = 1

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'dashboard',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_user ON chat_messages (user_id, id);

CREATE TABLE IF NOT EXISTS weight_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    weight_kg   REAL NOT NULL,
    note        TEXT,
    measured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weight_entries_user ON weight_entries (user_id, measured_at);

CREATE TABLE IF NOT EXISTS training_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    activity     TEXT NOT NULL,
    duration_min REAL,
    distance_km  REAL,
    intensity    TEXT,
    notes        TEXT,
    performed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_training_sessions_user ON training_sessions (user_id, performed_at);

CREATE TABLE IF NOT EXISTS telegram_links (
    telegram_chat_id INTEGER PRIMARY KEY,
    user_id          TEXT NOT NULL UNIQUE,
    linked_at        TEXT NOT NULL
);
"""


class CoachDatabase:
    """Thread-safe wrapper around a single SQLite connection.

    All blocking work runs in the HA executor; the lock serialises access
    because executor jobs may run on different threads.
    """

    def __init__(self, hass: HomeAssistant, path: str) -> None:
        self._hass = hass
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    async def async_open(self) -> None:
        await self._hass.async_add_executor_job(self._open)

    async def async_close(self) -> None:
        await self._hass.async_add_executor_job(self._close)

    def _open(self) -> None:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.executescript(SCHEMA_V1)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
        self._conn = conn

    def _close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    async def _run(self, func: Callable[[sqlite3.Connection], _T]) -> _T:
        def _job() -> _T:
            with self._lock:
                if self._conn is None:
                    raise RuntimeError("AI Coach database is not open")
                result = func(self._conn)
                self._conn.commit()
                return result

        return await self._hass.async_add_executor_job(_job)

    # Chat history

    async def async_add_message(
        self, user_id: str, role: str, content: str, source: str = "dashboard"
    ) -> dict[str, Any]:
        created_at = dt_util.utcnow().isoformat()

        def _insert(conn: sqlite3.Connection) -> dict[str, Any]:
            cur = conn.execute(
                "INSERT INTO chat_messages (user_id, role, content, source, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, role, content, source, created_at),
            )
            return {
                "id": cur.lastrowid,
                "role": role,
                "content": content,
                "source": source,
                "created_at": created_at,
            }

        return await self._run(_insert)

    async def async_get_history(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        def _select(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT id, role, content, source, created_at FROM chat_messages "
                "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

        return await self._run(_select)

    async def async_clear_history(self, user_id: str) -> int:
        def _delete(conn: sqlite3.Connection) -> int:
            return conn.execute(
                "DELETE FROM chat_messages WHERE user_id = ?", (user_id,)
            ).rowcount

        return await self._run(_delete)

    # Weight

    async def async_add_weight(
        self, user_id: str, weight_kg: float, note: str | None = None
    ) -> None:
        measured_at = dt_util.utcnow().isoformat()
        await self._run(
            lambda conn: conn.execute(
                "INSERT INTO weight_entries (user_id, weight_kg, note, measured_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, weight_kg, note, measured_at),
            )
        )

    async def async_get_weights(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        def _select(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT weight_kg, note, measured_at FROM weight_entries "
                "WHERE user_id = ? ORDER BY measured_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(_select)

    # Training

    async def async_add_training(
        self,
        user_id: str,
        activity: str,
        duration_min: float | None = None,
        distance_km: float | None = None,
        intensity: str | None = None,
        notes: str | None = None,
    ) -> None:
        performed_at = dt_util.utcnow().isoformat()
        await self._run(
            lambda conn: conn.execute(
                "INSERT INTO training_sessions "
                "(user_id, activity, duration_min, distance_km, intensity, notes, performed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, activity, duration_min, distance_km, intensity, notes, performed_at),
            )
        )

    async def async_get_trainings(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        def _select(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT activity, duration_min, distance_km, intensity, notes, performed_at "
                "FROM training_sessions WHERE user_id = ? ORDER BY performed_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(_select)
