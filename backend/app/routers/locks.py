"""Check-out / check-in lock router."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import access_control, activity_service, locks_store
from ..auth import CurrentSession, get_current_session
from ..schemas import LockOut, CheckoutRequest
from ..storage_providers.base import ProviderError
from ..access_helpers import to_http

router = APIRouter(prefix="/locks", tags=["locks"])


def _current_user(session: CurrentSession) -> str:
    return session.user.get("username") or "unknown"


@router.post("", response_model=LockOut, status_code=201)
def checkout_resource(req: CheckoutRequest, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, req.resource_id, req.resource_type, "edit")
    try:
        lock = locks_store.checkout(
            connection_id=session.connection_id,
            resource_id=req.resource_id,
            locked_by=_current_user(session),
            comment=req.comment,
        )
    except sqlite3.IntegrityError:
        existing = locks_store.get_lock(session.connection_id, req.resource_id)
        raise HTTPException(
            status_code=409,
            detail=f"This document is already checked out by {existing['locked_by'] if existing else 'another user'}",
        )
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=req.resource_type,
        resource_id=req.resource_id,
        resource_name=None,
        event_type="checked_out",
        actor=_current_user(session),
        payload={"comment": req.comment},
    )
    return LockOut(**lock)


@router.delete("/{resource_id}", status_code=204)
def checkin_resource(
    resource_id: str,
    resource_type: str = Query(default="file"),
    session: CurrentSession = Depends(get_current_session),
):
    lock = locks_store.get_lock(session.connection_id, resource_id)
    if lock is None:
        raise HTTPException(status_code=404, detail="This document is not checked out")
    actor = _current_user(session)
    is_superadmin = session.user.get("is_superadmin", False)
    # The lock holder or a superadmin can check in — without the override,
    # a checkout from a departed/unavailable user could never be released
    # again by anyone.
    if lock["locked_by"] != actor and not is_superadmin:
        raise HTTPException(status_code=403, detail="Only the user who checked out this document (or an admin) can check it in")
    locks_store.checkin(session.connection_id, resource_id)
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=None,
        event_type="checked_in",
        actor=actor,
    )


@router.get("/{resource_id}", response_model=LockOut | None)
def get_lock(resource_id: str, resource_type: str = "file", session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, resource_id, resource_type, "view")
    lock = locks_store.get_lock(session.connection_id, resource_id)
    return LockOut(**lock) if lock else None


@router.get("", response_model=list[LockOut])
def list_locks(session: CurrentSession = Depends(get_current_session)):
    return [LockOut(**l) for l in locks_store.list_locks(session.connection_id)]
