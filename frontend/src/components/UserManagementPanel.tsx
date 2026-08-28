/**
 * UserManagementPanel — admin UI for creating, editing roles, and
 * deactivating user accounts.  Only renders when the current user has the
 * "admin" role (enforced server-side too; this is just a UX guard).
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPatch, apiPost, ApiError } from "../api/client";
import type { User } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";

const ALL_ROLES = ["admin", "editor", "viewer"] as const;
type Role = (typeof ALL_ROLES)[number];

function RolePill({ role }: { role: string }) {
  const colour = role === "admin" ? "#e53e3e" : role === "editor" ? "#3b82d4" : "#57606a";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 7px",
        borderRadius: 10,
        fontSize: 11,
        fontWeight: 600,
        background: colour + "22",
        color: colour,
        border: `1px solid ${colour}44`,
        marginRight: 4,
      }}
    >
      {role}
    </span>
  );
}

export function UserManagementPanel({ onClose }: { onClose: () => void }) {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // create-user form
  const [showCreate, setShowCreate] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newDisplayName, setNewDisplayName] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRoles, setNewRoles] = useState<Role[]>(["viewer"]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // inline role editor for an existing user
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editRoles, setEditRoles] = useState<Role[]>([]);
  const [editBusy, setEditBusy] = useState(false);

  const reload = () => {
    setLoading(true);
    apiGet<User[]>("/users")
      .then(setUsers)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load users."))
      .finally(() => setLoading(false));
  };

  useEffect(reload, []);

  const toggleRole = (r: Role) =>
    setNewRoles((prev) => (prev.includes(r) ? prev.filter((x) => x !== r) : [...prev, r]));

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
        roles: newRoles,
      });
      setShowCreate(false);
      setNewUsername("");
      setNewDisplayName("");
      setNewEmail("");
      setNewPassword("");
      setNewRoles(["viewer"]);
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

  const startEditRoles = (user: User) => {
    setError(null);
    setEditingId(user.id);
    setEditRoles(user.roles.filter((r): r is Role => (ALL_ROLES as readonly string[]).includes(r)));
  };

  const toggleEditRole = (r: Role) =>
    setEditRoles((prev) => (prev.includes(r) ? prev.filter((x) => x !== r) : [...prev, r]));

  const saveEditRoles = async (user: User) => {
    setEditBusy(true);
    try {
      await apiPatch(`/users/${user.id}`, { roles: editRoles });
      setEditingId(null);
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't update roles.");
    } finally {
      setEditBusy(false);
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
    <Modal title="User Management" onClose={onClose} width={620}>
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
          <div style={{ marginBottom: 12 }}>
            <span style={{ fontSize: 13, fontWeight: 500 }}>Roles</span>
            <div style={{ display: "flex", gap: 12, marginTop: 6 }}>
              {ALL_ROLES.map((r) => (
                <label key={r} style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer", fontSize: 13 }}>
                  <input type="checkbox" checked={newRoles.includes(r)} onChange={() => toggleRole(r)} />
                  {r}
                </label>
              ))}
            </div>
          </div>
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
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>User</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Roles</th>
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
                  {editingId === u.id ? (
                    <div style={{ display: "flex", gap: 10 }}>
                      {ALL_ROLES.map((r) => (
                        <label key={r} style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer", fontSize: 12 }}>
                          <input type="checkbox" checked={editRoles.includes(r)} onChange={() => toggleEditRole(r)} />
                          {r}
                        </label>
                      ))}
                    </div>
                  ) : (
                    u.roles.map((r) => <RolePill key={r} role={r} />)
                  )}
                </td>
                <td style={{ padding: "8px 8px" }}>
                  <span style={{ fontSize: 12, color: u.is_active ? "var(--success, #22a06b)" : "var(--muted)" }}>
                    {u.is_active ? "Active" : "Disabled"}
                  </span>
                </td>
                <td style={{ padding: "8px 8px", textAlign: "right", whiteSpace: "nowrap" }}>
                  {editingId === u.id ? (
                    <>
                      <button
                        className="icon-btn"
                        title="Save roles"
                        disabled={editBusy || editRoles.length === 0}
                        onClick={() => saveEditRoles(u)}
                      >
                        <Icon name="check" size={15} />
                      </button>
                      <button
                        className="icon-btn"
                        title="Cancel"
                        disabled={editBusy}
                        style={{ marginLeft: 4 }}
                        onClick={() => setEditingId(null)}
                      >
                        <Icon name="close" size={15} />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        className="icon-btn"
                        title="Edit roles"
                        onClick={() => startEditRoles(u)}
                      >
                        <Icon name="rename" size={15} />
                      </button>
                      <button
                        className="icon-btn"
                        title={u.is_active ? "Disable account" : "Enable account"}
                        style={{ marginLeft: 4 }}
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
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Modal>
  );
}
