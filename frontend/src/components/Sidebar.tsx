import { useConnections } from "../contexts/ConnectionsContext";
import type { ViewMode } from "../types";
import { Icon, ProviderBadge } from "../icons";
import { useTheme } from "../hooks/useTheme";
import { useAuth } from "../contexts/AuthContext";
import { NotificationsBell } from "./NotificationsBell";
import { AdminMenu } from "./AdminMenu";

export function Sidebar({
  view,
  onViewChange,
  onNewFolder,
  onUploadClick,
  uploading,
  onOpenIntegrations,
  onOpenUsers,
  onOpenDocClasses,
  onOpenWebhooks,
  onOpenRetention,
  onOpenAuditLog,
  onLogout,
}: {
  view: ViewMode;
  onViewChange: (v: ViewMode) => void;
  onNewFolder: () => void;
  onUploadClick: () => void;
  uploading: boolean;
  onOpenIntegrations: () => void;
  onOpenUsers: () => void;
  onOpenDocClasses: () => void;
  onOpenWebhooks: () => void;
  onOpenRetention: () => void;
  onOpenAuditLog: () => void;
  onLogout: () => void;
}) {
  const { connections, activeConnectionId, selectConnection } = useConnections();
  const { pref, cycle } = useTheme();
  const { user, hasRole } = useAuth();
  const isAdmin = hasRole("admin");

  const displayName = user?.display_name || user?.username || "User";

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-logo">
          <Icon name="folder" size={22} />
        </span>
        <span className="sidebar-brand-name">C-ECM</span>
        <NotificationsBell />
      </div>

      <nav className="sidebar-nav sidebar-connections-nav">
        <div className="sidebar-section-label">Connections</div>
        {connections.length === 0 && <p className="muted sidebar-connections-empty">No connections yet.</p>}
        {connections.map((c) => (
          <button
            key={c.id}
            className={c.id === activeConnectionId ? "active" : ""}
            onClick={() => {
              selectConnection(c.id);
              onViewChange("mine");
            }}
          >
            <ProviderBadge providerKey={c.provider_key} size={17} />
            {c.display_name}
          </button>
        ))}
      </nav>

      <div className="sidebar-new">
        <button className="new-menu-btn" onClick={onNewFolder} disabled={!activeConnectionId}>
          <Icon name="folder-plus" size={17} />
          New folder
        </button>
        <button className="new-menu-btn primary" onClick={onUploadClick} disabled={!activeConnectionId || uploading}>
          <Icon name="upload" size={17} />
          {uploading ? "Uploading..." : "Upload"}
        </button>
      </div>

      <nav className="sidebar-nav">
        <button className={view === "trash" ? "active" : ""} onClick={() => onViewChange("trash")}>
          <Icon name="trash" size={17} />
          Trash
        </button>
        <button className={view === "workflows" ? "active" : ""} onClick={() => onViewChange("workflows")}>
          <Icon name="check-circle" size={17} />
          Approvals
        </button>
        <button className={view === "global-search" ? "active" : ""} onClick={() => onViewChange("global-search")}>
          <Icon name="search" size={17} />
          Global Search
        </button>
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-user-row">
          <span className="sidebar-user" title={user?.email || user?.username}>{displayName}</span>
          <div className="sidebar-user-actions">
            {isAdmin && (
              <AdminMenu
                onOpenIntegrations={onOpenIntegrations}
                onOpenUsers={onOpenUsers}
                onOpenDocClasses={onOpenDocClasses}
                onOpenWebhooks={onOpenWebhooks}
                onOpenRetention={onOpenRetention}
                onOpenAuditLog={onOpenAuditLog}
              />
            )}
            <button
              className="theme-toggle"
              onClick={cycle}
              aria-label={`Theme: ${pref}. Click to change.`}
              title={`Theme: ${pref}`}
            >
              <Icon name={pref === "dark" ? "moon" : pref === "light" ? "sun" : "monitor"} size={16} />
            </button>
          </div>
        </div>
        <button className="sidebar-logout" onClick={onLogout}>
          <Icon name="logout" size={15} />
          Sign out
        </button>
      </div>
    </aside>
  );
}
