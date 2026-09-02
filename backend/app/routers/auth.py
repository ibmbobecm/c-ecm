from fastapi import APIRouter, Depends

from .. import auth as auth_module
from ..auth import CurrentUser, get_app_session, get_current_user
from ..schemas import AppLoginRequest, TokenResponse, UserOut
from .users import build_user_out

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(req: AppLoginRequest):
    token, user = auth_module.app_login(req.username, req.password)
    return TokenResponse(access_token=token, user=build_user_out(user))


@router.post("/logout", status_code=204)
def logout(current_user: CurrentUser = Depends(get_current_user), session_id: str = Depends(get_app_session)):
    auth_module.app_logout(session_id, current_user["username"])


@router.get("/me", response_model=UserOut)
def me(current_user: CurrentUser = Depends(get_current_user)):
    return build_user_out(current_user)
