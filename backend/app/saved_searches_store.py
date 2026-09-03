"""Saved searches — Repository storage for a named, re-runnable filter set.
Running one is just replaying its stored params through the existing
StorageProvider.search() call; this module only persists the definition.
"""

import datetime
import json
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

saved_searches = sa.Table(
    "saved_searches", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("owner", sa.String(255), nullable=False),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("connection_id", sa.String(32)),
    sa.Column("query_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("last_run_at", sa.String(40)),
)

_engine = db.get_engine("saved_searches")


def init_db() -> None:
    db.create_all(_metadata, "saved_searches")


def _row(row: sa.Row) -> dict:
    return {
        "id": row["id"],
        "owner": row["owner"],
        "name": row["name"],
        "connection_id": row["connection_id"],
        "query": json.loads(row["query_json"]),
        "created_at": row["created_at"],
        "last_run_at": row["last_run_at"],
    }


def create(owner: str, name: str, connection_id: str | None, query: dict) -> dict:
    sid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            saved_searches.insert().values(
                id=sid, owner=owner, name=name, connection_id=connection_id,
                query_json=json.dumps(query), created_at=now, last_run_at=None,
            )
        )
        row = conn.execute(sa.select(saved_searches).where(saved_searches.c.id == sid)).mappings().first()
    return _row(row)


def list_for_owner(owner: str) -> list[dict]:
    stmt = (
        sa.select(saved_searches)
        .where(saved_searches.c.owner == owner)
        .order_by(saved_searches.c.created_at.desc())
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_row(r) for r in rows]


def get(search_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(saved_searches).where(saved_searches.c.id == search_id)
        ).mappings().first()
    return _row(row) if row else None


def touch_last_run(search_id: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            saved_searches.update()
            .where(saved_searches.c.id == search_id)
            .values(last_run_at=now)
        )


def delete(search_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(saved_searches.delete().where(saved_searches.c.id == search_id))
