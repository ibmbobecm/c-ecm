/**
 * AccessGrantsDialog — lets someone with the 'manage_resource_permissions'
 * feature see and edit exactly who can view/edit a specific file or
 * folder (routers/access_grants.py). Only reachable when
 * can("manage_resource_permissions") — same client-side-guard-plus-real-
 * server-side-gate pattern as every other admin-only entry point in this
 * app; the backend enforces this regardless of what the UI shows.
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPost, ApiError } from "../api/client";
import type { AccessGrant, DriveItem, Group, User } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";

export function AccessGrantsDialogContent({ item, onClose }: { item: DriveItem; onClose?: () => void }) {
  const [grants, setGrants] = useState<AccessGrant[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [principalType, setPrincipalType] = useState<"user" | "group">("user");
  const [principalId, setPrincipalId] = useState("");
  const [level, setLevel] = useState<"view" | "edit">("view");

  const load = () => {
    setLoading(true);
    Promise.all([
      apiGet<AccessGrant[]>(`/resources/${item.id}/access-grants?resource_type=${item.type}`),
      apiGet<User[]>("/users"),
      apiGet<Group[]>("/groups"),
    ])
      .then(([g, u, gr]) => {
        setGrants(g);
        setUsers(u);
        setGroups(gr);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Couldn't load access grants."))
      .finally(() => setLoading(false));
  };

  useEffect(load, [item.id]);

  const alreadyGranted = new Set(grants.map((g) => `${g.principal_type}:${g.principal_id}`));
  const availablePrincipals =
    principalType === "user"
      ? users.filter((u) => !alreadyGranted.has(`user:${u.id}`))
      : groups.filter((g) => !alreadyGranted.has(`group:${g.id}`));

  const addGrant = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!principalId) return;
    setBusy(true);
    setError(null);
    try {
      await apiPost(`/resources/${item.id}/access-grants?resource_type=${item.type}`, {
        principal_type: principalType,
        principal_id: principalId,
        level,
      });
      setPrincipalId("");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't add that grant.");
    } finally {
      setBusy(false);
    }
  };

  const removeGrant = async (grantId: string) => {
    setBusy(true);
    setError(null);
    try {
      await apiDelete(`/resources/${item.id}/access-grants/${grantId}`);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't remove that grant.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p className="muted" style={{ margin: "0 0 12px", fontSize: 12 }}>
        {grants.length === 0
          ? "No restrictions yet — everyone with access to this connection can view and edit this " + item.type + "."
          : `Restricted — only the ${grants.length} ${grants.length === 1 ? "grant" : "grants"} below (and any inherited from a parent folder) can access this ${item.type}.`}
      </p>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          {grants.length > 0 && (
            <ul style={{ listStyle: "none", padding: 0, margin: "0 0 14px", display: "flex", flexDirection: "column", gap: 6 }}>
              {grants.map((g) => (
                <li key={g.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 13 }}>
                  <span>
                    {g.principal_type === "group" && <Icon name="command" size={13} className="muted" />}
                    {" "}
                    {g.principal_display}
                    <span
                      style={{
                        marginLeft: 8, fontSize: 11, fontWeight: 600, padding: "1px 7px", borderRadius: 10,
                        background: g.level === "edit" ? "#3b82d422" : "#57606a22",
                        color: g.level === "edit" ? "#3b82d4" : "#57606a",
                      }}
                    >
                      {g.level}
                    </span>
                  </span>
                  <button className="icon-btn" title="Remove access" disabled={busy} onClick={() => removeGrant(g.id)}>
                    <Icon name="close" size={13} />
                  </button>
                </li>
              ))}
            </ul>
          )}

          {error && <div className="auth-error" style={{ marginBottom: 12 }}>{error}</div>}

          <form onSubmit={addGrant} className="auth-form" style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, alignItems: "flex-end" }}>
            <label style={{ flex: "1 1 auto", minWidth: 100 }}>
              Type
              <select value={principalType} onChange={(e) => { setPrincipalType(e.target.value as "user" | "group"); setPrincipalId(""); }}>
                <option value="user">User</option>
                <option value="group">Group</option>
              </select>
            </label>
            <label style={{ flex: "2 1 auto", minWidth: 160 }}>
              {principalType === "user" ? "User" : "Group"}
              <select value={principalId} onChange={(e) => setPrincipalId(e.target.value)}>
                <option value="">Choose…</option>
                {principalType === "user"
                  ? (availablePrincipals as User[]).map((u) => (
                      <option key={u.id} value={u.id}>{u.display_name} (@{u.username})</option>
                    ))
                  : (availablePrincipals as Group[]).map((g) => (
                      <option key={g.id} value={g.id}>{g.name}</option>
                    ))}
              </select>
            </label>
            <label style={{ flex: "1 1 auto", minWidth: 100 }}>
              Access
              <select value={level} onChange={(e) => setLevel(e.target.value as "view" | "edit")}>
                <option value="view">View</option>
                <option value="edit">Edit</option>
              </select>
            </label>
            <button type="submit" disabled={!principalId || busy} style={{ flex: "0 0 auto" }}>
              Add
            </button>
          </form>
        </>
      )}

      {onClose && (
        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button type="button" className="secondary" onClick={onClose}>Done</button>
        </div>
      )}
    </>
  );
}

export function AccessGrantsDialog({ item, onClose }: { item: DriveItem; onClose: () => void }) {
  return (
    <Modal title={`Access — ${item.name}`} onClose={onClose} width={460}>
      <AccessGrantsDialogContent item={item} onClose={onClose} />
    </Modal>
  );
}
