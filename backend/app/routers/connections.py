import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from .. import ai_agents_store, comments_store, connections_store, esignature_store, locks_store, metadata_store, resource_permissions_store, share_links_store, tags_store, workflows_store
from ..auth import CurrentUser, get_current_user, require_feature
from ..schemas import ConfigFieldOut, ConnectionCreateRequest, ConnectionOut, ProviderOut
from ..storage_providers.base import AuthMode, ProviderError
from ..storage_providers.coming_soon import COMING_SOON_PROVIDERS
from ..storage_providers.registry import get_provider, list_providers

router = APIRouter(prefix="/connections", tags=["connections"])

# OAuth state -> (provider_key, display_name), so the callback knows which
# provider issued the code and what to name the resulting connection.
_oauth_pending: dict[str, tuple[str, str]] = {}

_admin = require_feature("manage_connections")


def _out(c: dict) -> ConnectionOut:
    return ConnectionOut(**c)


@router.get("/providers", response_model=list[ProviderOut])
def providers(_user: CurrentUser = Depends(get_current_user)):
    real = [
        ProviderOut(
            key=p.key, display_name=p.display_name, auth_mode=p.auth_mode.value, configured=p.configured,
            config_fields=[
                ConfigFieldOut(key=f.key, label=f.label, placeholder=f.placeholder, required=f.required)
                for f in p.config_fields
            ],
            requires_credentials=p.requires_credentials,
            credential_labels=p.credential_labels,
        )
        for p in list_providers()
    ]
    coming_soon = [
        ProviderOut(
            key=c["key"], display_name=c["display_name"], auth_mode="oauth", configured=False,
            config_fields=[], requires_credentials=False, credential_labels=("Username", "Password"),
            coming_soon=True,
        )
        for c in COMING_SOON_PROVIDERS
    ]
    return real + coming_soon


@router.get("", response_model=list[ConnectionOut])
def list_connections(_user: CurrentUser = Depends(get_current_user)):
    return [_out(c) for c in connections_store.list_connections()]


@router.post("", response_model=ConnectionOut, status_code=201)
def create_connection(req: ConnectionCreateRequest, _user: CurrentUser = Depends(get_current_user)):
    try:
        provider = get_provider(req.provider_key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{req.provider_key}'")
    if provider.auth_mode != AuthMode.CREDENTIALS:
        raise HTTPException(status_code=400, detail=f"{provider.display_name} connects via OAuth, not a password")
    if not provider.configured:
        raise HTTPException(status_code=409, detail=f"{provider.display_name} isn't configured yet")
    if connections_store.name_exists(req.display_name):
        raise HTTPException(
            status_code=409, detail=f"A connection named \"{req.display_name}\" already exists — choose a different name"
        )
    try:
        creds = provider.authenticate(req.username, req.password, req.config)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    if creds is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    identity = provider.whoami(creds)
    try:
        created = connections_store.create_connection(req.provider_key, req.display_name, creds, identity)
    except connections_store.DuplicateConnectionNameError:
        # Only reachable if the name was taken between the check above and
        # this insert — the check is still worth having, since it fails
        # fast without wasting a real auth round-trip against a possibly
        # slow remote server for the common case.
        raise HTTPException(
            status_code=409, detail=f"A connection named \"{req.display_name}\" already exists — choose a different name"
        )
    return _out(created)


@router.delete("/{connection_id}", status_code=204)
def delete_connection(connection_id: str, _user: CurrentUser = Depends(_admin)):
    connections_store.delete_connection(connection_id)
    # Tags/comments/share-links/metadata/locks/e-signatures/workflow
    # instances live in their own SQLite files — clean them all up so
    # nothing orphans forever referencing a connection_id that can never
    # be resolved again.
    # The activity log is deliberately excluded — an audit trail should
    # outlive the thing it's about, not disappear with it.
    tags_store.delete_for_connection(connection_id)
    comments_store.delete_for_connection(connection_id)
    share_links_store.delete_for_connection(connection_id)
    metadata_store.delete_for_connection(connection_id)
    locks_store.delete_for_connection(connection_id)
    esignature_store.delete_for_connection(connection_id)
    workflows_store.delete_for_connection(connection_id)
    ai_agents_store.delete_for_connection(connection_id)
    resource_permissions_store.delete_for_connection(connection_id)


@router.get("/oauth/{provider_key}/start")
def oauth_start(provider_key: str, display_name: str, _user: CurrentUser = Depends(get_current_user)):
    try:
        provider = get_provider(provider_key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider_key}'")
    if provider.auth_mode != AuthMode.OAUTH:
        raise HTTPException(status_code=400, detail=f"{provider.display_name} doesn't use OAuth")
    if not provider.configured:
        raise HTTPException(status_code=409, detail=f"{provider.display_name} isn't configured yet")
    if connections_store.name_exists(display_name):
        raise HTTPException(
            status_code=409, detail=f"A connection named \"{display_name}\" already exists — choose a different name"
        )
    state = secrets.token_urlsafe(24)
    _oauth_pending[state] = (provider_key, display_name)
    from ..config import OAUTH_REDIRECT_BASE
    redirect_uri = f"{OAUTH_REDIRECT_BASE}/connections/oauth/{provider_key}/callback"
    return {"authorize_url": provider.get_authorize_url(state, redirect_uri)}


@router.get("/oauth/{provider_key}/callback")
def oauth_callback(provider_key: str, code: str, state: str):
    pending = _oauth_pending.pop(state, None)
    if pending is None or pending[0] != provider_key:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    _key, display_name = pending
    provider = get_provider(provider_key)
    from ..config import OAUTH_REDIRECT_BASE
    redirect_uri = f"{OAUTH_REDIRECT_BASE}/connections/oauth/{provider_key}/callback"
    try:
        creds = provider.complete_oauth(code, redirect_uri)
    except ProviderError as exc:
        return RedirectResponse(url=f"/oauth-complete.html#error={exc.detail}")
    identity = provider.whoami(creds)
    # No form left to reject a taken name into at this point — the user
    # already granted access on the provider's own consent page — so
    # de-duplicate automatically instead of dead-ending the flow.
    final_name = connections_store.unique_display_name(display_name or identity)
    connections_store.create_connection(provider_key, final_name, creds, identity)
    return RedirectResponse(url=f"/oauth-complete.html#connected={provider_key}")
