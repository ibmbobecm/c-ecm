import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from .html_sanitize import sanitize_rich_html


class ConfigFieldOut(BaseModel):
    key: str
    label: str
    placeholder: str = ""
    required: bool = True


class ProviderOut(BaseModel):
    key: str
    display_name: str
    auth_mode: str  # "credentials" | "oauth"
    configured: bool
    config_fields: list[ConfigFieldOut] = []
    requires_credentials: bool = True
    credential_labels: tuple[str, str] = ("Username", "Password")
    # True only for placeholder entries with no real StorageProvider behind
    # them yet (see storage_providers/coming_soon.py) — shown in the
    # Connections grid for visibility, but not clickable/connectable.
    coming_soon: bool = False


# ---------- users / groups / features (access control) --------------------

class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None = None
    is_superadmin: bool = False
    is_active: bool = True
    created_at: str
    last_login_at: str | None = None
    groups: list[str] = []  # group names this user belongs to, for display
    group_ids: list[str] = []  # same groups, by id -- for client-side group-assignee matching
    features: list[str] = []  # flattened feature set from all their groups


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=6, max_length=200)
    display_name: str = Field(min_length=1, max_length=100)
    email: str | None = None
    is_superadmin: bool = False


class UserUpdateRequest(BaseModel):
    display_name: str | None = None
    email: str | None = None
    is_superadmin: bool | None = None
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=6, max_length=200)


class FeatureOut(BaseModel):
    key: str
    label: str
    description: str


class GroupOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    created_at: str
    feature_keys: list[str] = []
    member_count: int = 0


class GroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    feature_keys: list[str] = []


class GroupUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    feature_keys: list[str] | None = None


# ---------- per-resource access grants --------------------------------------

class AccessGrantOut(BaseModel):
    id: str
    resource_id: str
    resource_type: str
    principal_type: Literal["user", "group"]
    principal_id: str
    principal_display: str  # username or group name, resolved for display
    level: Literal["view", "edit"]
    created_at: str
    created_by: str | None = None


class AccessGrantCreateRequest(BaseModel):
    principal_type: Literal["user", "group"]
    principal_id: str
    level: Literal["view", "edit"]


class EffectiveAccessOut(BaseModel):
    # "view"/"edit" — an applicable grant was found. "none" — the
    # resource is restricted and this caller has no applicable grant.
    # null — unrestricted (no grant anywhere in the ancestor chain,
    # today's default). "none" and null are deliberately distinct: one
    # means "explicitly locked out," the other means "nothing's set up."
    level: Literal["view", "edit", "none"] | None


class AppLoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ConnectionCreateRequest(BaseModel):
    provider_key: str
    display_name: str = Field(min_length=1, max_length=100)
    username: str = ""
    password: str = ""
    config: dict[str, str] | None = None


class ConnectionOut(BaseModel):
    id: str
    provider_key: str
    display_name: str
    identity: str | None
    created_at: str


class AdminSettingsOut(BaseModel):
    google_client_id: str
    google_client_secret_set: bool
    ms_client_id: str
    ms_client_secret_set: bool
    ms_tenant: str
    box_client_id: str
    box_client_secret_set: bool
    dropbox_client_id: str
    dropbox_client_secret_set: bool
    laserfiche_client_id: str
    laserfiche_client_secret_set: bool
    sharefile_client_id: str
    sharefile_client_secret_set: bool
    egnyte_client_id: str
    egnyte_client_secret_set: bool
    egnyte_domain: str
    confluence_client_id: str
    confluence_client_secret_set: bool
    huddle_client_id: str
    huddle_client_secret_set: bool
    netdocuments_client_id: str
    netdocuments_client_secret_set: bool
    zoho_workdrive_client_id: str
    zoho_workdrive_client_secret_set: bool
    imanage_client_id: str
    imanage_client_secret_set: bool
    imanage_base_url: str
    onehub_client_id: str
    onehub_client_secret_set: bool
    salesforce_files_client_id: str
    salesforce_files_client_secret_set: bool
    oracle_content_management_client_id: str
    oracle_content_management_client_secret_set: bool
    oracle_content_management_base_url: str
    oracle_content_management_idcs_url: str
    kiteworks_client_id: str
    kiteworks_client_secret_set: bool
    kiteworks_base_url: str
    evernote_teams_client_id: str
    evernote_teams_client_secret_set: bool
    saml_enabled: bool
    saml_idp_entity_id: str
    saml_idp_sso_url: str
    saml_idp_x509_cert_set: bool
    saml_default_group_id: str
    saml_sp_entity_id: str
    docusign_integration_key: str
    docusign_user_id: str
    docusign_account_id: str
    docusign_private_key_set: bool
    docusign_environment: str
    docusign_webhook_hmac_key_set: bool
    docusign_configured: bool
    ai_backend: str
    anthropic_api_key_set: bool
    anthropic_model: str
    anthropic_configured: bool
    ai_api_key_set: bool
    ai_base_url: str
    ai_model: str
    ai_openai_configured: bool
    ollama_url: str
    ollama_model: str
    ibm_cloud_api_key_set: bool
    watsonx_project_id: str
    watsonx_url: str
    watsonx_model: str
    watsonx_configured: bool
    watson_nlu_url: str
    watson_nlu_apikey_set: bool
    watson_nlu_configured: bool
    watson_disco_url: str
    watson_disco_apikey_set: bool
    watson_disco_project_id: str
    watson_disco_configured: bool


