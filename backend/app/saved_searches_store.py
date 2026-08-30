"""Saved searches — Repository storage for a named, re-runnable filter set.
Running one is just replaying its stored params through the existing
StorageProvider.search() call; this module only persists the definition.
"""

import datetime
import json
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "saved_searches.db"


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")  # wait for a concurrent writer instead of failing instantly
    return conn


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_searches (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                connection_id TEXT,
                query_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_run_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "owner": row["owner"],
        "name": row["name"],
        "connection_id": row["connection_id"],
        "query": json.loads(row["query_json"]),
        "created_at": row["created_at"],
        "last_run_at": row["last_run_at"],
    }


def create(owner: str, name: str, connection_id: str | None, query: dict) -> dict:
    sid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO saved_searches (id, owner, name, connection_id, query_json, created_at, last_run_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (sid, owner, name, connection_id, json.dumps(query), now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM saved_searches WHERE id = ?", (sid,)).fetchone()
    finally:
        conn.close()
    return _row(row)


def list_for_owner(owner: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM saved_searches WHERE owner = ? ORDER BY created_at DESC", (owner,)
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def get(search_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM saved_searches WHERE id = ?", (search_id,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def touch_last_run(search_id: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE saved_searches SET last_run_at = ? WHERE id = ?",
            (datetime.datetime.now(datetime.timezone.utc).isoformat(), search_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete(search_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))
        conn.commit()
    finally:
        conn.close()
