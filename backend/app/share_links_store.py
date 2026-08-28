"""FileDrive's own share-link registry — the default (and, for now, only)
implementation behind StorageProvider.create_share_link()/list_share_links()/
revoke_share_link(). None of the nine backends' native sharing is called
here; this works identically for all of them because it's built entirely
on operations every provider already implements (get_file/get_content),
fronted by a token FileDrive itself issues and resolves. A provider can
still override the three methods on StorageProvider if it later wants to
hand back a real backend-hosted link instead — this module doesn't need to
know or care if that happens.

The public, unauthenticated GET /share/{token} route (routers/sharing.py)
is the only thing that reads this table without a FileDrive login — that
route's whole job is to turn a valid, unexpired token back into
(connection_id, resource_id) and then make the ordinary authenticated
provider calls on the visitor's behalf.
"""

import datetime
import hashlib
import hmac
import secrets
import sqlite3
import uuid

from .config import DATA_DIR
from .storage_providers.base import ShareLink

_DB_PATH = DATA_DIR / "share_links.db"


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
            CREATE TABLE IF NOT EXISTS share_links (
                id TEXT PRIMARY KEY,
                token TEXT NOT NULL UNIQUE,
                connection_id TEXT NOT NULL,
                provider_key TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                role TEXT NOT NULL,
                expires_at TEXT,
                password_hash TEXT,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_share_links_resource ON share_links (connection_id, resource_id)")
        conn.commit()
    finally:
        conn.close()


def _hash_password(password: str) -> str:
    # A lightweight deterrent, not an auth-grade KDF — same plaintext-
    # locally stance this app takes everywhere else, just not literally
    # bare plaintext for something that leaves the machine in a URL. Salted
    # per-link (stored as "salt$digest" in the one existing column, so this
    # doesn't need a schema migration) so two links with the same password
    # don't produce the same hash, and so a precomputed rainbow table for
    # common passwords doesn't work across every link at once.
    salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    salt, sep, digest = stored.partition("$")
    if not sep:
        return False
    candidate = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate, digest)


def _row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "token": row["token"],
        "connection_id": row["connection_id"],
        "provider_key": row["provider_key"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "role": row["role"],
        "expires_at": row["expires_at"],
        "password_protected": row["password_hash"] is not None,
        "created_at": row["created_at"],
        "revoked_at": row["revoked_at"],
    }


def _to_share_link(row: sqlite3.Row, base_url: str) -> ShareLink:
    return ShareLink(
        id=row["id"],
        url=f"{base_url}/share/{row['token']}",
        role=row["role"],
        expires_at=datetime.datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        password_protected=row["password_hash"] is not None,
    )


def create(
    connection_id: str,
    provider_key: str,
    resource_id: str,
    resource_type: str,
    role: str,
    expires_at: datetime.datetime | None,
    password: str | None,
) -> ShareLink:
    from .config import API_BASE_URL

    link_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(24)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO share_links (id, token, connection_id, provider_key, resource_id, resource_type, role, "
            "expires_at, password_hash, created_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                link_id,
                token,
                connection_id,
                provider_key,
                resource_id,
                resource_type,
                role,
                expires_at.isoformat() if expires_at else None,
                _hash_password(password) if password else None,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM share_links WHERE id = ?", (link_id,)).fetchone()
    finally:
        conn.close()
    return _to_share_link(row, API_BASE_URL)


def list_for_resource(connection_id: str, resource_id: str) -> list[ShareLink]:
    from .config import API_BASE_URL

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM share_links WHERE connection_id = ? AND resource_id = ? AND revoked_at IS NULL ORDER BY created_at DESC",
            (connection_id, resource_id),
        ).fetchall()
        return [_to_share_link(r, API_BASE_URL) for r in rows]
    finally:
        conn.close()


def revoke(connection_id: str, link_id: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE share_links SET revoked_at = ? WHERE id = ? AND connection_id = ?",
            (datetime.datetime.now(datetime.timezone.utc).isoformat(), link_id, connection_id),
        )
        conn.commit()
    finally:
        conn.close()


def resolve(token: str) -> dict | None:
    """Looks up a token for the public GET /share/{token} route. Returns
    None for a token that doesn't exist, is revoked, or has expired — the
    router doesn't need to distinguish which, since the visitor-facing
    response is the same "this link isn't available" either way."""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM share_links WHERE token = ?", (token,)).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        if row["expires_at"] and datetime.datetime.fromisoformat(row["expires_at"]) < datetime.datetime.now(datetime.timezone.utc):
            return None
        return _row(row)
    finally:
        conn.close()


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM share_links WHERE connection_id = ? AND resource_id = ?", (connection_id, resource_id))
        conn.commit()
    finally:
        conn.close()


def delete_for_connection(connection_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM share_links WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()


def check_password(token_row: dict, password: str | None) -> bool:
    conn = _conn()
    try:
        row = conn.execute("SELECT password_hash FROM share_links WHERE id = ?", (token_row["id"],)).fetchone()
        if row["password_hash"] is None:
            return True
        return password is not None and _verify_password(password, row["password_hash"])
    finally:
        conn.close()
