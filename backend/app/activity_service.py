"""Observer subject wrapping events_store: record_event() persists the
event, then calls every registered listener synchronously — in-process,
no message queue, matching this app's SQLite-only infrastructure. This is
how notifications (and any future consumer) react to activity without
events_store or the routers needing to know they exist. subscribe() is
called once at startup (see main.py's lifespan); a listener that raises
must never break the action that triggered it, so failures are logged and
swallowed here, not propagated.
"""

import logging
from typing import Callable

from . import events_store

logger = logging.getLogger("activity_service")

_listeners: list[Callable[[dict], None]] = []


def subscribe(listener: Callable[[dict], None]) -> None:
    _listeners.append(listener)


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
    event = events_store.record_event(
        connection_id=connection_id,
        provider_key=provider_key,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        event_type=event_type,
        actor=actor,
        payload=payload,
    )
    for listener in _listeners:
        try:
            listener(event)
        except Exception:
            logger.exception("Activity listener failed for event %s (%s)", event["id"], event["event_type"])
    return event


def list_events(**kwargs) -> list[dict]:
    return events_store.list_events(**kwargs)


def count_events(**kwargs) -> int:
    return events_store.count_events(**kwargs)


def count_distinct_actors(**kwargs) -> int:
    return events_store.count_distinct_actors(**kwargs)


def aggregate_by_type(**kwargs) -> list[dict]:
    return events_store.aggregate_by_type(**kwargs)


def aggregate_by_actor(**kwargs) -> list[dict]:
    return events_store.aggregate_by_actor(**kwargs)


def aggregate_by_day(**kwargs) -> list[dict]:
    return events_store.aggregate_by_day(**kwargs)


def list_distinct_actors() -> list[str]:
    return events_store.list_distinct_actors()
