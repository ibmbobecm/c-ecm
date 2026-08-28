from fastapi import APIRouter, Depends

from .. import settings_store
from ..auth import CurrentUser, require_role
from ..config import (
    BOX_CLIENT_ID,
    BOX_CLIENT_SECRET,
    DOCUSIGN_ACCOUNT_ID,
    DOCUSIGN_ENVIRONMENT,
    DOCUSIGN_INTEGRATION_KEY,
    DOCUSIGN_PRIVATE_KEY,
    DOCUSIGN_USER_ID,
    DOCUSIGN_WEBHOOK_HMAC_KEY,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    MS_CLIENT_ID,
    MS_CLIENT_SECRET,
    MS_TENANT,
)
from ..schemas import AdminSettingsOut, AdminSettingsUpdate

router = APIRouter(prefix="/admin", tags=["admin"])

_KEYS_WITH_DEFAULTS = {
    "google_client_id": GOOGLE_CLIENT_ID,
    "google_client_secret": GOOGLE_CLIENT_SECRET,
    "ms_client_id": MS_CLIENT_ID,
    "ms_client_secret": MS_CLIENT_SECRET,
    "ms_tenant": MS_TENANT,
    "box_client_id": BOX_CLIENT_ID,
    "box_client_secret": BOX_CLIENT_SECRET,
    "docusign_integration_key": DOCUSIGN_INTEGRATION_KEY,
    "docusign_user_id": DOCUSIGN_USER_ID,
    "docusign_account_id": DOCUSIGN_ACCOUNT_ID,
    "docusign_private_key": DOCUSIGN_PRIVATE_KEY,
    "docusign_environment": DOCUSIGN_ENVIRONMENT,
    "docusign_webhook_hmac_key": DOCUSIGN_WEBHOOK_HMAC_KEY,
}

_admin = require_role("admin")


@router.get("/settings", response_model=AdminSettingsOut)
def get_settings(_user: CurrentUser = Depends(_admin)):
    values = settings_store.get_settings(list(_KEYS_WITH_DEFAULTS), _KEYS_WITH_DEFAULTS)
    return AdminSettingsOut(
        google_client_id=values["google_client_id"],
        google_client_secret_set=bool(values["google_client_secret"]),
        ms_client_id=values["ms_client_id"],
        ms_client_secret_set=bool(values["ms_client_secret"]),
        ms_tenant=values["ms_tenant"] or "common",
        box_client_id=values["box_client_id"],
        box_client_secret_set=bool(values["box_client_secret"]),
        docusign_integration_key=values["docusign_integration_key"],
        docusign_user_id=values["docusign_user_id"],
        docusign_account_id=values["docusign_account_id"],
        docusign_private_key_set=bool(values["docusign_private_key"]),
        docusign_environment=values["docusign_environment"] or "demo",
        docusign_webhook_hmac_key_set=bool(values["docusign_webhook_hmac_key"]),
        docusign_configured=bool(
            values["docusign_integration_key"] and values["docusign_user_id"]
            and values["docusign_account_id"] and values["docusign_private_key"]
        ),
    )


@router.put("/settings", response_model=AdminSettingsOut)
def update_settings(req: AdminSettingsUpdate, _user: CurrentUser = Depends(_admin)):
    # Blank/omitted means "leave unchanged" — secrets are never echoed back
    # by GET, so there's no other way for the form to say "didn't touch this".
    for key in _KEYS_WITH_DEFAULTS:
        value = getattr(req, key, None)
        if value:
            settings_store.set_setting(key, value)
    return get_settings()
