"""Users router — CRUD for C-ECM's own user accounts (multi-user RBAC).

All write operations require the 'admin' role.  GET /users/me is
available to any authenticated user.
"""

from fastapi import APIRouter, Depends, HTTPException

from .. import users_store
from ..auth import CurrentUser, get_current_user, require_role
from ..schemas import (
    UserCreateRequest,
    UserOut,
    UserUpdateRequest,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
def me(current_user: CurrentUser = Depends(get_current_user)):
    return UserOut(
        id=current_user["id"],
        username=current_user["username"],
        display_name=current_user["display_name"],
        email=current_user.get("email"),
        roles=current_user["roles"],
        is_active=current_user["is_active"],
        created_at=current_user["created_at"],
        last_login_at=current_user.get("last_login_at"),
    )


@router.get("", response_model=list[UserOut])
def list_users(_admin=Depends(require_role("admin"))):
    return [
        UserOut(
            id=u["id"], username=u["username"], display_name=u["display_name"],
            email=u.get("email"), roles=u["roles"], is_active=u["is_active"],
            created_at=u["created_at"], last_login_at=u.get("last_login_at"),
        )
        for u in users_store.list_users()
    ]


@router.post("", response_model=UserOut, status_code=201)
def create_user(req: UserCreateRequest, _admin=Depends(require_role("admin"))):
    if users_store.get_by_username(req.username):
        raise HTTPException(status_code=409, detail=f"Username '{req.username}' is already taken")
    user = users_store.create_user(req.username, req.password, req.display_name, req.email, req.roles)
    return UserOut(
        id=user["id"], username=user["username"], display_name=user["display_name"],
        email=user.get("email"), roles=user["roles"], is_active=user["is_active"],
        created_at=user["created_at"], last_login_at=user.get("last_login_at"),
    )


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: str, req: UserUpdateRequest, _admin=Depends(require_role("admin"))):
    current = users_store.get_by_id(user_id)
    if current is None:
        raise HTTPException(status_code=404, detail="User not found")
    # If this user is currently an active admin, and the update would
    # take away either the "admin" role or is_active, make sure some
    # OTHER active admin would still exist afterwards — otherwise nobody
    # could ever reach an admin-only route (including this one) again.
    if current["is_active"] and "admin" in current["roles"]:
        will_be_active = req.is_active if req.is_active is not None else current["is_active"]
        will_be_admin = "admin" in req.roles if req.roles is not None else "admin" in current["roles"]
        if not (will_be_active and will_be_admin) and users_store.count_active_admins(exclude_user_id=user_id) == 0:
            raise HTTPException(status_code=400, detail="Cannot remove the last active admin account")
    updated = users_store.update_user(
        user_id,
        display_name=req.display_name,
        email=req.email,
        roles=req.roles,
        is_active=req.is_active,
        new_password=req.new_password,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut(
        id=updated["id"], username=updated["username"], display_name=updated["display_name"],
        email=updated.get("email"), roles=updated["roles"], is_active=updated["is_active"],
        created_at=updated["created_at"], last_login_at=updated.get("last_login_at"),
    )


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, current_user: CurrentUser = Depends(get_current_user), _admin=Depends(require_role("admin"))):
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    users_store.delete_user(user_id)
