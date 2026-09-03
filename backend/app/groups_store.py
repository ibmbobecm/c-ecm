"""Groups — the unit access control is granted through. A Group holds a set
of Feature keys (see features.py); a User can belong to any number of
Groups and inherits the union of every group's features. Superadmin users
bypass this entirely (see auth.require_feature) — groups only matter for
everyone else.

Same store shape/conventions as users_store.py: its own configurable engine
(see db.py — one SQLite file in sqlite mode, a shared database in postgres/
oracle mode), plain dict rows. `user_groups.user_id` has no real foreign key
since users live in a separate store (users_store.py — its own SQLite file
in sqlite mode) — same cross-store reference-by-id pattern already used by
tags_store/comments_store on resource_id.
"""

import datetime
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

groups = sa.Table(
    "groups", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    # COLLATE NOCASE (SQLite-only syntax) has no portable equivalent across
    # sqlite/postgres/oracle, so the UNIQUE constraint below is plain
    # (case-sensitive at the DB layer) on every backend now, not just on
    # postgres/oracle — the closest safe substitute. Case-insensitive
    # uniqueness is still enforced the same way it always effectively was
    # from the application's perspective: every create/update path checks
    # name_exists() (which compares via func.lower() below, portable across
    # all three dialects) before writing.
    sa.Column("name", sa.String(255), nullable=False, unique=True),
    sa.Column("description", sa.Text),
    sa.Column("created_at", sa.String(40), nullable=False),
)

group_features = sa.Table(
    "group_features", _metadata,
    sa.Column("group_id", sa.String(32), sa.ForeignKey("groups.id", ondelete="CASCADE"),
              primary_key=True, nullable=False),
    sa.Column("feature_key", sa.String(64), primary_key=True, nullable=False),
)

user_groups = sa.Table(
    "user_groups", _metadata,
    sa.Column("user_id", sa.String(32), primary_key=True, nullable=False),
    sa.Column("group_id", sa.String(32), sa.ForeignKey("groups.id", ondelete="CASCADE"),
              primary_key=True, nullable=False),
)

_engine = db.get_engine("groups")


def init_db() -> None:
    db.create_all(_metadata, "groups")


def _group_row(row) -> dict:
    return {"id": row["id"], "name": row["name"], "description": row["description"], "created_at": row["created_at"]}


def _group_out(conn, row) -> dict:
    g = _group_row(row)
    feat_rows = conn.execute(
        sa.select(group_features.c.feature_key)
        .where(group_features.c.group_id == g["id"])
        .order_by(group_features.c.feature_key)
    ).mappings().all()
    g["feature_keys"] = [r["feature_key"] for r in feat_rows]
    g["member_count"] = conn.execute(
        sa.select(sa.func.count()).select_from(user_groups).where(user_groups.c.group_id == g["id"])
    ).scalar_one()
    return g


def list_groups() -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(sa.select(groups).order_by(sa.func.lower(groups.c.name))).mappings().all()
        return [_group_out(conn, r) for r in rows]


def get_group(group_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(sa.select(groups).where(groups.c.id == group_id)).mappings().first()
        return _group_out(conn, row) if row else None


def name_exists(name: str, exclude_id: str | None = None) -> bool:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(groups.c.id).where(
                sa.func.lower(groups.c.name) == sa.func.lower(name),
                groups.c.id != (exclude_id or ""),
            )
        ).mappings().first()
        return row is not None


def create_group(name: str, description: str | None, feature_keys: list[str]) -> dict:
    gid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            groups.insert().values(id=gid, name=name, description=description, created_at=now)
        )
        if feature_keys:
            # dict.fromkeys(...) dedupes while preserving order — the original
            # relied on SQLite's "INSERT OR IGNORE" to silently drop duplicate
            # (group_id, feature_key) pairs from the incoming list; that
            # syntax is SQLite-only, and since gid is freshly minted above
            # (no existing group_features rows can collide with it yet), a
            # plain dedupe-then-insert here reaches the exact same end state
            # on every backend.
            rows = [{"group_id": gid, "feature_key": k} for k in dict.fromkeys(feature_keys)]
            conn.execute(group_features.insert(), rows)
        row = conn.execute(sa.select(groups).where(groups.c.id == gid)).mappings().first()
        return _group_out(conn, row)


