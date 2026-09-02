"""Per-resource access grants — the store behind access_control.py's
require_resource_level(). A grant targets either a user or a group
(principal_type/principal_id) on a specific (connection_id, resource_id)
and gives them "view" (read-only) or "edit" (read+write) there. A grant
on a folder is meant to cascade to everything inside it — that walk
happens in access_control.py, not here; this module only stores and
queries the raw grant rows, the same "store owns persistence, router/
service owns the logic built on top of it" split as every other store in
this codebase (e.g. groups_store.py vs auth.require_feature).

Restrictions are opt-in: a connection with zero rows here behaves exactly
as it did before this feature existed (any authenticated user has full
access). connection_has_any_grants() is the cheap fast-path check
require_resource_level() uses to skip the ancestor walk entirely for the
common case where nobody has restricted anything.
"""

import datetime
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "resource_permissions.db"


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_permissions (
                id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                principal_type TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                level TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_resource_permissions_lookup "
            "ON resource_permissions (connection_id, resource_id)"
        )
        # Backs connection_has_any_grants()'s SELECT EXISTS — a plain
        # index on connection_id alone (the lookup index above is
        # (connection_id, resource_id), still usable, but a narrower
        # single-column index makes the "does this connection have ANY
        # row at all" check as cheap as possible, since it's the one
        # query every gated route pays on every request).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_resource_permissions_connection "
            "ON resource_permissions (connection_id)"
        )
        conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "principal_type": row["principal_type"],
        "principal_id": row["principal_id"],
        "level": row["level"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
    }


def connection_has_any_grants(connection_id: str) -> bool:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM resource_permissions WHERE connection_id = ? LIMIT 1", (connection_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def list_for_resource(connection_id: str, resource_id: str) -> list[dict]:
    """Grants set directly on this resource (not inherited from an
    ancestor) — what the access-grants UI for this specific resource
    shows/edits."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM resource_permissions WHERE connection_id = ? AND resource_id = ? ORDER BY created_at",
            (connection_id, resource_id),
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def grants_for_resource_batch(connection_id: str, resource_ids: list[str]) -> dict[str, list[dict]]:
    """One query for every id in an ancestor chain, instead of one query
    per ancestor level — used by access_control.py's walk. Returns
    {resource_id: [grant, ...]} only for ids that actually have grants."""
    if not resource_ids:
        return {}
    conn = _conn()
    try:
        placeholders = ",".join("?" * len(resource_ids))
        rows = conn.execute(
            f"SELECT * FROM resource_permissions WHERE connection_id = ? AND resource_id IN ({placeholders})",
            (connection_id, *resource_ids),
        ).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["resource_id"], []).append(_row(r))
        return out
    finally:
        conn.close()


def create(connection_id: str, resource_id: str, resource_type: str, principal_type: str,
           principal_id: str, level: str, created_by: str | None) -> dict:
    grant_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO resource_permissions (id, connection_id, resource_id, resource_type, principal_type, "
            "principal_id, level, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (grant_id, connection_id, resource_id, resource_type, principal_type, principal_id, level, now, created_by),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM resource_permissions WHERE id = ?", (grant_id,)).fetchone()
        return _row(row)
    finally:
        conn.close()


def get(grant_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM resource_permissions WHERE id = ?", (grant_id,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def delete(grant_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM resource_permissions WHERE id = ?", (grant_id,))
        conn.commit()
    finally:
        conn.close()


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM resource_permissions WHERE connection_id = ? AND resource_id = ?",
            (connection_id, resource_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_for_connection(connection_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM resource_permissions WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()


def delete_for_group(group_id: str) -> None:
    """Called when a group is deleted, so grants pointing at it don't
    silently become permanent open-ended holes — see groups_store.py's
    delete_group; wired in from routers/groups.py, not groups_store.py
    itself, to keep that module from needing to know about this one."""
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM resource_permissions WHERE principal_type = 'group' AND principal_id = ?", (group_id,)
        )
        conn.commit()
    finally:
        conn.close()
