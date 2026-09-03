"""Document check-out / check-in soft-lock store.

For providers that have native locking (FileNet supports CHECKOUT/CHECKIN
at the CE API level), the provider itself enforces the lock.  For providers
without native locking (S3, Azure Blob, Local Disk, Google Drive, etc.) this
module acts as the lock registry — a simple table that records who checked
out a document and when.

The router enforces that only the lock holder can upload a new version while
the lock is active.  Abandoning a checkout without checking back in is
possible by admins (force-release) so work is never permanently blocked.
"""

import datetime
import sqlite3
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

locks = sa.Table(
    "locks", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("connection_id", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("locked_by", sa.String(255), nullable=False),
    sa.Column("locked_at", sa.String(40), nullable=False),
    sa.Column("comment", sa.Text),
    sa.Index("idx_locks_resource", "connection_id", "resource_id", unique=True),
)

_engine = db.get_engine("locks")


def init_db() -> None:
    db.create_all(_metadata, "locks")


def _row(row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "locked_by": row["locked_by"],
        "locked_at": row["locked_at"],
        "comment": row["comment"],
    }


def checkout(connection_id: str, resource_id: str, locked_by: str, comment: str | None = None) -> dict:
    """Raises sqlite3.IntegrityError if already locked (caller converts to HTTP 409)."""
    lock_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        with _engine.begin() as conn:
            conn.execute(
                locks.insert().values(
                    id=lock_id,
                    connection_id=connection_id,
                    resource_id=resource_id,
                    locked_by=locked_by,
                    locked_at=now,
                    comment=comment,
                )
            )
            row = conn.execute(sa.select(locks).where(locks.c.id == lock_id)).mappings().first()
    except sa.exc.IntegrityError as exc:
        # Re-raised as sqlite3.IntegrityError (rather than left as
        # sa.exc.IntegrityError) so the router's existing
        # `except sqlite3.IntegrityError` keeps working unchanged across every
        # backend dialect — sqlite/postgres/oracle each raise their own
        # driver-specific unique-violation error, which SQLAlchemy always
        # wraps in sa.exc.IntegrityError regardless of dialect.
        raise sqlite3.IntegrityError(str(exc)) from exc
    return _row(row)


def checkin(connection_id: str, resource_id: str) -> None:
    """Releases the lock regardless of who holds it (router validates ownership before calling)."""
    with _engine.begin() as conn:
        conn.execute(
            locks.delete().where(locks.c.connection_id == connection_id, locks.c.resource_id == resource_id)
        )


def get_lock(connection_id: str, resource_id: str) -> dict | None:
    stmt = sa.select(locks).where(locks.c.connection_id == connection_id, locks.c.resource_id == resource_id)
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _row(row) if row else None


def list_locks(connection_id: str) -> list[dict]:
    stmt = sa.select(locks).where(locks.c.connection_id == connection_id).order_by(locks.c.locked_at.desc())
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_row(r) for r in rows]


def delete_for_connection(connection_id: str) -> None:
    """Remove all lock records belonging to a connection (called when a connection is deleted)."""
    with _engine.begin() as conn:
        conn.execute(locks.delete().where(locks.c.connection_id == connection_id))
