"""Approval workflow router."""

from fastapi import APIRouter, Depends, HTTPException

from .. import access_control, activity_service, groups_store, notification_service, users_store, workflows_store
from ..auth import CurrentSession, CurrentUser, get_current_session, get_current_user, require_feature
from ..schemas import (
    WorkflowAddResourceRequest,
    WorkflowDefinitionCreateRequest,
    WorkflowDefinitionOut,
    WorkflowInstanceCreateRequest,
    WorkflowInstanceOut,
    WorkflowReassignRequest,
    WorkflowStepActionRequest,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])

_admin = require_feature("manage_workflow_definitions")


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or "unknown"


def _resource_name(session: CurrentSession, resource_id: str, resource_type: str) -> str | None:
    try:
        if resource_type == "file":
            return session.provider.get_file(session.creds, resource_id).name
        return session.provider.get_children(session.creds, resource_id).folder.name
    except Exception:
        return None


def _is_involved(session: CurrentSession, inst: dict) -> bool:
    """Who may reassign a step or add/remove a document on an in-review
    instance: a superadmin, anyone who can design workflows at all, the
    person who requested it, or whoever the current step is presently
    assigned to (direct or via group) — deliberately NOT "any authenticated
    user" even when the step itself is open to anyone, since redirecting
    approval routing is a more sensitive action than casting one vote on
    an already-open step."""
    user = session.user
    if user.get("is_superadmin") or groups_store.user_has_feature(user["id"], "manage_workflow_definitions"):
        return True
    if inst["requested_by"] == _actor(session):
        return True
    if inst["status"] != "in_review":
        return False
    steps = inst["steps"]
    step_idx = inst["current_step"]
    if step_idx >= len(steps):
        return False
    assignees = steps[step_idx].get("assignees") or []
    if not assignees:
        return False
    actor = _actor(session)
    group_ids = {g["id"] for g in groups_store.list_user_groups(user["id"])}
    return any(
        (a["type"] == "user" and a["id"] == actor) or (a["type"] == "group" and a["id"] in group_ids)
        for a in assignees
    )


def _validate_assignees(assignees) -> None:
    for a in assignees:
        if a.type == "user":
            if users_store.get_by_username(a.id) is None:
                raise HTTPException(status_code=400, detail=f'No user with username "{a.id}"')
        elif groups_store.get_group(a.id) is None:
            raise HTTPException(status_code=400, detail="One of the selected groups no longer exists")


# ---------- definitions ----------------------------------------------------

@router.get("/definitions", response_model=list[WorkflowDefinitionOut])
def list_definitions(_user: CurrentUser = Depends(get_current_user)):
    return [WorkflowDefinitionOut(**d) for d in workflows_store.list_definitions()]


@router.post("/definitions", response_model=WorkflowDefinitionOut, status_code=201)
def create_definition(req: WorkflowDefinitionCreateRequest, session: CurrentSession = Depends(get_current_session)):
    for step in req.steps:
        _validate_assignees(step.assignees)
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
    wf_def = workflows_store.get_definition(req.definition_id)
    if wf_def is None:
        raise HTTPException(status_code=404, detail="Workflow definition not found")

    resources = []
    for r in req.resources:
        # Checked per-resource, not just on the first one — otherwise a
        # second/third attached document you don't actually have edit
        # access to would ride along on a request you're allowed to start.
        access_control.require_resource_level(session, r.resource_id, r.resource_type, "edit")
        resources.append({
            "resource_id": r.resource_id,
            "resource_type": r.resource_type,
            "resource_name": _resource_name(session, r.resource_id, r.resource_type),
        })

    inst = workflows_store.create_instance(
        definition_id=req.definition_id,
        connection_id=session.connection_id,
        resources=resources,
        steps=wf_def["steps"],
        requested_by=_actor(session),
        comment=req.comment,
    )
    primary = resources[0]
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=primary["resource_type"],
        resource_id=primary["resource_id"],
        resource_name=primary["resource_name"],
        event_type="workflow_started",
        actor=_actor(session),
        payload={"definition_id": req.definition_id, "instance_id": inst["id"], "resource_count": len(resources)},
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
    group_ids = {g["id"] for g in groups_store.list_user_groups(session.user["id"])}
    try:
        updated = workflows_store.act_on_step(instance_id, _actor(session), group_ids, req.action, req.comment)
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
    primary = inst["resources"][0]
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=primary["resource_type"],
        resource_id=primary["resource_id"],
        resource_name=primary["resource_name"],
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
    is_superadmin = session.user.get("is_superadmin", False)
    if inst["requested_by"] != _actor(session) and not is_superadmin:
        raise HTTPException(status_code=403, detail="Only the requester or a superadmin can cancel this request")
    updated = workflows_store.cancel_instance(instance_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found or already completed")
    primary = inst["resources"][0]
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=primary["resource_type"],
        resource_id=primary["resource_id"],
        resource_name=primary["resource_name"],
        event_type="workflow_cancelled",
        actor=_actor(session),
        payload={"instance_id": instance_id},
    )
    return WorkflowInstanceOut(**updated)


