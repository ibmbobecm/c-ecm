"""Signature-request tracking (Repository pattern, same shape as every
other *_store.py module) — records what was sent to DocuSign for which
resource, and its last known status. The actual signing happens entirely
on DocuSign's side; this module never touches document content.
"""

import datetime
import json
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "esignature.db"


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
            CREATE TABLE IF NOT EXISTS esignature_requests (
                id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_name TEXT,
                envelope_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                signers_json TEXT NOT NULL DEFAULT '[]',
                subject TEXT,
                requested_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                signed_version_number INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_esig_resource ON esignature_requests (connection_id, resource_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_esig_envelope ON esignature_requests (envelope_id)")
        conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "resource_name": row["resource_name"],
        "envelope_id": row["envelope_id"],
        "status": row["status"],
        "signers": json.loads(row["signers_json"]),
        "subject": row["subject"],
        "requested_by": row["requested_by"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "signed_version_number": row["signed_version_number"],
    }


def create(
    connection_id: str, resource_id: str, resource_type: str, resource_name: str | None,
    envelope_id: str, signers: list[dict], subject: str, requested_by: str,
) -> dict:
    rid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO esignature_requests "
            "(id, connection_id, resource_id, resource_type, resource_name, envelope_id, status, signers_json, "
            "subject, requested_by, created_at) VALUES (?, ?, ?, ?, ?, ?, 'sent', ?, ?, ?, ?)",
            (rid, connection_id, resource_id, resource_type, resource_name, envelope_id,
             json.dumps(signers), subject, requested_by, now),
        )
        conn.commit()
        return _row(conn.execute("SELECT * FROM esignature_requests WHERE id = ?", (rid,)).fetchone())
    finally:
        conn.close()


def get(request_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM esignature_requests WHERE id = ?", (request_id,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def get_by_envelope_id(envelope_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM esignature_requests WHERE envelope_id = ?", (envelope_id,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def list_for_resource(connection_id: str, resource_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM esignature_requests WHERE connection_id = ? AND resource_id = ? ORDER BY created_at DESC",
            (connection_id, resource_id),
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def list_all(*, connection_id: str | None = None, status: str | None = None) -> list[dict]:
    clauses, params = [], []
    if connection_id:
        clauses.append("connection_id = ?")
        params.append(connection_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _conn()
    try:
        rows = conn.execute(f"SELECT * FROM esignature_requests {where} ORDER BY created_at DESC", params).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def update_status(request_id: str, status: str, *, completed: bool = False, signed_version_number: int | None = None) -> None:
    conn = _conn()
    try:
        if completed:
            conn.execute(
                "UPDATE esignature_requests SET status = ?, completed_at = ?, signed_version_number = ? WHERE id = ?",
                (status, datetime.datetime.now(datetime.timezone.utc).isoformat(), signed_version_number, request_id),
            )
        else:
            conn.execute("UPDATE esignature_requests SET status = ? WHERE id = ?", (status, request_id))
        conn.commit()
    finally:
        conn.close()


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM esignature_requests WHERE connection_id = ? AND resource_id = ?", (connection_id, resource_id))
        conn.commit()
    finally:
        conn.close()


def delete_for_connection(connection_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM esignature_requests WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()
