from fastapi import APIRouter, Depends

from .. import ai_service, settings_store
from ..auth import CurrentUser, require_feature
from ..config import (
    API_BASE_URL,
    BOX_CLIENT_ID,
    BOX_CLIENT_SECRET,
    DOCUSIGN_ACCOUNT_ID,
    DOCUSIGN_ENVIRONMENT,
    DOCUSIGN_INTEGRATION_KEY,
    DOCUSIGN_PRIVATE_KEY,
    DOCUSIGN_USER_ID,
    DOCUSIGN_WEBHOOK_HMAC_KEY,
    DROPBOX_CLIENT_ID,
    DROPBOX_CLIENT_SECRET,
    FD_AI_BACKEND_DEFAULT,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    IBM_CLOUD_API_KEY,
    LASERFICHE_CLIENT_ID,
    LASERFICHE_CLIENT_SECRET,
    MS_CLIENT_ID,
    MS_CLIENT_SECRET,
    MS_TENANT,
    SHAREFILE_CLIENT_ID,
    SHAREFILE_CLIENT_SECRET,
    WATSON_DISCO_APIKEY,
    WATSON_DISCO_PROJECT_ID,
    WATSON_DISCO_URL,
    WATSON_NLU_APIKEY,
    WATSON_NLU_URL,
    WATSONX_MODEL,
    WATSONX_PROJECT_ID,
    WATSONX_URL,
)
from ..schemas import AdminSettingsOut, AdminSettingsUpdate

router = APIRouter(prefix="/admin", tags=["admin"])

# Providers added after Google/MS/Box never got their own config.py env-var
# fallback constant (they're admin-settings-only, same "" default the
# provider's own settings_store.get_setting(...) call already uses) — so
# their entries here fall back to "" directly rather than importing a
# same-named constant that doesn't exist.
_KEYS_WITH_DEFAULTS = {
    "google_client_id": GOOGLE_CLIENT_ID,
    "google_client_secret": GOOGLE_CLIENT_SECRET,
    "ms_client_id": MS_CLIENT_ID,
    "ms_client_secret": MS_CLIENT_SECRET,
    "ms_tenant": MS_TENANT,
    "box_client_id": BOX_CLIENT_ID,
    "box_client_secret": BOX_CLIENT_SECRET,
    "dropbox_client_id": DROPBOX_CLIENT_ID,
    "dropbox_client_secret": DROPBOX_CLIENT_SECRET,
    "laserfiche_client_id": LASERFICHE_CLIENT_ID,
    "laserfiche_client_secret": LASERFICHE_CLIENT_SECRET,
    "sharefile_client_id": SHAREFILE_CLIENT_ID,
    "sharefile_client_secret": SHAREFILE_CLIENT_SECRET,
    "egnyte_client_id": "",
    "egnyte_client_secret": "",
    "egnyte_domain": "",
    "confluence_client_id": "",
    "confluence_client_secret": "",
    "huddle_client_id": "",
    "huddle_client_secret": "",
    "netdocuments_client_id": "",
    "netdocuments_client_secret": "",
    "zoho_workdrive_client_id": "",
    "zoho_workdrive_client_secret": "",
    "imanage_client_id": "",
    "imanage_client_secret": "",
    "imanage_base_url": "",
    "onehub_client_id": "",
    "onehub_client_secret": "",
    "salesforce_files_client_id": "",
    "salesforce_files_client_secret": "",
    "oracle_content_management_client_id": "",
    "oracle_content_management_client_secret": "",
    "oracle_content_management_base_url": "",
    "oracle_content_management_idcs_url": "",
    "kiteworks_client_id": "",
    "kiteworks_client_secret": "",
    "kiteworks_base_url": "",
    "evernote_teams_client_id": "",
    "evernote_teams_client_secret": "",
    "saml_enabled": "",
    "saml_idp_entity_id": "",
    "saml_idp_sso_url": "",
    "saml_idp_x509_cert": "",
    "saml_default_group_id": "",
    "docusign_integration_key": DOCUSIGN_INTEGRATION_KEY,
    "docusign_user_id": DOCUSIGN_USER_ID,
    "docusign_account_id": DOCUSIGN_ACCOUNT_ID,
    "docusign_private_key": DOCUSIGN_PRIVATE_KEY,
    "docusign_environment": DOCUSIGN_ENVIRONMENT,
    "docusign_webhook_hmac_key": DOCUSIGN_WEBHOOK_HMAC_KEY,
    "ai_backend": FD_AI_BACKEND_DEFAULT,
    "ibm_cloud_api_key": IBM_CLOUD_API_KEY,
    "watsonx_project_id": WATSONX_PROJECT_ID,
    "watsonx_url": WATSONX_URL,
    "watsonx_model": WATSONX_MODEL,
    "watson_nlu_url": WATSON_NLU_URL,
    "watson_nlu_apikey": WATSON_NLU_APIKEY,
    "watson_disco_url": WATSON_DISCO_URL,
    "watson_disco_apikey": WATSON_DISCO_APIKEY,
    "watson_disco_project_id": WATSON_DISCO_PROJECT_ID,
}

