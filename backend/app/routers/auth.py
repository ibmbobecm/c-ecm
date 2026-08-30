from fastapi import APIRouter, Depends

from .. import auth as auth_module
from ..auth import CurrentUser, get_app_session, get_current_user
from ..schemas import AppLoginRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(req: AppLoginRequest):
    token, user = auth_module.app_login(req.username, req.password)
    return TokenResponse(
        access_token=token,
        user=UserOut(
            id=user["id"],
            username=user["username"],
            display_name=user["display_name"],
            email=user.get("email"),
            roles=user["roles"],
            is_active=user["is_active"],
            created_at=user["created_at"],
            last_login_at=user.get("last_login_at"),
        ),
    )


@router.post("/logout", status_code=204)
def logout(current_user: CurrentUser = Depends(get_current_user), session_id: str = Depends(get_app_session)):
    auth_module.app_logout(session_id, current_user["username"])


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
