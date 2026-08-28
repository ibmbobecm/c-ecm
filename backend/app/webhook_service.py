"""Outbound webhook registry and dispatcher.

Webhooks give downstream systems real-time awareness of FileDrive events
without polling.  Every event that passes through activity_service is
offered to registered webhooks.  Delivery is attempted synchronously
(in a background thread so it doesn't block the request) with simple
exponential backoff (3 attempts).  Each delivery is HMAC-SHA256 signed with
the webhook's secret so the receiver can verify authenticity.

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
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlparse

import requests

from .config import DATA_DIR

logger = logging.getLogger("webhook_service")
_DB_PATH = DATA_DIR / "webhooks.db"


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


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webhooks (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                secret TEXT NOT NULL,
                event_types_json TEXT NOT NULL DEFAULT '[]',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_triggered_at TEXT,
                last_status_code INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id TEXT PRIMARY KEY,
                webhook_id TEXT NOT NULL,
                event_id TEXT,
                attempt INTEGER NOT NULL DEFAULT 1,
                status_code INTEGER,
                error TEXT,
                delivered_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "secret": row["secret"],
        "event_types": json.loads(row["event_types_json"]),
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "last_triggered_at": row["last_triggered_at"],
        "last_status_code": row["last_status_code"],
    }


def list_webhooks() -> list[dict]:
    conn = _conn()
    try:
        return [_row(r) for r in conn.execute("SELECT * FROM webhooks ORDER BY created_at").fetchall()]
    finally:
        conn.close()


def get_webhook(webhook_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def create_webhook(url: str, secret: str, event_types: list[str]) -> dict:
    _validate_webhook_url(url)
    wid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO webhooks (id, url, secret, event_types_json, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (wid, url, secret, json.dumps(event_types), now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM webhooks WHERE id = ?", (wid,)).fetchone()
        return _row(row)
    finally:
        conn.close()


def update_webhook(webhook_id: str, *, url: str | None = None, secret: str | None = None,
                   event_types: list[str] | None = None, active: bool | None = None) -> dict | None:
    if url is not None:
        _validate_webhook_url(url)
    conn = _conn()
    try:
        updates = []
        if url is not None:
            updates.append(("url", url))
        if secret is not None:
            updates.append(("secret", secret))
        if event_types is not None:
            updates.append(("event_types_json", json.dumps(event_types)))
        if active is not None:
            updates.append(("active", int(active)))
        if updates:
            set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
            conn.execute(f"UPDATE webhooks SET {set_clause} WHERE id = ?", (*[v for _, v in updates], webhook_id))
            conn.commit()
        row = conn.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def delete_webhook(webhook_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM webhook_deliveries WHERE webhook_id = ?", (webhook_id,))
        conn.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
        conn.commit()
    finally:
        conn.close()


# ---------- delivery -------------------------------------------------------

def _sign(secret: str, payload: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _deliver_one(webhook: dict, event: dict) -> None:
    try:
        _validate_webhook_url(webhook["url"])
    except WebhookUrlError as exc:
        logger.warning("Skipping delivery to webhook %s: %s", webhook["id"], exc)
        return
    payload = json.dumps(event, default=str).encode()
    signature = _sign(webhook["secret"], payload)
    headers = {
        "Content-Type": "application/json",
        "X-FileDrive-Signature": signature,
        "X-FileDrive-Event": event.get("event_type", ""),
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
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO webhook_deliveries (id, webhook_id, event_id, attempt, status_code, error, delivered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, webhook["id"], event.get("id"), attempt, last_code, last_error, now),
        )
        conn.execute(
            "UPDATE webhooks SET last_triggered_at = ?, last_status_code = ? WHERE id = ?",
            (now, last_code, webhook["id"]),
        )
        conn.commit()
    finally:
        conn.close()


def on_event(event: dict) -> None:
    """Activity-service subscriber: dispatches to all matching active webhooks in background threads."""
    event_type = event.get("event_type", "")
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM webhooks WHERE active = 1").fetchall()
        matching = []
        for row in rows:
            types = json.loads(row["event_types_json"])
            if not types or event_type in types:
                matching.append(_row(row))
    finally:
        conn.close()

    for wh in matching:
        threading.Thread(target=_deliver_one, args=(wh, event), daemon=True).start()
