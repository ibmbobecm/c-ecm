export type User = {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  is_superadmin: boolean;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  groups: string[]; // group names this user belongs to, for display
  group_ids: string[]; // same groups, by id -- for matching a group-type workflow assignee
  features: string[]; // flattened feature set from all their groups
};

export type Feature = {
  key: string;
  label: string;
  description: string;
};

export type Group = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  feature_keys: string[];
  member_count: number;
};

export type AccessGrant = {
  id: string;
  resource_id: string;
  resource_type: string;
  principal_type: "user" | "group";
  principal_id: string;
  principal_display: string;
  level: "view" | "edit";
  created_at: string;
  created_by: string | null;
};

export type EffectiveAccess = {
  level: "view" | "edit" | "none" | null;
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
  coming_soon: boolean;
};

export type AdminSettings = {
  google_client_id: string;
  google_client_secret_set: boolean;
  ms_client_id: string;
  ms_client_secret_set: boolean;
  ms_tenant: string;
  box_client_id: string;
  box_client_secret_set: boolean;
  dropbox_client_id: string;
  dropbox_client_secret_set: boolean;
  laserfiche_client_id: string;
  laserfiche_client_secret_set: boolean;
  sharefile_client_id: string;
  sharefile_client_secret_set: boolean;
  egnyte_client_id: string;
  egnyte_client_secret_set: boolean;
  egnyte_domain: string;
  confluence_client_id: string;
  confluence_client_secret_set: boolean;
  huddle_client_id: string;
  huddle_client_secret_set: boolean;
  netdocuments_client_id: string;
  netdocuments_client_secret_set: boolean;
  zoho_workdrive_client_id: string;
  zoho_workdrive_client_secret_set: boolean;
  imanage_client_id: string;
  imanage_client_secret_set: boolean;
  imanage_base_url: string;
  onehub_client_id: string;
  onehub_client_secret_set: boolean;
  salesforce_files_client_id: string;
  salesforce_files_client_secret_set: boolean;
  oracle_content_management_client_id: string;
  oracle_content_management_client_secret_set: boolean;
  oracle_content_management_base_url: string;
  oracle_content_management_idcs_url: string;
  kiteworks_client_id: string;
  kiteworks_client_secret_set: boolean;
  kiteworks_base_url: string;
  evernote_teams_client_id: string;
  evernote_teams_client_secret_set: boolean;
  saml_enabled: boolean;
  saml_idp_entity_id: string;
  saml_idp_sso_url: string;
  saml_idp_x509_cert_set: boolean;
  saml_default_group_id: string;
  saml_sp_entity_id: string;
  docusign_integration_key: string;
  docusign_user_id: string;
  docusign_account_id: string;
  docusign_private_key_set: boolean;
  docusign_environment: string;
  docusign_webhook_hmac_key_set: boolean;
  docusign_configured: boolean;
  ai_backend: string;
  anthropic_api_key_set: boolean;
  anthropic_model: string;
  anthropic_configured: boolean;
  ai_api_key_set: boolean;
  ai_base_url: string;
  ai_model: string;
  ai_openai_configured: boolean;
  ollama_url: string;
  ollama_model: string;
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
  applied_to_count?: number | null;
};

export type ResourceMetadataHistoryEntry = {
  id: string;
  resource_id: string;
  resource_type: string;
  old_class_id: string | null;
  new_class_id: string | null;
  old_values: Record<string, unknown>;
  new_values: Record<string, unknown>;
  changed_by: string | null;
  changed_at: string;
};

// --- webhooks --------------------------------------------------------------

export type Webhook = {
  id: string;
  url: string;
  secret_set: boolean;
  destination_type: "custom" | "slack" | "discord";
  event_types: string[];
  active: boolean;
  created_at: string;
  last_triggered_at: string | null;
  last_status_code: number | null;
  connection_id: string | null;
  resource_id: string | null;
  resource_type: string | null;
  resource_name: string | null;
};

// --- workflows -------------------------------------------------------------

export type AssigneeRef = {
  type: "user" | "group";
  id: string; // username for type="user", group id for type="group"
};

export type WorkflowStepDef = {
  name: string;
  assignees: AssigneeRef[]; // empty = any authenticated user
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

export type WorkflowInstanceResource = {
  id: string;
  resource_id: string;
  resource_type: "file" | "folder";
  resource_name: string | null;
  added_at: string;
  added_by: string;
};

export type WorkflowInstance = {
  id: string;
  definition_id: string;
  connection_id: string;
  resources: WorkflowInstanceResource[];
  status: "in_review" | "approved" | "rejected" | "cancelled";
  current_step: number;
  steps: WorkflowStepDef[]; // this instance's own snapshot -- reflects reassignment, not the shared definition
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

export type AiAgent = {
  id: string;
  name: string;
  description: string;
  connection_id: string;
  provider_key: string;
  scope_type: "folder" | "file";
  resource_id: string;
  resource_name: string;
  owner: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  chat_url: string;
  embed_url: string;
  demo_url: string;
  demo_download_url: string;
};

export type AiAgentStats = AiAgent & {
  chat_count: number;
  tokens_total: number;
  last_chat_at: string | null;
  lead_count: number;
};

export type AiAgentLead = {
  id: string;
  agent_id: string;
  email: string | null;
  phone: string | null;
  message: string;
  created_at: string;
};
