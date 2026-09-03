"""Notification storage (Repository) — plain CRUD over what notification_service
decides to write here. Kept separate from that decision logic the same way
events_store.py is kept separate from activity_service.py's Observer layer.
"""

import datetime
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

notifications = sa.Table(
    "notifications", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("owner", sa.String(255), nullable=False),
    sa.Column("event_id", sa.String(32)),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("read_at", sa.String(40)),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Index("idx_notifications_owner", "owner", "created_at"),
)

_engine = db.get_engine("notifications")


def init_db() -> None:
    # A per-rule notification-preferences table (mute this event type,
    # mute this connection, ...) belongs here later — deliberately not
    # built yet: notification_service.py's hardcoded _NOTIFIABLE_EVENT_TYPES
    # is today's whole policy, so a rules table with no reader or writer
    # anywhere would just be dead schema.
    db.create_all(_metadata, "notifications")


def _row(row) -> dict:
    return {
        "id": row["id"],
        "owner": row["owner"],
        "event_id": row["event_id"],
        "message": row["message"],
        "read_at": row["read_at"],
        "created_at": row["created_at"],
    }


def create(owner: str, event_id: str | None, message: str) -> dict:
    nid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            notifications.insert().values(
                id=nid, owner=owner, event_id=event_id, message=message, read_at=None, created_at=now
            )
        )
        row = conn.execute(sa.select(notifications).where(notifications.c.id == nid)).mappings().first()
    return _row(row)


def create_many(owners: list[str], event_id: str | None, message: str) -> None:
    """Same insert as create(), fanned out to many owners in one connection
    and one commit instead of N — for broadcast-style writes (e.g.
    notification_service's per-active-user fan-out) where opening/committing/
    closing a fresh connection per recipient dominates the cost under
    concurrency (each cycle is a separate WAL-lock acquisition serialized
    against every other writer on this same file).
    """
    if not owners:
        return
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows = [
        {"id": uuid.uuid4().hex, "owner": owner, "event_id": event_id, "message": message,
         "read_at": None, "created_at": now}
        for owner in owners
    ]
    with _engine.begin() as conn:
        conn.execute(notifications.insert(), rows)


def list_for_owner(owner: str, unread_only: bool = False, limit: int = 50) -> list[dict]:
    stmt = sa.select(notifications).where(notifications.c.owner == owner)
    if unread_only:
        stmt = stmt.where(notifications.c.read_at.is_(None))
    stmt = stmt.order_by(notifications.c.created_at.desc()).limit(min(limit, 200))
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_row(r) for r in rows]


def unread_count(owner: str) -> int:
    stmt = sa.select(sa.func.count()).select_from(notifications).where(
        notifications.c.owner == owner, notifications.c.read_at.is_(None)
    )
    with _engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


def mark_read(notification_id: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            notifications.update()
            .where(notifications.c.id == notification_id, notifications.c.read_at.is_(None))
            .values(read_at=now)
        )


def mark_all_read(owner: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            notifications.update()
            .where(notifications.c.owner == owner, notifications.c.read_at.is_(None))
            .values(read_at=now)
        )
