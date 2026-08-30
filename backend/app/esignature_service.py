"""DocuSign e-signature integration.

This is a genuine integration against DocuSign's documented eSignature
REST API v2.1 — not a reimplementation of signing. Earlier BA/PO gap
analysis for this project explicitly scoped e-signature as "integrate
with a real provider, never build signing infrastructure" (regulated,
certified-provider territory), so this calls out to DocuSign rather than
inventing anything cryptographic here.

UNVERIFIED against a live DocuSign account — same disclosure already
carried by the Google Drive/Microsoft Graph/Box storage providers: written
against DocuSign's published API contract, not yet exercised against a
real integration key. Run it against a real DocuSign developer account
before trusting it the way FileNet's and local disk's providers are
trusted (those were verified live, this wasn't).

Uses the JWT Grant ("Service Integration") flow rather than the
interactive Authorization Code popup the storage OAuth providers use —
sending an envelope is a backend action; it shouldn't need a live browser
consent screen every time. One admin-authorized DocuSign user is
impersonated for the whole deployment (configured once via Admin
Settings: integration key + RSA private key + impersonated user id +
account id), the same "one app-level credential" shape as Google/MS/Box's
client id/secret, just following DocuSign's own recommended
server-to-server auth model instead.
"""

import base64
import datetime
import hashlib
import hmac as hmac_mod
import logging
import threading

import jwt as pyjwt
import requests

from . import settings_store
from .config import (
    DOCUSIGN_ACCOUNT_ID,
    DOCUSIGN_ENVIRONMENT,
    DOCUSIGN_INTEGRATION_KEY,
    DOCUSIGN_PRIVATE_KEY,
    DOCUSIGN_USER_ID,
    DOCUSIGN_WEBHOOK_HMAC_KEY,
)

logger = logging.getLogger("esignature_service")

_AUTH_SERVER = {"demo": "account-d.docusign.com", "production": "account.docusign.com"}

_KEYS_WITH_DEFAULTS = {
    "docusign_integration_key": DOCUSIGN_INTEGRATION_KEY,
    "docusign_user_id": DOCUSIGN_USER_ID,
    "docusign_account_id": DOCUSIGN_ACCOUNT_ID,
    "docusign_private_key": DOCUSIGN_PRIVATE_KEY,
    "docusign_environment": DOCUSIGN_ENVIRONMENT,
    "docusign_webhook_hmac_key": DOCUSIGN_WEBHOOK_HMAC_KEY,
}


class ESignatureError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def get_settings() -> dict:
    return settings_store.get_settings(list(_KEYS_WITH_DEFAULTS), _KEYS_WITH_DEFAULTS)


def is_configured() -> bool:
    s = get_settings()
    return bool(
        s["docusign_integration_key"] and s["docusign_user_id"]
        and s["docusign_account_id"] and s["docusign_private_key"]
    )


# ---------- auth -------------------------------------------------------

_token_lock = threading.Lock()
_cached_token: dict | None = None  # {"access_token", "base_uri", "expires_at"}


def _auth_server(settings: dict) -> str:
    return _AUTH_SERVER.get(settings["docusign_environment"] or "demo", _AUTH_SERVER["demo"])