class AdminSettingsUpdate(BaseModel):
    google_client_id: str | None = None
    google_client_secret: str | None = None
    ms_client_id: str | None = None
    ms_client_secret: str | None = None
    ms_tenant: str | None = None
    box_client_id: str | None = None
    box_client_secret: str | None = None
    dropbox_client_id: str | None = None
    dropbox_client_secret: str | None = None
    laserfiche_client_id: str | None = None
    laserfiche_client_secret: str | None = None
    sharefile_client_id: str | None = None
    sharefile_client_secret: str | None = None
    egnyte_client_id: str | None = None
    egnyte_client_secret: str | None = None
    egnyte_domain: str | None = None
    confluence_client_id: str | None = None
    confluence_client_secret: str | None = None
    huddle_client_id: str | None = None
    huddle_client_secret: str | None = None
    netdocuments_client_id: str | None = None
    netdocuments_client_secret: str | None = None
    zoho_workdrive_client_id: str | None = None
    zoho_workdrive_client_secret: str | None = None
    imanage_client_id: str | None = None
    imanage_client_secret: str | None = None
    imanage_base_url: str | None = None
    onehub_client_id: str | None = None
    onehub_client_secret: str | None = None
    salesforce_files_client_id: str | None = None
    salesforce_files_client_secret: str | None = None
    oracle_content_management_client_id: str | None = None
    oracle_content_management_client_secret: str | None = None
    oracle_content_management_base_url: str | None = None
    oracle_content_management_idcs_url: str | None = None
    kiteworks_client_id: str | None = None
    kiteworks_client_secret: str | None = None
    kiteworks_base_url: str | None = None
    evernote_teams_client_id: str | None = None
    evernote_teams_client_secret: str | None = None
    saml_enabled: bool | None = None
    saml_idp_entity_id: str | None = None
    saml_idp_sso_url: str | None = None
    saml_idp_x509_cert: str | None = None
    saml_default_group_id: str | None = None
    docusign_integration_key: str | None = None
    docusign_user_id: str | None = None
    docusign_account_id: str | None = None
    docusign_private_key: str | None = None
    docusign_environment: str | None = None
    docusign_webhook_hmac_key: str | None = None
    ai_backend: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    ai_api_key: str | None = None
    ai_base_url: str | None = None
    ai_model: str | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None
    ibm_cloud_api_key: str | None = None
    watsonx_project_id: str | None = None
    watsonx_url: str | None = None
    watsonx_model: str | None = None
    watson_nlu_url: str | None = None
    watson_nlu_apikey: str | None = None
    watson_disco_url: str | None = None
    watson_disco_apikey: str | None = None
    watson_disco_project_id: str | None = None


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = None


class FolderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: str | None = Field(default=None)
    move_to_root: bool = False  # parent_id=None is ambiguous (unset vs "move to root"); this disambiguates


class FolderOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    created_at: datetime.datetime | None = None
    type: str = "folder"


class FileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    folder_id: str | None = Field(default=None)
    move_to_root: bool = False


