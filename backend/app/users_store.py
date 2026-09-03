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
import uuid

import bcrypt
import sqlalchemy as sa

from . import db
from .config import APP_PASSWORD, APP_USERNAME

_metadata = sa.MetaData()

users = sa.Table(
    "users", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("username", sa.String(255), nullable=False),
    sa.Column("password_hash", sa.String(255), nullable=False),
    sa.Column("display_name", sa.String(255), nullable=False),
    sa.Column("email", sa.String(255)),
    # No DB-level default here (unlike the original DEFAULT '[]'): Oracle's
    # CLOB type has historically shaky/version-dependent support for column
    # defaults, so every INSERT below sets roles_json='[]' explicitly
    # instead — same resulting value, no dialect-specific DDL risk.
    sa.Column("roles_json", sa.Text, nullable=False),
    sa.Column("is_superadmin", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("last_login_at", sa.String(40)),
)

# SQLite's "UNIQUE ... COLLATE NOCASE" has no portable equivalent on
# postgres/oracle. Closest safe substitute: enforce/query case-insensitive
# uniqueness via lower(username) everywhere (this index, and every
# username/email lookup below) instead of a collation.
sa.Index("idx_users_username", sa.func.lower(users.c.username), unique=True)

_engine = db.get_engine("users")


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def _migrate_roles_to_groups(conn) -> None:
    """Runs exactly once — only reachable from the branch in init_db() that
    fires when the is_superadmin column was just added, i.e. an existing
    pre-groups install. A fresh install never has rows here at this point
    (users are only ever created after init_db() completes), so this is a
    real one-time migration, not something that re-runs on every startup."""
    from . import groups_store

    rows = conn.execute(sa.select(users.c.id, users.c.roles_json)).mappings().all()
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
        conn.execute(users.update().where(users.c.id.in_(admin_ids)).values(is_superadmin=True))
    if editor_only_ids:
        groups_store.init_db()
        groups_store.seed_legacy_editors_group(editor_only_ids)


def init_db() -> None:
    db.create_all(_metadata, "users")

    with _engine.begin() as conn:
        # Pre-existing DB from before groups/features existed: add the new
        # column and carry old role-based access forward exactly once. Only
        # a pre-existing SQLite file can be missing is_superadmin this way —
        # postgres/oracle support was added together with is_superadmin/
        # groups, so create_all() above always creates those fresh with the
        # column already present; there is no legacy postgres/oracle schema
        # to migrate from.
        if _engine.dialect.name == "sqlite":
            existing_cols = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(users)")).fetchall()}
            if "is_superadmin" not in existing_cols:
                conn.execute(sa.text("ALTER TABLE users ADD COLUMN is_superadmin INTEGER NOT NULL DEFAULT 0"))
                _migrate_roles_to_groups(conn)

        # Seed the built-in admin from environment if it doesn't already exist.
        exists = conn.execute(
            sa.select(users.c.id).where(sa.func.lower(users.c.username) == APP_USERNAME.lower())
        ).first()
        if not exists:
            uid = uuid.uuid4().hex
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(
                users.insert().values(
                    id=uid, username=APP_USERNAME, password_hash=_hash(APP_PASSWORD),
                    display_name=APP_USERNAME, email=None, roles_json="[]",
                    is_superadmin=True, is_active=True, created_at=now,
                )
            )


def _row(row) -> dict:
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
    with _engine.begin() as conn:
        row = conn.execute(
            sa.select(users).where(sa.func.lower(users.c.username) == username.lower())
        ).mappings().first()
        if row is None or not row["is_active"]:
            return None
        if not _verify(password, row["password_hash"]):
            return None
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(users.update().where(users.c.id == row["id"]).values(last_login_at=now))
        return _row(row)


def get_by_username(username: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(users).where(sa.func.lower(users.c.username) == username.lower())
        ).mappings().first()
        return _row(row) if row else None


def get_by_email(email: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(users).where(sa.func.lower(users.c.email) == email.lower())
        ).mappings().first()
        return _row(row) if row else None


def get_by_id(user_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(sa.select(users).where(users.c.id == user_id)).mappings().first()
        return _row(row) if row else None


def count_active_superadmins(exclude_user_id: str | None = None) -> int:
    """Used to block an update that would leave the system with zero active
    superadmins — that account is the only guaranteed way to reach
    admin-only routes (including this one) if no group happens to grant
    manage_users, so removing the last one would be an unrecoverable
    lockout, not just a mistake to undo."""
    with _engine.connect() as conn:
        return conn.execute(
            sa.select(sa.func.count()).select_from(users).where(
                users.c.is_active.is_(True),
                users.c.is_superadmin.is_(True),
                users.c.id != (exclude_user_id or ""),
            )
        ).scalar_one()


def list_users() -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(sa.select(users).order_by(sa.func.lower(users.c.username))).mappings().all()
        return [_row(r) for r in rows]


def create_user(username: str, password: str, display_name: str, email: str | None,
                 is_superadmin: bool = False) -> dict:
    uid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            users.insert().values(
                id=uid, username=username, password_hash=_hash(password), display_name=display_name,
                email=email, roles_json="[]", is_superadmin=bool(is_superadmin), is_active=True, created_at=now,
            )
        )
        row = conn.execute(sa.select(users).where(users.c.id == uid)).mappings().first()
        return _row(row)


def update_user(user_id: str, *, display_name: str | None = None, email: str | None = None,
                 is_superadmin: bool | None = None, is_active: bool | None = None,
                 new_password: str | None = None) -> dict | None:
    with _engine.begin() as conn:
        row = conn.execute(sa.select(users).where(users.c.id == user_id)).mappings().first()
        if row is None:
            return None
        updates: dict[str, object] = {}
        if display_name is not None:
            updates["display_name"] = display_name
        if email is not None:
            updates["email"] = email
        if is_superadmin is not None:
            updates["is_superadmin"] = bool(is_superadmin)
        if is_active is not None:
            updates["is_active"] = bool(is_active)
        if new_password is not None:
            updates["password_hash"] = _hash(new_password)
        if updates:
            conn.execute(users.update().where(users.c.id == user_id).values(**updates))
        row = conn.execute(sa.select(users).where(users.c.id == user_id)).mappings().first()
        return _row(row)


def delete_user(user_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(users.delete().where(users.c.id == user_id))