def _get_access_token() -> tuple[str, str]:
    """Returns (access_token, base_uri) — base_uri is the account's own
    API host (e.g. https://demo.docusign.net), discovered via /oauth/
    userinfo rather than hardcoded, since production accounts are spread
    across multiple regional hosts (na1/na2/eu/...). Cached in-process
    until shortly before expiry; refreshed under a lock so concurrent
    callers don't each mint a fresh JWT and separately hit DocuSign's
    token endpoint."""
    global _cached_token
    now = datetime.datetime.now(datetime.timezone.utc)
    with _token_lock:
        if _cached_token and _cached_token["expires_at"] > now:
            return _cached_token["access_token"], _cached_token["base_uri"]

        if not is_configured():
            raise ESignatureError("DocuSign isn't configured on this server (set it up in Admin Settings)", status_code=503)
        s = get_settings()
        auth_server = _auth_server(s)

        assertion = pyjwt.encode(
            {
                "iss": s["docusign_integration_key"],
                "sub": s["docusign_user_id"],
                "aud": auth_server,
                "iat": now,
                "exp": now + datetime.timedelta(minutes=10),
                "scope": "signature impersonation",
            },
            s["docusign_private_key"],
            algorithm="RS256",
        )
        try:
            resp = requests.post(
                f"https://{auth_server}/oauth/token",
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ESignatureError(f"Couldn't reach DocuSign: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ESignatureError(f"DocuSign auth failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        token_data = resp.json()
        access_token = token_data["access_token"]

        try:
            userinfo = requests.get(
                f"https://{auth_server}/oauth/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}, timeout=15,
            )
            userinfo.raise_for_status()
        except requests.RequestException as exc:
            raise ESignatureError(f"Couldn't resolve the DocuSign account: {exc}", status_code=502)
        accounts = userinfo.json().get("accounts", [])
        account = next((a for a in accounts if a["account_id"] == s["docusign_account_id"]), None)
        if account is None:
            raise ESignatureError("The configured DocuSign account id wasn't found for this user", status_code=502)

        _cached_token = {
            "access_token": access_token,
            "base_uri": account["base_uri"],
            "expires_at": now + datetime.timedelta(seconds=token_data.get("expires_in", 3600) - 300),
        }
        return access_token, account["base_uri"]


def _api_root() -> tuple[str, str, str]:
    token, base_uri = _get_access_token()
    return token, base_uri, get_settings()["docusign_account_id"]


# ---------- envelopes ----------------------------------------------------

def create_envelope(document_bytes: bytes, document_name: str, signers: list[dict], subject: str, message: str) -> str:
    """Sends a document for signature. `signers` is a list of
    {"name", "email", "routing_order"} in signing order. Signature tabs are
    placed by absolute position (page 1, stacked near the bottom-left)
    rather than DocuSign's anchor-string tabs, since an anchor tab only
    places correctly if the document's actual text contains that literal
    string — absolute positioning works for any document sent through
    here, not just ones authored with a signature placeholder in them.
    Returns the DocuSign envelope id."""
    token, base_uri, account_id = _api_root()
    ext = document_name.rsplit(".", 1)[-1] if "." in document_name else "pdf"
    recipients = []
    for i, signer in enumerate(signers, start=1):
        recipients.append({
            "email": signer["email"],
            "name": signer["name"],
            "recipientId": str(i),
            "routingOrder": str(signer.get("routing_order", 1)),
            "tabs": {
                "signHereTabs": [{
                    "documentId": "1",
                    "pageNumber": "1",
                    "xPosition": "100",
                    "yPosition": str(650 - (i - 1) * 40),
                }]
            },
        })
    body = {
        "emailSubject": subject[:100],
        "emailBlurb": (message or "")[:1000],
        "documents": [{
            "documentBase64": base64.b64encode(document_bytes).decode(),
            "name": document_name,
            "fileExtension": ext,
            "documentId": "1",
        }],
        "recipients": {"signers": recipients},
        "status": "sent",
    }
    try:
        resp = requests.post(
            f"{base_uri}/restapi/v2.1/accounts/{account_id}/envelopes",
            json=body, headers={"Authorization": f"Bearer {token}"}, timeout=60,
        )
    except requests.RequestException as exc:
        raise ESignatureError(f"Couldn't reach DocuSign: {exc}", status_code=502)
    if resp.status_code >= 400:
        raise ESignatureError(f"DocuSign envelope creation failed ({resp.status_code}): {resp.text[:400]}", status_code=502)
    return resp.json()["envelopeId"]


def get_envelope_status(envelope_id: str) -> dict:
    token, base_uri, account_id = _api_root()
    try:
        resp = requests.get(
            f"{base_uri}/restapi/v2.1/accounts/{account_id}/envelopes/{envelope_id}",
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
    except requests.RequestException as exc:
        raise ESignatureError(f"Couldn't reach DocuSign: {exc}", status_code=502)
    if resp.status_code >= 400:
        raise ESignatureError(f"DocuSign status check failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
    return resp.json()


def download_signed_document(envelope_id: str) -> bytes:
    token, base_uri, account_id = _api_root()
    try:
        resp = requests.get(
            f"{base_uri}/restapi/v2.1/accounts/{account_id}/envelopes/{envelope_id}/documents/combined",
            headers={"Authorization": f"Bearer {token}"}, timeout=60,
        )
    except requests.RequestException as exc:
        raise ESignatureError(f"Couldn't reach DocuSign: {exc}", status_code=502)
    if resp.status_code >= 400:
        raise ESignatureError(f"Couldn't download the signed document ({resp.status_code}): {resp.text[:300]}", status_code=502)
    return resp.content


def void_envelope(envelope_id: str, reason: str) -> None:
    token, base_uri, account_id = _api_root()
    try:
        resp = requests.put(
            f"{base_uri}/restapi/v2.1/accounts/{account_id}/envelopes/{envelope_id}",
            json={"status": "voided", "voidedReason": (reason or "Voided by C-ECM")[:200]},
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
    except requests.RequestException as exc:
        raise ESignatureError(f"Couldn't reach DocuSign: {exc}", status_code=502)
    if resp.status_code >= 400:
        raise ESignatureError(f"Couldn't void the envelope ({resp.status_code}): {resp.text[:300]}", status_code=502)


# ---------- webhook signature verification --------------------------------

def verify_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """DocuSign Connect HMAC verification (X-DocuSign-Signature-1). If no
    HMAC key is configured, verification is skipped and this returns True
    — matching the state DocuSign itself is in when Connect HMAC isn't
    enabled on the account — but it means the webhook route cannot be
    trusted for anything destructive without one configured; the router
    logs a warning whenever it processes an unverified callback."""
    hmac_key = get_settings()["docusign_webhook_hmac_key"]
    if not hmac_key:
        return True
    if not signature_header:
        return False
    expected = base64.b64encode(hmac_mod.new(hmac_key.encode(), raw_body, hashlib.sha256).digest()).decode()
    return hmac_mod.compare_digest(expected, signature_header)
