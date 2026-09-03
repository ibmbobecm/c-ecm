from fastapi import APIRouter, Depends, HTTPException

from .. import access_control, activity_service, tags_store
from ..auth import CurrentSession, get_app_session, get_current_session, require_feature
from ..schemas import BulkTagsRequest, TagAttachRequest, TagCreateRequest, TagOut

router = APIRouter(tags=["tags"])

_manage_tags = require_feature("manage_tags")

# get_bulk_resource_tags (below) has no per-resource check for the same
# reason as comments.py's bulk counts endpoint — it fans out over a whole
# folder listing at once; tag *names* on many resources aren't sensitive
# enough to justify an ancestor walk per item. Single-resource tag routes
# below ARE checked, at "view" — tagging something you can see doesn't
# need full edit rights. Deleting a tag *definition* is different: it's a
# global, cascading action (every attachment of it on every connection
# disappears at once), so — unlike attach/detach — that one needs a real
# feature gate rather than just being logged in.


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or session.creds.get("username") or "unknown"


def _resource_name(session: CurrentSession, resource_id: str, resource_type: str) -> str | None:
    # Best-effort only — a nicer activity-log message is worth one extra
    # provider round-trip, but never worth failing the tag/comment action
    # over if the backend hiccups on this lookup.
    try:
        if resource_type == "file":
            return session.provider.get_file(session.creds, resource_id).name
        return session.provider.get_children(session.creds, resource_id).folder.name
    except Exception:
        return None


@router.get("/tags", response_model=list[TagOut])
def list_tags(_session_id: str = Depends(get_app_session)):
    return [TagOut(**t) for t in tags_store.list_tags()]


@router.post("/tags", response_model=TagOut, status_code=201)
def create_tag(req: TagCreateRequest, _session_id: str = Depends(get_app_session)):
    return TagOut(**tags_store.get_or_create_tag(req.name, req.color))


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: str, _user=Depends(_manage_tags)):
    tags_store.delete_tag(tag_id)


@router.post("/resources/tags/bulk", response_model=dict[str, list[TagOut]])
def get_bulk_resource_tags(req: BulkTagsRequest, session: CurrentSession = Depends(get_current_session)):
    result = tags_store.get_tags_for_resources(session.connection_id, req.resource_ids)
    return {rid: [TagOut(**t) for t in tags] for rid, tags in result.items()}


@router.get("/resources/{resource_id}/tags", response_model=list[TagOut])
def get_resource_tags(resource_id: str, resource_type: str = "file", session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, resource_id, resource_type, "view")
    return [TagOut(**t) for t in tags_store.get_tags_for_resource(session.connection_id, resource_id)]


@router.post("/resources/{resource_id}/tags", response_model=list[TagOut], status_code=201)
def attach_tag(resource_id: str, req: TagAttachRequest, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, resource_id, req.resource_type, "view")
    tag = next((t for t in tags_store.list_tags() if t["id"] == req.tag_id), None)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    actor = _actor(session)
    tags_store.tag_resource(session.connection_id, resource_id, req.resource_type, req.tag_id, actor)
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=req.resource_type,
        resource_id=resource_id,
        resource_name=_resource_name(session, resource_id, req.resource_type),
        event_type="tagged",
        actor=actor,
        payload={"tag_name": tag["name"]},
    )
    return [TagOut(**t) for t in tags_store.get_tags_for_resource(session.connection_id, resource_id)]


@router.delete("/resources/{resource_id}/tags/{tag_id}", status_code=204)
def detach_tag(resource_id: str, tag_id: str, resource_type: str = "file", session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, resource_id, resource_type, "view")
    tags_store.untag_resource(session.connection_id, resource_id, tag_id)
