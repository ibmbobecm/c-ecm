"""Groups router — the admin UI for the group/feature access-control
system. All writes (and the group list itself) require 'manage_groups' (or
superadmin). GET /features is the read-only feature catalog the group
editor's checkboxes are built from (see features.py)."""

from fastapi import APIRouter, Depends, HTTPException

from .. import groups_store, resource_permissions_store, users_store
from ..auth import require_feature
from ..features import FEATURES, FEATURE_KEYS
from ..schemas import FeatureOut, GroupCreateRequest, GroupOut, GroupUpdateRequest, UserOut
from .users import build_user_out

router = APIRouter(tags=["groups"])

_manage_groups = require_feature("manage_groups")


def _validate_feature_keys(feature_keys: list[str]) -> None:
    unknown = [k for k in feature_keys if k not in FEATURE_KEYS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown feature key(s): {', '.join(unknown)}")


@router.get("/features", response_model=list[FeatureOut])
def list_features(_admin=Depends(_manage_groups)):
    return [FeatureOut(key=f.key, label=f.label, description=f.description) for f in FEATURES]


@router.get("/groups", response_model=list[GroupOut])
def list_groups(_admin=Depends(_manage_groups)):
    return [GroupOut(**g) for g in groups_store.list_groups()]


@router.post("/groups", response_model=GroupOut, status_code=201)
def create_group(req: GroupCreateRequest, _admin=Depends(_manage_groups)):
    if groups_store.name_exists(req.name):
        raise HTTPException(status_code=409, detail=f"A group named \"{req.name}\" already exists")
    _validate_feature_keys(req.feature_keys)
    group = groups_store.create_group(req.name, req.description, req.feature_keys)
    return GroupOut(**group)


@router.patch("/groups/{group_id}", response_model=GroupOut)
def update_group(group_id: str, req: GroupUpdateRequest, _admin=Depends(_manage_groups)):
    if groups_store.get_group(group_id) is None:
        raise HTTPException(status_code=404, detail="Group not found")
    if req.name is not None and groups_store.name_exists(req.name, exclude_id=group_id):
        raise HTTPException(status_code=409, detail=f"A group named \"{req.name}\" already exists")
    if req.feature_keys is not None:
        _validate_feature_keys(req.feature_keys)
    updated = groups_store.update_group(group_id, name=req.name, description=req.description, feature_keys=req.feature_keys)
    if updated is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return GroupOut(**updated)


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(group_id: str, _admin=Depends(_manage_groups)):
    groups_store.delete_group(group_id)
    resource_permissions_store.delete_for_group(group_id)


@router.post("/groups/{group_id}/members/{user_id}", response_model=GroupOut)
def add_member(group_id: str, user_id: str, _admin=Depends(_manage_groups)):
    if groups_store.get_group(group_id) is None:
        raise HTTPException(status_code=404, detail="Group not found")
    if users_store.get_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    groups_store.add_user_to_group(user_id, group_id)
    return GroupOut(**groups_store.get_group(group_id))


@router.delete("/groups/{group_id}/members/{user_id}", response_model=GroupOut)
def remove_member(group_id: str, user_id: str, _admin=Depends(_manage_groups)):
    if groups_store.get_group(group_id) is None:
        raise HTTPException(status_code=404, detail="Group not found")
    groups_store.remove_user_from_group(user_id, group_id)
    return GroupOut(**groups_store.get_group(group_id))


@router.get("/groups/{group_id}/members", response_model=list[UserOut])
def list_members(group_id: str, _admin=Depends(_manage_groups)):
    if groups_store.get_group(group_id) is None:
        raise HTTPException(status_code=404, detail="Group not found")
    member_ids = set(groups_store.list_group_members(group_id))
    return [build_user_out(u) for u in users_store.list_users() if u["id"] in member_ids]
