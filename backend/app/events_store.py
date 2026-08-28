"""Cross-backend activity/audit log — the one place FileDrive can answer
"what happened, on which connection, to which item" uniformly across all
nine backends, none of which expose that in a common shape (some don't
expose it as an API at all). This module is pure storage (Repository
pattern, same shape as connections_store.py/settings_store.py) — recording
policy (who gets notified, etc.) lives in activity_service.py, which wraps
this with an Observer layer. Routers never import this module directly;
they go through activity_service.record_event().
"""

import datetime
import json
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "events.db"


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                connection_id TEXT,
                provider_key TEXT,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                resource_name TEXT,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_resource ON events (connection_id, resource_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON events (created_at)")
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "provider_key": row["provider_key"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "resource_name": row["resource_name"],
        "event_type": row["event_type"],
        "actor": row["actor"],
        "payload": json.loads(row["payload_json"]),
        "created_at": row["created_at"],
    }


def record_event(
    *,
    connection_id: str | None,
    provider_key: str | None,
    resource_type: str,
    resource_id: str,
    resource_name: str | None,
    event_type: str,
    actor: str,
    payload: dict | None = None,
) -> dict:
    event_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO events (id, connection_id, provider_key, resource_type, resource_id, resource_name, "
            "event_type, actor, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                connection_id,
                provider_key,
                resource_type,
                resource_id,
                resource_name,
                event_type,
                actor,
                json.dumps(payload or {}),
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row)


def list_events(
    *,
    connection_id: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict]:
    clauses: list[str] = []
    params: list[str] = []
    if connection_id is not None:
        clauses.append("connection_id = ?")
        params.append(connection_id)
    if resource_id is not None:
        clauses.append("resource_id = ?")
        params.append(resource_id)
    if event_type is not None:
        clauses.append("event_type = ?")
        params.append(event_type)
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY created_at DESC LIMIT ?", (*params, min(limit, 200))
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
