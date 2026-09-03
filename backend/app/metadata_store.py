"""Document classes (types) and per-resource custom metadata.

A document class defines a schema: a list of typed fields (text, number,
date, boolean, select).  When a user uploads a file they can assign it a
class, and the UI then shows the matching fields for the user to fill in.
Values are stored as a JSON blob keyed by the field key.

This is intentionally C-ECM-native (not mapped to FileNet CE document
classes or Alfresco content models in this release) — it works identically
across all nine providers.  A future provider override can sync to the
native class system if needed.
"""

import datetime
import json
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

# ---------- schema ---------------------------------------------------------

document_classes = sa.Table(
    "document_classes", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("description", sa.Text),
    sa.Column("fields_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("created_at", sa.String(40), nullable=False),
    # Postgres/Oracle don't support SQLite's "COLLATE NOCASE" the same way,
    # so this is now a plain (case-sensitive) unique index -- case-insensitive
    # class-name uniqueness is a SQLite-only nicety in this release.
    sa.Index("idx_dc_name", "name", unique=True),
)

resource_metadata = sa.Table(
    "resource_metadata", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("connection_id", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("class_id", sa.String(32), sa.ForeignKey("document_classes.id", ondelete="SET NULL")),
    sa.Column("values_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("updated_at", sa.String(40), nullable=False),
    sa.Index("idx_rm_resource", "connection_id", "resource_id", unique=True),
    sa.Index("idx_rm_class", "class_id"),
)

resource_metadata_history = sa.Table(
    "resource_metadata_history", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("connection_id", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("old_class_id", sa.String(32)),
    sa.Column("new_class_id", sa.String(32)),
    sa.Column("old_values_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("new_values_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("changed_by", sa.String(255)),
    sa.Column("changed_at", sa.String(40), nullable=False),
    sa.Index("idx_rmh_resource", "connection_id", "resource_id", "changed_at"),
)

_engine = db.get_engine("metadata")


def init_db() -> None:
    db.create_all(_metadata, "metadata")


# ---------- document classes -----------------------------------------------

def _class_row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "fields": json.loads(row["fields_json"]),
        "created_at": row["created_at"],
    }


def list_classes() -> list[dict]:
    stmt = sa.select(document_classes).order_by(document_classes.c.name)
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_class_row(r) for r in rows]


def get_class(class_id: str) -> dict | None:
    stmt = sa.select(document_classes).where(document_classes.c.id == class_id)
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _class_row(row) if row else None


def create_class(name: str, description: str | None, fields: list[dict]) -> dict:
    cid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            document_classes.insert().values(
                id=cid, name=name, description=description, fields_json=json.dumps(fields), created_at=now
            )
        )
        row = conn.execute(sa.select(document_classes).where(document_classes.c.id == cid)).mappings().first()
    return _class_row(row)


def update_class(class_id: str, *, name: str | None = None, description: str | None = None,
                 fields: list[dict] | None = None) -> dict | None:
    with _engine.begin() as conn:
        updates = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if fields is not None:
            updates["fields_json"] = json.dumps(fields)
        if updates:
            conn.execute(document_classes.update().where(document_classes.c.id == class_id).values(**updates))
        row = conn.execute(sa.select(document_classes).where(document_classes.c.id == class_id)).mappings().first()
    return _class_row(row) if row else None


def delete_class(class_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(document_classes.delete().where(document_classes.c.id == class_id))


# ---------- resource metadata values ---------------------------------------

def _meta_row(row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "class_id": row["class_id"],
        "values": json.loads(row["values_json"]),
        "updated_at": row["updated_at"],
    }


def get_metadata(connection_id: str, resource_id: str) -> dict | None:
    stmt = sa.select(resource_metadata).where(
        resource_metadata.c.connection_id == connection_id,
        resource_metadata.c.resource_id == resource_id,
    )
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _meta_row(row) if row else None


def set_metadata(connection_id: str, resource_id: str, resource_type: str,
                 class_id: str | None, values: dict, *, actor: str | None = None) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        existing = conn.execute(
            sa.select(resource_metadata.c.class_id, resource_metadata.c.values_json).where(
                resource_metadata.c.connection_id == connection_id,
                resource_metadata.c.resource_id == resource_id,
            )
        ).mappings().first()
        old_class_id = existing["class_id"] if existing else None
        old_values = existing["values_json"] if existing else "{}"
        if existing:
            conn.execute(
                resource_metadata.update()
                .where(
                    resource_metadata.c.connection_id == connection_id,
                    resource_metadata.c.resource_id == resource_id,
                )
                .values(class_id=class_id, values_json=json.dumps(values), updated_at=now)
            )
        else:
            rid = uuid.uuid4().hex
            conn.execute(
                resource_metadata.insert().values(
                    id=rid, connection_id=connection_id, resource_id=resource_id, resource_type=resource_type,
                    class_id=class_id, values_json=json.dumps(values), updated_at=now,
                )
            )
        # Skip a history row entirely when nothing actually changed (e.g. an
        # edit opened and saved with no edits) -- comparing the serialized
        # JSON is fine here since both sides go through the same
        # json.dumps/loads round-trip, so equal dicts always serialize
        # identically.
        new_values_json = json.dumps(values)
        if old_class_id != class_id or old_values != new_values_json:
            conn.execute(
                resource_metadata_history.insert().values(
                    id=uuid.uuid4().hex, connection_id=connection_id, resource_id=resource_id,
                    resource_type=resource_type, old_class_id=old_class_id, new_class_id=class_id,
                    old_values_json=old_values, new_values_json=new_values_json, changed_by=actor, changed_at=now,
                )
            )
        row = conn.execute(
            sa.select(resource_metadata).where(
                resource_metadata.c.connection_id == connection_id,
                resource_metadata.c.resource_id == resource_id,
            )
        ).mappings().first()
    return _meta_row(row)


def set_metadata_batch(connection_id: str, resources: list[tuple[str, str]],
                        class_id: str | None, values: dict, *, actor: str | None = None) -> int:
    """Same upsert-plus-history-row logic as set_metadata(), applied to
    many resources sharing the same class_id/values in one connection/
    transaction — used by "apply to children", which previously called
    set_metadata() once per descendant (a full connect + SELECT + upsert +
    conditional history insert + commit + close each time) with no cap on
    subtree size. Returns the number of resources updated."""
    if not resources:
        return 0
    # De-dupe by resource_id, keeping the LAST occurrence — this table's
    # uniqueness key is (connection_id, resource_id) only, not resource_type,
    # and at least one provider (local disk) hands out ids that collide
    # between a file and a folder. Calling set_metadata() once per item in
    # sequence would silently let the later call win for a colliding id;
    # this preserves that same "last one wins" outcome instead of trying to
    # INSERT both in one executemany() and hitting the UNIQUE constraint.
    deduped: dict[str, str] = {}
    for resource_id, resource_type in resources:
        deduped[resource_id] = resource_type
    resources = list(deduped.items())

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    new_values_json = json.dumps(values)
    resource_ids = [rid for rid, _rtype in resources]
    with _engine.begin() as conn:
        existing_rows = conn.execute(
            sa.select(
                resource_metadata.c.resource_id, resource_metadata.c.class_id, resource_metadata.c.values_json
            ).where(
                resource_metadata.c.connection_id == connection_id,
                resource_metadata.c.resource_id.in_(resource_ids),
            )
        ).mappings().all()
        existing_by_id = {r["resource_id"]: r for r in existing_rows}

        updates, inserts, history_rows = [], [], []
        for resource_id, resource_type in resources:
            existing = existing_by_id.get(resource_id)
            old_class_id = existing["class_id"] if existing else None
            old_values = existing["values_json"] if existing else "{}"
            if existing:
                updates.append({"connection_id": connection_id, "resource_id": resource_id})
            else:
                inserts.append({
                    "id": uuid.uuid4().hex, "connection_id": connection_id, "resource_id": resource_id,
                    "resource_type": resource_type, "class_id": class_id, "values_json": new_values_json,
                    "updated_at": now,
                })
            if old_class_id != class_id or old_values != new_values_json:
                history_rows.append({
                    "id": uuid.uuid4().hex, "connection_id": connection_id, "resource_id": resource_id,
                    "resource_type": resource_type, "old_class_id": old_class_id, "new_class_id": class_id,
                    "old_values_json": old_values, "new_values_json": new_values_json,
                    "changed_by": actor, "changed_at": now,
                })

        # class_id/values_json/updated_at are identical for every row in this
        # batch, so each update is its own simple statement rather than a
        # bulk executemany — only connection_id/resource_id vary per row.
        for u in updates:
            conn.execute(
                resource_metadata.update()
                .where(
                    resource_metadata.c.connection_id == u["connection_id"],
                    resource_metadata.c.resource_id == u["resource_id"],
                )
                .values(class_id=class_id, values_json=new_values_json, updated_at=now)
            )
        if inserts:
            conn.execute(resource_metadata.insert(), inserts)
        if history_rows:
            conn.execute(resource_metadata_history.insert(), history_rows)
        return len(resources)


def _history_row(row) -> dict:
    return {
        "id": row["id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "old_class_id": row["old_class_id"],
        "new_class_id": row["new_class_id"],
        "old_values": json.loads(row["old_values_json"]),
        "new_values": json.loads(row["new_values_json"]),
        "changed_by": row["changed_by"],
        "changed_at": row["changed_at"],
    }


def list_metadata_history(connection_id: str, resource_id: str) -> list[dict]:
    stmt = (
        sa.select(resource_metadata_history)
        .where(
            resource_metadata_history.c.connection_id == connection_id,
            resource_metadata_history.c.resource_id == resource_id,
        )
        .order_by(resource_metadata_history.c.changed_at.desc())
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_history_row(r) for r in rows]


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(resource_metadata.delete().where(
            resource_metadata.c.connection_id == connection_id,
            resource_metadata.c.resource_id == resource_id,
        ))
        conn.execute(resource_metadata_history.delete().where(
            resource_metadata_history.c.connection_id == connection_id,
            resource_metadata_history.c.resource_id == resource_id,
        ))


def delete_for_resources_batch(connection_id: str, resource_ids: list[str]) -> None:
    """Same cleanup as delete_for_resource(), for many resources in one
    connection/commit — used when permanently deleting a folder with
    descendants, which previously called delete_for_resource() once per
    descendant (each opening its own connection)."""
    if not resource_ids:
        return
    with _engine.begin() as conn:
        conn.execute(resource_metadata.delete().where(
            resource_metadata.c.connection_id == connection_id,
            resource_metadata.c.resource_id.in_(resource_ids),
        ))
        conn.execute(resource_metadata_history.delete().where(
            resource_metadata_history.c.connection_id == connection_id,
            resource_metadata_history.c.resource_id.in_(resource_ids),
        ))


def delete_for_connection(connection_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(resource_metadata.delete().where(resource_metadata.c.connection_id == connection_id))
        conn.execute(resource_metadata_history.delete().where(
            resource_metadata_history.c.connection_id == connection_id
        ))