_admin = require_feature("manage_admin_settings")


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
        dropbox_client_id=values["dropbox_client_id"],
        dropbox_client_secret_set=bool(values["dropbox_client_secret"]),
        laserfiche_client_id=values["laserfiche_client_id"],
        laserfiche_client_secret_set=bool(values["laserfiche_client_secret"]),
        sharefile_client_id=values["sharefile_client_id"],
        sharefile_client_secret_set=bool(values["sharefile_client_secret"]),
        egnyte_client_id=values["egnyte_client_id"],
        egnyte_client_secret_set=bool(values["egnyte_client_secret"]),
        egnyte_domain=values["egnyte_domain"],
        confluence_client_id=values["confluence_client_id"],
        confluence_client_secret_set=bool(values["confluence_client_secret"]),
        huddle_client_id=values["huddle_client_id"],
        huddle_client_secret_set=bool(values["huddle_client_secret"]),
        netdocuments_client_id=values["netdocuments_client_id"],
        netdocuments_client_secret_set=bool(values["netdocuments_client_secret"]),
        zoho_workdrive_client_id=values["zoho_workdrive_client_id"],
        zoho_workdrive_client_secret_set=bool(values["zoho_workdrive_client_secret"]),
        imanage_client_id=values["imanage_client_id"],
        imanage_client_secret_set=bool(values["imanage_client_secret"]),
        imanage_base_url=values["imanage_base_url"],
        onehub_client_id=values["onehub_client_id"],
        onehub_client_secret_set=bool(values["onehub_client_secret"]),
        salesforce_files_client_id=values["salesforce_files_client_id"],
        salesforce_files_client_secret_set=bool(values["salesforce_files_client_secret"]),
        oracle_content_management_client_id=values["oracle_content_management_client_id"],
        oracle_content_management_client_secret_set=bool(values["oracle_content_management_client_secret"]),
        oracle_content_management_base_url=values["oracle_content_management_base_url"],
        oracle_content_management_idcs_url=values["oracle_content_management_idcs_url"],
        kiteworks_client_id=values["kiteworks_client_id"],
        kiteworks_client_secret_set=bool(values["kiteworks_client_secret"]),
        kiteworks_base_url=values["kiteworks_base_url"],
        evernote_teams_client_id=values["evernote_teams_client_id"],
        evernote_teams_client_secret_set=bool(values["evernote_teams_client_secret"]),
        saml_enabled=values["saml_enabled"] == "1",
        saml_idp_entity_id=values["saml_idp_entity_id"],
        saml_idp_sso_url=values["saml_idp_sso_url"],
        saml_idp_x509_cert_set=bool(values["saml_idp_x509_cert"]),
        saml_default_group_id=values["saml_default_group_id"],
        saml_sp_entity_id=API_BASE_URL,
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
        ai_backend=values["ai_backend"] or "none",
        ibm_cloud_api_key_set=bool(values["ibm_cloud_api_key"]),
        watsonx_project_id=values["watsonx_project_id"],
        watsonx_url=values["watsonx_url"],
        watsonx_model=values["watsonx_model"],
        watsonx_configured=bool(values["ibm_cloud_api_key"] and values["watsonx_project_id"]),
        watson_nlu_url=values["watson_nlu_url"],
        watson_nlu_apikey_set=bool(values["watson_nlu_apikey"]),
        watson_nlu_configured=bool(values["watson_nlu_url"] and values["watson_nlu_apikey"]),
        watson_disco_url=values["watson_disco_url"],
        watson_disco_apikey_set=bool(values["watson_disco_apikey"]),
        watson_disco_project_id=values["watson_disco_project_id"],
        watson_disco_configured=bool(
            values["watson_disco_url"] and values["watson_disco_apikey"] and values["watson_disco_project_id"]
        ),
    )


@router.put("/settings", response_model=AdminSettingsOut)
def update_settings(req: AdminSettingsUpdate, _user: CurrentUser = Depends(_admin)):
    # Blank/omitted means "leave unchanged" — secrets are never echoed back
    # by GET, so there's no other way for the form to say "didn't touch this".
    # saml_enabled is the one real bool in this request (everything else is
    # a string), so it's handled separately: `if value` would silently drop
    # an explicit "turn SSO off" (False is falsy too), and it isn't a
    # leave-unchanged-when-blank field the way the string settings are.
    for key in _KEYS_WITH_DEFAULTS:
        if key == "saml_enabled":
            continue
        value = getattr(req, key, None)
        if value:
            settings_store.set_setting(key, value)
    if req.saml_enabled is not None:
        settings_store.set_setting("saml_enabled", "1" if req.saml_enabled else "")
    # Picks up any AI/Watson keys just saved above immediately, instead of
    # only on the next server restart.
    ai_service.refresh_from_settings()
    return get_settings()
