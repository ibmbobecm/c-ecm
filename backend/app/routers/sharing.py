import datetime
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import access_control, activity_service, connections_store, share_links_store
from ..access_helpers import to_http
from ..auth import CurrentSession, get_current_session
from ..schemas import ShareLinkCreateRequest, ShareLinkOut
from ..storage_providers.base import ProviderError
from ..storage_providers.registry import get_provider

router = APIRouter(tags=["sharing"])

# Deliberately no APIRouter prefix shared with the authenticated endpoints
# below — GET /share/{token} is the one route in this app a visitor with
# no C-ECM login reaches at all, so it can't sit behind
# get_current_session like everything else.
public_router = APIRouter(tags=["sharing-public"])

# GET /share/{token}?password=... has no session/rate-limiting layer in
# front of it by design (it's the one deliberately unauthenticated route
# in the app), which means a password-protected link is otherwise
# brute-forceable with unlimited attempts. In-process/in-memory is
# consistent with how auth.py tracks app sessions — good enough for a
# single-process local deployment, not meant to survive a restart.
_FAILED_ATTEMPTS_LOCK = threading.Lock()
_failed_attempts: dict[str, list[float]] = {}
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 300


def _check_rate_limit(token: str) -> None:
    now = time.monotonic()
    with _FAILED_ATTEMPTS_LOCK:
        attempts = [t for t in _failed_attempts.get(token, []) if now - t < _WINDOW_SECONDS]
        _failed_attempts[token] = attempts
        if len(attempts) >= _MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Too many attempts on this link — try again later")


def _record_failed_attempt(token: str) -> None:
    with _FAILED_ATTEMPTS_LOCK:
        _failed_attempts.setdefault(token, []).append(time.monotonic())


def _clear_attempts(token: str) -> None:
    with _FAILED_ATTEMPTS_LOCK:
        _failed_attempts.pop(token, None)


def _resource_name(session: CurrentSession, resource_id: str, resource_type: str) -> str | None:
    try:
        if resource_type == "file":
            return session.provider.get_file(session.creds, resource_id).name
        return session.provider.get_children(session.creds, resource_id).folder.name
    except Exception:
        return None


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or session.creds.get("username") or "unknown"


@router.post("/resources/{resource_id}/share-links", response_model=ShareLinkOut, status_code=201)
def create_share_link(resource_id: str, req: ShareLinkCreateRequest, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, resource_id, req.resource_type, "edit")
    expires_at = None
    if req.expires_in_days:
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=req.expires_in_days)
    try:
        link = session.provider.create_share_link(
            session.creds, session.connection_id, resource_id, req.resource_type, req.role, expires_at, req.password
        )
    except ProviderError as exc:
        raise to_http(exc)
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=req.resource_type,
        resource_id=resource_id,
        resource_name=_resource_name(session, resource_id, req.resource_type),
        event_type="share_link_created",
        actor=_actor(session),
        payload={"role": req.role},
    )
    return ShareLinkOut(id=link.id, url=link.url, role=link.role, expires_at=link.expires_at, password_protected=link.password_protected)


@router.get("/resources/{resource_id}/share-links", response_model=list[ShareLinkOut])
def list_share_links(resource_id: str, resource_type: str = Query(default="file"), session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, resource_id, resource_type, "edit")
    try:
        links = session.provider.list_share_links(session.creds, session.connection_id, resource_id, resource_type)
    except ProviderError as exc:
        raise to_http(exc)
    return [
        ShareLinkOut(id=l.id, url=l.url, role=l.role, expires_at=l.expires_at, password_protected=l.password_protected)
        for l in links
    ]


@router.delete("/share-links/{resource_id}/{link_id}", status_code=204)
def revoke_share_link(resource_id: str, link_id: str, resource_type: str = Query(default="file"), session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, resource_id, resource_type, "edit")
    try:
        session.provider.revoke_share_link(session.creds, session.connection_id, resource_id, resource_type, link_id)
    except ProviderError as exc:
        raise to_http(exc)


@public_router.get("/share/{token}")
def open_share_link(token: str, password: str | None = Query(default=None)):
    entry = share_links_store.resolve(token)
    if entry is None:
        raise HTTPException(status_code=404, detail="This link is invalid, expired, or has been revoked")
    if entry["password_protected"]:
        _check_rate_limit(token)
        if not share_links_store.check_password(entry, password):
            _record_failed_attempt(token)
            raise HTTPException(status_code=401, detail="Password required or incorrect")
        _clear_attempts(token)
    if entry["resource_type"] != "file":
        raise HTTPException(status_code=400, detail="Only file share links can be opened directly")

    creds_entry = connections_store.get_creds(entry["connection_id"])
    if creds_entry is None:
        raise HTTPException(status_code=404, detail="The connection behind this link no longer exists")
    provider_key, creds = creds_entry
    try:
        provider = get_provider(provider_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="The backend behind this link is no longer available")

    try:
        info = provider.get_file(creds, entry["resource_id"])
        data = provider.get_content(creds, entry["resource_id"])
    except ProviderError as exc:
        raise to_http(exc)
    return Response(
        content=data,
        media_type=info.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{info.name}"'},
    )
