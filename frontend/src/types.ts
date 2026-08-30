export type User = {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  roles: string[];
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
};

export type ConfigField = {
  key: string;
  label: string;
  placeholder: string;
  required: boolean;
};

export type Provider = {
  key: string;
  display_name: string;
  auth_mode: "credentials" | "oauth";
  configured: boolean;
  config_fields: ConfigField[];
  requires_credentials: boolean;
  credential_labels: [string, string];
};

export type AdminSettings = {
  google_client_id: string;
  google_client_secret_set: boolean;
  ms_client_id: string;
  ms_client_secret_set: boolean;
  ms_tenant: string;
  box_client_id: string;
  box_client_secret_set: boolean;
  docusign_integration_key: string;
  docusign_user_id: string;
  docusign_account_id: string;
  docusign_private_key_set: boolean;
  docusign_environment: string;
  docusign_webhook_hmac_key_set: boolean;
  docusign_configured: boolean;
  ai_backend: string;
  ibm_cloud_api_key_set: boolean;
  watsonx_project_id: string;
  watsonx_url: string;
  watsonx_model: string;
  watsonx_configured: boolean;
  watson_nlu_url: string;
  watson_nlu_apikey_set: boolean;
  watson_nlu_configured: boolean;
  watson_disco_url: string;
  watson_disco_apikey_set: boolean;
  watson_disco_project_id: string;
  watson_disco_configured: boolean;
};

export type ESignatureSigner = {
  name: string;
  email: string;
  routing_order: number;
};

export type ESignatureRequest = {
  id: string;
  connection_id: string;
  resource_id: string;
  resource_type: string;
  resource_name: string | null;
  envelope_id: string;
  status: "sent" | "delivered" | "completed" | "declined" | "voided" | string;
  signers: ESignatureSigner[];
  subject: string | null;
  requested_by: string;
  created_at: string;
  completed_at: string | null;
  signed_version_number: number | null;
};

export type Connection = {
  id: string;
  provider_key: string;
  display_name: string;
  identity: string | null;
  created_at: string;
};

export type FolderItem = {
  type: "folder";
  id: string;
  name: string;
  parent_id: string | null;
  created_at: string | null;
};

export type FileItem = {
  type: "file";
  id: string;
  name: string;
  folder_id: string | null;
  version_number: number;
  size_bytes: number | null;
  content_type: string | null;
  updated_at: string | null;
};

export type DriveItem = FolderItem | FileItem;

export type BreadcrumbEntry = {
  id: string | null;
  name: string;
};

export type FolderContents = {
  folder: FolderItem | null;
  breadcrumb: BreadcrumbEntry[];
  folders: FolderItem[];
  files: FileItem[];
};

export type FileVersion = {
  id: string;
  version_number: number;
  size_bytes: number | null;
  content_type: string | null;
  is_current: boolean;
  updated_at: string | null;
};

export type SearchResult = {
  folders: FolderItem[];
  files: FileItem[];
};

export type ViewMode = "mine" | "trash" | "workflows" | "global-search";

export type GlobalSearchHit = {
  connection_id: string;
  connection_name: string;
  provider_key: string;
  resource_type: "file" | "folder";
  resource_id: string;
  name: string;
  size_bytes: number | null;
  content_type: string | null;
  updated_at: string | null;
};

export type GlobalSearchResult = {
  query: string;
  hits: GlobalSearchHit[];
  connection_errors: Record<string, string>;
};

export type SortKey = "name" | "modified" | "size";

export type SortState = {
  key: SortKey;
  dir: "asc" | "desc";
};

export type Tag = {
  id: string;
  name: string;
  color: string;
  created_at: string;
};

export type Comment = {
  id: string;
  connection_id: string;
  resource_id: string;
  resource_type: string;
  parent_comment_id: string | null;
  body: string;
  mentioned_users: string[];
  resolved_at: string | null;
  resolved_by: string | null;
  created_by: string;
  created_at: string;
  edited_at: string | null;
};

export type Notification = {
  id: string;
  message: string;
  read_at: string | null;
  created_at: string;
};

export type NotificationSummary = {
  unread_count: number;
  notifications: Notification[];
};

export type ShareLink = {
  id: string;
  url: string;
  role: "view" | "comment" | "edit";
  expires_at: string | null;
  password_protected: boolean;
};

export type ActivityEvent = {
  id: string;
  connection_id: string | null;
  provider_key: string | null;
  resource_type: string;
  resource_id: string;
  resource_name: string | null;
  event_type: string;
  actor: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type ActivityTypeCount = { event_type: string; count: number };
export type ActivityActorCount = { actor: string; count: number };
export type ActivityDayCount = { day: string; count: number };
export type ActivityAlert = {
  severity: "warning" | "danger";
  title: string;
  detail: string;
  actor: string;
  event_type: string;
  count: number;
  window_start: string;
  window_end: string;
};
export type ActivitySummary = {
  total_events: number;
  unique_actors: number;
  by_type: ActivityTypeCount[];
  by_actor: ActivityActorCount[];
  by_day: ActivityDayCount[];
  alerts: ActivityAlert[];
};

// --- locks -----------------------------------------------------------------

export type Lock = {
  id: string;
  connection_id: string;
  resource_id: string;
  locked_by: string;
  locked_at: string;
  comment: string | null;
};

// --- document classes / metadata -------------------------------------------

export type MetadataFieldDef = {
  key: string;
  label: string;
  type: "text" | "number" | "date" | "boolean" | "select";
  required: boolean;
  options: string[];
};

export type DocumentClass = {
  id: string;
  name: string;
  description: string | null;
  fields: MetadataFieldDef[];
  created_at: string;
};

export type ResourceMetadata = {
  id: string;
  connection_id: string;
  resource_id: string;
  resource_type: string;
  class_id: string | null;
  values: Record<string, unknown>;
  updated_at: string;
};

// --- webhooks --------------------------------------------------------------

export type Webhook = {
  id: string;
  url: string;
  secret: string;
  event_types: string[];
  active: boolean;
  created_at: string;
  last_triggered_at: string | null;
  last_status_code: number | null;
};

// --- workflows -------------------------------------------------------------

export type WorkflowStepDef = {
  name: string;
  reviewers: string[];
  required_approvals: number;
};

export type WorkflowDefinition = {
  id: string;
  name: string;
  description: string | null;
  steps: WorkflowStepDef[];
  created_by: string;
  created_at: string;
};

export type WorkflowStepAction = {
  id: string;
  step_index: number;
  reviewer: string;
  action: string;
  comment: string | null;
  acted_at: string;
};

export type WorkflowInstance = {
  id: string;
  definition_id: string;
  connection_id: string;
  resource_id: string;
  resource_type: string;
  resource_name: string | null;
  status: "in_review" | "approved" | "rejected" | "cancelled";
  current_step: number;
  requested_by: string;
  comment: string | null;
  created_at: string;
  completed_at: string | null;
  step_actions: WorkflowStepAction[];
};

// --- retention -------------------------------------------------------------

export type RetentionPolicy = {
  id: string;
  name: string;
  description: string | null;
  retention_days: number;
  action: "review" | "archive" | "auto_delete";
  class_id: string | null;
  connection_id: string | null;
  active: boolean;
  created_at: string;
};

export type RetentionRecord = {
  id: string;
  policy_id: string;
  connection_id: string;
  resource_id: string;
  resource_type: string;
  resource_name: string | null;
  due_date: string;
  status: string;
  legal_hold: boolean;
  actioned_at: string | null;
  created_at: string;
};
