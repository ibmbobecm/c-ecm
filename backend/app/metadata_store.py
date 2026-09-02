"""Document classes (types) and per-resource custom metadata.

A document class defines a schema: a list of typed fields (text, number,
date, boolean, select).  When a user uploads a file they can assign it a
class, and the UI then shows the matching fields for the user to fill in.
Values are stored as a JSON blob keyed by the field key.

This is intentionally C-ECM-native (not mapped to FileNet CE document
classes or Alfresco content models in this release) — it works identically
across all nine providers.  A future provider override can sync to the
native class system if needed.
"""

import datetime
import json
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "metadata.db"


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")  # wait for a concurrent writer instead of failing instantly
    return conn


# ---------- schema ---------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_classes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    fields_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dc_name ON document_classes (name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS resource_metadata (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    class_id TEXT REFERENCES document_classes(id) ON DELETE SET NULL,
    values_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rm_resource
    ON resource_metadata (connection_id, resource_id);
CREATE INDEX IF NOT EXISTS idx_rm_class ON resource_metadata (class_id);

CREATE TABLE IF NOT EXISTS resource_metadata_history (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    old_class_id TEXT,
    new_class_id TEXT,
    old_values_json TEXT NOT NULL DEFAULT '{}',
    new_values_json TEXT NOT NULL DEFAULT '{}',
    changed_by TEXT,
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rmh_resource
    ON resource_metadata_history (connection_id, resource_id, changed_at);
"""


def init_db() -> None:
    conn = _conn()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------- document classes -----------------------------------------------

def _class_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "fields": json.loads(row["fields_json"]),
        "created_at": row["created_at"],
    }


def list_classes() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM document_classes ORDER BY name COLLATE NOCASE").fetchall()
        return [_class_row(r) for r in rows]
    finally:
        conn.close()


def get_class(class_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM document_classes WHERE id = ?", (class_id,)).fetchone()
        return _class_row(row) if row else None
    finally:
        conn.close()


def create_class(name: str, description: str | None, fields: list[dict]) -> dict:
    cid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO document_classes (id, name, description, fields_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (cid, name, description, json.dumps(fields), now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM document_classes WHERE id = ?", (cid,)).fetchone()
        return _class_row(row)
    finally:
        conn.close()


def update_class(class_id: str, *, name: str | None = None, description: str | None = None,
                 fields: list[dict] | None = None) -> dict | None:
    conn = _conn()
    try:
        updates = []
        if name is not None:
            updates.append(("name", name))
        if description is not None:
            updates.append(("description", description))
        if fields is not None:
            updates.append(("fields_json", json.dumps(fields)))
        if updates:
            set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
            conn.execute(f"UPDATE document_classes SET {set_clause} WHERE id = ?",
                         (*[v for _, v in updates], class_id))
            conn.commit()
        row = conn.execute("SELECT * FROM document_classes WHERE id = ?", (class_id,)).fetchone()
        return _class_row(row) if row else None
    finally:
        conn.close()


def delete_class(class_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM document_classes WHERE id = ?", (class_id,))
        conn.commit()
    finally:
        conn.close()


# ---------- resource metadata values ---------------------------------------

def _meta_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "class_id": row["class_id"],
        "values": json.loads(row["values_json"]),
        "updated_at": row["updated_at"],
    }


def get_metadata(connection_id: str, resource_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM resource_metadata WHERE connection_id = ? AND resource_id = ?",
            (connection_id, resource_id),
        ).fetchone()
        return _meta_row(row) if row else None
    finally:
        conn.close()


def set_metadata(connection_id: str, resource_id: str, resource_type: str,
                 class_id: str | None, values: dict, *, actor: str | None = None) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT class_id, values_json FROM resource_metadata WHERE connection_id = ? AND resource_id = ?",
            (connection_id, resource_id),
        ).fetchone()
        old_class_id = existing["class_id"] if existing else None
        old_values = existing["values_json"] if existing else "{}"
        if existing:
            conn.execute(
                "UPDATE resource_metadata SET class_id = ?, values_json = ?, updated_at = ? "
                "WHERE connection_id = ? AND resource_id = ?",
                (class_id, json.dumps(values), now, connection_id, resource_id),
            )
        else:
            rid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO resource_metadata (id, connection_id, resource_id, resource_type, class_id, values_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rid, connection_id, resource_id, resource_type, class_id, json.dumps(values), now),
            )
        # Skip a history row entirely when nothing actually changed (e.g. an
        # edit opened and saved with no edits) -- comparing the serialized
        # JSON is fine here since both sides go through the same
        # json.dumps/loads round-trip, so equal dicts always serialize
        # identically.
        new_values_json = json.dumps(values)
        if old_class_id != class_id or old_values != new_values_json:
            conn.execute(
                "INSERT INTO resource_metadata_history "
                "(id, connection_id, resource_id, resource_type, old_class_id, new_class_id, "
                "old_values_json, new_values_json, changed_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, connection_id, resource_id, resource_type, old_class_id, class_id,
                 old_values, new_values_json, actor, now),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM resource_metadata WHERE connection_id = ? AND resource_id = ?",
            (connection_id, resource_id),
        ).fetchone()
        return _meta_row(row)
    finally:
        conn.close()


def _history_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "old_class_id": row["old_class_id"],
        "new_class_id": row["new_class_id"],
        "old_values": json.loads(row["old_values_json"]),
        "new_values": json.loads(row["new_values_json"]),
        "changed_by": row["changed_by"],
        "changed_at": row["changed_at"],
    }


def list_metadata_history(connection_id: str, resource_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM resource_metadata_history WHERE connection_id = ? AND resource_id = ? "
            "ORDER BY changed_at DESC",
            (connection_id, resource_id),
        ).fetchall()
        return [_history_row(r) for r in rows]
    finally:
        conn.close()


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM resource_metadata WHERE connection_id = ? AND resource_id = ?",
                     (connection_id, resource_id))
        conn.execute("DELETE FROM resource_metadata_history WHERE connection_id = ? AND resource_id = ?",
                     (connection_id, resource_id))
        conn.commit()
    finally:
        conn.close()


def delete_for_connection(connection_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM resource_metadata WHERE connection_id = ?", (connection_id,))
        conn.execute("DELETE FROM resource_metadata_history WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()