class FileOut(BaseModel):
    id: str
    name: str
    folder_id: str | None
    version_number: int
    size_bytes: int | None
    content_type: str | None
    updated_at: datetime.datetime | None = None
    type: str = "file"


class FileVersionOut(BaseModel):
    id: str
    version_number: int
    size_bytes: int | None
    content_type: str | None
    is_current: bool
    updated_at: datetime.datetime | None = None


class BreadcrumbEntry(BaseModel):
    id: str | None
    name: str


class FolderContentsOut(BaseModel):
    folder: FolderOut | None  # None when listing root
    breadcrumb: list[BreadcrumbEntry]
    folders: list[FolderOut]
    files: list[FileOut]


class SearchResultOut(BaseModel):
    folders: list[FolderOut]
    files: list[FileOut]


class GlobalSearchHit(BaseModel):
    """A single hit from cross-backend global search."""
    connection_id: str
    connection_name: str
    provider_key: str
    resource_type: str          # "file" | "folder"
    resource_id: str
    name: str
    size_bytes: int | None = None
    content_type: str | None = None
    updated_at: datetime.datetime | None = None


class GlobalSearchResultOut(BaseModel):
    query: str
    hits: list[GlobalSearchHit]
    connection_errors: dict[str, str] = {}  # connection_id → error message


# --- activity ---


class ActivityEventOut(BaseModel):
    id: str
    connection_id: str | None
    provider_key: str | None
    resource_type: str
    resource_id: str
    resource_name: str | None
    event_type: str
    actor: str
    payload: dict
    created_at: str


class ActivityTypeCountOut(BaseModel):
    event_type: str
    count: int


class ActivityActorCountOut(BaseModel):
    actor: str
    count: int


class ActivityDayCountOut(BaseModel):
    day: str
    count: int


class ActivityAlertOut(BaseModel):
    severity: str  # "warning" | "danger"
    title: str
    detail: str
    actor: str
    event_type: str
    count: int
    window_start: str
    window_end: str


class ActivitySummaryOut(BaseModel):
    total_events: int
    unique_actors: int
    by_type: list[ActivityTypeCountOut]
    by_actor: list[ActivityActorCountOut]
    by_day: list[ActivityDayCountOut]
    alerts: list[ActivityAlertOut]


# --- tags ---


class TagOut(BaseModel):
    id: str
    name: str
    color: str
    created_at: str


class TagCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="#5B8DEF", min_length=4, max_length=9)


class TagAttachRequest(BaseModel):
    resource_type: str = Field(pattern=r"^(file|folder)$")
    tag_id: str


class BulkTagsRequest(BaseModel):
    resource_ids: list[str] = Field(max_length=500)


class BulkCommentCountsRequest(BaseModel):
    resource_ids: list[str] = Field(max_length=500)


# --- comments ---


class CommentCreateRequest(BaseModel):
    resource_type: str = Field(pattern=r"^(file|folder)$")
    body: str = Field(min_length=1, max_length=4000)
    parent_comment_id: str | None = None
    mentioned_users: list[str] = []


class CommentUpdateRequest(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    resolved: bool | None = None


class CommentOut(BaseModel):
    id: str
    connection_id: str
    resource_id: str
    resource_type: str
    parent_comment_id: str | None
    body: str
    mentioned_users: list[str]
    resolved_at: str | None
    resolved_by: str | None
    created_by: str
    created_at: str
    edited_at: str | None


# --- notifications ---


class NotificationOut(BaseModel):
    id: str
    message: str
    read_at: str | None
    created_at: str


class NotificationSummaryOut(BaseModel):
    unread_count: int
    notifications: list[NotificationOut]


# --- saved searches ---


class SavedSearchQuery(BaseModel):
    text: str = ""
    file_types: list[str] = []
    tag_ids: list[str] = []


class SavedSearchCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    connection_id: str | None = None
    query: SavedSearchQuery


class SavedSearchOut(BaseModel):
    id: str
    name: str
    connection_id: str | None
    query: SavedSearchQuery
    created_at: str
    last_run_at: str | None


# --- sharing ---


class ShareLinkCreateRequest(BaseModel):
    resource_type: str = Field(pattern=r"^(file|folder)$")
    role: str = Field(default="view", pattern=r"^(view|comment|edit)$")
    expires_in_days: int | None = Field(default=None, ge=1, le=365)
    password: str | None = None


class ShareLinkOut(BaseModel):
    id: str
    url: str
    role: str
    expires_at: datetime.datetime | None
    password_protected: bool


# --- permissions ---


class PermissionEntryOut(BaseModel):
    principal_type: str
    principal_id: str
    principal_display: str
    role: str
    inherited: bool
    source: str | None = None


# --- e-signature (DocuSign) ---


class ESignatureSignerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200)
    routing_order: int = Field(default=1, ge=1, le=20)


