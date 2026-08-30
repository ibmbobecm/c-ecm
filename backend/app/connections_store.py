"""Persistent storage for backend connections — a user connects FileNet,
Alfresco, Google Drive, etc. once (via Settings), and it's remembered across
logins, not just cached for one session's lifetime like the old per-login
model was. Plaintext SQLite locally, same as this app's other credential
handling; flagged for a real secrets vault later, not addressed now.
"""

import datetime
import json
import sqlite3
import uuid

from .config import CONNECTIONS_DB_PATH, DATA_DIR


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CONNECTIONS_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")  # wait for a concurrent writer instead of failing instantly
    return conn


class DuplicateConnectionNameError(Exception):
    """Raised when a connection's display_name collides (case-insensitively)
    with an existing one — names double as how a user tells connections
    apart in the switcher, so two "IBM FileNet"s isn't just untidy, it's
    genuinely ambiguous."""

    def __init__(self, display_name: str):
        self.display_name = display_name
        super().__init__(f"A connection named \"{display_name}\" already exists")


def _dedupe_existing_names(conn: sqlite3.Connection) -> None:
    """One-time migration: rows created before this uniqueness rule existed
    may already share a display_name. Keep the older row's name as-is and
    disambiguate any later same-named row, so the UNIQUE index below can
    actually be created on the data that's already there."""
    rows = conn.execute("SELECT id, display_name FROM connections ORDER BY created_at").fetchall()
    seen_lower: set[str] = set()
    for row in rows:
        name = row["display_name"]
        lower = name.lower()
        if lower not in seen_lower:
            seen_lower.add(lower)
            continue
        n = 2
        while f"{name} ({n})".lower() in seen_lower:
            n += 1
        new_name = f"{name} ({n})"
        conn.execute("UPDATE connections SET display_name = ? WHERE id = ?", (new_name, row["id"]))
        seen_lower.add(new_name.lower())


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connections (
                id TEXT PRIMARY KEY,
                provider_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                creds_json TEXT NOT NULL,
                identity TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _dedupe_existing_names(conn)
        # A unique index (not an inline column constraint) so this applies
        # cleanly to a table that already existed before this check did.
        # COLLATE NOCASE so "IBM FileNet" and "ibm filenet" still collide —
        # they'd be indistinguishable in the connection switcher otherwise.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_connections_name_nocase "
            "ON connections (display_name COLLATE NOCASE)"
        )
        conn.commit()
    finally:
        conn.close()


def name_exists(display_name: str) -> bool:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM connections WHERE display_name = ? COLLATE NOCASE", (display_name,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def unique_display_name(base: str) -> str:
    """Returns `base` if it's free, otherwise the first "`base` (N)" that is —
    used where there's no form to send a rejection back to (the OAuth
    callback lands after the user already granted access on Google's/etc.
    own page, so failing outright there would be a dead end)."""
    if not name_exists(base):
        return base
    n = 2
    while name_exists(f"{base} ({n})"):
        n += 1
    return f"{base} ({n})"


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "provider_key": row["provider_key"],
        "display_name": row["display_name"],
        "identity": row["identity"],
        "created_at": row["created_at"],
    }


def list_connections() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM connections ORDER BY created_at").fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_connection(connection_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_creds(connection_id: str) -> tuple[str, dict] | None:
    """Returns (provider_key, creds) or None."""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
        if row is None:
            return None
        return row["provider_key"], json.loads(row["creds_json"])
    finally:
        conn.close()


def create_connection(provider_key: str, display_name: str, creds: dict, identity: str) -> dict:
    conn_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        try:
            conn.execute(
                "INSERT INTO connections (id, provider_key, display_name, creds_json, identity, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conn_id, provider_key, display_name, json.dumps(creds), identity, now),
            )
        except sqlite3.IntegrityError:
            raise DuplicateConnectionNameError(display_name)
        conn.commit()
    finally:
        conn.close()
    return get_connection(conn_id)


def update_creds(connection_id: str, creds: dict) -> None:
    conn = _conn()
    try:
        conn.execute("UPDATE connections SET creds_json = ? WHERE id = ?", (json.dumps(creds), connection_id))
        conn.commit()
    finally:
        conn.close()


def delete_connection(connection_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM connections WHERE id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()
