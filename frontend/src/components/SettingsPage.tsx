import { useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { Icon } from "../icons";
import { IntegrationsPage } from "../pages/IntegrationsPage";
import { AuditLogPage } from "../pages/AuditLogPage";
import { AiAgentsReportPage } from "../pages/AiAgentsReportPage";
import { UserManagementPanel } from "./UserManagementPanel";
import { GroupManagementPanel } from "./GroupManagementPanel";
import { ClassListView } from "./DocumentClassesPanel";
import { WebhookManagementPanel } from "./WebhookManagementPanel";
import { RetentionPolicyPanel } from "./RetentionPolicyPanel";
import { SamlSettingsPanel } from "./SamlSettingsPanel";

export type SettingsTab = "connections" | "users" | "groups" | "doc-classes" | "webhooks" | "retention" | "reports" | "ai-agents" | "sso";

// requiredFeature: undefined means "any authenticated user" — mirrors the
// backend 1:1 (only routes actually gated by require_feature() get a tab
// gated here too; everything else stays open exactly like before).
const TABS: { key: SettingsTab; label: string; icon: Parameters<typeof Icon>[0]["name"]; requiredFeature?: string }[] = [
  { key: "connections", label: "Connections", icon: "plug" },
  { key: "users", label: "Users", icon: "eye", requiredFeature: "manage_users" },
  { key: "groups", label: "Groups", icon: "command", requiredFeature: "manage_groups" },
  { key: "doc-classes", label: "Doc Classes", icon: "tag", requiredFeature: "manage_document_classes" },
  { key: "webhooks", label: "Webhooks", icon: "link", requiredFeature: "manage_webhooks" },
  { key: "retention", label: "Retention", icon: "lock", requiredFeature: "manage_retention" },
  { key: "reports", label: "Reports", icon: "bar-chart", requiredFeature: "view_activity_log" },
  { key: "ai-agents", label: "AI Agents", icon: "bot", requiredFeature: "manage_ai_agents_admin" },
  { key: "sso", label: "SSO", icon: "settings", requiredFeature: "manage_admin_settings" },
];

// A single settings area (Box-style: one page, tabs across the top) rather
// than each section being its own full-page takeover or modal popup — the
// Drive sidebar stays visible and clicking a tab just swaps this pane's
// content, the same way the individual sections used to each swap the
// entire app or pop up their own dialog.
export function SettingsPage({
  initialTab,
  onClose,
}: {
  initialTab: SettingsTab;
  onClose: () => void;
}) {
  const { can } = useAuth();
  const visibleTabs = TABS.filter((t) => !t.requiredFeature || can(t.requiredFeature));
  const [tab, setTab] = useState<SettingsTab>(
    visibleTabs.some((t) => t.key === initialTab) ? initialTab : (visibleTabs[0]?.key ?? "connections")
  );

  return (
    <div className="settings-page">
      <div className="settings-header">
        <button className="icon-btn" onClick={onClose} aria-label="Back to Drive" title="Back to Drive">
          <Icon name="chevron-right" size={16} className="viewer-back-icon" />
        </button>
        <h1>Settings</h1>
      </div>

      <div className="settings-tabs">
        {visibleTabs.map((t) => (
          <button
            key={t.key}
            className={"settings-tab-btn" + (tab === t.key ? " active" : "")}
            onClick={() => setTab(t.key)}
          >
            <Icon name={t.icon} size={15} />
            {t.label}
          </button>
        ))}
      </div>

      <div className="settings-tab-content">
        {tab === "connections" && <IntegrationsPage />}
        {tab === "users" && <UserManagementPanel />}
        {tab === "groups" && <GroupManagementPanel />}
        {tab === "doc-classes" && <ClassListView />}
        {tab === "webhooks" && <WebhookManagementPanel />}
        {tab === "retention" && <RetentionPolicyPanel />}
        {tab === "reports" && <AuditLogPage />}
        {tab === "ai-agents" && <AiAgentsReportPage />}
        {tab === "sso" && <SamlSettingsPanel />}
      </div>
    </div>
  );
}
