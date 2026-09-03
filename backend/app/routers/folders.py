from fastapi import APIRouter, Depends, HTTPException, Query

from .. import access_control, activity_service, ai_agents_store, comments_store, metadata_store, resource_permissions_store, share_links_store, tags_store, workflows_store
from ..access_helpers import to_http
from ..auth import CurrentSession, get_current_session
from ..schemas import FolderContentsOut, FolderCreateRequest, FolderOut, FolderUpdateRequest
from ..serializers import folder_contents_out, folder_out
from ..storage_providers.base import ProviderError

router = APIRouter(prefix="/folders", tags=["folders"])


def _folder_name(session: CurrentSession, folder_id: str) -> str | None:
    try:
        return session.provider.get_children(session.creds, folder_id).folder.name
    except Exception:
        return None


def _folder_name_and_parent(session: CurrentSession, folder_id: str) -> tuple[str | None, str | None]:
    try:
        folder = session.provider.get_children(session.creds, folder_id).folder
        return folder.name, folder.parent_id
    except Exception:
        return None, None


def collect_descendants(session: CurrentSession, folder_id: str) -> list[dict]:
    """Walks a folder's entire subtree (files and subfolders, at any depth)
    and returns each as {"resource_id", "resource_type"}. Two callers:

    - trash_folder (below) snapshots this so a later permanent-delete can
      clean up tags/comments/share-links on every descendant too, not just
      the folder itself. Must run at TRASH time, not at permanent-delete
      time: once a folder is trashed, most providers' get_children() 404s
      on it (local disk's query filters deleted_at IS NULL on the folder
      itself), so this is the only point where the subtree is still
      walkable — the result is tucked into the trash event's payload and
      read back later.
    - the metadata router's "apply to children" option on a folder's Set
      Metadata, which needs the same live subtree walk to cascade a
      document class/values onto everything inside."""
    descendants: list[dict] = []
    try:
        contents = session.provider.get_children(session.creds, folder_id)
    except Exception:
        return descendants
    for f in contents.files:
        descendants.append({"resource_id": f.id, "resource_type": "file"})
    for sub in contents.folders:
        descendants.append({"resource_id": sub.id, "resource_type": "folder"})
        descendants.extend(collect_descendants(session, sub.id))
    return descendants


def _is_in_subtree(session: CurrentSession, root_id: str, target_id: str) -> bool:
    """True if target_id is root_id itself, or lives anywhere inside its
    subtree. Every provider only exposes generic get_children(), so this
    is checked here in the router rather than per-provider — it's the one
    validation that has to hold no matter which of the nine backends is
    active, and the API can't rely on the frontend's own MoveDialog
    filtering to be the only thing enforcing it."""
    if target_id == root_id:
        return True
    try:
        contents = session.provider.get_children(session.creds, root_id)
    except Exception:
        return False
    return any(_is_in_subtree(session, sub.id, target_id) for sub in contents.folders)


def _cleanup_local_data(session: CurrentSession, resource_id: str, resource_type: str) -> None:
    tags_store.delete_for_resource(session.connection_id, resource_id)
    comments_store.delete_for_resource(session.connection_id, resource_id)
    share_links_store.delete_for_resource(session.connection_id, resource_id)
    metadata_store.delete_for_resource(session.connection_id, resource_id)
    workflows_store.delete_for_resource(session.connection_id, resource_id)
    ai_agents_store.delete_for_resource(session.connection_id, resource_id)
    resource_permissions_store.delete_for_resource(session.connection_id, resource_id)


def _cleanup_local_data_batch(session: CurrentSession, resource_ids: list[str]) -> None:
    """Same 7-store cleanup as _cleanup_local_data(), for many resources at
    once — one connect/commit per store instead of one per store PER
    resource. Used when permanently deleting a folder with descendants:
    calling _cleanup_local_data() in a Python loop meant 7 fresh SQLite
    connections opened per descendant."""
    connection_id = session.connection_id
    tags_store.delete_for_resources_batch(connection_id, resource_ids)
    comments_store.delete_for_resources_batch(connection_id, resource_ids)
    share_links_store.delete_for_resources_batch(connection_id, resource_ids)
    metadata_store.delete_for_resources_batch(connection_id, resource_ids)
    workflows_store.delete_for_resources_batch(connection_id, resource_ids)
    ai_agents_store.delete_for_resources_batch(connection_id, resource_ids)
    resource_permissions_store.delete_for_resources_batch(connection_id, resource_ids)


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or session.creds.get("username") or "unknown"


