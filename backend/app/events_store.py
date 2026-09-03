"""Cross-backend activity/audit log — the one place C-ECM can answer
"what happened, on which connection, to which item" uniformly across all
nine backends, none of which expose that in a common shape (some don't
expose it as an API at all). This module is pure storage (Repository
pattern, same shape as connections_store.py/settings_store.py) — recording
policy (who gets notified, etc.) lives in activity_service.py, which wraps
this with an Observer layer. Routers never import this module directly;
they go through activity_service.record_event().
"""

import datetime
import json
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

events = sa.Table(
    "events", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("connection_id", sa.String(32)),
    sa.Column("provider_key", sa.String(64)),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_name", sa.Text),
    sa.Column("event_type", sa.String(64), nullable=False),
    sa.Column("actor", sa.String(255), nullable=False),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Index("idx_events_resource", "connection_id", "resource_id", "created_at"),
    sa.Index("idx_events_created", "created_at"),
    # Added for the admin audit/reporting page — filtering and
    # aggregating by event_type and actor were previously unindexed
    # full-table scans (list_events didn't even accept an `actor` filter
    # at all until this page needed one).
    sa.Index("idx_events_type", "event_type"),
    sa.Index("idx_events_actor", "actor"),
)

_engine = db.get_engine("events")


def init_db() -> None:
    db.create_all(_metadata, "events")


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "provider_key": row["provider_key"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "resource_name": row["resource_name"],
        "event_type": row["event_type"],
        "actor": row["actor"],
        "payload": json.loads(row["payload_json"]),
        "created_at": row["created_at"],
    }


def record_event(
    *,
    connection_id: str | None,
    provider_key: str | None,
    resource_type: str,
    resource_id: str,
    resource_name: str | None,
    event_type: str,
    actor: str,
    payload: dict | None = None,
) -> dict:
    event_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            events.insert().values(
                id=event_id,
                connection_id=connection_id,
                provider_key=provider_key,
                resource_type=resource_type,
                resource_id=resource_id,
                resource_name=resource_name,
                event_type=event_type,
                actor=actor,
                payload_json=json.dumps(payload or {}),
                created_at=now,
            )
        )
        row = conn.execute(sa.select(events).where(events.c.id == event_id)).mappings().first()
    return _row_to_dict(row)


def _build_where(
    *,
    connection_id: str | None,
    resource_id: str | None,
    event_type: str | None,
    event_types: list[str] | None,
    actor: str | None,
    since: str | None,
    until: str | None,
) -> list:
    clauses = []
    if connection_id is not None:
        clauses.append(events.c.connection_id == connection_id)
    if resource_id is not None:
        clauses.append(events.c.resource_id == resource_id)
    if event_type is not None:
        clauses.append(events.c.event_type == event_type)
    if event_types:
        clauses.append(events.c.event_type.in_(event_types))
    if actor is not None:
        clauses.append(events.c.actor == actor)
    if since is not None:
        clauses.append(events.c.created_at >= since)
    if until is not None:
        clauses.append(events.c.created_at <= until)
    return clauses


def list_events(
    *,
    connection_id: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    event_types: list[str] | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    clauses = _build_where(
        connection_id=connection_id, resource_id=resource_id, event_type=event_type, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    stmt = (
        sa.select(events)
        .where(*clauses)
        .order_by(events.c.created_at.desc())
        .limit(min(limit, 500))
        .offset(max(offset, 0))
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_row_to_dict(r) for r in rows]


def count_events(
    *,
    connection_id: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    event_types: list[str] | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> int:
    clauses = _build_where(
        connection_id=connection_id, resource_id=resource_id, event_type=event_type, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    stmt = sa.select(sa.func.count()).select_from(events).where(*clauses)
    with _engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


def aggregate_by_type(
    *,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    event_types: list[str] | None = None,
) -> list[dict]:
    """[{event_type, count}], for the "breakdown by event type" pie chart."""
    clauses = _build_where(
        connection_id=None, resource_id=None, event_type=None, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    count_col = sa.func.count().label("count")
    stmt = (
        sa.select(events.c.event_type, count_col)
        .select_from(events)
        .where(*clauses)
        .group_by(events.c.event_type)
        .order_by(count_col.desc())
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [{"event_type": r["event_type"], "count": r["count"]} for r in rows]


def count_distinct_actors(
    *,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    event_types: list[str] | None = None,
) -> int:
    """Distinct-actor count honoring every filter, including `actor` itself —
    unlike aggregate_by_actor below, whose "most active users" ranking
    deliberately ignores the actor filter so it keeps comparing everyone,
    not just whoever the table happens to be filtered to."""
    clauses = _build_where(
        connection_id=None, resource_id=None, event_type=None, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    stmt = sa.select(sa.func.count(sa.func.distinct(events.c.actor))).select_from(events).where(*clauses)
    with _engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


def aggregate_by_actor(
    *,
    since: str | None = None,
    until: str | None = None,
    event_types: list[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    """[{actor, count}] ordered by activity volume, for "most active users"."""
    clauses = _build_where(
        connection_id=None, resource_id=None, event_type=None, event_types=event_types,
        actor=None, since=since, until=until,
    )
    count_col = sa.func.count().label("count")
    stmt = (
        sa.select(events.c.actor, count_col)
        .select_from(events)
        .where(*clauses)
        .group_by(events.c.actor)
        .order_by(count_col.desc())
        .limit(limit)
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [{"actor": r["actor"], "count": r["count"]} for r in rows]


def aggregate_by_day(
    *,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    event_types: list[str] | None = None,
) -> list[dict]:
    """[{day, count}] (day = "YYYY-MM-DD", UTC) for the events-over-time bar
    chart. created_at is stored as an ISO-8601 string, so a plain substring
    slice is enough to bucket by day without needing SQLite's date functions."""
    clauses = _build_where(
        connection_id=None, resource_id=None, event_type=None, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    # substr(string, start, length) is the one function signature SQLite,
    # PostgreSQL, and Oracle all accept, so this stays func.substr rather
    # than needing a dialect-specific date-truncation function.
    day_col = sa.func.substr(events.c.created_at, 1, 10).label("day")
    count_col = sa.func.count().label("count")
    stmt = (
        sa.select(day_col, count_col)
        .select_from(events)
        .where(*clauses)
        .group_by(day_col)
        .order_by(day_col.asc())
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [{"day": r["day"], "count": r["count"]} for r in rows]


def list_distinct_actors() -> list[str]:
    # COLLATE NOCASE has no portable equivalent across sqlite/postgres/oracle
    # dialects here (postgres/oracle default collations are case-sensitive,
    # and neither exposes a bare "NOCASE" collation name) — func.lower()
    # gives the same case-insensitive ordering portably on all three.
    stmt = (
        sa.select(events.c.actor)
        .distinct()
        .select_from(events)
        .order_by(sa.func.lower(events.c.actor))
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [r["actor"] for r in rows]
