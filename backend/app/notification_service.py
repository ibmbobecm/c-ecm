"""Observer subscriber: turns activity events into notifications.

Registered once at startup via activity_service.subscribe(on_event).

With multi-user support, notifications fan out to ALL active users so every
team member sees relevant activity.  The `owner` column on each notification
already exists, so no schema change is needed.
"""

from . import notifications_store, users_store

_NOTIFIABLE_EVENT_TYPES = {
    "created",
    "deleted",
    "restored",
    "version_created",
    "version_restored",
    "commented",
    "share_link_created",
    "tagged",
    "checked_out",
    "checked_in",
    "workflow_started",
    "workflow_approved",
    "workflow_rejected",
    "legal_hold_set",
    "legal_hold_released",
}

_MESSAGE_TEMPLATES = {
    "created": '{actor} created "{name}"',
    "deleted": '{actor} deleted "{name}"',
    "restored": '{actor} restored "{name}" from Trash',
    "version_created": '{actor} uploaded a new version of "{name}"',
    "version_restored": '{actor} restored an earlier version of "{name}"',
    "commented": '{actor} commented on "{name}"',
    "share_link_created": '{actor} created a share link for "{name}"',
    "tagged": '{actor} tagged "{name}"',
    "checked_out": '{actor} checked out "{name}"',
    "checked_in": '{actor} checked in "{name}"',
    "workflow_started": '{actor} requested approval for "{name}"',
    "workflow_approved": '{actor} approved "{name}"',
    "workflow_rejected": '{actor} rejected "{name}"',
    "legal_hold_set": 'Legal hold placed on "{name}"',
    "legal_hold_released": 'Legal hold released on "{name}"',
}


def _format_message(event: dict) -> str:
    template = _MESSAGE_TEMPLATES.get(event["event_type"], '{actor} updated "{name}"')
    return template.format(actor=event["actor"], name=event["resource_name"] or "an item")


def on_event(event: dict) -> None:
    if event["event_type"] not in _NOTIFIABLE_EVENT_TYPES:
        return
    message = _format_message(event)
    # Fan out to all active users
    try:
        users = users_store.list_users()
        owners = [u["username"] for u in users if u["is_active"]]
    except Exception:
        owners = []
    if not owners:
        owners = ["admin"]
    for owner in owners:
        try:
            notifications_store.create(owner=owner, event_id=event["id"], message=message)
        except Exception:
            pass


def list_for_owner(owner: str, unread_only: bool = False, limit: int = 50) -> list[dict]:
    return notifications_store.list_for_owner(owner, unread_only=unread_only, limit=limit)


def unread_count(owner: str) -> int:
    return notifications_store.unread_count(owner)


def mark_read(notification_id: str) -> None:
    notifications_store.mark_read(notification_id)


def mark_all_read(owner: str) -> None:
    notifications_store.mark_all_read(owner)
