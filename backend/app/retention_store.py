"""Retention & Disposition Policy Engine.

Retention policies define how long documents must be kept and what
happens when they expire:
  - action: 'review'      — transition to 'under_review', notify admin
  - action: 'archive'     — mark as archived (future: move to cold storage)
  - action: 'auto_delete' — trash the document automatically

Policies are applied at the connection level and can be overridden per
document class.  Legal holds override all policies.

The scheduler (called from main.py lifespan via APScheduler or a daily
background thread) evaluates all tracked resources each day.
"""

import datetime
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

# name has no portable case-insensitive collation (SQLite's COLLATE NOCASE
# doesn't exist on Postgres/Oracle) — a unique functional index on
# lower(name) gives the same case-insensitive uniqueness guarantee and
# compiles on all three dialects (see idx_rp_name below).
_rp_name = sa.Column("name", sa.String(255), nullable=False)

retention_policies = sa.Table(
    "retention_policies", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    _rp_name,
    sa.Column("description", sa.Text),
    sa.Column("retention_days", sa.Integer, nullable=False),
    sa.Column("action", sa.String(64), nullable=False, server_default="review"),
    sa.Column("class_id", sa.String(32)),
    sa.Column("connection_id", sa.String(32)),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Index("idx_rp_name", sa.func.lower(_rp_name), unique=True),
)

retention_records = sa.Table(
    "retention_records", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("policy_id", sa.String(32), sa.ForeignKey("retention_policies.id", ondelete="CASCADE"), nullable=False),
    sa.Column("connection_id", sa.String(32), nullable=False),
    # resource_id is a foreign resource id from one of the storage providers,
    # not one of this app's own uuid4().hex ids — it can be numeric,
    # path-like, or an opaque provider token, so it gets a generous bound
    # rather than the 32-char id column width used above.
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("resource_name", sa.Text),
    sa.Column("due_date", sa.String(40), nullable=False),
    sa.Column("status", sa.String(64), nullable=False, server_default="active"),
    sa.Column("legal_hold", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("actioned_at", sa.String(40)),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Index("idx_rr_resource", "connection_id", "resource_id", "policy_id", unique=True),
    sa.Index("idx_rr_due", "due_date", "status"),
)

_engine = db.get_engine("retention")


def init_db() -> None:
    db.create_all(_metadata, "retention")


# ---------- policies -------------------------------------------------------

def _policy_row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "retention_days": row["retention_days"],
        "action": row["action"],
        "class_id": row["class_id"],
        "connection_id": row["connection_id"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


def list_policies() -> list[dict]:
    stmt = sa.select(retention_policies).order_by(sa.func.lower(retention_policies.c.name))
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_policy_row(r) for r in rows]


def get_policy(policy_id: str) -> dict | None:
    stmt = sa.select(retention_policies).where(retention_policies.c.id == policy_id)
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _policy_row(row) if row else None


def create_policy(name: str, description: str | None, retention_days: int, action: str,
                  class_id: str | None = None, connection_id: str | None = None) -> dict:
    pid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            retention_policies.insert().values(
                id=pid, name=name, description=description, retention_days=retention_days,
                action=action, class_id=class_id, connection_id=connection_id, active=True, created_at=now,
            )
        )
        row = conn.execute(sa.select(retention_policies).where(retention_policies.c.id == pid)).mappings().first()
    return _policy_row(row)


def update_policy(policy_id: str, **kwargs) -> dict | None:
    allowed = {"name", "description", "retention_days", "action", "class_id", "connection_id", "active"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return get_policy(policy_id)
    with _engine.begin() as conn:
        conn.execute(retention_policies.update().where(retention_policies.c.id == policy_id).values(**updates))
        row = conn.execute(sa.select(retention_policies).where(retention_policies.c.id == policy_id)).mappings().first()
    return _policy_row(row) if row else None


def delete_policy(policy_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(retention_records.delete().where(retention_records.c.policy_id == policy_id))
        conn.execute(retention_policies.delete().where(retention_policies.c.id == policy_id))


# ---------- records --------------------------------------------------------

def _rec_row(row) -> dict:
    return {
        "id": row["id"],
        "policy_id": row["policy_id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "resource_name": row["resource_name"],
        "due_date": row["due_date"],
        "status": row["status"],
        "legal_hold": bool(row["legal_hold"]),
        "actioned_at": row["actioned_at"],
        "created_at": row["created_at"],
    }


def enroll_resource(policy_id: str, connection_id: str, resource_id: str,
                    resource_type: str, resource_name: str | None,
                    start_date: datetime.datetime | None = None) -> dict:
    policy = get_policy(policy_id)
    if policy is None:
        raise ValueError(f"Policy {policy_id} not found")
    base = start_date or datetime.datetime.now(datetime.timezone.utc)
    due = base + datetime.timedelta(days=policy["retention_days"])
    rid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        # Portable stand-in for sqlite's "INSERT OR REPLACE": that construct
        # has no Postgres/Oracle equivalent, and here it must specifically
        # replace (not merge into) any existing row sharing this unique key
        # (connection_id, resource_id, policy_id) — re-enrolling assigns a
        # fresh id and resets due_date/status/legal_hold, same as the
        # original delete-and-insert-on-conflict behavior.
        conn.execute(
            retention_records.delete().where(
                retention_records.c.connection_id == connection_id,
                retention_records.c.resource_id == resource_id,
                retention_records.c.policy_id == policy_id,
            )
        )
        conn.execute(
            retention_records.insert().values(
                id=rid, policy_id=policy_id, connection_id=connection_id, resource_id=resource_id,
                resource_type=resource_type, resource_name=resource_name, due_date=due.isoformat(),
                status="active", legal_hold=False, created_at=now,
            )
        )
        row = conn.execute(sa.select(retention_records).where(retention_records.c.id == rid)).mappings().first()
    return _rec_row(row)


def set_legal_hold(connection_id: str, resource_id: str, hold: bool) -> None:
    with _engine.begin() as conn:
        conn.execute(
            retention_records.update()
            .where(retention_records.c.connection_id == connection_id, retention_records.c.resource_id == resource_id)
            .values(legal_hold=bool(hold))
        )


def list_records(*, connection_id: str | None = None, status: str | None = None,
                 due_before: str | None = None) -> list[dict]:
    stmt = sa.select(retention_records)
    if connection_id:
        stmt = stmt.where(retention_records.c.connection_id == connection_id)
    if status:
        stmt = stmt.where(retention_records.c.status == status)
    if due_before:
        stmt = stmt.where(retention_records.c.due_date <= due_before)
    stmt = stmt.order_by(retention_records.c.due_date)
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_rec_row(r) for r in rows]


def mark_actioned(record_id: str, status: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            retention_records.update()
            .where(retention_records.c.id == record_id)
            .values(status=status, actioned_at=now)
        )


def get_record(record_id: str) -> dict | None:
    stmt = sa.select(retention_records).where(retention_records.c.id == record_id)
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _rec_row(row) if row else None


def set_legal_hold_by_record_id(record_id: str, hold: bool) -> None:
    with _engine.begin() as conn:
        conn.execute(
            retention_records.update()
            .where(retention_records.c.id == record_id)
            .values(legal_hold=bool(hold))
        )


def run_due_check() -> list[dict]:
    """Called by the scheduler.  Returns records that are due and not on legal hold."""
    today = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stmt = sa.select(retention_records).where(
        retention_records.c.status == "active",
        retention_records.c.legal_hold.is_(False),
        retention_records.c.due_date <= today,
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_rec_row(r) for r in rows]
