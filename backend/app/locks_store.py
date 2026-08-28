"""Document check-out / check-in soft-lock store.

For providers that have native locking (FileNet supports CHECKOUT/CHECKIN
at the CE API level), the provider itself enforces the lock.  For providers
without native locking (S3, Azure Blob, Local Disk, Google Drive, etc.) this
module acts as the lock registry — a simple table that records who checked
out a document and when.

The router enforces that only the lock holder can upload a new version while
the lock is active.  Abandoning a checkout without checking back in is
possible by admins (force-release) so work is never permanently blocked.
"""

import datetime
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "locks.db"


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
            CREATE TABLE IF NOT EXISTS locks (
                id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                locked_by TEXT NOT NULL,
                locked_at TEXT NOT NULL,
                comment TEXT
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_locks_resource "
            "ON locks (connection_id, resource_id)"
        )
        conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "locked_by": row["locked_by"],
        "locked_at": row["locked_at"],
        "comment": row["comment"],
    }


def checkout(connection_id: str, resource_id: str, locked_by: str, comment: str | None = None) -> dict:
    """Raises sqlite3.IntegrityError if already locked (caller converts to HTTP 409)."""
    lock_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO locks (id, connection_id, resource_id, locked_by, locked_at, comment) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (lock_id, connection_id, resource_id, locked_by, now, comment),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM locks WHERE id = ?", (lock_id,)).fetchone()
        return _row(row)
    finally:
        conn.close()


def checkin(connection_id: str, resource_id: str) -> None:
    """Releases the lock regardless of who holds it (router validates ownership before calling)."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM locks WHERE connection_id = ? AND resource_id = ?", (connection_id, resource_id))
        conn.commit()
    finally:
        conn.close()


def get_lock(connection_id: str, resource_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM locks WHERE connection_id = ? AND resource_id = ?", (connection_id, resource_id)
        ).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def list_locks(connection_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM locks WHERE connection_id = ? ORDER BY locked_at DESC", (connection_id,)
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def delete_for_connection(connection_id: str) -> None:
    """Remove all lock records belonging to a connection (called when a connection is deleted)."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM locks WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()
