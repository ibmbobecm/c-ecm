"""Comments — a FileDrive-native layer (Repository pattern) so commenting
works uniformly even on backends with no native comments at all (M-Files)
or comments with no public API (Dropbox). Comment creation is wired to the
activity log by the router, not this module — this is pure storage.
"""

import datetime
import json
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "comments.db"


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
            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                parent_comment_id TEXT,
                body TEXT NOT NULL,
                mentioned_users_json TEXT NOT NULL DEFAULT '[]',
                resolved_at TEXT,
                resolved_by TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                edited_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_resource ON comments (connection_id, resource_id, created_at)")
        conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "parent_comment_id": row["parent_comment_id"],
        "body": row["body"],
        "mentioned_users": json.loads(row["mentioned_users_json"]),
        "resolved_at": row["resolved_at"],
        "resolved_by": row["resolved_by"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "edited_at": row["edited_at"],
    }


def create(
    connection_id: str,
    resource_id: str,
    resource_type: str,
    body: str,
    created_by: str,
    parent_comment_id: str | None = None,
    mentioned_users: list[str] | None = None,
) -> dict:
    cid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO comments (id, connection_id, resource_id, resource_type, parent_comment_id, body, "
            "mentioned_users_json, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, connection_id, resource_id, resource_type, parent_comment_id, body, json.dumps(mentioned_users or []), created_by, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM comments WHERE id = ?", (cid,)).fetchone()
    finally:
        conn.close()
    return _row(row)


def count_for_resources(connection_id: str, resource_ids: list[str]) -> dict[str, int]:
    """Batch comment counts for a whole folder listing, mirroring
    tags_store.get_tags_for_resources — avoids one query per visible item."""
    if not resource_ids:
        return {}
    conn = _conn()
    try:
        placeholders = ",".join("?" * len(resource_ids))
        rows = conn.execute(
            f"SELECT resource_id, COUNT(*) AS c FROM comments WHERE connection_id = ? AND resource_id IN ({placeholders}) "
            f"GROUP BY resource_id",
            (connection_id, *resource_ids),
        ).fetchall()
        counts = {rid: 0 for rid in resource_ids}
        for row in rows:
            counts[row["resource_id"]] = row["c"]
        return counts
    finally:
        conn.close()


def list_for_resource(connection_id: str, resource_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM comments WHERE connection_id = ? AND resource_id = ? ORDER BY created_at ASC",
            (connection_id, resource_id),
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def get(comment_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def edit(comment_id: str, body: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE comments SET body = ?, edited_at = ? WHERE id = ?",
            (body, datetime.datetime.now(datetime.timezone.utc).isoformat(), comment_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_resolved(comment_id: str, resolved: bool, resolved_by: str | None) -> None:
    conn = _conn()
    try:
        if resolved:
            conn.execute(
                "UPDATE comments SET resolved_at = ?, resolved_by = ? WHERE id = ?",
                (datetime.datetime.now(datetime.timezone.utc).isoformat(), resolved_by, comment_id),
            )
        else:
            conn.execute("UPDATE comments SET resolved_at = NULL, resolved_by = NULL WHERE id = ?", (comment_id,))
        conn.commit()
    finally:
        conn.close()


def delete(comment_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM comments WHERE id = ? OR parent_comment_id = ?", (comment_id, comment_id))
        conn.commit()
    finally:
        conn.close()


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM comments WHERE connection_id = ? AND resource_id = ?", (connection_id, resource_id))
        conn.commit()
    finally:
        conn.close()


def delete_for_connection(connection_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM comments WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()
