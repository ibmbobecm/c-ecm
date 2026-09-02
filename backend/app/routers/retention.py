"""Retention policy router."""

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import activity_service, connections_store, retention_service, retention_store
from ..auth import CurrentSession, CurrentUser, get_current_session, get_current_user, require_feature
from ..schemas import (
    RetentionEnrollRequest,
    RetentionPolicyCreateRequest,
    RetentionPolicyOut,
    RetentionRecordOut,
)


class _PolicyPatch(BaseModel):
    active: bool | None = None


class _RecordPatch(BaseModel):
    legal_hold: bool | None = None

router = APIRouter(prefix="/retention", tags=["retention"])

_admin = require_feature("manage_retention")


@router.get("/policies", response_model=list[RetentionPolicyOut])
def list_policies(_user: CurrentUser = Depends(get_current_user)):
    return [RetentionPolicyOut(**p) for p in retention_store.list_policies()]


@router.post("/policies", response_model=RetentionPolicyOut, status_code=201)
def create_policy(req: RetentionPolicyCreateRequest, _admin_user: CurrentUser = Depends(_admin)):
    p = retention_store.create_policy(
        req.name, req.description, req.retention_days, req.action,
        req.class_id, req.connection_id
    )
    return RetentionPolicyOut(**p)


@router.patch("/policies/{policy_id}", response_model=RetentionPolicyOut)
def update_policy(policy_id: str, req: _PolicyPatch, _admin_user: CurrentUser = Depends(_admin)):
    """Partial update — currently only 'active' is patchable from the UI."""
    p = retention_store.update_policy(policy_id, **req.model_dump(exclude_none=True))
    if p is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return RetentionPolicyOut(**p)


@router.delete("/policies/{policy_id}", status_code=204)
def delete_policy(policy_id: str, _admin_user: CurrentUser = Depends(_admin)):
    retention_store.delete_policy(policy_id)


@router.post("/run-now")
def run_now(_admin_user: CurrentUser = Depends(_admin)):
    """Applies due-record actions immediately instead of waiting for the
    hourly scheduler — mainly for an admin who doesn't want to wait, and
    for verifying a new policy actually does what it says."""
    return {"results": retention_service.apply_due_actions()}


@router.get("/records", response_model=list[RetentionRecordOut])
def list_records(
    status: str | None = Query(default=None),
    connection_id: str | None = Query(default=None),
    _user: CurrentUser = Depends(get_current_user),
):
    """List all retention records.  Optionally filter by connection or status."""
    return [
        RetentionRecordOut(**r)
        for r in retention_store.list_records(connection_id=connection_id, status=status)
    ]


@router.post("/records", response_model=RetentionRecordOut, status_code=201)
def enroll_resource(req: RetentionEnrollRequest, session: CurrentSession = Depends(get_current_session)):
    try:
        rec = retention_store.enroll_resource(
            req.policy_id, session.connection_id, req.resource_id, req.resource_type, req.resource_name
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return RetentionRecordOut(**rec)


@router.patch("/records/{record_id}", response_model=RetentionRecordOut)
def update_record(
    record_id: str,
    req: _RecordPatch,
    _admin_user: CurrentUser = Depends(_admin),
):
    """Toggle legal hold on a specific retention record by its record id.

    Admin-only: legal hold is a compliance control — if any authenticated
    user (including a read-only viewer) could clear it, it would provide
    no actual protection against a file being altered/deleted while under
    hold, which defeats the entire point of the feature."""
    rec = retention_store.get_record(record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Retention record not found")
    if req.legal_hold is not None and req.legal_hold != rec["legal_hold"]:
        retention_store.set_legal_hold_by_record_id(record_id, req.legal_hold)
        # This is the path the UI actually calls to toggle legal hold, but
        # unlike the older /records/{resource_id}/legal-hold route it wrote
        # no activity event at all — a compliance control changing with no
        # audit trail of who did it or when. Matching that route's logging
        # so both paths are consistent.
        conn = connections_store.get_connection(rec["connection_id"])
        activity_service.record_event(
            connection_id=rec["connection_id"],
            provider_key=conn["provider_key"] if conn else None,
            resource_type=rec["resource_type"],
            resource_id=rec["resource_id"],
            resource_name=rec["resource_name"],
            event_type="legal_hold_set" if req.legal_hold else "legal_hold_released",
            actor=_admin_user.get("username") or "unknown",
        )
    rec = retention_store.get_record(record_id)
    return RetentionRecordOut(**rec)


@router.post("/records/{resource_id}/legal-hold", status_code=204)
def set_legal_hold(
    resource_id: str,
    hold: bool = Query(default=True),
    session: CurrentSession = Depends(get_current_session),
    _admin_user: CurrentUser = Depends(_admin),
):
    retention_store.set_legal_hold(session.connection_id, resource_id, hold)
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type="file",
        resource_id=resource_id,
        resource_name=None,
        event_type="legal_hold_set" if hold else "legal_hold_released",
        actor=_admin_user.get("username") or "unknown",
    )