@router.post("/instances/{instance_id}/resources", response_model=WorkflowInstanceOut, status_code=201)
def add_resource(instance_id: str, req: WorkflowAddResourceRequest, session: CurrentSession = Depends(get_current_session)):
    inst = workflows_store.get_instance(instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    if inst["status"] != "in_review":
        raise HTTPException(status_code=409, detail="This workflow is no longer awaiting action")
    if not _is_involved(session, inst):
        raise HTTPException(status_code=403, detail="You aren't involved in this approval request")
    access_control.require_resource_level(session, req.resource_id, req.resource_type, "edit")
    resource_name = _resource_name(session, req.resource_id, req.resource_type)
    updated = workflows_store.add_resource(instance_id, req.resource_id, req.resource_type, resource_name, _actor(session))
    if updated is None:
        raise HTTPException(status_code=409, detail="Could not add this document")
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=req.resource_type,
        resource_id=req.resource_id,
        resource_name=resource_name,
        event_type="workflow_document_added",
        actor=_actor(session),
        payload={"instance_id": instance_id},
    )
    return WorkflowInstanceOut(**updated)


@router.delete("/instances/{instance_id}/resources/{resource_row_id}", response_model=WorkflowInstanceOut)
def remove_resource(instance_id: str, resource_row_id: str, session: CurrentSession = Depends(get_current_session)):
    inst = workflows_store.get_instance(instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    if inst["status"] != "in_review":
        raise HTTPException(status_code=409, detail="This workflow is no longer awaiting action")
    if not _is_involved(session, inst):
        raise HTTPException(status_code=403, detail="You aren't involved in this approval request")
    removed = next((r for r in inst["resources"] if r["id"] == resource_row_id), None)
    try:
        updated = workflows_store.remove_resource(instance_id, resource_row_id)
    except workflows_store.LastResourceError:
        raise HTTPException(status_code=400, detail="A workflow needs at least one document — remove it or cancel the request instead")
    if updated is None:
        raise HTTPException(status_code=404, detail="Document not found on this workflow")
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=removed["resource_type"] if removed else "file",
        resource_id=removed["resource_id"] if removed else "",
        resource_name=removed["resource_name"] if removed else None,
        event_type="workflow_document_removed",
        actor=_actor(session),
        payload={"instance_id": instance_id},
    )
    return WorkflowInstanceOut(**updated)


@router.post("/instances/{instance_id}/reassign", response_model=WorkflowInstanceOut)
def reassign(instance_id: str, req: WorkflowReassignRequest, session: CurrentSession = Depends(get_current_session)):
    inst = workflows_store.get_instance(instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    if inst["status"] != "in_review":
        raise HTTPException(status_code=409, detail="This workflow is no longer awaiting action")
    if not _is_involved(session, inst):
        raise HTTPException(status_code=403, detail="You aren't involved in this approval request")
    _validate_assignees(req.assignees)
    updated = workflows_store.reassign_current_step(instance_id, [a.model_dump() for a in req.assignees])
    if updated is None:
        raise HTTPException(status_code=409, detail="Could not reassign this step")
    primary = inst["resources"][0]
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=primary["resource_type"],
        resource_id=primary["resource_id"],
        resource_name=primary["resource_name"],
        event_type="workflow_reassigned",
        actor=_actor(session),
        payload={"instance_id": instance_id, "comment": req.comment, "step_index": inst["current_step"]},
    )
    return WorkflowInstanceOut(**updated)
