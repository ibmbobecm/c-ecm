"""Per-resource access grants router — lets someone with the
'manage_resource_permissions' feature see and edit exactly who can
view/edit a specific file or folder. This is separate from
routers/permissions.py (which is a read-only passthrough to a storage
backend's own NATIVE ACLs) — these are C-ECM's own grants, enforced by
access_control.require_resource_level() in every content route.

GET .../effective-access is deliberately open to any authenticated
session (not manage_resource_permissions-gated) — it only ever reveals
the CALLER's own resolved level, which they're entitled to know
regardless of whether they can manage anyone else's.
"""

from fastapi import APIRouter, Depends, HTTPException

from .. import access_control, groups_store, resource_permissions_store, users_store
from ..auth import CurrentSession, get_current_session, require_feature
from ..schemas import AccessGrantCreateRequest, AccessGrantOut, EffectiveAccessOut

router = APIRouter(tags=["access-grants"])

_manage_permissions = require_feature("manage_resource_permissions")


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or session.creds.get("username") or "unknown"


def _principal_display(principal_type: str, principal_id: str) -> str:
    if principal_type == "user":
        u = users_store.get_by_id(principal_id)
        return u["display_name"] if u else "(deleted user)"
    g = groups_store.get_group(principal_id)
    return g["name"] if g else "(deleted group)"


def _out(grant: dict) -> AccessGrantOut:
    return AccessGrantOut(
        id=grant["id"], resource_id=grant["resource_id"], resource_type=grant["resource_type"],
        principal_type=grant["principal_type"], principal_id=grant["principal_id"],
        principal_display=_principal_display(grant["principal_type"], grant["principal_id"]),
        level=grant["level"], created_at=grant["created_at"], created_by=grant.get("created_by"),
    )


@router.get("/resources/{resource_id}/access-grants", response_model=list[AccessGrantOut])
def list_access_grants(
    resource_id: str, resource_type: str = "file",
    session: CurrentSession = Depends(get_current_session), _admin=Depends(_manage_permissions),
):
    grants = resource_permissions_store.list_for_resource(session.connection_id, resource_id)
    return [_out(g) for g in grants if g["resource_type"] == resource_type]


@router.post("/resources/{resource_id}/access-grants", response_model=AccessGrantOut, status_code=201)
def create_access_grant(
    resource_id: str, req: AccessGrantCreateRequest, resource_type: str = "file",
    session: CurrentSession = Depends(get_current_session), _admin=Depends(_manage_permissions),
):
    if req.principal_type == "user" and users_store.get_by_id(req.principal_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    if req.principal_type == "group" and groups_store.get_group(req.principal_id) is None:
        raise HTTPException(status_code=404, detail="Group not found")
    grant = resource_permissions_store.create(
        session.connection_id, resource_id, resource_type, req.principal_type, req.principal_id,
        req.level, created_by=_actor(session),
    )
    return _out(grant)


@router.delete("/resources/{resource_id}/access-grants/{grant_id}", status_code=204)
def delete_access_grant(
    resource_id: str, grant_id: str,
    session: CurrentSession = Depends(get_current_session), _admin=Depends(_manage_permissions),
):
    grant = resource_permissions_store.get(grant_id)
    if grant is None or grant["connection_id"] != session.connection_id or grant["resource_id"] != resource_id:
        raise HTTPException(status_code=404, detail="Grant not found")
    resource_permissions_store.delete(grant_id)


@router.get("/resources/{resource_id}/effective-access", response_model=EffectiveAccessOut)
def get_effective_access(
    resource_id: str, resource_type: str = "file", session: CurrentSession = Depends(get_current_session),
):
    level = access_control.effective_level(session, resource_id, resource_type)
    return EffectiveAccessOut(level=level)
