"""The fixed catalog of app "features" — named capabilities a Group can be
granted, which its members then inherit. This is the single source of
truth: both `auth.require_feature()` (the actual gate) and `GET /features`
(the list the group-editor UI renders checkboxes from) read this list, so
adding a new gated capability means adding one entry here, not updating
two places that could drift apart.

Each key corresponds 1:1 to a capability that was previously gated by
`require_role("admin")` or `require_role("editor")` before the switch to
groups/features — see the routers listed below for exactly where each one
is enforced.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    description: str


FEATURES: list[Feature] = [
    Feature("manage_users", "Manage users", "Create, edit, deactivate, and delete user accounts."),
    Feature("manage_groups", "Manage groups", "Create groups, assign features to them, and assign users to groups."),
    Feature("manage_admin_settings", "Manage admin settings",
            "Configure OAuth apps, DocuSign, the AI backend, and SAML SSO."),
    Feature("view_activity_log", "View activity log", "See the audit trail of actions across all connections."),
    Feature("manage_document_classes", "Manage document classes", "Define and edit metadata schemas (document classes)."),
    Feature("manage_retention", "Manage retention policies", "Create and edit retention/disposition policies."),
    Feature("manage_webhooks", "Manage webhooks", "Create, edit, and delete outgoing webhooks."),
    Feature("manage_ai_agents_admin", "Manage AI agents (admin)", "Administer AI agent configuration across the deployment."),
    Feature("send_esignature", "Send for e-signature", "Send documents out for signature via DocuSign."),
    Feature("manage_connections", "Manage connections", "Delete storage backend connections."),
    Feature("manage_workflow_definitions", "Manage workflow definitions", "Create and delete approval workflow templates."),
    Feature("manage_resource_permissions", "Manage resource permissions",
            "Grant or revoke view/edit access to specific files and folders for other users or groups."),
]

FEATURE_KEYS: frozenset[str] = frozenset(f.key for f in FEATURES)
