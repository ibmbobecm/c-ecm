"""Multi-user account store — replaces the single APP_USERNAME/APP_PASSWORD
config with a proper users + roles table.  Roles are stored as a JSON array
on the user row; the role guard in auth.py reads them at request time via
CurrentUser so no additional DB round-trip happens per-request.

Passwords are stored as bcrypt hashes.  The first call to init_db() seeds
the admin account from environment variables so existing deployments are
not broken (same username/password that worked before keeps working).

Roles understood by the role guard:
  admin   — full access, including user management and admin routes
  editor  — read + write (upload, create, delete, share)
  viewer  — read-only (browse, download, preview, comment)
"""

import datetime
import json
import sqlite3
import uuid

import bcrypt

from .config import APP_PASSWORD, APP_USERNAME, DATA_DIR

_DB_PATH = DATA_DIR / "users.db"


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                email TEXT,
                roles_json TEXT NOT NULL DEFAULT '["viewer"]',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users (username COLLATE NOCASE)")
        conn.commit()
        # Seed the built-in admin from environment if it doesn't already exist.
        if not conn.execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (APP_USERNAME,)).fetchone():
            uid = uuid.uuid4().hex
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO users (id, username, password_hash, display_name, roles_json, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (uid, APP_USERNAME, _hash(APP_PASSWORD), APP_USERNAME, json.dumps(["admin", "editor", "viewer"]), now),
            )
            conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "email": row["email"],
        "roles": json.loads(row["roles_json"]),
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def authenticate(username: str, password: str) -> dict | None:
    """Returns user dict on success, None on bad credentials or inactive account."""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        if row is None or not row["is_active"]:
            return None
        if not _verify(password, row["password_hash"]):
            return None
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
        conn.commit()
        return _row(row)
    finally:
        conn.close()


def get_by_username(username: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def get_by_id(user_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def count_active_admins(exclude_user_id: str | None = None) -> int:
    """Used to block an update that would leave the system with zero
    active admins — that account is the only way to reach admin-only
    routes (including this one), so it would be an unrecoverable
    lockout, not just a mistake to undo. Seeding in init_db() only fires
    for a username that doesn't exist yet, so restarting the server
    doesn't repair it either."""
    conn = _conn()
    try:
        rows = conn.execute("SELECT id, roles_json FROM users WHERE is_active = 1").fetchall()
        return sum(
            1 for r in rows
            if r["id"] != exclude_user_id and "admin" in json.loads(r["roles_json"])
        )
    finally:
        conn.close()


def list_users() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def create_user(username: str, password: str, display_name: str, email: str | None, roles: list[str]) -> dict:
    uid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name, email, roles_json, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (uid, username, _hash(password), display_name, email, json.dumps(roles), now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return _row(row)
    finally:
        conn.close()


def update_user(user_id: str, *, display_name: str | None = None, email: str | None = None,
                roles: list[str] | None = None, is_active: bool | None = None,
                new_password: str | None = None) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        updates: list[tuple[str, object]] = []
        if display_name is not None:
            updates.append(("display_name", display_name))
        if email is not None:
            updates.append(("email", email))
        if roles is not None:
            updates.append(("roles_json", json.dumps(roles)))
        if is_active is not None:
            updates.append(("is_active", int(is_active)))
        if new_password is not None:
            updates.append(("password_hash", _hash(new_password)))
        if updates:
            set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
            values = [v for _, v in updates]
            conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", (*values, user_id))
            conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row(row)
    finally:
        conn.close()


def delete_user(user_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