def _log(session: CurrentSession, event_type: str, result, extra: dict | None = None) -> None:
    # parent_id always goes in the payload (not just on "moved", where it
    # was already passed explicitly as new_parent_id) -- it's what lets a
    # webhook scoped to a folder match events on subfolders created
    # directly inside it, not just the folder resource itself.
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type="folder",
        resource_id=result.id,
        resource_name=result.name,
        event_type=event_type,
        actor=_actor(session),
        payload={"parent_id": result.parent_id, **(extra or {})},
    )


@router.post("", response_model=FolderOut, status_code=201)
def create_folder(req: FolderCreateRequest, session: CurrentSession = Depends(get_current_session)):
    if req.parent_id is not None:
        access_control.require_resource_level(session, req.parent_id, "folder", "edit")
    try:
        result = session.provider.create_folder(session.creds, req.parent_id, req.name)
    except ProviderError as exc:
        raise to_http(exc)
    _log(session, "created", result)
    return folder_out(result)


@router.get("/contents", response_model=FolderContentsOut)
def list_contents(
    folder_id: str | None = Query(default=None),
    view: str = Query(default="mine", pattern=r"^(mine|trash)$"),
    session: CurrentSession = Depends(get_current_session),
):
    # Trash listing is intentionally not resource-permission-filtered here
    # — a trashed item's live ancestor chain often can't be walked anymore
    # (many providers 404 get_children on a trashed folder), so there's no
    # reliable way to resolve what it inherits. Known scope limit, not an
    # oversight: trash is a comparatively low-stakes view (nothing there
    # is directly usable without restoring it first, which IS gated below).
    if view != "trash" and folder_id is not None:
        access_control.require_resource_level(session, folder_id, "folder", "view")
    try:
        if view == "trash":
            result = session.provider.list_trash(session.creds)
        else:
            result = session.provider.get_children(session.creds, folder_id)
    except ProviderError as exc:
        raise to_http(exc)
    return folder_contents_out(result)


@router.patch("/{folder_id}", response_model=FolderOut)
def update_folder(folder_id: str, req: FolderUpdateRequest, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, folder_id, "folder", "edit")
    if req.parent_id is not None:
        access_control.require_resource_level(session, req.parent_id, "folder", "edit")
    if req.parent_id is not None and _is_in_subtree(session, folder_id, req.parent_id):
        raise HTTPException(status_code=400, detail="Can't move a folder into itself or one of its own subfolders")
    try:
        result = None
        if req.name is not None:
            result = session.provider.rename_folder(session.creds, folder_id, req.name)
            _log(session, "renamed", result)
        if req.move_to_root:
            result = session.provider.move_folder(session.creds, folder_id, None)
            _log(session, "moved", result, {"new_parent_id": None})
        elif req.parent_id is not None:
            result = session.provider.move_folder(session.creds, folder_id, req.parent_id)
            _log(session, "moved", result, {"new_parent_id": req.parent_id})
    except ProviderError as exc:
        raise to_http(exc)
    return folder_out(result)


@router.delete("/{folder_id}", status_code=204)
def trash_folder(folder_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, folder_id, "folder", "edit")
    name, parent_id = _folder_name_and_parent(session, folder_id)
    descendants = collect_descendants(session, folder_id)
    try:
        session.provider.trash_folder(session.creds, folder_id)
    except ProviderError as exc:
        raise to_http(exc)
    payload: dict = {"parent_id": parent_id}
    if descendants:
        payload["descendants"] = descendants
    activity_service.record_event(
        connection_id=session.connection_id, provider_key=session.provider_key, resource_type="folder",
        resource_id=folder_id, resource_name=name, event_type="deleted", actor=_actor(session),
        payload=payload,
    )


@router.post("/{folder_id}/restore", response_model=FolderOut)
def restore_folder(folder_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, folder_id, "folder", "edit")
    try:
        result = session.provider.restore_folder(session.creds, folder_id)
    except ProviderError as exc:
        raise to_http(exc)
    _log(session, "restored", result)
    return folder_out(result)


@router.delete("/{folder_id}/permanent", status_code=204)
def delete_folder_permanent(folder_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, folder_id, "folder", "edit")
    name = _folder_name(session, folder_id)
    # The subtree snapshot taken when this folder was trashed (see
    # trash_folder above) — the only place that walk is still possible.
    trash_events = activity_service.list_events(
        connection_id=session.connection_id, resource_id=folder_id, event_type="deleted", limit=1
    )
    descendants = trash_events[0]["payload"].get("descendants", []) if trash_events else []
    try:
        session.provider.delete_folder(session.creds, folder_id)
    except ProviderError as exc:
        raise to_http(exc)
    _cleanup_local_data_batch(session, [folder_id, *(d["resource_id"] for d in descendants)])
    activity_service.record_event(
        connection_id=session.connection_id, provider_key=session.provider_key, resource_type="folder",
        resource_id=folder_id, resource_name=name, event_type="permanently_deleted", actor=_actor(session),
    )