class ESignatureRequestCreate(BaseModel):
    resource_type: str = Field(default="file", pattern=r"^file$")  # only files can be sent for signature
    signers: list[ESignatureSignerIn] = Field(min_length=1, max_length=20)
    subject: str = Field(min_length=1, max_length=100)
    message: str | None = Field(default=None, max_length=1000)


class ESignatureRequestOut(BaseModel):
    id: str
    connection_id: str
    resource_id: str
    resource_type: str
    resource_name: str | None
    envelope_id: str
    status: str
    signers: list[ESignatureSignerIn]
    subject: str | None
    requested_by: str
    created_at: str
    completed_at: str | None
    signed_version_number: int | None = None


# --- locks -----------------------------------------------------------------

class CheckoutRequest(BaseModel):
    resource_id: str
    resource_type: str = Field(default="file", pattern=r"^(file|folder)$")
    comment: str | None = None


class LockOut(BaseModel):
    id: str
    connection_id: str
    resource_id: str
    locked_by: str
    locked_at: str
    comment: str | None = None


# --- document classes / metadata -------------------------------------------

class MetadataFieldDef(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    type: Literal["text", "number", "date", "boolean", "select"] = "text"
    required: bool = False
    options: list[str] = []   # for select type


def _check_unique_field_keys(fields: list[MetadataFieldDef]) -> list[MetadataFieldDef]:
    seen = set()
    for f in fields:
        if f.key in seen:
            raise ValueError(f'Duplicate field key "{f.key}" — every field in a class needs a unique key')
        seen.add(f.key)
    return fields


class DocumentClassCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    fields: list[MetadataFieldDef] = []

    @field_validator("fields")
    @classmethod
    def _validate_fields(cls, fields: list[MetadataFieldDef]) -> list[MetadataFieldDef]:
        return _check_unique_field_keys(fields)


class DocumentClassUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    fields: list[MetadataFieldDef] | None = None

    @field_validator("fields")
    @classmethod
    def _validate_fields(cls, fields: list[MetadataFieldDef] | None) -> list[MetadataFieldDef] | None:
        return fields if fields is None else _check_unique_field_keys(fields)


class DocumentClassOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    fields: list[dict] = []
    created_at: str


class ResourceMetadataSetRequest(BaseModel):
    resource_type: str = Field(default="file", pattern=r"^(file|folder)$")
    class_id: str | None = None
    values: dict[str, Any] = {}
    # Only meaningful when resource_type == "folder": also stamp this same
    # class_id/values onto every file and subfolder anywhere in this
    # folder's subtree, not just the folder resource itself.
    apply_to_children: bool = False


class ResourceMetadataOut(BaseModel):
    id: str
    connection_id: str
    resource_id: str
    resource_type: str
    class_id: str | None = None
    values: dict[str, Any] = {}
    updated_at: str
    # Set only when apply_to_children was used -- how many descendant
    # files/folders also got this class_id/values stamped onto them.
    applied_to_count: int | None = None


class ResourceMetadataHistoryEntryOut(BaseModel):
    id: str
    resource_id: str
    resource_type: str
    old_class_id: str | None = None
    new_class_id: str | None = None
    old_values: dict[str, Any] = {}
    new_values: dict[str, Any] = {}
    changed_by: str | None = None
    changed_at: str


# --- webhooks --------------------------------------------------------------

class WebhookCreateRequest(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    # Declared before `secret` so its already-validated value is visible to
    # _require_secret_for_custom() via ValidationInfo.data (Pydantic
    # validates fields in declaration order).
    destination_type: Literal["custom", "slack", "discord"] = "custom"
    # Only required for "custom" -- a Slack/Discord webhook URL is already
    # self-authenticating (the URL itself is the secret), so HMAC signing
    # doesn't apply there.
    secret: str | None = Field(default=None, max_length=200)
    event_types: list[str] = []
    # Mandatory: which file/folder this fires for. An unscoped webhook
    # fires on every event across every connection, which is rarely what
    # anyone actually wants and too easy to create by accident.
    connection_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_name: str = Field(min_length=1)

    # A plain @field_validator("secret") would not catch this: Pydantic v2
    # skips field validators for a field left at its default (unset) value
    # unless validate_default=True is set on it -- since "secret" omitted
    # entirely is exactly the case that needs catching here, a
    # model_validator (which always runs) is used instead.
    @model_validator(mode="after")
    def _require_secret_for_custom(self) -> "WebhookCreateRequest":
        if self.destination_type == "custom" and (not self.secret or len(self.secret) < 8):
            raise ValueError("secret must be at least 8 characters for a custom webhook")
        return self


class WebhookUpdateRequest(BaseModel):
    url: str | None = None
    destination_type: Literal["custom", "slack", "discord"] | None = None
    secret: str | None = None
    event_types: list[str] | None = None
    active: bool | None = None
    connection_id: str | None = None
    resource_id: str | None = None
    resource_type: str | None = None
    resource_name: str | None = None
    clear_scope: bool = False  # explicit, since omitting the scope fields above must mean "leave as-is," not "remove"


class WebhookOut(BaseModel):
    id: str
    url: str
    secret_set: bool = False   # never the raw value — same *_set convention as admin settings' secrets
    destination_type: str = "custom"
    event_types: list[str] = []
    active: bool
    created_at: str
    last_triggered_at: str | None = None
    last_status_code: int | None = None
    connection_id: str | None = None
    resource_id: str | None = None
    resource_type: str | None = None
    resource_name: str | None = None


# --- workflows -------------------------------------------------------------

class AssigneeRef(BaseModel):
    type: str = Field(pattern=r"^(user|group)$")
    id: str   # username for type="user", group id for type="group"


class WorkflowStepDef(BaseModel):
    name: str
    assignees: list[AssigneeRef] = []   # empty = any authenticated user
    required_approvals: int = 1


class WorkflowDefinitionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    steps: list[WorkflowStepDef]


class WorkflowDefinitionOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    steps: list[dict] = []
    created_by: str
    created_at: str


class WorkflowResourceRef(BaseModel):
    resource_id: str
    resource_type: str = Field(default="file", pattern=r"^(file|folder)$")


class WorkflowInstanceCreateRequest(BaseModel):
    definition_id: str
    resources: list[WorkflowResourceRef] = Field(min_length=1)
    comment: str | None = None


class WorkflowAddResourceRequest(BaseModel):
    resource_id: str
    resource_type: str = Field(default="file", pattern=r"^(file|folder)$")


class WorkflowReassignRequest(BaseModel):
    assignees: list[AssigneeRef] = Field(min_length=1)
    comment: str | None = None


class WorkflowStepActionRequest(BaseModel):
    action: str = Field(pattern=r"^(approved|rejected)$")
    comment: str | None = None


class WorkflowStepActionOut(BaseModel):
    id: str
    step_index: int
    reviewer: str
    action: str
    comment: str | None = None
    acted_at: str


class WorkflowInstanceResourceOut(BaseModel):
    id: str
    resource_id: str
    resource_type: str
    resource_name: str | None = None
    added_at: str
    added_by: str


class WorkflowInstanceOut(BaseModel):
    id: str
    definition_id: str
    connection_id: str
    resources: list[WorkflowInstanceResourceOut] = []
    status: str
    current_step: int
    steps: list[WorkflowStepDef] = []
    requested_by: str
    comment: str | None = None
    created_at: str
    completed_at: str | None = None
    step_actions: list[WorkflowStepActionOut] = []


# --- retention -------------------------------------------------------------

class RetentionPolicyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    retention_days: int = Field(ge=1)
    action: str = Field(default="review", pattern=r"^(review|archive|auto_delete)$")
    class_id: str | None = None
    connection_id: str | None = None


class RetentionPolicyOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    retention_days: int
    action: str
    class_id: str | None = None
    connection_id: str | None = None
    active: bool
    created_at: str


class RetentionEnrollRequest(BaseModel):
    policy_id: str
    resource_id: str
    resource_type: str = Field(default="file", pattern=r"^(file|folder)$")
    resource_name: str | None = None


class RetentionRecordOut(BaseModel):
    id: str
    policy_id: str
    connection_id: str
    resource_id: str
    resource_type: str
    resource_name: str | None = None
    due_date: str
    status: str
    legal_hold: bool
    actioned_at: str | None = None


# --- AI agents ---------------------------------------------------------

class AiAgentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    scope_type: str = Field(pattern=r"^(folder|file)$")
    resource_id: str
    resource_name: str = ""


class AiAgentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class AiAgentOut(BaseModel):
    id: str
    name: str
    description: str
    connection_id: str
    provider_key: str
    scope_type: str
    resource_id: str
    resource_name: str
    owner: str
    is_active: bool
    created_at: str
    updated_at: str
    chat_url: str
    embed_url: str
    demo_url: str
    demo_download_url: str


class AiAgentStatsOut(AiAgentOut):
    chat_count: int
    tokens_total: int
    last_chat_at: str | None = None
    lead_count: int = 0


class AiAgentChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AiAgentChatOut(BaseModel):
    answer: str
    sources: list[str] = []
    tokens_used: int | None = None
    tokens_estimated: bool = True


class AiAgentLeadCreateRequest(BaseModel):
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    message: str = Field(min_length=1, max_length=2000)


class AiAgentLeadOut(BaseModel):
    id: str
    agent_id: str
    email: str | None = None
    phone: str | None = None
    message: str
    created_at: str


class AiAgentImageOut(BaseModel):
    """What the pencil editor's image-upload modal gets back -- `url` is
    what it hands straight to Quill's insertEmbed()."""
    id: str
    url: str
    content_type: str
    size_bytes: int


class AiAgentSiteOut(BaseModel):
    headline: str | None = None
    subheadline: str | None = None
    body: str | None = None
    accent_color: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    contact_address: str | None = None
    contact_note: str | None = None
    seo_description: str | None = None
    footer_tagline: str | None = None
    updated_at: str | None = None


class AiAgentSiteUpdateRequest(BaseModel):
    headline: str | None = Field(default=None, max_length=200)
    subheadline: str | None = Field(default=None, max_length=300)
    # Declared before `body` so its already-validated value is visible to
    # _sanitize_body() via ValidationInfo.data (Pydantic validates fields
    # in declaration order) -- both the admin bar's on-page pencil editor
    # and its Customize panel now write real HTML through a Quill editor
    # and set this true; only a legacy body saved before either existed
    # is still plain text, so bleach only runs when there's actually HTML
    # to sanitize.
    is_rich_html: bool = False
    body: str | None = Field(default=None, max_length=8000)  # rich HTML from the pencil editor costs more chars than plain text
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")
    contact_email: str | None = Field(default=None, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=60)
    contact_address: str | None = Field(default=None, max_length=300)
    contact_note: str | None = Field(default=None, max_length=300)
    seo_description: str | None = Field(default=None, max_length=300)
    footer_tagline: str | None = Field(default=None, max_length=160)

    @field_validator("body")
    @classmethod
    def _sanitize_body(cls, v: str | None, info: ValidationInfo) -> str | None:
        return sanitize_rich_html(v) if info.data.get("is_rich_html") else v


class AiAgentEditTokenOut(BaseModel):
    edit_token: str
    expires_at: str


class PublicAiAgentSiteUpdateRequest(AiAgentSiteUpdateRequest):
    edit_token: str


# --- AI agent site pages / blog ----------------------------------------

class AiAgentPageCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    is_rich_html: bool = False  # see AiAgentSiteUpdateRequest.is_rich_html
    content: str = Field(default="", max_length=20_000)
    nav_order: int = 0

    @field_validator("content")
    @classmethod
    def _sanitize_content(cls, v: str, info: ValidationInfo) -> str:
        return (sanitize_rich_html(v) or "") if info.data.get("is_rich_html") else v


class AiAgentPageUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    is_rich_html: bool = False  # see AiAgentSiteUpdateRequest.is_rich_html
    content: str | None = Field(default=None, max_length=20_000)
    nav_order: int | None = None

    @field_validator("content")
    @classmethod
    def _sanitize_content(cls, v: str | None, info: ValidationInfo) -> str | None:
        return sanitize_rich_html(v) if info.data.get("is_rich_html") else v


class AiAgentPageOut(BaseModel):
    id: str
    agent_id: str
    slug: str
    title: str
    content: str
    nav_order: int
    created_at: str
    updated_at: str


class AiAgentPostCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(default="", max_length=500)
    is_rich_html: bool = False  # see AiAgentSiteUpdateRequest.is_rich_html
    content: str = Field(default="", max_length=20_000)

    @field_validator("content")
    @classmethod
    def _sanitize_content(cls, v: str, info: ValidationInfo) -> str:
        return (sanitize_rich_html(v) or "") if info.data.get("is_rich_html") else v


class AiAgentPostUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    excerpt: str | None = Field(default=None, max_length=500)
    is_rich_html: bool = False  # see AiAgentSiteUpdateRequest.is_rich_html
    content: str | None = Field(default=None, max_length=20_000)

    @field_validator("content")
    @classmethod
    def _sanitize_content(cls, v: str | None, info: ValidationInfo) -> str | None:
        return sanitize_rich_html(v) if info.data.get("is_rich_html") else v


class AiAgentPostOut(BaseModel):
    id: str
    agent_id: str
    slug: str
    title: str
    excerpt: str
    content: str
    published_at: str
    updated_at: str


class AiAgentSiteDraftPageOut(BaseModel):
    title: str
    content: str


class AiAgentSiteDraftPostOut(BaseModel):
    title: str
    excerpt: str
    content: str


class AiAgentSiteDraftOut(BaseModel):
    headline: str
    subheadline: str
    body: str
    contact_note: str
    seo_description: str = ""
    footer_tagline: str = ""
    pages: list[AiAgentSiteDraftPageOut] = []
    posts: list[AiAgentSiteDraftPostOut] = []
    sources: list[str] = []
    tokens_used: int | None = None
    tokens_estimated: bool = True


# --- edit-token-gated variants, used by the live site's own admin bar --

class PublicAiAgentPageCreateRequest(AiAgentPageCreateRequest):
    edit_token: str


class PublicAiAgentPageUpdateRequest(AiAgentPageUpdateRequest):
    edit_token: str


class PublicAiAgentPostCreateRequest(AiAgentPostCreateRequest):
    edit_token: str


class PublicAiAgentPostUpdateRequest(AiAgentPostUpdateRequest):
    edit_token: str


class AiAgentSitePublishedOut(BaseModel):
    """What the admin bar's "Generate & publish" button gets back — the
    generated draft was applied immediately (unlike the authenticated
    /ai-agents/{id}/site/generate endpoint, which only proposes a draft
    for the app's UI to apply piece-by-piece): there's no per-item review
    UI to put on the live page itself, so a one-click "make this real"
    action is what the admin bar offers instead."""
    pages_created: int
    posts_created: int
    tokens_used: int | None = None
    tokens_estimated: bool = True


class PublicAiAgentTargetedEditRequest(BaseModel):
    edit_token: str
    instruction: str = Field(min_length=1, max_length=1000)


class AiAgentTargetedEditResultOut(BaseModel):
    """What the admin bar's "describe a specific change" box gets back —
    a surgical alternative to AiAgentSitePublishedOut's whole-site
    regenerate: only the site fields / pages / posts the instruction
    actually named were touched, everything else is untouched."""
    summary: str
    site_updated: bool
    pages_updated: int
    posts_updated: int
    tokens_used: int | None = None
    tokens_estimated: bool = True


class PublicAiAgentItemDraftRequest(BaseModel):
    edit_token: str
    kind: str = Field(pattern=r"^(page|post)$")
    topic: str = Field(default="", max_length=200)


class AiAgentItemDraftOut(BaseModel):
    """What the "Add a page"/"Add a post" forms' own "Generate with AI"
    button gets back -- nothing is saved yet, this just fills the
    create-form's fields for the admin to review before clicking Add."""
    title: str
    content: str
    excerpt: str | None = None


class PublicAiAgentOut(BaseModel):
    name: str
    description: str
