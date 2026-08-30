"""Tags/custom metadata — deliberately C-ECM-native and backend-agnostic
(no StorageProvider involvement at all), mirroring how connections_store.py
already sits outside the provider Strategy hierarchy. No backend's native
tag concept (Drive Labels, Box metadata templates, Alfresco Aspects, ...)
maps 1:1 across all nine providers, so this is the uniform layer that works
identically regardless of which connection a resource lives on.
"""

import datetime
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "tags.db"


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
            CREATE TABLE IF NOT EXISTS tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                color TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_name_nocase ON tags (name COLLATE NOCASE)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_tags (
                id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                tagged_by TEXT NOT NULL,
                tagged_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_resource_tags_unique "
            "ON resource_tags (connection_id, resource_id, tag_id)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_tags_lookup ON resource_tags (connection_id, resource_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_tags_by_tag ON resource_tags (tag_id)")
        conn.commit()
    finally:
        conn.close()


def _tag_row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "color": row["color"], "created_at": row["created_at"]}


def list_tags() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM tags ORDER BY name COLLATE NOCASE").fetchall()
        return [_tag_row(r) for r in rows]
    finally:
        conn.close()


def get_or_create_tag(name: str, color: str) -> dict:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if row:
            return _tag_row(row)
        tag_id = uuid.uuid4().hex
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute("INSERT INTO tags (id, name, color, created_at) VALUES (?, ?, ?, ?)", (tag_id, name, color, now))
        conn.commit()
        row = conn.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
        return _tag_row(row)
    finally:
        conn.close()


def delete_tag(tag_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM resource_tags WHERE tag_id = ?", (tag_id,))
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        conn.commit()
    finally:
        conn.close()


def tag_resource(connection_id: str, resource_id: str, resource_type: str, tag_id: str, tagged_by: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO resource_tags (id, connection_id, resource_id, resource_type, tag_id, tagged_by, tagged_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                connection_id,
                resource_id,
                resource_type,
                tag_id,
                tagged_by,
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def untag_resource(connection_id: str, resource_id: str, tag_id: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM resource_tags WHERE connection_id = ? AND resource_id = ? AND tag_id = ?",
            (connection_id, resource_id, tag_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_tags_for_resource(connection_id: str, resource_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT t.* FROM tags t JOIN resource_tags rt ON rt.tag_id = t.id "
            "WHERE rt.connection_id = ? AND rt.resource_id = ? ORDER BY t.name COLLATE NOCASE",
            (connection_id, resource_id),
        ).fetchall()
        return [_tag_row(r) for r in rows]
    finally:
        conn.close()


def get_tags_for_resources(connection_id: str, resource_ids: list[str]) -> dict[str, list[dict]]:
    """Batch form of get_tags_for_resource, for annotating a whole folder
    listing without N+1 queries — every FolderContents response needs this."""
    if not resource_ids:
        return {}
    conn = _conn()
    try:
        placeholders = ",".join("?" * len(resource_ids))
        rows = conn.execute(
            f"SELECT rt.resource_id, t.* FROM tags t JOIN resource_tags rt ON rt.tag_id = t.id "
            f"WHERE rt.connection_id = ? AND rt.resource_id IN ({placeholders}) ORDER BY t.name COLLATE NOCASE",
            (connection_id, *resource_ids),
        ).fetchall()
        result: dict[str, list[dict]] = {rid: [] for rid in resource_ids}
        for row in rows:
            result[row["resource_id"]].append(_tag_row(row))
        return result
    finally:
        conn.close()


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    """Called when a single file/folder is permanently deleted — same
    orphaning concern as delete_for_connection, scoped to one resource."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM resource_tags WHERE connection_id = ? AND resource_id = ?", (connection_id, resource_id))
        conn.commit()
    finally:
        conn.close()


def delete_for_connection(connection_id: str) -> None:
    """Called when a connection is removed — its tag attachments would
    otherwise reference a connection_id nothing can ever resolve again.
    Tag *definitions* are shared across connections, so only the
    attachments are removed, not the tags themselves."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM resource_tags WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()


def get_resources_for_tag(tag_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT connection_id, resource_id, resource_type, tagged_at FROM resource_tags WHERE tag_id = ? ORDER BY tagged_at DESC",
            (tag_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
