"""Cross-backend activity/audit log — the one place C-ECM can answer
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
    conn.execute("PRAGMA busy_timeout=5000")  # wait for a concurrent writer instead of failing instantly
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
        # Added for the admin audit/reporting page — filtering and
        # aggregating by event_type and actor were previously unindexed
        # full-table scans (list_events didn't even accept an `actor` filter
        # at all until this page needed one).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_actor ON events (actor)")
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


def _build_where(
    *,
    connection_id: str | None,
    resource_id: str | None,
    event_type: str | None,
    event_types: list[str] | None,
    actor: str | None,
    since: str | None,
    until: str | None,
) -> tuple[str, list[str]]:
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
    if event_types:
        clauses.append(f"event_type IN ({','.join('?' * len(event_types))})")
        params.extend(event_types)
    if actor is not None:
        clauses.append("actor = ?")
        params.append(actor)
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("created_at <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_events(
    *,
    connection_id: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    event_types: list[str] | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    where, params = _build_where(
        connection_id=connection_id, resource_id=resource_id, event_type=event_type, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, min(limit, 500), max(offset, 0)),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def count_events(
    *,
    connection_id: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    event_types: list[str] | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> int:
    where, params = _build_where(
        connection_id=connection_id, resource_id=resource_id, event_type=event_type, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    conn = _conn()
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM events {where}", params).fetchone()
        return row["n"]
    finally:
        conn.close()


def aggregate_by_type(
    *,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    event_types: list[str] | None = None,
) -> list[dict]:
    """[{event_type, count}], for the "breakdown by event type" pie chart."""
    where, params = _build_where(
        connection_id=None, resource_id=None, event_type=None, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT event_type, COUNT(*) AS count FROM events {where} GROUP BY event_type ORDER BY count DESC", params
        ).fetchall()
        return [{"event_type": r["event_type"], "count": r["count"]} for r in rows]
    finally:
        conn.close()


def count_distinct_actors(
    *,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    event_types: list[str] | None = None,
) -> int:
    """Distinct-actor count honoring every filter, including `actor` itself —
    unlike aggregate_by_actor below, whose "most active users" ranking
    deliberately ignores the actor filter so it keeps comparing everyone,
    not just whoever the table happens to be filtered to."""
    where, params = _build_where(
        connection_id=None, resource_id=None, event_type=None, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    conn = _conn()
    try:
        row = conn.execute(f"SELECT COUNT(DISTINCT actor) AS n FROM events {where}", params).fetchone()
        return row["n"]
    finally:
        conn.close()


def aggregate_by_actor(
    *,
    since: str | None = None,
    until: str | None = None,
    event_types: list[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    """[{actor, count}] ordered by activity volume, for "most active users"."""
    where, params = _build_where(
        connection_id=None, resource_id=None, event_type=None, event_types=event_types,
        actor=None, since=since, until=until,
    )
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT actor, COUNT(*) AS count FROM events {where} GROUP BY actor ORDER BY count DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [{"actor": r["actor"], "count": r["count"]} for r in rows]
    finally:
        conn.close()


def aggregate_by_day(
    *,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    event_types: list[str] | None = None,
) -> list[dict]:
    """[{day, count}] (day = "YYYY-MM-DD", UTC) for the events-over-time bar
    chart. created_at is stored as an ISO-8601 string, so a plain substring
    slice is enough to bucket by day without needing SQLite's date functions."""
    where, params = _build_where(
        connection_id=None, resource_id=None, event_type=None, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count FROM events {where} "
            "GROUP BY day ORDER BY day ASC",
            params,
        ).fetchall()
        return [{"day": r["day"], "count": r["count"]} for r in rows]
    finally:
        conn.close()


def list_distinct_actors() -> list[str]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT DISTINCT actor FROM events ORDER BY actor COLLATE NOCASE").fetchall()
        return [r["actor"] for r in rows]
    finally:
        conn.close()
