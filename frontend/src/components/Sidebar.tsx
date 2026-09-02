import { useEffect, useState } from "react";
import { useConnections } from "../contexts/ConnectionsContext";
import type { ViewMode } from "../types";
import { Icon, ProviderBadge } from "../icons";
import { useTheme } from "../hooks/useTheme";
import { useAuth } from "../contexts/AuthContext";
import { NotificationsBell } from "./NotificationsBell";
import { NewMenu } from "./NewMenu";

const COLLAPSED_KEY = "sidebar-collapsed";

export function Sidebar({
  view,
  onViewChange,
  onNewFolder,
  onUploadClick,
  onUploadFolderClick,
  uploading,
  onOpenSettings,
  onLogout,
}: {
  view: ViewMode;
  onViewChange: (v: ViewMode) => void;
  onNewFolder: () => void;
  onUploadClick: () => void;
  onUploadFolderClick: () => void;
  uploading: boolean;
  onOpenSettings: () => void;
  onLogout: () => void;
}) {
  const { connections, activeConnectionId, selectConnection } = useConnections();
  const { pref, cycle } = useTheme();
  const { user } = useAuth();
  // Show the gear icon whenever at least one Settings tab would actually
  // render for this user — mirrors SettingsPage.tsx's per-tab feature
  // gating instead of an old blanket "admin role" check.
  const canSeeSettings = Boolean(user?.is_superadmin) || (user?.features?.length ?? 0) > 0;
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSED_KEY) === "1");

  useEffect(() => {
    localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  const displayName = user?.display_name || user?.username || "User";

  return (
    <aside className={"sidebar" + (collapsed ? " collapsed" : "")}>
      <div className="sidebar-brand">
        <span className="sidebar-logo">
          <Icon name="folder" size={22} />
        </span>
        <span className="sidebar-brand-name">C-ECM</span>
        {!collapsed && <NotificationsBell />}
        <button
          className="sidebar-collapse-btn"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <Icon name="chevron-right" size={14} className={collapsed ? "" : "sidebar-collapse-icon"} />
        </button>
      </div>

      <div className="sidebar-new">
        <NewMenu
          disabled={!activeConnectionId}
          uploading={uploading}
          onNewFolder={onNewFolder}
          onUploadFiles={onUploadClick}
          onUploadFolder={onUploadFolderClick}
        />
      </div>

      <nav className="sidebar-nav">
        <button className={view === "global-search" ? "active" : ""} onClick={() => onViewChange("global-search")} title="Global Search">
          <Icon name="search" size={17} />
          <span className="sidebar-label">Global Search</span>
        </button>
        <button className={view === "workflows" ? "active" : ""} onClick={() => onViewChange("workflows")} title="Approvals">
          <Icon name="check-circle" size={17} />
          <span className="sidebar-label">Approvals</span>
        </button>
        <button className={view === "trash" ? "active" : ""} onClick={() => onViewChange("trash")} title="Trash">
          <Icon name="trash" size={17} />
          <span className="sidebar-label">Trash</span>
        </button>
      </nav>

      <nav className="sidebar-nav sidebar-connections-nav">
        <div className="sidebar-section-label">Connections</div>
        {connections.length === 0 && <p className="muted sidebar-connections-empty">No connections yet.</p>}
        {connections.map((c) => (
          <button
            key={c.id}
            className={c.id === activeConnectionId ? "active" : ""}
            title={c.display_name}
            onClick={() => {
              selectConnection(c.id);
              onViewChange("mine");
            }}
          >
            <ProviderBadge providerKey={c.provider_key} size={17} />
            <span className="sidebar-label">{c.display_name}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-user-row">
          <span className="sidebar-user" title={user?.email || user?.username}>{displayName}</span>
          <div className="sidebar-user-actions">
            {canSeeSettings && (
              <button className="icon-btn" onClick={onOpenSettings} aria-label="Settings" title="Settings">
                <Icon name="settings" size={18} />
              </button>
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
        <button className="sidebar-logout" onClick={onLogout} title="Sign out">
          <Icon name="logout" size={15} />
          <span className="sidebar-label">Sign out</span>
        </button>
      </div>
    </aside>
  );
}
