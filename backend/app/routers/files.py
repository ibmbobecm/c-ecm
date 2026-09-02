from fastapi import APIRouter, Depends, HTTPException, Query

from .. import access_control, activity_service, ai_agents_store, comments_store, esignature_store, locks_store, metadata_store, resource_permissions_store, share_links_store, tags_store, workflows_store
from ..access_helpers import to_http
from ..auth import CurrentSession, get_current_session
from ..config import MAX_UPLOAD_BYTES
from ..schemas import FileOut, FileUpdateRequest, FileVersionOut
from ..serializers import file_out, file_version_out
from ..storage_providers.base import ProviderError
from fastapi import File as FastFile, Form, UploadFile

router = APIRouter(prefix="/files", tags=["files"])


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or session.creds.get("username") or "unknown"


def _file_name(session: CurrentSession, file_id: str) -> str | None:
    try:
        return session.provider.get_file(session.creds, file_id).name
    except Exception:
        return None


def _file_name_and_folder(session: CurrentSession, file_id: str) -> tuple[str | None, str | None]:
    try:
        info = session.provider.get_file(session.creds, file_id)
        return info.name, info.folder_id
    except Exception:
        return None, None


def _log(session: CurrentSession, event_type: str, result, extra: dict | None = None) -> None:
    # folder_id always goes in the payload (not just on "moved", where it
    # was already passed explicitly as new_folder_id) -- it's what lets a
    # webhook scoped to a folder match events on files directly inside it,
    # not just the folder resource itself.
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type="file",
        resource_id=result.id,
        resource_name=result.name,
        event_type=event_type,
        actor=_actor(session),
        payload={"folder_id": result.folder_id, **(extra or {})},
    )


@router.post("", response_model=FileOut, status_code=201)
async def upload_file(
    upload: UploadFile = FastFile(...),
    folder_id: str | None = Form(default=None),
    name: str | None = Form(default=None),
    session: CurrentSession = Depends(get_current_session),
):
    if folder_id is not None:
        access_control.require_resource_level(session, folder_id, "folder", "edit")
    data = await upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds maximum upload size")
    file_name = name or upload.filename or "Untitled"
    content_type = upload.content_type or "application/octet-stream"
    try:
        result = session.provider.create_document(session.creds, folder_id, file_name, content_type, data)
    except ProviderError as exc:
        raise to_http(exc)
    _log(session, "created", result, {"size_bytes": result.size_bytes})
    return file_out(result)


@router.get("/{file_id}", response_model=FileOut)
def get_file(file_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, file_id, "file", "view")
    try:
        result = session.provider.get_file(session.creds, file_id)
    except ProviderError as exc:
        raise to_http(exc)
    return file_out(result)


@router.patch("/{file_id}", response_model=FileOut)
def update_file(file_id: str, req: FileUpdateRequest, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, file_id, "file", "edit")
    if req.folder_id is not None:
        access_control.require_resource_level(session, req.folder_id, "folder", "edit")
    try:
        result = None
        if req.name is not None:
            result = session.provider.rename_file(session.creds, file_id, req.name)
            _log(session, "renamed", result)
        if req.move_to_root:
            result = session.provider.move_file(session.creds, file_id, None)
            _log(session, "moved", result, {"new_folder_id": None})
        elif req.folder_id is not None:
            result = session.provider.move_file(session.creds, file_id, req.folder_id)
            _log(session, "moved", result, {"new_folder_id": req.folder_id})
        if result is None:
            result = session.provider.get_file(session.creds, file_id)
    except ProviderError as exc:
        raise to_http(exc)
    return file_out(result)


@router.get("/{file_id}/download")
def download_file(file_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, file_id, "file", "view")
    try:
        info = session.provider.get_file(session.creds, file_id)
        data = session.provider.get_content(session.creds, file_id)
    except ProviderError as exc:
        raise to_http(exc)
    # Covers both the in-app preview (FilePreview.tsx hits this same endpoint
    # to render images/PDF/docx/etc.) and an explicit Download click — the
    # backend can't tell those apart, and for an audit trail "was this
    # document's content accessed" is the meaningful question either way.
    activity_service.record_event(
        connection_id=session.connection_id, provider_key=session.provider_key, resource_type="file",
        resource_id=file_id, resource_name=info.name, event_type="viewed", actor=_actor(session),
    )
    from fastapi.responses import Response
    return Response(
        content=data,
        media_type=info.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{info.name}"'},
    )


