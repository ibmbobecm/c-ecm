"""Groups — the unit access control is granted through. A Group holds a set
of Feature keys (see features.py); a User can belong to any number of
Groups and inherits the union of every group's features. Superadmin users
bypass this entirely (see auth.require_feature) — groups only matter for
everyone else.

Same store shape/conventions as users_store.py: its own SQLite file, WAL
mode, plain dict rows. `user_groups.user_id` has no real foreign key since
users live in a separate SQLite file (users.db) — same cross-store
reference-by-id pattern already used by tags_store/comments_store on
resource_id.
"""

import datetime
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "groups.db"


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                description TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS group_features (
                group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                feature_key TEXT NOT NULL,
                PRIMARY KEY (group_id, feature_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_groups (
                user_id TEXT NOT NULL,
                group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, group_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _group_row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "description": row["description"], "created_at": row["created_at"]}


def _group_out(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    g = _group_row(row)
    g["feature_keys"] = [
        r["feature_key"] for r in conn.execute(
            "SELECT feature_key FROM group_features WHERE group_id = ? ORDER BY feature_key", (g["id"],)
        ).fetchall()
    ]
    g["member_count"] = conn.execute(
        "SELECT COUNT(*) AS c FROM user_groups WHERE group_id = ?", (g["id"],)
    ).fetchone()["c"]
    return g


def list_groups() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM groups ORDER BY name COLLATE NOCASE").fetchall()
        return [_group_out(conn, r) for r in rows]
    finally:
        conn.close()


def get_group(group_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        return _group_out(conn, row) if row else None
    finally:
        conn.close()


def name_exists(name: str, exclude_id: str | None = None) -> bool:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id FROM groups WHERE name = ? COLLATE NOCASE AND id != ?", (name, exclude_id or "")
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def create_group(name: str, description: str | None, feature_keys: list[str]) -> dict:
    gid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO groups (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (gid, name, description, now),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO group_features (group_id, feature_key) VALUES (?, ?)",
            [(gid, k) for k in feature_keys],
        )
        conn.commit()
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (gid,)).fetchone()
        return _group_out(conn, row)
    finally:
        conn.close()


def update_group(group_id: str, *, name: str | None = None, description: str | None = None,
                  feature_keys: list[str] | None = None) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if row is None:
            return None
        updates: list[tuple[str, object]] = []
        if name is not None:
            updates.append(("name", name))
        if description is not None:
            updates.append(("description", description))
        if updates:
            set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
            values = [v for _, v in updates]
            conn.execute(f"UPDATE groups SET {set_clause} WHERE id = ?", (*values, group_id))
        if feature_keys is not None:
            conn.execute("DELETE FROM group_features WHERE group_id = ?", (group_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO group_features (group_id, feature_key) VALUES (?, ?)",
                [(group_id, k) for k in feature_keys],
            )
        conn.commit()
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        return _group_out(conn, row)
    finally:
        conn.close()


def delete_group(group_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        conn.commit()
    finally:
        conn.close()


def add_user_to_group(user_id: str, group_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?, ?)", (user_id, group_id))
        conn.commit()
    finally:
        conn.close()


def remove_user_from_group(user_id: str, group_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM user_groups WHERE user_id = ? AND group_id = ?", (user_id, group_id))
        conn.commit()
    finally:
        conn.close()


def list_group_members(group_id: str) -> list[str]:
    """Returns member user ids — callers join against users_store for details."""
    conn = _conn()
    try:
        rows = conn.execute("SELECT user_id FROM user_groups WHERE group_id = ?", (group_id,)).fetchall()
        return [r["user_id"] for r in rows]
    finally:
        conn.close()


def list_user_groups(user_id: str) -> list[dict]:
    """Returns [{id, name}] for every group this user belongs to."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT g.id, g.name FROM groups g JOIN user_groups ug ON ug.group_id = g.id "
            "WHERE ug.user_id = ? ORDER BY g.name COLLATE NOCASE",
            (user_id,),
        ).fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]
    finally:
        conn.close()


def user_features(user_id: str) -> list[str]:
    """Flattened, deduplicated set of every feature this user's groups grant."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT gf.feature_key FROM group_features gf "
            "JOIN user_groups ug ON ug.group_id = gf.group_id WHERE ug.user_id = ? "
            "ORDER BY gf.feature_key",
            (user_id,),
        ).fetchall()
        return [r["feature_key"] for r in rows]
    finally:
        conn.close()


def user_has_feature(user_id: str, feature_key: str) -> bool:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM group_features gf JOIN user_groups ug ON ug.group_id = gf.group_id "
            "WHERE ug.user_id = ? AND gf.feature_key = ? LIMIT 1",
            (user_id, feature_key),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def delete_for_user(user_id: str) -> None:
    """Called when a user account is deleted, so membership rows don't orphan."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM user_groups WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def seed_legacy_editors_group(member_user_ids: list[str]) -> None:
    """One-time migration seed (see users_store.py's is_superadmin backfill,
    which calls this): reproduces the old 'editor' role's one distinct
    capability (send_esignature) as a real group, so existing editors keep
    working access through the roles -> groups/features migration instead
    of silently losing it. Only meant to be called once, when the groups
    table is first created empty — callers are responsible for that guard."""
    if not member_user_ids:
        return
    existing = list_groups()
    if any(g["name"] == "Legacy Editors" for g in existing):
        return
    group = create_group("Legacy Editors", "Auto-created during the roles → groups/features migration — "
                          "preserves what the old 'editor' role could do.", ["send_esignature"])
    for uid in member_user_ids:
        add_user_to_group(uid, group["id"])
