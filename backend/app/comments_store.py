"""Comments — a C-ECM-native layer (Repository pattern) so commenting
works uniformly even on backends with no native comments at all (M-Files)
or comments with no public API (Dropbox). Comment creation is wired to the
activity log by the router, not this module — this is pure storage.
"""

import datetime
import json
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

comments = sa.Table(
    "comments", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("connection_id", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("parent_comment_id", sa.String(32)),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("mentioned_users_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("resolved_at", sa.String(40)),
    sa.Column("resolved_by", sa.String(255)),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("edited_at", sa.String(40)),
    sa.Index("idx_comments_resource", "connection_id", "resource_id", "created_at"),
)


def init_db() -> None:
    db.create_all(_metadata, "comments")


_engine = db.get_engine("comments")


def _row(row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "parent_comment_id": row["parent_comment_id"],
        "body": row["body"],
        "mentioned_users": json.loads(row["mentioned_users_json"]),
        "resolved_at": row["resolved_at"],
        "resolved_by": row["resolved_by"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "edited_at": row["edited_at"],
    }


def create(
    connection_id: str,
    resource_id: str,
    resource_type: str,
    body: str,
    created_by: str,
    parent_comment_id: str | None = None,
    mentioned_users: list[str] | None = None,
) -> dict:
    cid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            comments.insert().values(
                id=cid,
                connection_id=connection_id,
                resource_id=resource_id,
                resource_type=resource_type,
                parent_comment_id=parent_comment_id,
                body=body,
                mentioned_users_json=json.dumps(mentioned_users or []),
                created_by=created_by,
                created_at=now,
            )
        )
        row = conn.execute(sa.select(comments).where(comments.c.id == cid)).mappings().first()
    return _row(row)


def count_for_resources(connection_id: str, resource_ids: list[str]) -> dict[str, int]:
    """Batch comment counts for a whole folder listing, mirroring
    tags_store.get_tags_for_resources — avoids one query per visible item."""
    if not resource_ids:
        return {}
    stmt = (
        sa.select(comments.c.resource_id, sa.func.count().label("c"))
        .where(comments.c.connection_id == connection_id, comments.c.resource_id.in_(resource_ids))
        .group_by(comments.c.resource_id)
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    counts = {rid: 0 for rid in resource_ids}
    for row in rows:
        counts[row["resource_id"]] = row["c"]
    return counts


def list_for_resource(connection_id: str, resource_id: str) -> list[dict]:
    stmt = (
        sa.select(comments)
        .where(comments.c.connection_id == connection_id, comments.c.resource_id == resource_id)
        .order_by(comments.c.created_at.asc())
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_row(r) for r in rows]


def get(comment_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(sa.select(comments).where(comments.c.id == comment_id)).mappings().first()
    return _row(row) if row else None


def edit(comment_id: str, body: str) -> None:
    with _engine.begin() as conn:
        conn.execute(
            comments.update()
            .where(comments.c.id == comment_id)
            .values(body=body, edited_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        )


def set_resolved(comment_id: str, resolved: bool, resolved_by: str | None) -> None:
    with _engine.begin() as conn:
        if resolved:
            conn.execute(
                comments.update()
                .where(comments.c.id == comment_id)
                .values(resolved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), resolved_by=resolved_by)
            )
        else:
            conn.execute(
                comments.update()
                .where(comments.c.id == comment_id)
                .values(resolved_at=None, resolved_by=None)
            )


def delete(comment_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(
            comments.delete().where(
                sa.or_(comments.c.id == comment_id, comments.c.parent_comment_id == comment_id)
            )
        )


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(
            comments.delete().where(comments.c.connection_id == connection_id, comments.c.resource_id == resource_id)
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
            comments.delete().where(
                comments.c.connection_id == connection_id, comments.c.resource_id.in_(resource_ids)
            )
        )


def delete_for_connection(connection_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(comments.delete().where(comments.c.connection_id == connection_id))
