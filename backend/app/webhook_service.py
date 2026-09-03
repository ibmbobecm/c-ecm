"""Outbound webhook registry and dispatcher.

Webhooks give downstream systems real-time awareness of C-ECM events
without polling.  Every event that passes through activity_service is
offered to registered webhooks.  Delivery is attempted synchronously
(in a background thread so it doesn't block the request) with simple
exponential backoff (3 attempts).

Three destination types:
  custom  — the original behavior: the raw event JSON, HMAC-SHA256 signed
            with the webhook's secret so the receiver can verify
            authenticity.
  slack   — a Slack incoming-webhook URL; delivered as {"text": ...}, no
            signature (Slack doesn't check one — the URL itself is the
            secret).
  discord — a Discord webhook URL; delivered as {"content": ...}, same
            reasoning.

The webhook_service subscriber is registered once at startup in main.py's
lifespan alongside the existing notification_service subscriber.
"""

import datetime
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
import time
import uuid
from urllib.parse import urlparse

import requests
import sqlalchemy as sa

from . import crypto_util
from . import db

logger = logging.getLogger("webhook_service")

_metadata = sa.MetaData()

webhooks = sa.Table(
    "webhooks", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("secret", sa.Text),
    sa.Column("event_types_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("last_triggered_at", sa.String(40)),
    sa.Column("last_status_code", sa.Integer),
    sa.Column("connection_id", sa.String(32)),
    sa.Column("resource_id", sa.String(255)),
    sa.Column("resource_type", sa.String(64)),
    sa.Column("resource_name", sa.Text),
    sa.Column("destination_type", sa.String(64), nullable=False, server_default="custom"),
)

webhook_deliveries = sa.Table(
    "webhook_deliveries", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("webhook_id", sa.String(32), nullable=False),
    sa.Column("event_id", sa.String(32)),
    sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
    sa.Column("status_code", sa.Integer),
    sa.Column("error", sa.Text),
    sa.Column("delivered_at", sa.String(40), nullable=False),
)

_engine = db.get_engine("webhooks")


class WebhookUrlError(ValueError):
    pass


def _validate_webhook_url(url: str) -> None:
    """Blocks SSRF: a registered webhook can otherwise make this backend
    issue an authenticated-feeling POST to any address it can reach,
    including internal infrastructure (cloud metadata endpoints at
    169.254.169.254, localhost, RFC1918 ranges, etc.) — a real risk since
    this is an outbound HTTP call the server itself makes on a timer/event,
    not something scoped to the registering user's own network access.
    Validated against the RESOLVED IP, not the hostname string, since a
    DNS name (attacker-controlled or not) can point anywhere; also
    re-checked at delivery time (see _deliver_one) since DNS can change
    between registration and delivery (a rebinding attack)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebhookUrlError("Webhook URL must be http or https")
    if not parsed.hostname:
        raise WebhookUrlError("Webhook URL must include a host")
    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise WebhookUrlError(f"Couldn't resolve webhook host '{parsed.hostname}': {exc}")
    for family, _, _, _, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise WebhookUrlError(f"Webhook URL resolves to a non-public address ({ip}) — not allowed")


def init_db() -> None:
    db.create_all(_metadata, "webhooks")

    # One-time migration: encrypt any webhook secret still holding plain
    # text from before encryption-at-rest existed. ensure_encrypted is
    # idempotent, so this is safe on every startup — an already-encrypted
    # (or empty) secret is read back unchanged and skipped.
    with _engine.begin() as conn:
        rows = conn.execute(sa.select(webhooks.c.id, webhooks.c.secret)).mappings().all()
        for row in rows:
            if not row["secret"]:
                continue
            upgraded = crypto_util.ensure_encrypted(row["secret"])
            if upgraded != row["secret"]:
                conn.execute(webhooks.update().where(webhooks.c.id == row["id"]).values(secret=upgraded))


def _row(row) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "secret": crypto_util.decrypt(row["secret"]) if row["secret"] else None,
        "event_types": json.loads(row["event_types_json"]),
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "last_triggered_at": row["last_triggered_at"],
        "last_status_code": row["last_status_code"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "resource_name": row["resource_name"],
        "destination_type": row["destination_type"],
    }


def list_webhooks() -> list[dict]:
    stmt = sa.select(webhooks).order_by(webhooks.c.created_at)
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_row(r) for r in rows]


def get_webhook(webhook_id: str) -> dict | None:
    stmt = sa.select(webhooks).where(webhooks.c.id == webhook_id)
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _row(row) if row else None


def create_webhook(url: str, secret: str | None, event_types: list[str], *, connection_id: str | None = None,
                   resource_id: str | None = None, resource_type: str | None = None,
                   resource_name: str | None = None, destination_type: str = "custom") -> dict:
    _validate_webhook_url(url)
    wid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            webhooks.insert().values(
                id=wid, url=url, secret=crypto_util.encrypt(secret) if secret else "",
                event_types_json=json.dumps(event_types), active=True, created_at=now,
                connection_id=connection_id, resource_id=resource_id, resource_type=resource_type,
                resource_name=resource_name, destination_type=destination_type,
            )
        )
        row = conn.execute(sa.select(webhooks).where(webhooks.c.id == wid)).mappings().first()
    return _row(row)


def update_webhook(webhook_id: str, *, url: str | None = None, secret: str | None = None,
                   event_types: list[str] | None = None, active: bool | None = None,
                   connection_id: str | None = None, resource_id: str | None = None,
                   resource_type: str | None = None, resource_name: str | None = None,
                   destination_type: str | None = None, clear_scope: bool = False) -> dict | None:
    if url is not None:
        _validate_webhook_url(url)
    updates: dict = {}
    if url is not None:
        updates["url"] = url
    if secret is not None:
        updates["secret"] = crypto_util.encrypt(secret) if secret else ""
    if event_types is not None:
        updates["event_types_json"] = json.dumps(event_types)
    if active is not None:
        updates["active"] = active
    if destination_type is not None:
        updates["destination_type"] = destination_type
    if clear_scope:
        updates.update({"connection_id": None, "resource_id": None, "resource_type": None, "resource_name": None})
    elif resource_id is not None:
        updates.update({
            "connection_id": connection_id, "resource_id": resource_id,
            "resource_type": resource_type, "resource_name": resource_name,
        })
    with _engine.begin() as conn:
        if updates:
            conn.execute(webhooks.update().where(webhooks.c.id == webhook_id).values(**updates))
        row = conn.execute(sa.select(webhooks).where(webhooks.c.id == webhook_id)).mappings().first()
    return _row(row) if row else None


def delete_webhook(webhook_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(webhook_deliveries.delete().where(webhook_deliveries.c.webhook_id == webhook_id))
        conn.execute(webhooks.delete().where(webhooks.c.id == webhook_id))


# ---------- delivery -------------------------------------------------------

def _sign(secret: str, payload: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _format_summary(event: dict, bold: str) -> str:
    """A one-line, human-readable summary of an event -- used for the chat
    destinations (Slack/Discord), which show a message, not a JSON blob.
    `bold` is the platform's own bold-marker (Slack: "*", Discord: "**"),
    wrapped around the actor and resource name."""
    actor = event.get("actor") or "Someone"
    verb = (event.get("event_type") or "updated").replace("_", " ")
    resource_type = event.get("resource_type") or "resource"
    resource = event.get("resource_name") or event.get("resource_id") or "a resource"
    return f'{bold}{actor}{bold} {verb} the {resource_type} {bold}"{resource}"{bold}'


def _deliver_one(webhook: dict, event: dict) -> None:
    try:
        _validate_webhook_url(webhook["url"])
    except WebhookUrlError as exc:
        logger.warning("Skipping delivery to webhook %s: %s", webhook["id"], exc)
        return

    destination = webhook.get("destination_type") or "custom"
    if destination == "slack":
        payload = json.dumps({"text": _format_summary(event, "*")}).encode()
        headers = {"Content-Type": "application/json"}
    elif destination == "discord":
        payload = json.dumps({"content": _format_summary(event, "**")}).encode()
        headers = {"Content-Type": "application/json"}
    else:
        payload = json.dumps(event, default=str).encode()
        signature = _sign(webhook["secret"], payload)
        headers = {
            "Content-Type": "application/json",
            "X-C-ECM-Signature": signature,
            "X-C-ECM-Event": event.get("event_type", ""),
        }
    last_code: int | None = None
    last_error: str | None = None
    for attempt in range(1, 4):
        try:
            # allow_redirects=False: a redirect target bypasses the URL
            # validation above entirely (an attacker's public server can
            # 302 to an internal address) — never follow one automatically.
            resp = requests.post(webhook["url"], data=payload, headers=headers, timeout=10, allow_redirects=False)
            last_code = resp.status_code
            last_error = None
            if resp.ok:
                break
        except Exception as exc:
            last_code = None
            last_error = str(exc)[:500]
        if attempt < 3:
            time.sleep(2 ** attempt)

    # Record delivery outcome
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            webhook_deliveries.insert().values(
                id=uuid.uuid4().hex, webhook_id=webhook["id"], event_id=event.get("id"),
                attempt=attempt, status_code=last_code, error=last_error, delivered_at=now,
            )
        )
        conn.execute(
            webhooks.update().where(webhooks.c.id == webhook["id"]).values(
                last_triggered_at=now, last_status_code=last_code
            )
        )


def _matches_scope(webhook: dict, event: dict) -> bool:
    """A webhook with no resource_id is unscoped -- fires for every event
    (optionally still narrowed to one connection). A scoped webhook fires
    for events directly on that exact file/folder, or -- since a folder is
    almost always what someone actually wants to watch, not just the
    folder object itself -- events on items whose immediate parent is that
    folder. Deeper nesting (a file two folders down) isn't matched: that
    would need walking each event's full ancestor chain back through
    whichever backend (FileNet, an SSH-mounted AS/400, S3, ...) owns it,
    and webhook delivery runs decoupled from any request's credentials, so
    there's nothing to call back into to do that walk."""
    if webhook["connection_id"] and event.get("connection_id") != webhook["connection_id"]:
        return False
    if not webhook["resource_id"]:
        return True
    if event.get("resource_id") == webhook["resource_id"]:
        return True
    payload = event.get("payload") or {}
    return payload.get("folder_id") == webhook["resource_id"] or payload.get("parent_id") == webhook["resource_id"]


def on_event(event: dict) -> None:
    """Activity-service subscriber: dispatches to all matching active webhooks in background threads."""
    event_type = event.get("event_type", "")
    stmt = sa.select(webhooks).where(webhooks.c.active.is_(True))
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    matching = []
    for row in rows:
        types = json.loads(row["event_types_json"])
        wh = _row(row)
        if (not types or event_type in types) and _matches_scope(wh, event):
            matching.append(wh)

    for wh in matching:
        threading.Thread(target=_deliver_one, args=(wh, event), daemon=True).start()
