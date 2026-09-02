"""Document classes and custom metadata router.

Routes are aliased so both the legacy path (/document-classes) and the
convenience shorthand (/metadata/classes, /metadata/resource/:id) work —
the frontend uses the shorthand; existing integrations can use either.
"""

from fastapi import APIRouter, Depends, HTTPException

from .. import access_control, metadata_store
from ..auth import CurrentSession, CurrentUser, get_current_session, get_current_user, require_feature
from ..schemas import (
    DocumentClassCreateRequest,
    DocumentClassOut,
    DocumentClassUpdateRequest,
    ResourceMetadataHistoryEntryOut,
    ResourceMetadataOut,
    ResourceMetadataSetRequest,
)
from .folders import collect_descendants

router = APIRouter(tags=["metadata"])

_admin = require_feature("manage_document_classes")


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or session.creds.get("username") or "unknown"


# ---------- document classes -----------------------------------------------

@router.get("/document-classes", response_model=list[DocumentClassOut])
@router.get("/metadata/classes", response_model=list[DocumentClassOut])
def list_classes(_user: CurrentUser = Depends(get_current_user)):
    return [DocumentClassOut(**c) for c in metadata_store.list_classes()]


@router.post("/document-classes", response_model=DocumentClassOut, status_code=201)
@router.post("/metadata/classes", response_model=DocumentClassOut, status_code=201)
def create_class(req: DocumentClassCreateRequest, _admin: CurrentUser = Depends(_admin)):
    cls = metadata_store.create_class(req.name, req.description, [f.model_dump() for f in req.fields])
    return DocumentClassOut(**cls)


@router.patch("/document-classes/{class_id}", response_model=DocumentClassOut)
@router.patch("/metadata/classes/{class_id}", response_model=DocumentClassOut)
def update_class(class_id: str, req: DocumentClassUpdateRequest, _admin: CurrentUser = Depends(_admin)):
    updated = metadata_store.update_class(
        class_id,
        name=req.name,
        description=req.description,
        fields=[f.model_dump() for f in req.fields] if req.fields is not None else None,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Document class not found")
    return DocumentClassOut(**updated)


@router.delete("/document-classes/{class_id}", status_code=204)
@router.delete("/metadata/classes/{class_id}", status_code=204)
def delete_class(class_id: str, _admin: CurrentUser = Depends(_admin)):
    metadata_store.delete_class(class_id)


# ---------- resource metadata values ----------------------------------------

@router.get("/resources/{resource_id}/metadata", response_model=ResourceMetadataOut | None)
@router.get("/metadata/resource/{resource_id}", response_model=ResourceMetadataOut | None)
def get_resource_metadata(resource_id: str, resource_type: str = "file", session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, resource_id, resource_type, "view")
    m = metadata_store.get_metadata(session.connection_id, resource_id)
    return ResourceMetadataOut(**m) if m else None


@router.put("/resources/{resource_id}/metadata", response_model=ResourceMetadataOut)
@router.put("/metadata/resource/{resource_id}", response_model=ResourceMetadataOut)
def set_resource_metadata(resource_id: str, req: ResourceMetadataSetRequest,
                           session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, resource_id, req.resource_type, "edit")
    actor = _actor(session)
    m = metadata_store.set_metadata(
        session.connection_id, resource_id, req.resource_type, req.class_id, req.values, actor=actor
    )
    out = ResourceMetadataOut(**m)
    if req.resource_type == "folder" and req.apply_to_children:
        descendants = collect_descendants(session, resource_id)
        for d in descendants:
            metadata_store.set_metadata(
                session.connection_id, d["resource_id"], d["resource_type"], req.class_id, req.values, actor=actor
            )
        out.applied_to_count = len(descendants)
    return out


@router.get("/resources/{resource_id}/metadata/history", response_model=list[ResourceMetadataHistoryEntryOut])
@router.get("/metadata/resource/{resource_id}/history", response_model=list[ResourceMetadataHistoryEntryOut])
def get_resource_metadata_history(resource_id: str, resource_type: str = "file", session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, resource_id, resource_type, "view")
    return [ResourceMetadataHistoryEntryOut(**h) for h in metadata_store.list_metadata_history(session.connection_id, resource_id)]
