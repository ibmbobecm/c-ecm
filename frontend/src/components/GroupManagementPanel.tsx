/**
 * GroupManagementPanel — admin UI for the group/feature access-control
 * system. Create groups, assign them Features (from the fixed catalog at
 * GET /features), and assign Users to them — a user inherits the union of
 * every group's features they belong to. Only renders when the current
 * user has the 'manage_groups' feature (enforced server-side too).
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPatch, apiPost, ApiError } from "../api/client";
import type { Feature, Group, User } from "../types";
import { Icon } from "../icons";

export function GroupManagementPanel() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [features, setFeatures] = useState<Feature[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newFeatureKeys, setNewFeatureKeys] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editFeatureKeys, setEditFeatureKeys] = useState<string[]>([]);
  const [editBusy, setEditBusy] = useState(false);
  const [memberIds, setMemberIds] = useState<Set<string>>(new Set());
  const [addMemberId, setAddMemberId] = useState("");

  const reload = () => {
    setLoading(true);
    Promise.all([apiGet<Group[]>("/groups"), apiGet<Feature[]>("/features"), apiGet<User[]>("/users")])
      .then(([g, f, u]) => {
        setGroups(g);
        setFeatures(f);
        setUsers(u);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load groups."))
      .finally(() => setLoading(false));
  };

  useEffect(reload, []);

  const toggleNewFeature = (key: string) =>
    setNewFeatureKeys((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));

  const toggleEditFeature = (key: string) =>
    setEditFeatureKeys((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      await apiPost("/groups", { name: newName, description: newDescription || null, feature_keys: newFeatureKeys });
      setShowCreate(false);
      setNewName("");
      setNewDescription("");
      setNewFeatureKeys([]);
      reload();
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "Couldn't create group.");
    } finally {
      setBusy(false);
    }
  };

  const startExpand = (group: Group) => {
    setError(null);
    if (expandedId === group.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(group.id);
    setEditFeatureKeys(group.feature_keys);
    apiGet<User[]>(`/groups/${group.id}/members`)
      .then((members) => setMemberIds(new Set(members.map((m) => m.id))))
      .catch(() => setMemberIds(new Set()));
  };

  const saveFeatures = async (group: Group) => {
    setEditBusy(true);
    try {
      await apiPatch(`/groups/${group.id}`, { feature_keys: editFeatureKeys });
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't update group.");
    } finally {
      setEditBusy(false);
    }
  };

  const addMember = async (group: Group) => {
    if (!addMemberId) return;
    try {
      await apiPost(`/groups/${group.id}/members/${addMemberId}`, {});
      setMemberIds((prev) => new Set(prev).add(addMemberId));
      setAddMemberId("");
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't add member.");
    }
  };

  const removeMember = async (group: Group, userId: string) => {
    try {
      await apiDelete(`/groups/${group.id}/members/${userId}`);
      setMemberIds((prev) => {
        const next = new Set(prev);
        next.delete(userId);
        return next;
      });
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't remove member.");
    }
  };

  const handleDelete = async (group: Group) => {
    if (!window.confirm(`Delete group "${group.name}"? Members lose whatever access it granted them.`)) return;
    try {
      await apiDelete(`/groups/${group.id}`);
      if (expandedId === group.id) setExpandedId(null);
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't delete group.");
    }
  };

  return (
    <div className="settings-tab-pane">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <span className="muted" style={{ fontSize: 13 }}>{groups.length} group{groups.length !== 1 ? "s" : ""}</span>
        <button className="btn-primary" style={{ fontSize: 13 }} onClick={() => setShowCreate((s) => !s)}>
          <Icon name="plus" size={13} />
          New group
        </button>
      </div>

      {showCreate && (
        <form className="auth-form" onSubmit={handleCreate} style={{ background: "var(--surface)", padding: 16, borderRadius: 8, marginBottom: 16, border: "1px solid var(--border)" }}>
          <h4 style={{ margin: "0 0 12px" }}>Create group</h4>
          <label>Name <input required value={newName} onChange={(e) => setNewName(e.target.value)} /></label>
          <label>Description <input value={newDescription} onChange={(e) => setNewDescription(e.target.value)} placeholder="Optional" /></label>
          <div style={{ marginBottom: 4 }}>
            <span style={{ fontSize: 13, fontWeight: 500 }}>Features</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
              {features.map((f) => (
                <label key={f.key} style={{ display: "flex", flexDirection: "row", alignItems: "flex-start", gap: 6, cursor: "pointer", fontSize: 13 }}>
                  <input type="checkbox" checked={newFeatureKeys.includes(f.key)} onChange={() => toggleNewFeature(f.key)} style={{ marginTop: 2 }} />
                  <span>
                    {f.label}
                    <span className="muted" style={{ display: "block", fontSize: 11 }}>{f.description}</span>
                  </span>
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
      ) : groups.length === 0 ? (
        <p className="muted">No groups yet — everyone's access is either "super admin" or nothing until you create one.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {groups.map((g) => (
            <div key={g.id} style={{ border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
              <button
                type="button"
                onClick={() => startExpand(g)}
                style={{
                  width: "100%", display: "flex", justifyContent: "space-between", alignItems: "center",
                  padding: "10px 12px", background: "var(--surface)", border: "none", cursor: "pointer", textAlign: "left",
                }}
              >
                <div>
                  <div style={{ fontWeight: 500, fontSize: 13 }}>{g.name}</div>
                  {g.description && <div className="muted" style={{ fontSize: 11 }}>{g.description}</div>}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 12 }}>
                  <span className="muted">{g.feature_keys.length} feature{g.feature_keys.length !== 1 ? "s" : ""}</span>
                  <span className="muted">{g.member_count} member{g.member_count !== 1 ? "s" : ""}</span>
                  <Icon name={expandedId === g.id ? "chevron-down" : "chevron-right"} size={14} />
                </div>
              </button>

              {expandedId === g.id && (
                <div style={{ padding: 14, borderTop: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 16 }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Features</div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {features.map((f) => (
                        <label key={f.key} style={{ display: "flex", flexDirection: "row", alignItems: "flex-start", gap: 6, cursor: "pointer", fontSize: 13 }}>
                          <input type="checkbox" checked={editFeatureKeys.includes(f.key)} onChange={() => toggleEditFeature(f.key)} style={{ marginTop: 2 }} />
                          <span>
                            {f.label}
                            <span className="muted" style={{ display: "block", fontSize: 11 }}>{f.description}</span>
                          </span>
                        </label>
                      ))}
                    </div>
                    <button type="button" disabled={editBusy} onClick={() => saveFeatures(g)} style={{ marginTop: 8, fontSize: 12 }}>
                      {editBusy ? "Saving…" : "Save features"}
                    </button>
                  </div>

                  <div>
                    <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Members</div>
                    {users.filter((u) => memberIds.has(u.id)).length === 0 ? (
                      <p className="muted" style={{ fontSize: 12, margin: 0 }}>No members yet.</p>
                    ) : (
                      <ul style={{ listStyle: "none", padding: 0, margin: "0 0 8px", display: "flex", flexDirection: "column", gap: 4 }}>
                        {users.filter((u) => memberIds.has(u.id)).map((u) => (
                          <li key={u.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 13 }}>
                            <span>{u.display_name} <span className="muted" style={{ fontSize: 11 }}>@{u.username}</span></span>
                            <button className="icon-btn" title="Remove from group" onClick={() => removeMember(g, u.id)}>
                              <Icon name="close" size={13} />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="auth-form" style={{ flexDirection: "row", gap: 6 }}>
                      <select value={addMemberId} onChange={(e) => setAddMemberId(e.target.value)} style={{ fontSize: 13 }}>
                        <option value="">Add a user…</option>
                        {users.filter((u) => !memberIds.has(u.id)).map((u) => (
                          <option key={u.id} value={u.id}>{u.display_name} (@{u.username})</option>
                        ))}
                      </select>
                      <button type="button" disabled={!addMemberId} onClick={() => addMember(g)} style={{ fontSize: 12 }}>
                        Add
                      </button>
                    </div>
                  </div>

                  <div>
                    <button
                      type="button"
                      className="icon-btn"
                      style={{ color: "var(--danger, #e53e3e)", fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}
                      onClick={() => handleDelete(g)}
                    >
                      <Icon name="trash" size={13} />
                      Delete group
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
