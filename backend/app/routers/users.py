"""Users router — CRUD for C-ECM's own user accounts.

Write operations require the 'manage_users' feature (or superadmin).
GET /users/me is available to any authenticated user. Group membership
itself is managed from routers/groups.py, not here — see build_user_out()
below for how a user's groups/features get attached to the response.
"""

from fastapi import APIRouter, Depends, HTTPException

from .. import groups_store, users_store
from ..auth import CurrentUser, get_current_user, require_feature
from ..schemas import (
    UserCreateRequest,
    UserOut,
    UserUpdateRequest,
)

router = APIRouter(prefix="/users", tags=["users"])

_manage_users = require_feature("manage_users")


def build_user_out(user: dict) -> UserOut:
    groups = groups_store.list_user_groups(user["id"])
    return UserOut(
        id=user["id"], username=user["username"], display_name=user["display_name"],
        email=user.get("email"), is_superadmin=user["is_superadmin"], is_active=user["is_active"],
        created_at=user["created_at"], last_login_at=user.get("last_login_at"),
        groups=[g["name"] for g in groups],
        features=groups_store.user_features(user["id"]),
    )


@router.get("/me", response_model=UserOut)
def me(current_user: CurrentUser = Depends(get_current_user)):
    return build_user_out(current_user)


@router.get("", response_model=list[UserOut])
def list_users(_admin=Depends(_manage_users)):
    return [build_user_out(u) for u in users_store.list_users()]


@router.post("", response_model=UserOut, status_code=201)
def create_user(req: UserCreateRequest, _admin=Depends(_manage_users)):
    if users_store.get_by_username(req.username):
        raise HTTPException(status_code=409, detail=f"Username '{req.username}' is already taken")
    user = users_store.create_user(req.username, req.password, req.display_name, req.email, req.is_superadmin)
    return build_user_out(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: str, req: UserUpdateRequest, _admin=Depends(_manage_users)):
    current = users_store.get_by_id(user_id)
    if current is None:
        raise HTTPException(status_code=404, detail="User not found")
    # If this user is currently an active superadmin, and the update would
    # take away either is_superadmin or is_active, make sure some OTHER
    # active superadmin would still exist afterwards — otherwise nobody
    # could ever reach an admin-only route (including this one) again,
    # unless some group happens to grant manage_users, which isn't
    # guaranteed.
    if current["is_active"] and current["is_superadmin"]:
        will_be_active = req.is_active if req.is_active is not None else current["is_active"]
        will_be_superadmin = req.is_superadmin if req.is_superadmin is not None else current["is_superadmin"]
        if not (will_be_active and will_be_superadmin) and users_store.count_active_superadmins(exclude_user_id=user_id) == 0:
            raise HTTPException(status_code=400, detail="Cannot remove the last active superadmin account")
    updated = users_store.update_user(
        user_id,
        display_name=req.display_name,
        email=req.email,
        is_superadmin=req.is_superadmin,
        is_active=req.is_active,
        new_password=req.new_password,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return build_user_out(updated)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, current_user: CurrentUser = Depends(get_current_user), _admin=Depends(_manage_users)):
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    users_store.delete_user(user_id)
    groups_store.delete_for_user(user_id)
