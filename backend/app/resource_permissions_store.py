"""Per-resource access grants — the store behind access_control.py's
require_resource_level(). A grant targets either a user or a group
(principal_type/principal_id) on a specific (connection_id, resource_id)
and gives them "view" (read-only) or "edit" (read+write) there. A grant
on a folder is meant to cascade to everything inside it — that walk
happens in access_control.py, not here; this module only stores and
queries the raw grant rows, the same "store owns persistence, router/
service owns the logic built on top of it" split as every other store in
this codebase (e.g. groups_store.py vs auth.require_feature).

Restrictions are opt-in: a connection with zero rows here behaves exactly
as it did before this feature existed (any authenticated user has full
access). connection_has_any_grants() is the cheap fast-path check
require_resource_level() uses to skip the ancestor walk entirely for the
common case where nobody has restricted anything.
"""

import datetime
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

resource_permissions = sa.Table(
    "resource_permissions", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("connection_id", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("principal_type", sa.String(64), nullable=False),
    sa.Column("principal_id", sa.String(32), nullable=False),
    sa.Column("level", sa.String(64), nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("created_by", sa.String(255)),
    sa.Index("idx_resource_permissions_lookup", "connection_id", "resource_id"),
    # Backs connection_has_any_grants()'s SELECT EXISTS — a plain
    # index on connection_id alone (the lookup index above is
    # (connection_id, resource_id), still usable, but a narrower
    # single-column index makes the "does this connection have ANY
    # row at all" check as cheap as possible, since it's the one
    # query every gated route pays on every request).
    sa.Index("idx_resource_permissions_connection", "connection_id"),
)

_engine = db.get_engine("resource_permissions")


def init_db() -> None:
    db.create_all(_metadata, "resource_permissions")


def _row(row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "principal_type": row["principal_type"],
        "principal_id": row["principal_id"],
        "level": row["level"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
    }


def connection_has_any_grants(connection_id: str) -> bool:
    stmt = sa.select(sa.literal(1)).select_from(resource_permissions).where(
        resource_permissions.c.connection_id == connection_id
    ).limit(1)
    with _engine.connect() as conn:
        row = conn.execute(stmt).first()
        return row is not None


def list_for_resource(connection_id: str, resource_id: str) -> list[dict]:
    """Grants set directly on this resource (not inherited from an
    ancestor) — what the access-grants UI for this specific resource
    shows/edits."""
    stmt = sa.select(resource_permissions).where(
        resource_permissions.c.connection_id == connection_id,
        resource_permissions.c.resource_id == resource_id,
    ).order_by(resource_permissions.c.created_at)
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
        return [_row(r) for r in rows]


def grants_for_resource_batch(connection_id: str, resource_ids: list[str]) -> dict[str, list[dict]]:
    """One query for every id in an ancestor chain, instead of one query
    per ancestor level — used by access_control.py's walk. Returns
    {resource_id: [grant, ...]} only for ids that actually have grants."""
    if not resource_ids:
        return {}
    stmt = sa.select(resource_permissions).where(
        resource_permissions.c.connection_id == connection_id,
        resource_permissions.c.resource_id.in_(resource_ids),
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["resource_id"], []).append(_row(r))
    return out


def create(connection_id: str, resource_id: str, resource_type: str, principal_type: str,
           principal_id: str, level: str, created_by: str | None) -> dict:
    grant_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            resource_permissions.insert().values(
                id=grant_id, connection_id=connection_id, resource_id=resource_id, resource_type=resource_type,
                principal_type=principal_type, principal_id=principal_id, level=level, created_at=now,
                created_by=created_by,
            )
        )
        row = conn.execute(
            sa.select(resource_permissions).where(resource_permissions.c.id == grant_id)
        ).mappings().first()
        return _row(row)


def get(grant_id: str) -> dict | None:
    stmt = sa.select(resource_permissions).where(resource_permissions.c.id == grant_id)
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
        return _row(row) if row else None


def delete(grant_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(resource_permissions.delete().where(resource_permissions.c.id == grant_id))


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(
            resource_permissions.delete().where(
                resource_permissions.c.connection_id == connection_id,
                resource_permissions.c.resource_id == resource_id,
            )
        )


def delete_for_resources_batch(connection_id: str, resource_ids: list[str]) -> None:
    """Same cleanup as delete_for_resource(), for many resources in one
    connection/commit — used when permanently deleting a folder with
    descendants, which previously called delete_for_resource() once per
    descendant (each opening its own connection)."""
    if not resource_ids:
        return
    with _engine.begin() as conn:
        conn.execute(
            resource_permissions.delete().where(
                resource_permissions.c.connection_id == connection_id,
                resource_permissions.c.resource_id.in_(resource_ids),
            )
        )


def delete_for_connection(connection_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(
            resource_permissions.delete().where(resource_permissions.c.connection_id == connection_id)
        )


def delete_for_group(group_id: str) -> None:
    """Called when a group is deleted, so grants pointing at it don't
    silently become permanent open-ended holes — see groups_store.py's
    delete_group; wired in from routers/groups.py, not groups_store.py
    itself, to keep that module from needing to know about this one."""
    with _engine.begin() as conn:
        conn.execute(
            resource_permissions.delete().where(
                resource_permissions.c.principal_type == "group",
                resource_permissions.c.principal_id == group_id,
            )
        )
