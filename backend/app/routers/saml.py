"""SAML SSO router — this app as a Service Provider.

GET  /saml/status    — unauthenticated; {enabled: bool}, drives whether the
                        frontend shows a "Sign in with SSO" button.
GET  /saml/metadata  — unauthenticated; this app's SP metadata XML, to hand
                        to the IdP admin when registering the app there.
GET  /saml/login     — unauthenticated; this IS the login mechanism —
                        redirects the browser to the IdP with a signed
                        AuthnRequest.
POST /saml/acs       — unauthenticated (the IdP is the caller, not a C-ECM
                        session — same shape as esignature.py's DocuSign
                        webhook or sharing.py's public share-link routes);
                        the Assertion Consumer Service the IdP posts the
                        SAMLResponse back to.

A full top-level browser navigation, not a popup — unlike the storage-
provider OAuth popup flow in connections.py, many IdPs' own login pages
refuse to render inside a popup/iframe, and SP-initiated SAML is
conventionally a full redirect away and back. The ACS handler redirects to
/sso-complete.html with the new session token in the URL fragment; that
static page just writes it to the same localStorage key AuthContext.tsx
already reads on mount and navigates to "/" — no separate frontend
SSO-login code path needed.
"""

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from .. import auth as auth_module
from .. import groups_store, saml_service, settings_store, users_store
from ..config import API_BASE_URL

logger = logging.getLogger("saml_router")

router = APIRouter(prefix="/saml", tags=["saml"])


def _base_request_data() -> dict:
    """scheme/host are derived from the configured API_BASE_URL, not the
    incoming request, matching how connections.py's OAuth flow builds its
    own redirect_uri from OAUTH_REDIRECT_BASE rather than request headers
    — avoids proxy/Host-header spoofing entirely, since neither is ever
    attacker-controlled."""
    parsed = urlparse(API_BASE_URL)
    return {
        "https": "on" if parsed.scheme == "https" else "off",
        "http_host": parsed.netloc,
        "server_port": str(parsed.port or (443 if parsed.scheme == "https" else 80)),
    }


@router.get("/status")
def status():
    return {"enabled": saml_service.is_enabled() and saml_service.is_configured()}


@router.get("/metadata")
def metadata():
    try:
        xml = saml_service.sp_metadata_xml()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return Response(content=xml, media_type="application/xml")


@router.get("/login")
def login():
    if not (saml_service.is_enabled() and saml_service.is_configured()):
        raise HTTPException(status_code=404, detail="SAML SSO isn't enabled on this server")
    request_data = {**_base_request_data(), "script_name": "/saml/login", "get_data": {}, "post_data": {}}
    saml_auth = saml_service.build_auth(request_data)
    return RedirectResponse(url=saml_auth.login())


@router.post("/acs")
async def acs(request: Request):
    if not (saml_service.is_enabled() and saml_service.is_configured()):
        raise HTTPException(status_code=404, detail="SAML SSO isn't enabled on this server")

    form = await request.form()
    post_data = {k: v for k, v in form.items()}
    request_data = {**_base_request_data(), "script_name": "/saml/acs", "get_data": {}, "post_data": post_data}
    saml_auth = saml_service.build_auth(request_data)

    saml_auth.process_response()
    errors = saml_auth.get_errors()
    if errors:
        detail = saml_auth.get_last_error_reason() or ", ".join(errors)
        logger.warning("SAML ACS validation failed: %s", detail)
        return RedirectResponse(url=f"/sso-complete.html#error={detail}")
    if not saml_auth.is_authenticated():
        return RedirectResponse(url="/sso-complete.html#error=Not+authenticated")

    attributes = saml_auth.get_attributes()
    name_id = saml_auth.get_nameid()
    email = (
        (attributes.get("email") or attributes.get("mail") or attributes.get(
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
        ) or [None])[0]
        or (name_id if name_id and "@" in name_id else None)
    )
    if not email:
        return RedirectResponse(url="/sso-complete.html#error=IdP+didn%27t+provide+an+email+address")

    user = users_store.get_by_email(email)
    if user is None:
        # JIT provisioning: first successful SAML login for this email
        # creates the local account. Random unusable password — this user
        # only ever signs in via SAML from here on.
        import secrets
        display_name = (attributes.get("displayName") or attributes.get("cn") or [email.split("@")[0]])[0]
        user = users_store.create_user(
            username=email, password=secrets.token_urlsafe(32),
            display_name=display_name, email=email, is_superadmin=False,
        )
        default_group_id = settings_store.get_setting("saml_default_group_id", "")
        if default_group_id and groups_store.get_group(default_group_id):
            groups_store.add_user_to_group(user["id"], default_group_id)
    elif not user["is_active"]:
        return RedirectResponse(url="/sso-complete.html#error=This+account+has+been+disabled")

    token = auth_module.start_session(user, event_type="login_saml")
    return RedirectResponse(url=f"/sso-complete.html#token={token}")
