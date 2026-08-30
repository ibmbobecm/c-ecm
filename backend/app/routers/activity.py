"""Admin audit log & reporting.

GET /activity was previously reachable by any authenticated user with no
role check at all — every user could enumerate every action across every
connection, including other people's. This whole module is now admin-only,
since that's the actual security boundary an audit trail needs.
"""
import csv
import datetime
import io
import json
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from .. import activity_service
from ..auth import CurrentUser, require_role
from ..schemas import ActivityAlertOut, ActivityEventOut, ActivitySummaryOut

router = APIRouter(prefix="/activity", tags=["activity"])

_admin = require_role("admin")

# Burst-detection thresholds for "alarming activity" — deliberately only
# covering what this event log can actually support (no IP/geo data exists,
# so "new location" or similar isn't detectable): repeated failed logins
# (credential-stuffing/brute-force signal) and unusually fast bulk deletes
# (accidental or malicious mass-deletion signal).
_ALERT_RULES = [
    {"event_types": ["login_failed"], "threshold": 3, "window_minutes": 15,
     "severity": "danger", "title": "Repeated failed login attempts", "label": "failed login attempts"},
    {"event_types": ["deleted", "permanently_deleted"], "threshold": 5, "window_minutes": 10,
     "severity": "warning", "title": "Bulk delete activity", "label": "delete events"},
]


def _parse_iso(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts)


def _burst_alerts(
    events: list[dict], *, threshold: int, window_minutes: int, severity: str, title: str, label: str, event_type: str
) -> list[ActivityAlertOut]:
    by_actor: dict[str, list[datetime.datetime]] = defaultdict(list)
    for e in events:
        by_actor[e["actor"]].append(_parse_iso(e["created_at"]))

    window = datetime.timedelta(minutes=window_minutes)
    alerts: list[ActivityAlertOut] = []
    for actor, times in by_actor.items():
        times.sort()
        best_count, best_start, best_end, left = 0, None, None, 0
        for right in range(len(times)):
            while times[right] - times[left] > window:
                left += 1
            count = right - left + 1
            if count > best_count:
                best_count, best_start, best_end = count, times[left], times[right]
        if best_count >= threshold:
            alerts.append(ActivityAlertOut(
                severity=severity,
                title=title,
                detail=f"{best_count} {label} by {actor} within {window_minutes} minutes",
                actor=actor,
                event_type=event_type,
                count=best_count,
                window_start=best_start.isoformat(),
                window_end=best_end.isoformat(),
            ))
    return alerts


def _detect_alerts(since: str | None, until: str | None) -> list[ActivityAlertOut]:
    alerts: list[ActivityAlertOut] = []
    for rule in _ALERT_RULES:
        events = activity_service.list_events(event_types=rule["event_types"], since=since, until=until, limit=500)
        alerts.extend(_burst_alerts(
            events, threshold=rule["threshold"], window_minutes=rule["window_minutes"],
            severity=rule["severity"], title=rule["title"], label=rule["label"], event_type=rule["event_types"][0],
        ))
    # Worst first
    alerts.sort(key=lambda a: (a.severity != "danger", -a.count))
    return alerts


@router.get("", response_model=list[ActivityEventOut])
def list_activity(
    connection_id: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    event_types: list[str] | None = Query(default=None),
    actor: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    _admin: CurrentUser = Depends(_admin),
):
    events = activity_service.list_events(
        connection_id=connection_id, resource_id=resource_id, event_type=event_type, event_types=event_types,
        actor=actor, since=since, until=until, limit=limit, offset=offset,
    )
    return [ActivityEventOut(**e) for e in events]


@router.get("/count")
def count_activity(
    connection_id: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    event_types: list[str] | None = Query(default=None),
    actor: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    _admin: CurrentUser = Depends(_admin),
):
    total = activity_service.count_events(
        connection_id=connection_id, resource_id=resource_id, event_type=event_type, event_types=event_types,
        actor=actor, since=since, until=until,
    )
    return {"total": total}


@router.get("/actors", response_model=list[str])
def list_actors(_admin: CurrentUser = Depends(_admin)):
    return activity_service.list_distinct_actors()


@router.get("/summary", response_model=ActivitySummaryOut)
def activity_summary(
    actor: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    event_types: list[str] | None = Query(default=None),
    _admin: CurrentUser = Depends(_admin),
):
    total = activity_service.count_events(actor=actor, since=since, until=until, event_types=event_types)
    # Distinct-actor count honors the actor filter (so filtering to one user
    # correctly shows 1, not the whole team) — by_actor below deliberately
    # does not, since its ranking is only useful compared across everyone,
    # and is separately capped at 20 rows, so len(by_actor) was never a
    # correct substitute for this even before the actor-filter gap.
    unique_actors = activity_service.count_distinct_actors(actor=actor, since=since, until=until, event_types=event_types)
    by_type = activity_service.aggregate_by_type(actor=actor, since=since, until=until, event_types=event_types)
    by_actor = activity_service.aggregate_by_actor(since=since, until=until, event_types=event_types)
    by_day = activity_service.aggregate_by_day(actor=actor, since=since, until=until, event_types=event_types)
    # Alerts intentionally ignore the actor/event_type filters — a burst by
    # one user shouldn't disappear just because the table above is filtered
    # to someone else; they only respect the date range being reported on.
    alerts = _detect_alerts(since, until)
    return ActivitySummaryOut(
        total_events=total,
        unique_actors=unique_actors,
        by_type=by_type,
        by_actor=by_actor,
        by_day=by_day,
        alerts=alerts,
    )


@router.get("/export.csv")
def export_activity_csv(
    connection_id: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    event_types: list[str] | None = Query(default=None),
    actor: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    _admin: CurrentUser = Depends(_admin),
):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["created_at", "actor", "event_type", "resource_type", "resource_name", "resource_id", "connection_id", "payload"])
    # list_events caps a single call at 500 — page through in batches so a
    # filtered export larger than that isn't silently truncated.
    offset = 0
    while True:
        batch = activity_service.list_events(
            connection_id=connection_id, resource_id=resource_id, event_type=event_type, event_types=event_types,
            actor=actor, since=since, until=until, limit=500, offset=offset,
        )
        if not batch:
            break
        for e in batch:
            writer.writerow([
                e["created_at"], e["actor"], e["event_type"], e["resource_type"],
                e["resource_name"] or "", e["resource_id"], e["connection_id"] or "", json.dumps(e["payload"]),
            ])
        offset += len(batch)
        if len(batch) < 500:
            break
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="activity-export.csv"'},
    )
