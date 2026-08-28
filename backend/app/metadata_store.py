"""Document classes (types) and per-resource custom metadata.

A document class defines a schema: a list of typed fields (text, number,
date, boolean, select).  When a user uploads a file they can assign it a
class, and the UI then shows the matching fields for the user to fill in.
Values are stored as a JSON blob keyed by the field key.

This is intentionally FileDrive-native (not mapped to FileNet CE document
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
                 class_id: str | None, values: dict) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT id FROM resource_metadata WHERE connection_id = ? AND resource_id = ?",
            (connection_id, resource_id),
        ).fetchone()
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
        conn.commit()
        row = conn.execute(
            "SELECT * FROM resource_metadata WHERE connection_id = ? AND resource_id = ?",
            (connection_id, resource_id),
        ).fetchone()
        return _meta_row(row)
    finally:
        conn.close()


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM resource_metadata WHERE connection_id = ? AND resource_id = ?",
                     (connection_id, resource_id))
        conn.commit()
    finally:
        conn.close()


def delete_for_connection(connection_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM resource_metadata WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()
