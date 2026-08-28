/**
 * RetentionPolicyPanel — admin UI to create and manage retention policies,
 * and view the retention records (documents enrolled in a policy).
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPatch, apiPost, ApiError } from "../api/client";
import type { RetentionPolicy, RetentionRecord } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";
import { formatDate } from "../utils";

function ActionBadge({ action }: { action: RetentionPolicy["action"] }) {
  const map: Record<string, { label: string; color: string }> = {
    review: { label: "Review", color: "#3b82d4" },
    archive: { label: "Archive", color: "#7c5cd8" },
    auto_delete: { label: "Auto-delete", color: "#e53e3e" },
  };
  const { label, color } = map[action] ?? { label: action, color: "#57606a" };
  return (
    <span style={{ fontSize: 11, background: color + "22", color, border: `1px solid ${color}44`, borderRadius: 4, padding: "1px 6px", fontWeight: 600 }}>
      {label}
    </span>
  );
}

export function RetentionPolicyPanel({ onClose }: { onClose: () => void }) {
  const [policies, setPolicies] = useState<RetentionPolicy[]>([]);
  const [records, setRecords] = useState<RetentionRecord[]>([]);
  const [tab, setTab] = useState<"policies" | "records">("policies");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newDays, setNewDays] = useState("365");
  const [newAction, setNewAction] = useState<RetentionPolicy["action"]>("review");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    Promise.all([
      apiGet<RetentionPolicy[]>("/retention/policies"),
      apiGet<RetentionRecord[]>("/retention/records"),
    ])
      .then(([p, r]) => { setPolicies(p); setRecords(r); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load retention data."))
      .finally(() => setLoading(false));
  };
  useEffect(reload, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      await apiPost("/retention/policies", {
        name: newName,
        description: newDesc || null,
        retention_days: parseInt(newDays, 10),
        action: newAction,
        class_id: null,
        connection_id: null,
      });
      setShowCreate(false);
      setNewName("");
      setNewDesc("");
      setNewDays("365");
      setNewAction("review");
      reload();
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "Couldn't create policy.");
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (p: RetentionPolicy) => {
    try {
      await apiPatch(`/retention/policies/${p.id}`, { active: !p.active });
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't update policy.");
    }
  };

  const handleDelete = async (p: RetentionPolicy) => {
    if (!window.confirm(`Delete policy "${p.name}"? Enrolled documents will be un-enrolled.`)) return;
    try {
      await apiDelete(`/retention/policies/${p.id}`);
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't delete policy.");
    }
  };

  const toggleLegalHold = async (r: RetentionRecord) => {
    // PATCH /retention/records/:record_id with { legal_hold: bool }
    try {
      await apiPatch(`/retention/records/${r.id}`, { legal_hold: !r.legal_hold });
      reload();
    } catch {
      // Fall back to the older query-param endpoint
      try {
        await apiPost(`/retention/records/${r.resource_id}/legal-hold?hold=${!r.legal_hold}`);
        reload();
      } catch (e2) {
        setError(e2 instanceof ApiError ? e2.message : "Couldn't update legal hold.");
      }
    }
  };

  return (
    <Modal title="Retention Policies" onClose={onClose} width={640}>
      <div style={{ display: "flex", gap: 0, marginBottom: 20, borderBottom: "1px solid var(--border)" }}>
        {(["policies", "records"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              background: "none",
              border: "none",
              borderBottom: tab === t ? "2px solid var(--accent, #3b82d4)" : "2px solid transparent",
              color: tab === t ? "var(--accent, #3b82d4)" : "var(--text)",
              fontWeight: tab === t ? 600 : 400,
              fontSize: 14,
              padding: "8px 16px",
              cursor: "pointer",
              marginBottom: -1,
            }}
          >
            {t === "policies" ? `Policies (${policies.length})` : `Records (${records.length})`}
          </button>
        ))}
      </div>

      {error && <div className="auth-error" style={{ marginBottom: 12 }}>{error}</div>}

      {tab === "policies" && (
        <>
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 14 }}>
            <button className="btn-primary" style={{ fontSize: 13 }} onClick={() => setShowCreate((s) => !s)}>
              <Icon name="plus" size={13} /> New policy
            </button>
          </div>

          {showCreate && (
            <form className="auth-form" onSubmit={handleCreate} style={{ background: "var(--surface)", padding: 16, borderRadius: 8, marginBottom: 16, border: "1px solid var(--border)" }}>
              <h4 style={{ margin: "0 0 12px" }}>New retention policy</h4>
              <label>Name <input required value={newName} onChange={(e) => setNewName(e.target.value)} /></label>
              <label>Description <input value={newDesc} onChange={(e) => setNewDesc(e.target.value)} /></label>
              <label>Retention period (days) <input type="number" min={1} required value={newDays} onChange={(e) => setNewDays(e.target.value)} /></label>
              <label>
                Action when due
                <select value={newAction} onChange={(e) => setNewAction(e.target.value as RetentionPolicy["action"])}>
                  <option value="review">Flag for review</option>
                  <option value="archive">Archive</option>
                  <option value="auto_delete">Auto-delete</option>
                </select>
              </label>
              {formError && <div className="auth-error">{formError}</div>}
              <div style={{ display: "flex", gap: 8 }}>
                <button type="submit" disabled={busy}>{busy ? "Creating…" : "Create"}</button>
                <button type="button" className="btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
              </div>
            </form>
          )}

          {loading ? (
            <p className="muted">Loading…</p>
          ) : policies.length === 0 ? (
            <p className="muted" style={{ textAlign: "center", padding: 32 }}>No retention policies yet.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {policies.map((p) => (
                <div key={p.id} style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: 12, opacity: p.active ? 1 : 0.6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 14 }}>{p.name}</div>
                      {p.description && <div className="muted" style={{ fontSize: 12 }}>{p.description}</div>}
                      <div style={{ marginTop: 6, display: "flex", gap: 8, alignItems: "center" }}>
                        <ActionBadge action={p.action} />
                        <span className="muted" style={{ fontSize: 12 }}>{p.retention_days} days</span>
                        <span style={{ fontSize: 12, color: p.active ? "#22a06b" : "#57606a" }}>{p.active ? "Active" : "Inactive"}</span>
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button className="icon-btn" title={p.active ? "Deactivate" : "Activate"} onClick={() => toggleActive(p)}>
                        <Icon name={p.active ? "eye-off" : "eye"} size={14} />
                      </button>
                      <button className="icon-btn" title="Delete" style={{ color: "var(--danger, #e53e3e)" }} onClick={() => handleDelete(p)}>
                        <Icon name="trash" size={14} />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {tab === "records" && (
        loading ? (
          <p className="muted">Loading…</p>
        ) : records.length === 0 ? (
          <p className="muted" style={{ textAlign: "center", padding: 32 }}>No documents enrolled in a retention policy yet.</p>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Document</th>
                <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Due date</th>
                <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Status</th>
                <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 600 }}>Hold</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={r.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "8px 8px" }}>
                    <div style={{ fontWeight: 500 }}>{r.resource_name ?? r.resource_id}</div>
                    <div className="muted" style={{ fontSize: 11 }}>{r.resource_type}</div>
                  </td>
                  <td style={{ padding: "8px 8px", fontSize: 12, color: new Date(r.due_date) <= new Date() ? "#e53e3e" : "inherit" }}>
                    {formatDate(r.due_date)}
                  </td>
                  <td style={{ padding: "8px 8px", fontSize: 12 }}>{r.status}</td>
                  <td style={{ padding: "8px 8px" }}>
                    <button
                      className="icon-btn"
                      title={r.legal_hold ? "Remove legal hold" : "Place on legal hold"}
                      style={{ color: r.legal_hold ? "#e53e3e" : "var(--muted)" }}
                      onClick={() => toggleLegalHold(r)}
                    >
                      <Icon name={r.legal_hold ? "lock" : "unlock"} size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}
    </Modal>
  );
}
