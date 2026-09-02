/**
 * UserManagementPanel — admin UI for creating, editing, and deactivating
 * user accounts. Only renders when the current user has the 'manage_users'
 * feature (enforced server-side too; this is just a UX guard). Group
 * membership is assigned from the Groups tab, not here — this panel only
 * shows a read-only "Groups" column for context.
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPatch, apiPost, ApiError } from "../api/client";
import type { User } from "../types";
import { Icon } from "../icons";

export function UserManagementPanel() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // create-user form
  const [showCreate, setShowCreate] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newDisplayName, setNewDisplayName] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newIsSuperadmin, setNewIsSuperadmin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [toggleBusyId, setToggleBusyId] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    apiGet<User[]>("/users")
      .then(setUsers)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load users."))
      .finally(() => setLoading(false));
  };

  useEffect(reload, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      await apiPost("/users", {
        username: newUsername,
        display_name: newDisplayName || newUsername,
        email: newEmail || null,
        password: newPassword,
        is_superadmin: newIsSuperadmin,
      });
      setShowCreate(false);
      setNewUsername("");
      setNewDisplayName("");
      setNewEmail("");
      setNewPassword("");
      setNewIsSuperadmin(false);
      reload();
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "Couldn't create user.");
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (user: User) => {
    try {
      await apiPatch(`/users/${user.id}`, { is_active: !user.is_active });
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't update user.");
    }
  };

  const toggleSuperadmin = async (user: User) => {
    setError(null);
    setToggleBusyId(user.id);
    try {
      await apiPatch(`/users/${user.id}`, { is_superadmin: !user.is_superadmin });
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't update user.");
    } finally {
      setToggleBusyId(null);
    }
  };

  const handleDelete = async (user: User) => {
    if (!window.confirm(`Permanently delete user "${user.username}"? This cannot be undone.`)) return;
    try {
      await apiDelete(`/users/${user.id}`);
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't delete user.");
    }
  };

  return (
    <div className="settings-tab-pane">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <span className="muted" style={{ fontSize: 13 }}>{users.length} user{users.length !== 1 ? "s" : ""}</span>
        <button className="btn-primary" style={{ fontSize: 13 }} onClick={() => setShowCreate((s) => !s)}>
          <Icon name="plus" size={13} />
          New user
        </button>
      </div>

      {showCreate && (
        <form className="auth-form" onSubmit={handleCreate} style={{ background: "var(--surface)", padding: 16, borderRadius: 8, marginBottom: 16, border: "1px solid var(--border)" }}>
          <h4 style={{ margin: "0 0 12px" }}>Create user</h4>
          <label>Username <input required value={newUsername} onChange={(e) => setNewUsername(e.target.value)} /></label>
          <label>Display name <input value={newDisplayName} onChange={(e) => setNewDisplayName(e.target.value)} placeholder={newUsername || "Optional"} /></label>
          <label>Email <input type="email" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} /></label>
          <label>Password <input type="password" required value={newPassword} onChange={(e) => setNewPassword(e.target.value)} /></label>
          <label style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 6, cursor: "pointer" }}>
            <input type="checkbox" checked={newIsSuperadmin} onChange={(e) => setNewIsSuperadmin(e.target.checked)} />
            Super admin (bypasses every feature check — access to everything)
          </label>
          <p className="muted" style={{ margin: "0 0 4px", fontSize: 12 }}>
            Everyone else's access comes entirely from Group membership — assign this user to one or more groups
            from the Groups tab after creating them.
          </p>
          {formError && <div className="auth-error">{formError}</div>}
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" disabled={busy}>{busy ? "Creating…" : "Create"}</button>
            <button type="button" className="btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
        </form>
      )}

      {error && <div className="auth-error" style={{ marginBottom: 12 }}>{error}</div>}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", minWidth: 620, borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>User</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Super admin</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Groups</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Status</th>
              <th style={{ width: 80 }} />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} style={{ borderBottom: "1px solid var(--border)", opacity: u.is_active ? 1 : 0.5 }}>
                <td style={{ padding: "8px 8px" }}>
                  <div style={{ fontWeight: 500 }}>{u.display_name}</div>
                  <div className="muted" style={{ fontSize: 11 }}>@{u.username}{u.email ? ` · ${u.email}` : ""}</div>
                </td>
                <td style={{ padding: "8px 8px" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={u.is_superadmin}
                      disabled={toggleBusyId === u.id}
                      onChange={() => toggleSuperadmin(u)}
                    />
                    {u.is_superadmin && <span style={{ fontSize: 11, color: "#e53e3e", fontWeight: 600 }}>Super admin</span>}
                  </label>
                </td>
                <td style={{ padding: "8px 8px" }}>
                  {u.groups.length > 0 ? (
                    <span className="muted" style={{ fontSize: 12 }}>{u.groups.join(", ")}</span>
                  ) : (
                    <span className="muted" style={{ fontSize: 12 }}>—</span>
                  )}
                </td>
                <td style={{ padding: "8px 8px" }}>
                  <span style={{ fontSize: 12, color: u.is_active ? "var(--success, #22a06b)" : "var(--muted)" }}>
                    {u.is_active ? "Active" : "Disabled"}
                  </span>
                </td>
                <td style={{ padding: "8px 8px", textAlign: "right", whiteSpace: "nowrap" }}>
                  <button
                    className="icon-btn"
                    title={u.is_active ? "Disable account" : "Enable account"}
                    onClick={() => toggleActive(u)}
                  >
                    <Icon name={u.is_active ? "eye-off" : "eye"} size={15} />
                  </button>
                  <button
                    className="icon-btn"
                    title="Delete user"
                    style={{ color: "var(--danger, #e53e3e)", marginLeft: 4 }}
                    onClick={() => handleDelete(u)}
                  >
                    <Icon name="trash" size={15} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
