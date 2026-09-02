"""Multi-user account store. Access control is no longer role-based — see
groups_store.py: a user's access is the union of their groups' features,
plus an `is_superadmin` bypass-everything flag replacing the old hardcoded
`admin` role. `editor`/`viewer` are gone; whatever they used to grant is
now either "any authenticated user" (unchanged) or a real Group (see the
migration below).

Passwords are stored as bcrypt hashes. The first call to init_db() seeds
the admin account from environment variables so existing deployments are
not broken (same username/password that worked before keeps working,
now as a superadmin instead of the old 3-role list).

`roles_json` is a leftover column from the old role system — kept in the
table (SQLite DROP COLUMN is avoidable risk for no real benefit) but no
longer read by anything except the one-time migration below, which uses it
exactly once (guarded by the "is_superadmin column didn't exist yet"
check) to carry every existing user's access forward: old 'admin' becomes
is_superadmin=True; old 'editor' (not admin) becomes membership in an
auto-created "Legacy Editors" group; old 'viewer'-only users get nothing
extra, matching that 'viewer' never granted anything beyond "logged in."
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
    conn.execute("PRAGMA busy_timeout=5000")  # wait for a concurrent writer instead of failing instantly
    return conn


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def _migrate_roles_to_groups(conn: sqlite3.Connection) -> None:
    """Runs exactly once — only reachable from the branch in init_db() that
    fires when the is_superadmin column was just added, i.e. an existing
    pre-groups install. A fresh install never has rows here at this point
    (users are only ever created after init_db() completes), so this is a
    real one-time migration, not something that re-runs on every startup."""
    from . import groups_store

    rows = conn.execute("SELECT id, roles_json FROM users").fetchall()
    admin_ids = []
    editor_only_ids = []
    for r in rows:
        try:
            roles = json.loads(r["roles_json"])
        except (TypeError, ValueError):
            roles = []
        if "admin" in roles:
            admin_ids.append(r["id"])
        elif "editor" in roles:
            editor_only_ids.append(r["id"])
    if admin_ids:
        conn.executemany("UPDATE users SET is_superadmin = 1 WHERE id = ?", [(uid,) for uid in admin_ids])
        conn.commit()
    if editor_only_ids:
        groups_store.init_db()
        groups_store.seed_legacy_editors_group(editor_only_ids)


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
                roles_json TEXT NOT NULL DEFAULT '[]',
                is_superadmin INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users (username COLLATE NOCASE)")
        conn.commit()

        # Pre-existing DB from before groups/features existed: add the new
        # column and carry old role-based access forward exactly once.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_superadmin" not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_superadmin INTEGER NOT NULL DEFAULT 0")
            conn.commit()
            _migrate_roles_to_groups(conn)

        # Seed the built-in admin from environment if it doesn't already exist.
        if not conn.execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (APP_USERNAME,)).fetchone():
            uid = uuid.uuid4().hex
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO users (id, username, password_hash, display_name, is_superadmin, is_active, created_at) "
                "VALUES (?, ?, ?, ?, 1, 1, ?)",
                (uid, APP_USERNAME, _hash(APP_PASSWORD), APP_USERNAME, now),
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
        "is_superadmin": bool(row["is_superadmin"]),
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


def get_by_email(email: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
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


def count_active_superadmins(exclude_user_id: str | None = None) -> int:
    """Used to block an update that would leave the system with zero active
    superadmins — that account is the only guaranteed way to reach
    admin-only routes (including this one) if no group happens to grant
    manage_users, so removing the last one would be an unrecoverable
    lockout, not just a mistake to undo."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE is_active = 1 AND is_superadmin = 1 AND id != ?",
            (exclude_user_id or "",),
        ).fetchone()
        return row["c"]
    finally:
        conn.close()


def list_users() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def create_user(username: str, password: str, display_name: str, email: str | None,
                 is_superadmin: bool = False) -> dict:
    uid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name, email, is_superadmin, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (uid, username, _hash(password), display_name, email, int(is_superadmin), now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return _row(row)
    finally:
        conn.close()


def update_user(user_id: str, *, display_name: str | None = None, email: str | None = None,
                 is_superadmin: bool | None = None, is_active: bool | None = None,
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
        if is_superadmin is not None:
            updates.append(("is_superadmin", int(is_superadmin)))
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
