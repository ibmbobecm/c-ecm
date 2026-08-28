from fastapi import APIRouter, Depends, HTTPException

from .. import activity_service, comments_store
from ..auth import CurrentSession, get_current_session
from ..schemas import BulkCommentCountsRequest, CommentCreateRequest, CommentOut, CommentUpdateRequest

router = APIRouter(tags=["comments"])


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or session.creds.get("username") or "unknown"


def _resource_name(session: CurrentSession, resource_id: str, resource_type: str) -> str | None:
    try:
        if resource_type == "file":
            return session.provider.get_file(session.creds, resource_id).name
        return session.provider.get_children(session.creds, resource_id).folder.name
    except Exception:
        return None


@router.post("/resources/comments/counts", response_model=dict[str, int])
def get_bulk_comment_counts(req: BulkCommentCountsRequest, session: CurrentSession = Depends(get_current_session)):
    return comments_store.count_for_resources(session.connection_id, req.resource_ids)


@router.get("/resources/{resource_id}/comments", response_model=list[CommentOut])
def list_comments(resource_id: str, session: CurrentSession = Depends(get_current_session)):
    return [CommentOut(**c) for c in comments_store.list_for_resource(session.connection_id, resource_id)]


@router.post("/resources/{resource_id}/comments", response_model=CommentOut, status_code=201)
def create_comment(resource_id: str, req: CommentCreateRequest, session: CurrentSession = Depends(get_current_session)):
    actor = _actor(session)
    comment = comments_store.create(
        connection_id=session.connection_id,
        resource_id=resource_id,
        resource_type=req.resource_type,
        body=req.body,
        created_by=actor,
        parent_comment_id=req.parent_comment_id,
        mentioned_users=req.mentioned_users,
    )
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=req.resource_type,
        resource_id=resource_id,
        resource_name=_resource_name(session, resource_id, req.resource_type),
        event_type="commented",
        actor=actor,
        payload={"comment_id": comment["id"], "body_preview": req.body[:120]},
    )
    return CommentOut(**comment)


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def update_comment(comment_id: str, req: CommentUpdateRequest, session: CurrentSession = Depends(get_current_session)):
    existing = comments_store.get(comment_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    actor = _actor(session)
    if req.body is not None:
        comments_store.edit(comment_id, req.body)
    if req.resolved is not None:
        comments_store.set_resolved(comment_id, req.resolved, actor if req.resolved else None)
    return CommentOut(**comments_store.get(comment_id))


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(comment_id: str, _session: CurrentSession = Depends(get_current_session)):
    comments_store.delete(comment_id)