def update_group(group_id: str, *, name: str | None = None, description: str | None = None,
                  feature_keys: list[str] | None = None) -> dict | None:
    with _engine.begin() as conn:
        row = conn.execute(sa.select(groups).where(groups.c.id == group_id)).mappings().first()
        if row is None:
            return None
        updates: dict = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if updates:
            conn.execute(groups.update().where(groups.c.id == group_id).values(**updates))
        if feature_keys is not None:
            conn.execute(group_features.delete().where(group_features.c.group_id == group_id))
            if feature_keys:
                # Same dedupe reasoning as create_group() — the DELETE just
                # above guarantees no pre-existing (group_id, feature_key)
                # rows to collide with, so a duplicate can only come from the
                # incoming feature_keys list itself.
                rows = [{"group_id": group_id, "feature_key": k} for k in dict.fromkeys(feature_keys)]
                conn.execute(group_features.insert(), rows)
        row = conn.execute(sa.select(groups).where(groups.c.id == group_id)).mappings().first()
        return _group_out(conn, row)


def delete_group(group_id: str) -> None:
    with _engine.begin() as conn:
        # Both group_features.group_id and user_groups.group_id declare
        # ON DELETE CASCADE above for a real RDBMS's own referential-
        # integrity story (postgres/oracle always enforce FKs), but db.py's
        # sqlite engine never issues "PRAGMA foreign_keys=ON" the way the old
        # per-call _conn() used to — so under sqlite this cascade would
        # silently not fire if left to the DB alone, orphaning membership/
        # feature rows. Deleting the child rows explicitly here (cheap,
        # already-indexed-by-PK deletes) makes group deletion complete on
        # every backend regardless of that pragma difference.
        conn.execute(group_features.delete().where(group_features.c.group_id == group_id))
        conn.execute(user_groups.delete().where(user_groups.c.group_id == group_id))
        conn.execute(groups.delete().where(groups.c.id == group_id))


def add_user_to_group(user_id: str, group_id: str) -> None:
    try:
        with _engine.begin() as conn:
            conn.execute(user_groups.insert().values(user_id=user_id, group_id=group_id))
    except sa.exc.IntegrityError:
        # Same idempotency the original's "INSERT OR IGNORE" gave: calling
        # this when the user is already a member of the group is a silent
        # no-op, not an error (callers — e.g. routers/saml.py's default-group
        # assignment — rely on that). "OR IGNORE" is SQLite-only syntax with
        # no single portable equivalent across postgres/oracle in the plain
        # SQLAlchemy Core expression API, so catching the (user_id, group_id)
        # primary-key violation is the portable substitute.
        pass


def remove_user_from_group(user_id: str, group_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(
            user_groups.delete().where(user_groups.c.user_id == user_id, user_groups.c.group_id == group_id)
        )


def list_group_members(group_id: str) -> list[str]:
    """Returns member user ids — callers join against users_store for details."""
    with _engine.connect() as conn:
        rows = conn.execute(
            sa.select(user_groups.c.user_id).where(user_groups.c.group_id == group_id)
        ).mappings().all()
        return [r["user_id"] for r in rows]


def list_user_groups(user_id: str) -> list[dict]:
    """Returns [{id, name}] for every group this user belongs to."""
    stmt = (
        sa.select(groups.c.id, groups.c.name)
        .select_from(groups.join(user_groups, user_groups.c.group_id == groups.c.id))
        .where(user_groups.c.user_id == user_id)
        .order_by(sa.func.lower(groups.c.name))
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
        return [{"id": r["id"], "name": r["name"]} for r in rows]


def user_features(user_id: str) -> list[str]:
    """Flattened, deduplicated set of every feature this user's groups grant."""
    stmt = (
        sa.select(group_features.c.feature_key)
        .distinct()
        .select_from(group_features.join(user_groups, user_groups.c.group_id == group_features.c.group_id))
        .where(user_groups.c.user_id == user_id)
        .order_by(group_features.c.feature_key)
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
        return [r["feature_key"] for r in rows]


def user_has_feature(user_id: str, feature_key: str) -> bool:
    stmt = (
        sa.select(sa.literal(1))
        .select_from(group_features.join(user_groups, user_groups.c.group_id == group_features.c.group_id))
        .where(user_groups.c.user_id == user_id, group_features.c.feature_key == feature_key)
        .limit(1)
    )
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
        return row is not None


def delete_for_user(user_id: str) -> None:
    """Called when a user account is deleted, so membership rows don't orphan."""
    with _engine.begin() as conn:
        conn.execute(user_groups.delete().where(user_groups.c.user_id == user_id))


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
