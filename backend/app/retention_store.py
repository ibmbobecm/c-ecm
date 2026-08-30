"""Retention & Disposition Policy Engine.

Retention policies define how long documents must be kept and what
happens when they expire:
  - action: 'review'      — transition to 'under_review', notify admin
  - action: 'archive'     — mark as archived (future: move to cold storage)
  - action: 'auto_delete' — trash the document automatically

Policies are applied at the connection level and can be overridden per
document class.  Legal holds override all policies.

The scheduler (called from main.py lifespan via APScheduler or a daily
background thread) evaluates all tracked resources each day.
"""

import datetime
import json
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "retention.db"


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")  # wait for a concurrent writer instead of failing instantly
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS retention_policies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    retention_days INTEGER NOT NULL,
    action TEXT NOT NULL DEFAULT 'review',
    class_id TEXT,
    connection_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rp_name ON retention_policies (name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS retention_records (
    id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL REFERENCES retention_policies(id) ON DELETE CASCADE,
    connection_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_name TEXT,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    legal_hold INTEGER NOT NULL DEFAULT 0,
    actioned_at TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rr_resource
    ON retention_records (connection_id, resource_id, policy_id);
CREATE INDEX IF NOT EXISTS idx_rr_due ON retention_records (due_date, status);
"""


def init_db() -> None:
    conn = _conn()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------- policies -------------------------------------------------------

def _policy_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "retention_days": row["retention_days"],
        "action": row["action"],
        "class_id": row["class_id"],
        "connection_id": row["connection_id"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


def list_policies() -> list[dict]:
    conn = _conn()
    try:
        return [_policy_row(r) for r in conn.execute("SELECT * FROM retention_policies ORDER BY name COLLATE NOCASE").fetchall()]
    finally:
        conn.close()


def get_policy(policy_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM retention_policies WHERE id = ?", (policy_id,)).fetchone()
        return _policy_row(row) if row else None
    finally:
        conn.close()


def create_policy(name: str, description: str | None, retention_days: int, action: str,
                  class_id: str | None = None, connection_id: str | None = None) -> dict:
    pid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO retention_policies (id, name, description, retention_days, action, class_id, connection_id, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (pid, name, description, retention_days, action, class_id, connection_id, now),
        )
        conn.commit()
        return _policy_row(conn.execute("SELECT * FROM retention_policies WHERE id = ?", (pid,)).fetchone())
    finally:
        conn.close()


def update_policy(policy_id: str, **kwargs) -> dict | None:
    allowed = {"name", "description", "retention_days", "action", "class_id", "connection_id", "active"}
    updates = [(k, v) for k, v in kwargs.items() if k in allowed and v is not None]
    if not updates:
        return get_policy(policy_id)
    conn = _conn()
    try:
        set_clause = ", ".join(f"{k} = ?" for k, _ in updates)
        conn.execute(f"UPDATE retention_policies SET {set_clause} WHERE id = ?", (*[v for _, v in updates], policy_id))
        conn.commit()
        row = conn.execute("SELECT * FROM retention_policies WHERE id = ?", (policy_id,)).fetchone()
        return _policy_row(row) if row else None
    finally:
        conn.close()


def delete_policy(policy_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM retention_records WHERE policy_id = ?", (policy_id,))
        conn.execute("DELETE FROM retention_policies WHERE id = ?", (policy_id,))
        conn.commit()
    finally:
        conn.close()


# ---------- records --------------------------------------------------------

def _rec_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "policy_id": row["policy_id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "resource_name": row["resource_name"],
        "due_date": row["due_date"],
        "status": row["status"],
        "legal_hold": bool(row["legal_hold"]),
        "actioned_at": row["actioned_at"],
        "created_at": row["created_at"],
    }


def enroll_resource(policy_id: str, connection_id: str, resource_id: str,
                    resource_type: str, resource_name: str | None,
                    start_date: datetime.datetime | None = None) -> dict:
    policy = get_policy(policy_id)
    if policy is None:
        raise ValueError(f"Policy {policy_id} not found")
    base = start_date or datetime.datetime.now(datetime.timezone.utc)
    due = base + datetime.timedelta(days=policy["retention_days"])
    rid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO retention_records "
            "(id, policy_id, connection_id, resource_id, resource_type, resource_name, due_date, status, legal_hold, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, ?)",
            (rid, policy_id, connection_id, resource_id, resource_type, resource_name, due.isoformat(), now),
        )
        conn.commit()
        return _rec_row(conn.execute("SELECT * FROM retention_records WHERE id = ?", (rid,)).fetchone())
    finally:
        conn.close()


def set_legal_hold(connection_id: str, resource_id: str, hold: bool) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE retention_records SET legal_hold = ? WHERE connection_id = ? AND resource_id = ?",
            (int(hold), connection_id, resource_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_records(*, connection_id: str | None = None, status: str | None = None,
                 due_before: str | None = None) -> list[dict]:
    clauses, params = [], []
    if connection_id:
        clauses.append("connection_id = ?")
        params.append(connection_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if due_before:
        clauses.append("due_date <= ?")
        params.append(due_before)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _conn()
    try:
        return [_rec_row(r) for r in conn.execute(
            f"SELECT * FROM retention_records {where} ORDER BY due_date", params
        ).fetchall()]
    finally:
        conn.close()


def mark_actioned(record_id: str, status: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "UPDATE retention_records SET status = ?, actioned_at = ? WHERE id = ?",
            (status, now, record_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_record(record_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM retention_records WHERE id = ?", (record_id,)).fetchone()
        return _rec_row(row) if row else None
    finally:
        conn.close()


def set_legal_hold_by_record_id(record_id: str, hold: bool) -> None:
    conn = _conn()
    try:
        conn.execute("UPDATE retention_records SET legal_hold = ? WHERE id = ?", (1 if hold else 0, record_id))
        conn.commit()
    finally:
        conn.close()


def run_due_check() -> list[dict]:
    """Called by the scheduler.  Returns records that are due and not on legal hold."""
    today = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM retention_records WHERE status = 'active' AND legal_hold = 0 AND due_date <= ?",
            (today,),
        ).fetchall()
        return [_rec_row(r) for r in rows]
    finally:
        conn.close()
