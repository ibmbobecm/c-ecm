"""Notification storage (Repository) — plain CRUD over what notification_service
decides to write here. Kept separate from that decision logic the same way
events_store.py is kept separate from activity_service.py's Observer layer.
"""

import datetime
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "notifications.db"


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
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                event_id TEXT,
                message TEXT NOT NULL,
                read_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_owner ON notifications (owner, created_at)")
        # A per-rule notification-preferences table (mute this event type,
        # mute this connection, ...) belongs here later — deliberately not
        # built yet: notification_service.py's hardcoded _NOTIFIABLE_EVENT_TYPES
        # is today's whole policy, so a rules table with no reader or writer
        # anywhere would just be dead schema.
        conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "owner": row["owner"],
        "event_id": row["event_id"],
        "message": row["message"],
        "read_at": row["read_at"],
        "created_at": row["created_at"],
    }


def create(owner: str, event_id: str | None, message: str) -> dict:
    nid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO notifications (id, owner, event_id, message, read_at, created_at) VALUES (?, ?, ?, ?, NULL, ?)",
            (nid, owner, event_id, message, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (nid,)).fetchone()
    finally:
        conn.close()
    return _row(row)


def list_for_owner(owner: str, unread_only: bool = False, limit: int = 50) -> list[dict]:
    conn = _conn()
    try:
        clause = "AND read_at IS NULL" if unread_only else ""
        rows = conn.execute(
            f"SELECT * FROM notifications WHERE owner = ? {clause} ORDER BY created_at DESC LIMIT ?",
            (owner, min(limit, 200)),
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def unread_count(owner: str) -> int:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE owner = ? AND read_at IS NULL", (owner,)
        ).fetchone()
        return row["c"]
    finally:
        conn.close()


def mark_read(notification_id: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (datetime.datetime.now(datetime.timezone.utc).isoformat(), notification_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_all_read(owner: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE notifications SET read_at = ? WHERE owner = ? AND read_at IS NULL",
            (datetime.datetime.now(datetime.timezone.utc).isoformat(), owner),
        )
        conn.commit()
    finally:
        conn.close()