@router.get("/{file_id}/versions", response_model=list[FileVersionOut])
def list_versions(file_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, file_id, "file", "view")
    try:
        rows = session.provider.list_versions(session.creds, file_id)
    except ProviderError as exc:
        raise to_http(exc)
    return [file_version_out(r) for r in rows]


@router.post("/{file_id}/versions", response_model=FileOut, status_code=201)
async def upload_new_version(
    file_id: str, upload: UploadFile = FastFile(...), session: CurrentSession = Depends(get_current_session)
):
    access_control.require_resource_level(session, file_id, "file", "edit")
    # Enforce check-out: only the lock holder may upload a new version while
    # the document is checked out.  Other users get 423 Locked.
    lock = locks_store.get_lock(session.connection_id, file_id)
    if lock:
        actor = _actor(session)
        if lock["locked_by"] != actor:
            raise HTTPException(
                status_code=423,
                detail=f'This document is checked out by {lock["locked_by"]} — only they can upload a new version',
            )
    data = await upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds maximum upload size")
    content_type = upload.content_type or "application/octet-stream"
    try:
        result = session.provider.create_version(session.creds, file_id, content_type, data)
    except ProviderError as exc:
        raise to_http(exc)
    _log(session, "version_created", result, {"version_number": result.version_number})
    return file_out(result)


@router.get("/{file_id}/versions/{version_id}/download")
def download_version(file_id: str, version_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, file_id, "file", "view")
    try:
        data = session.provider.get_version_content(session.creds, file_id, version_id)
        info = session.provider.get_file(session.creds, file_id)
        versions = session.provider.list_versions(session.creds, file_id)
    except ProviderError as exc:
        raise to_http(exc)
    version_content_type = next((v.content_type for v in versions if v.id == version_id), None)
    activity_service.record_event(
        connection_id=session.connection_id, provider_key=session.provider_key, resource_type="file",
        resource_id=file_id, resource_name=info.name, event_type="viewed", actor=_actor(session),
        payload={"version_id": version_id},
    )
    from fastapi.responses import Response
    return Response(
        content=data,
        media_type=version_content_type or info.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{info.name}"'},
    )


@router.post("/{file_id}/versions/{version_id}/restore", response_model=FileOut)
def restore_version(file_id: str, version_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, file_id, "file", "edit")
    try:
        result = session.provider.restore_version(session.creds, file_id, version_id)
    except ProviderError as exc:
        raise to_http(exc)
    _log(session, "version_restored", result, {"version_id": version_id})
    return file_out(result)


@router.delete("/{file_id}", status_code=204)
def trash_file(file_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, file_id, "file", "edit")
    name, folder_id = _file_name_and_folder(session, file_id)
    try:
        session.provider.trash_file(session.creds, file_id)
    except ProviderError as exc:
        raise to_http(exc)
    activity_service.record_event(
        connection_id=session.connection_id, provider_key=session.provider_key, resource_type="file",
        resource_id=file_id, resource_name=name, event_type="deleted", actor=_actor(session),
        payload={"folder_id": folder_id},
    )


@router.post("/{file_id}/restore", response_model=FileOut)
def restore_file(file_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, file_id, "file", "edit")
    try:
        result = session.provider.restore_file(session.creds, file_id)
    except ProviderError as exc:
        raise to_http(exc)
    _log(session, "restored", result)
    return file_out(result)


@router.delete("/{file_id}/permanent", status_code=204)
def delete_file_permanent(file_id: str, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, file_id, "file", "edit")
    name = _file_name(session, file_id)
    try:
        session.provider.delete_file(session.creds, file_id)
    except ProviderError as exc:
        raise to_http(exc)
    tags_store.delete_for_resource(session.connection_id, file_id)
    comments_store.delete_for_resource(session.connection_id, file_id)
    share_links_store.delete_for_resource(session.connection_id, file_id)
    metadata_store.delete_for_resource(session.connection_id, file_id)
    esignature_store.delete_for_resource(session.connection_id, file_id)
    workflows_store.delete_for_resource(session.connection_id, file_id)
    ai_agents_store.delete_for_resource(session.connection_id, file_id)
    resource_permissions_store.delete_for_resource(session.connection_id, file_id)
    locks_store.checkin(session.connection_id, file_id)  # release any stale checkout
    activity_service.record_event(
        connection_id=session.connection_id, provider_key=session.provider_key, resource_type="file",
        resource_id=file_id, resource_name=name, event_type="permanently_deleted", actor=_actor(session),
    )
