"""Approval workflow router."""

from fastapi import APIRouter, Depends, HTTPException

from .. import activity_service, notification_service, workflows_store
from ..auth import CurrentSession, CurrentUser, get_current_session, get_current_user, require_role
from ..schemas import (
    WorkflowDefinitionCreateRequest,
    WorkflowDefinitionOut,
    WorkflowInstanceCreateRequest,
    WorkflowInstanceOut,
    WorkflowStepActionRequest,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])

_admin = require_role("admin")


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or "unknown"


# ---------- definitions ----------------------------------------------------

@router.get("/definitions", response_model=list[WorkflowDefinitionOut])
def list_definitions(_user: CurrentUser = Depends(get_current_user)):
    return [WorkflowDefinitionOut(**d) for d in workflows_store.list_definitions()]


@router.post("/definitions", response_model=WorkflowDefinitionOut, status_code=201)
def create_definition(req: WorkflowDefinitionCreateRequest, session: CurrentSession = Depends(get_current_session)):
    d = workflows_store.create_definition(
        req.name, req.description, [s.model_dump() for s in req.steps], _actor(session)
    )
    return WorkflowDefinitionOut(**d)


@router.delete("/definitions/{def_id}", status_code=204)
def delete_definition(def_id: str, _user: CurrentUser = Depends(_admin)):
    if workflows_store.has_in_review_instances(def_id):
        raise HTTPException(
            status_code=409,
            detail="This workflow has requests still awaiting approval — cancel or resolve them first",
        )
    workflows_store.delete_definition(def_id)


# ---------- instances -------------------------------------------------------

@router.get("/instances", response_model=list[WorkflowInstanceOut])
def list_instances(
    resource_id: str | None = None,
    status: str | None = None,
    session: CurrentSession = Depends(get_current_session),
):
    return [
        WorkflowInstanceOut(**i)
        for i in workflows_store.list_instances(
            connection_id=session.connection_id, resource_id=resource_id, status=status
        )
    ]


@router.post("/instances", response_model=WorkflowInstanceOut, status_code=201)
def start_workflow(req: WorkflowInstanceCreateRequest, session: CurrentSession = Depends(get_current_session)):
    if workflows_store.get_definition(req.definition_id) is None:
        raise HTTPException(status_code=404, detail="Workflow definition not found")
    # Try to get a friendly resource name
    resource_name: str | None = None
    try:
        if req.resource_type == "file":
            resource_name = session.provider.get_file(session.creds, req.resource_id).name
        else:
            resource_name = session.provider.get_children(session.creds, req.resource_id).folder.name
    except Exception:
        pass

    inst = workflows_store.create_instance(
        definition_id=req.definition_id,
        connection_id=session.connection_id,
        resource_id=req.resource_id,
        resource_type=req.resource_type,
        resource_name=resource_name,
        requested_by=_actor(session),
        comment=req.comment,
    )
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=req.resource_type,
        resource_id=req.resource_id,
        resource_name=resource_name,
        event_type="workflow_started",
        actor=_actor(session),
        payload={"definition_id": req.definition_id, "instance_id": inst["id"]},
    )
    return WorkflowInstanceOut(**inst)


@router.get("/instances/{instance_id}", response_model=WorkflowInstanceOut)
def get_instance(instance_id: str, _user: CurrentUser = Depends(get_current_user)):
    inst = workflows_store.get_instance(instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    return WorkflowInstanceOut(**inst)


@router.post("/instances/{instance_id}/action", response_model=WorkflowInstanceOut)
def act(instance_id: str, req: WorkflowStepActionRequest, session: CurrentSession = Depends(get_current_session)):
    inst = workflows_store.get_instance(instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    if inst["status"] != "in_review":
        raise HTTPException(status_code=409, detail="This workflow is no longer awaiting action")
    prev_step = inst["current_step"]
    try:
        updated = workflows_store.act_on_step(instance_id, _actor(session), req.action, req.comment)
    except workflows_store.NotAnAuthorizedReviewerError:
        raise HTTPException(status_code=403, detail="You aren't a designated reviewer for this step")
    except workflows_store.AlreadyActedOnStepError:
        raise HTTPException(status_code=409, detail="You've already recorded an action on this step")
    if updated is None:
        raise HTTPException(status_code=409, detail="Action could not be applied")
    # req.action alone doesn't say what actually happened: an "approved" vote
    # might just be one of several needed on this step (nothing changed for
    # anyone else yet), might advance to a *different* step's reviewers, or
    # might be the final approval. Reporting it as "workflow_approved"
    # unconditionally — as this used to do — fired a "your document was
    # approved" notification on every partial vote, well before the
    # workflow was actually done. A rejection is always terminal, so that
    # case doesn't have this ambiguity.
    if updated["status"] == "rejected":
        event_type = "workflow_rejected"
    elif updated["status"] == "approved":
        event_type = "workflow_approved"
    elif updated["current_step"] != prev_step:
        event_type = "workflow_step_advanced"
    else:
        event_type = "workflow_step_voted"
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=inst["resource_type"],
        resource_id=inst["resource_id"],
        resource_name=inst["resource_name"],
        event_type=event_type,
        actor=_actor(session),
        payload={"instance_id": instance_id, "comment": req.comment, "step_index": prev_step},
    )
    return WorkflowInstanceOut(**updated)


@router.post("/instances/{instance_id}/cancel", response_model=WorkflowInstanceOut)
def cancel_instance(instance_id: str, session: CurrentSession = Depends(get_current_session)):
    inst = workflows_store.get_instance(instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    is_admin = "admin" in session.user.get("roles", [])
    if inst["requested_by"] != _actor(session) and not is_admin:
        raise HTTPException(status_code=403, detail="Only the requester or an admin can cancel this request")
    updated = workflows_store.cancel_instance(instance_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found or already completed")
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=inst["resource_type"],
        resource_id=inst["resource_id"],
        resource_name=inst["resource_name"],
        event_type="workflow_cancelled",
        actor=_actor(session),
        payload={"instance_id": instance_id},
    )
    return WorkflowInstanceOut(**updated)
