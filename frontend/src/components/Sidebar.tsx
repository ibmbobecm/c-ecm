import { useConnections } from "../contexts/ConnectionsContext";
import type { ViewMode } from "../types";
import { Icon, ProviderBadge } from "../icons";
import { useTheme } from "../hooks/useTheme";
import { useAuth } from "../contexts/AuthContext";
import { NotificationsBell } from "./NotificationsBell";

export function Sidebar({
  view,
  onViewChange,
  onNewFolder,
  onUploadClick,
  uploading,
  onManageConnections,
  onOpenUsers,
  onOpenDocClasses,
  onOpenWebhooks,
  onOpenRetention,
  onLogout,
}: {
  view: ViewMode;
  onViewChange: (v: ViewMode) => void;
  onNewFolder: () => void;
  onUploadClick: () => void;
  uploading: boolean;
  onManageConnections: () => void;
  onOpenUsers: () => void;
  onOpenDocClasses: () => void;
  onOpenWebhooks: () => void;
  onOpenRetention: () => void;
  onLogout: () => void;
}) {
  const { connections, activeConnectionId, selectConnection } = useConnections();
  const { pref, cycle } = useTheme();
  const { user, hasRole } = useAuth();
  const activeConnection = connections.find((c) => c.id === activeConnectionId) ?? null;
  const isAdmin = hasRole("admin");

  const displayName = user?.display_name || user?.username || "User";

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-logo">
          <Icon name="folder" size={22} />
        </span>
        <span className="sidebar-brand-name">FileDrive</span>
        <NotificationsBell />
      </div>

      <div className="connection-switcher">
        <div className="connection-switcher-select">
          {activeConnection && <ProviderBadge providerKey={activeConnection.provider_key} size={18} />}
          <select
            value={activeConnectionId ?? ""}
            onChange={(e) => selectConnection(e.target.value || null)}
            disabled={connections.length === 0}
          >
            {connections.length === 0 && <option value="">No connections</option>}
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.display_name}
              </option>
            ))}
          </select>
        </div>
        <button className="link-btn connection-manage-btn" onClick={onManageConnections}>
          Manage
        </button>
      </div>

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
        <button className={view === "mine" ? "active" : ""} onClick={() => onViewChange("mine")}>
          <Icon name="folder" size={17} />
          My Drive
        </button>
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

      {isAdmin && (
        <nav className="sidebar-nav sidebar-admin-nav">
          <div className="sidebar-section-label">Admin</div>
          <button onClick={onOpenUsers}>
            <Icon name="eye" size={17} />
            Users
          </button>
          <button onClick={onOpenDocClasses}>
            <Icon name="tag" size={17} />
            Doc Classes
          </button>
          <button onClick={onOpenWebhooks}>
            <Icon name="link" size={17} />
            Webhooks
          </button>
          <button onClick={onOpenRetention}>
            <Icon name="lock" size={17} />
            Retention
          </button>
        </nav>
      )}

      <div className="sidebar-footer">
        <div className="sidebar-user-row">
          <span className="sidebar-user" title={user?.email || user?.username}>{displayName}</span>
          <button
            className="theme-toggle"
            onClick={cycle}
            aria-label={`Theme: ${pref}. Click to change.`}
            title={`Theme: ${pref}`}
          >
            <Icon name={pref === "dark" ? "moon" : pref === "light" ? "sun" : "monitor"} size={16} />
          </button>
        </div>
        <button className="sidebar-logout" onClick={onLogout}>
          <Icon name="logout" size={15} />
          Sign out
        </button>
      </div>
    </aside>
  );
}
