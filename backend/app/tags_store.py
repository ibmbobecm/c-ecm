"""Tags/custom metadata — deliberately C-ECM-native and backend-agnostic
(no StorageProvider involvement at all), mirroring how connections_store.py
already sits outside the provider Strategy hierarchy. No backend's native
tag concept (Drive Labels, Box metadata templates, Alfresco Aspects, ...)
maps 1:1 across all nine providers, so this is the uniform layer that works
identically regardless of which connection a resource lives on.
"""

import datetime
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

tags = sa.Table(
    "tags", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("name", sa.String(64), nullable=False),
    sa.Column("color", sa.String(16), nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
)

# COLLATE NOCASE (sqlite-only) has no portable equivalent on postgres/oracle;
# a unique index on lower(name) enforces the same case-insensitive uniqueness
# on all three dialects (Oracle supports function-based indexes too), and
# every ORDER BY/WHERE below on name uses sa.func.lower() to match it.
sa.Index("idx_tags_name_nocase", sa.func.lower(tags.c.name), unique=True)

resource_tags = sa.Table(
    "resource_tags", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("connection_id", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("tag_id", sa.String(32), sa.ForeignKey("tags.id", ondelete="CASCADE"), nullable=False),
    sa.Column("tagged_by", sa.String(255), nullable=False),
    sa.Column("tagged_at", sa.String(40), nullable=False),
    sa.Index("idx_resource_tags_unique", "connection_id", "resource_id", "tag_id", unique=True),
    sa.Index("idx_resource_tags_lookup", "connection_id", "resource_id"),
    sa.Index("idx_resource_tags_by_tag", "tag_id"),
)


def init_db() -> None:
    db.create_all(_metadata, "tags")


_engine = db.get_engine("tags")


def _tag_row(row) -> dict:
    return {"id": row["id"], "name": row["name"], "color": row["color"], "created_at": row["created_at"]}


def list_tags() -> list[dict]:
    stmt = sa.select(tags).order_by(sa.func.lower(tags.c.name))
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_tag_row(r) for r in rows]


def get_or_create_tag(name: str, color: str) -> dict:
    with _engine.begin() as conn:
        row = conn.execute(sa.select(tags).where(sa.func.lower(tags.c.name) == name.lower())).mappings().first()
        if row:
            return _tag_row(row)
        tag_id = uuid.uuid4().hex
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(tags.insert().values(id=tag_id, name=name, color=color, created_at=now))
        row = conn.execute(sa.select(tags).where(tags.c.id == tag_id)).mappings().first()
        return _tag_row(row)


def delete_tag(tag_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(resource_tags.delete().where(resource_tags.c.tag_id == tag_id))
        conn.execute(tags.delete().where(tags.c.id == tag_id))


def tag_resource(connection_id: str, resource_id: str, resource_type: str, tag_id: str, tagged_by: str) -> None:
    # INSERT OR IGNORE has no portable equivalent across sqlite/postgres/
    # oracle without dialect-specific upsert syntax — check-then-insert
    # (same pattern as get_or_create_tag above) keeps re-tagging an
    # already-tagged resource a no-op instead of a unique-constraint error.
    with _engine.begin() as conn:
        existing = conn.execute(
            sa.select(resource_tags.c.id).where(
                resource_tags.c.connection_id == connection_id,
                resource_tags.c.resource_id == resource_id,
                resource_tags.c.tag_id == tag_id,
            )
        ).first()
        if existing:
            return
        conn.execute(
            resource_tags.insert().values(
                id=uuid.uuid4().hex,
                connection_id=connection_id,
                resource_id=resource_id,
                resource_type=resource_type,
                tag_id=tag_id,
                tagged_by=tagged_by,
                tagged_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )
        )


def untag_resource(connection_id: str, resource_id: str, tag_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(
            resource_tags.delete().where(
                resource_tags.c.connection_id == connection_id,
                resource_tags.c.resource_id == resource_id,
                resource_tags.c.tag_id == tag_id,
            )
        )


def get_tags_for_resource(connection_id: str, resource_id: str) -> list[dict]:
    stmt = (
        sa.select(tags)
        .select_from(tags.join(resource_tags, resource_tags.c.tag_id == tags.c.id))
        .where(resource_tags.c.connection_id == connection_id, resource_tags.c.resource_id == resource_id)
        .order_by(sa.func.lower(tags.c.name))
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_tag_row(r) for r in rows]


def get_tags_for_resources(connection_id: str, resource_ids: list[str]) -> dict[str, list[dict]]:
    """Batch form of get_tags_for_resource, for annotating a whole folder
    listing without N+1 queries — every FolderContents response needs this."""
    if not resource_ids:
        return {}
    stmt = (
        sa.select(resource_tags.c.resource_id, tags)
        .select_from(tags.join(resource_tags, resource_tags.c.tag_id == tags.c.id))
        .where(resource_tags.c.connection_id == connection_id, resource_tags.c.resource_id.in_(resource_ids))
        .order_by(sa.func.lower(tags.c.name))
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    result: dict[str, list[dict]] = {rid: [] for rid in resource_ids}
    for row in rows:
        result[row["resource_id"]].append(_tag_row(row))
    return result


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    """Called when a single file/folder is permanently deleted — same
    orphaning concern as delete_for_connection, scoped to one resource."""
    with _engine.begin() as conn:
        conn.execute(
            resource_tags.delete().where(
                resource_tags.c.connection_id == connection_id, resource_tags.c.resource_id == resource_id
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
            resource_tags.delete().where(
                resource_tags.c.connection_id == connection_id, resource_tags.c.resource_id.in_(resource_ids)
            )
        )


def delete_for_connection(connection_id: str) -> None:
    """Called when a connection is removed — its tag attachments would
    otherwise reference a connection_id nothing can ever resolve again.
    Tag *definitions* are shared across connections, so only the
    attachments are removed, not the tags themselves."""
    with _engine.begin() as conn:
        conn.execute(resource_tags.delete().where(resource_tags.c.connection_id == connection_id))


def get_resources_for_tag(tag_id: str) -> list[dict]:
    stmt = (
        sa.select(
            resource_tags.c.connection_id,
            resource_tags.c.resource_id,
            resource_tags.c.resource_type,
            resource_tags.c.tagged_at,
        )
        .where(resource_tags.c.tag_id == tag_id)
        .order_by(resource_tags.c.tagged_at.desc())
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]
