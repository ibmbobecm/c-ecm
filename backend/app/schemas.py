import datetime
from typing import Any

from pydantic import BaseModel, Field


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


# ---------- users / RBAC ---------------------------------------------------

class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None = None
    roles: list[str] = []
    is_active: bool = True
    created_at: str
    last_login_at: str | None = None


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=6, max_length=200)
    display_name: str = Field(min_length=1, max_length=100)
    email: str | None = None
    roles: list[str] = ["viewer"]


class UserUpdateRequest(BaseModel):
    display_name: str | None = None
    email: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=6, max_length=200)


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
    docusign_integration_key: str
    docusign_user_id: str
    docusign_account_id: str
    docusign_private_key_set: bool
    docusign_environment: str
    docusign_webhook_hmac_key_set: bool
    docusign_configured: bool


class AdminSettingsUpdate(BaseModel):
    google_client_id: str | None = None
    google_client_secret: str | None = None
    ms_client_id: str | None = None
    ms_client_secret: str | None = None
    ms_tenant: str | None = None
    box_client_id: str | None = None
    box_client_secret: str | None = None
    docusign_integration_key: str | None = None
    docusign_user_id: str | None = None
    docusign_account_id: str | None = None
    docusign_private_key: str | None = None
    docusign_environment: str | None = None
    docusign_webhook_hmac_key: str | None = None


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
    updated_at: str | None = None


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
    key: str
    label: str
    type: str = "text"   # text | number | date | boolean | select
    required: bool = False
    options: list[str] = []   # for select type


class DocumentClassCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    fields: list[MetadataFieldDef] = []


class DocumentClassUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    fields: list[MetadataFieldDef] | None = None


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


class ResourceMetadataOut(BaseModel):
    id: str
    connection_id: str
    resource_id: str
    resource_type: str
    class_id: str | None = None
    values: dict[str, Any] = {}
    updated_at: str


# --- webhooks --------------------------------------------------------------

class WebhookCreateRequest(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    secret: str = Field(min_length=8, max_length=200)
    event_types: list[str] = []


class WebhookUpdateRequest(BaseModel):
    url: str | None = None
    secret: str | None = None
    event_types: list[str] | None = None
    active: bool | None = None


class WebhookOut(BaseModel):
    id: str
    url: str
    secret: str
    event_types: list[str] = []
    active: bool
    created_at: str
    last_triggered_at: str | None = None
    last_status_code: int | None = None


# --- workflows -------------------------------------------------------------

class WorkflowStepDef(BaseModel):
    name: str
    reviewers: list[str] = []   # usernames; empty = any authenticated user
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


class WorkflowInstanceCreateRequest(BaseModel):
    definition_id: str
    resource_id: str
    resource_type: str = Field(default="file", pattern=r"^(file|folder)$")
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


class WorkflowInstanceOut(BaseModel):
    id: str
    definition_id: str
    connection_id: str
    resource_id: str
    resource_type: str
    resource_name: str | None = None
    status: str
    current_step: int
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
    created_at: str
