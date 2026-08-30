"""C-ECM authentication — multi-user JWT sessions with role-based access control.

Login flow:
  1. POST /auth/login  →  validate against users_store  →  JWT + session id
  2. Every request: Bearer JWT  →  validate  →  session in _app_sessions
  3. Content requests also carry X-Connection-Id  →  resolved to a provider

Role guard:
  require_role("admin")  →  Depends()-able FastAPI dependency
  require_role("editor") →  ditto
  Any route that doesn't call require_role accepts any authenticated user.

CurrentUser is a plain dict from users_store (keys: id, username, roles, …).
CurrentSession includes the CurrentUser so routers can access both in one
Depends() call.
"""

import datetime
import threading
import time
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import activity_service, connections_store, users_store
from .config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, JWT_SECRET
from .storage_providers.base import ProviderError, StorageProvider
from .storage_providers.registry import get_provider

_security = HTTPBearer()

# session_id → username  (in-memory; cleared on restart, which also invalidates
# all existing JWTs — acceptable for a local/on-prem deployment).
_app_sessions: dict[str, str] = {}
_app_sessions_lock = threading.Lock()

# POST /auth/login had no attempt limit at all — this is now a real
# multi-user bcrypt-backed account store (users_store.py), not a single
# hardcoded credential, so unlimited-attempt brute force against any one
# username is a real risk. Same in-process/in-memory lockout pattern
# already proven for the share-link password check (routers/sharing.py),
# keyed by username since that's the actual security boundary here.
_LOGIN_ATTEMPTS_LOCK = threading.Lock()
_login_attempts: dict[str, list[float]] = {}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 300


def _check_login_rate_limit(username: str) -> None:
    key = username.lower()
    now = time.monotonic()
    with _LOGIN_ATTEMPTS_LOCK:
        attempts = [t for t in _login_attempts.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
        _login_attempts[key] = attempts
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Too many failed login attempts — try again later")


def _record_failed_login(username: str) -> None:
    with _LOGIN_ATTEMPTS_LOCK:
        _login_attempts.setdefault(username.lower(), []).append(time.monotonic())


def _clear_login_attempts(username: str) -> None:
    with _LOGIN_ATTEMPTS_LOCK:
        _login_attempts.pop(username.lower(), None)

# Type alias exposed to routers
CurrentUser = dict


@dataclass
class CurrentSession:
    connection_id: str
    provider_key: str
    provider: StorageProvider
    creds: dict
    user: dict = field(default_factory=dict)


def _log_auth_event(event_type: str, username: str, user_id: str | None = None) -> None:
    activity_service.record_event(
        connection_id=None, provider_key=None, resource_type="user", resource_id=user_id or username,
        resource_name=username, event_type=event_type, actor=username,
    )


def app_login(username: str, password: str) -> tuple[str, dict]:
    """Returns (jwt_token, user_dict) or raises HTTPException."""
    _check_login_rate_limit(username)
    user = users_store.authenticate(username, password)
    if user is None:
        _record_failed_login(username)
        _log_auth_event("login_failed", username)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    _clear_login_attempts(username)
    import secrets
    session_id = secrets.token_hex(16)
    with _app_sessions_lock:
        _app_sessions[session_id] = user["username"]
    token = _create_token(session_id)
    _log_auth_event("login", user["username"], user["id"])
    return token, user


def app_logout(session_id: str, username: str) -> None:
    with _app_sessions_lock:
        _app_sessions.pop(session_id, None)
    _log_auth_event("logout", username)


def _create_token(session_id: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sid": session_id,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(credentials: HTTPAuthorizationCredentials) -> str:
    """Returns session_id or raises 401."""
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload["sid"]
    except (jwt.PyJWTError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def get_app_session(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> str:
    """Validates the C-ECM login.  Returns session_id."""
    session_id = _decode_token(credentials)
    with _app_sessions_lock:
        valid = session_id in _app_sessions
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired, please log in again")
    return session_id


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> CurrentUser:
    """Returns the full user dict for the authenticated session."""
    session_id = _decode_token(credentials)
    with _app_sessions_lock:
        username = _app_sessions.get(session_id)
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired, please log in again")
    user = users_store.get_by_username(username)
    if user is None or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account not found or disabled")
    return user


def require_role(role: str):
    """Returns a FastAPI dependency that enforces the given role."""
    def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if role not in user.get("roles", []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the '{role}' role",
            )
        return user
    return _check


def get_current_session(
    user: CurrentUser = Depends(get_current_user),
    x_connection_id: str | None = Header(default=None),
) -> CurrentSession:
    """Resolves which backend connection a content request applies to."""
    if not x_connection_id:
        raise HTTPException(status_code=400, detail="No connection selected")
    entry = connections_store.get_creds(x_connection_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    provider_key, creds = entry
    try:
        provider = get_provider(provider_key)
    except KeyError:
        raise HTTPException(status_code=409, detail=f"Provider '{provider_key}' is no longer available")

    try:
        refreshed, changed = provider.refresh_if_needed(creds)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    if changed:
        connections_store.update_creds(x_connection_id, refreshed)
        creds = refreshed

    return CurrentSession(
        connection_id=x_connection_id,
        provider_key=provider_key,
        provider=provider,
        creds=creds,
        user=user,
    )
