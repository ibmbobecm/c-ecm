/**
 * WorkflowsPanel — three-tab approval workflow hub.
 *
 * Inbox      : pending approvals for the current user (approve / reject)
 * My Requests: workflows submitted by the current user (status + cancel)
 * Designer   : admin-only workflow definition builder (create / delete)
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPost } from "../api/client";
import type { WorkflowInstance, WorkflowDefinition, WorkflowStepDef } from "../types";
import { useAuth } from "../contexts/AuthContext";
import { useConnections } from "../contexts/ConnectionsContext";
import { Icon } from "../icons";
import { formatDate } from "../utils";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: WorkflowInstance["status"] }) {
  const map: Record<string, { label: string; style: React.CSSProperties }> = {
    in_review: { label: "Pending",   style: { background: "var(--warning-tint)", color: "var(--warning)" } },
    approved:  { label: "Approved",  style: { background: "var(--success-tint)", color: "var(--success)" } },
    rejected:  { label: "Rejected",  style: { background: "var(--danger-tint)",  color: "var(--danger)"  } },
    cancelled: { label: "Cancelled", style: { background: "var(--bg-tertiary)",  color: "var(--text-secondary)" } },
  };
  const { label, style } = map[status] ?? map.in_review;
  return (
    <span style={{ fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 999, ...style }}>
      {label}
    </span>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      padding: "var(--space-4)",
      marginBottom: "var(--space-3)",
    }}>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Inbox
// ---------------------------------------------------------------------------

function InboxTab({
  instances,
  definitions,
  loading,
  onRefresh,
}: {
  instances: WorkflowInstance[];
  definitions: WorkflowDefinition[];
  loading: boolean;
  onRefresh: () => void;
}) {
  const { user } = useAuth();
  const [actioning, setActioning] = useState<string | null>(null);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [flash, setFlash] = useState<string | null>(null);

  const defName = (id: string) => definitions.find((d) => d.id === id)?.name ?? id;

  // Instances where the current user is a listed reviewer and hasn't acted yet
  const pending = instances.filter((i) => {
    if (i.status !== "in_review") return false;
    const step = i.step_actions;
    const def = definitions.find((d) => d.id === i.definition_id);
    if (!def) return false;
    const stepDef = def.steps[i.current_step];
    if (!stepDef) return false;
    const alreadyActed = step.some(
      (a) => a.reviewer === user?.username && a.step_index === i.current_step,
    );
    // An empty reviewers list means the backend allows ANY authenticated
    // user to act on this step (see act_on_step's `if reviewers and
    // reviewer not in reviewers`) — [].includes(...) is always false, so
    // without this check that kind of step would never show up in
    // anyone's inbox even though the API accepts an action on it.
    const isReviewer = stepDef.reviewers.length === 0 || stepDef.reviewers.includes(user?.username ?? "");
    return isReviewer && !alreadyActed;
  });

  const act = async (instanceId: string, action: "approved" | "rejected") => {
    setActioning(instanceId);
    try {
      await apiPost(`/workflows/instances/${instanceId}/action`, {
        action,
        comment: comments[instanceId] ?? "",
      });
      setFlash(action === "approved" ? "Approved!" : "Rejected.");
      setComments((prev) => { const n = { ...prev }; delete n[instanceId]; return n; });
      onRefresh();
    } catch {
      setFlash("Action failed. Please try again.");
    } finally {
      setActioning(null);
    }
  };

  return (
    <div>
      {flash && (
        <div className="flash flash-success" style={{ marginBottom: "var(--space-3)" }}>
          {flash}
          <button style={{ background: "none", border: "none", marginLeft: 8, cursor: "pointer" }} onClick={() => setFlash(null)}>
            <Icon name="close" size={13} />
          </button>
        </div>
      )}

      {loading && <p className="muted">Loading…</p>}

      {!loading && pending.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon"><Icon name="check-circle" size={40} /></div>
          <h3>All caught up</h3>
          <p>No documents are waiting for your review.</p>
        </div>
      )}

      {pending.map((inst) => {
        const def = definitions.find((d) => d.id === inst.definition_id);
        const stepDef = def?.steps[inst.current_step];
        return (
          <Card key={inst.id}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
              <span style={{ fontWeight: 600, fontSize: "var(--text-base)" }}>
                {inst.resource_name ?? inst.resource_id}
              </span>
              <StatusBadge status={inst.status} />
            </div>
            <div style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", marginBottom: 4 }}>
              Workflow: <strong>{defName(inst.definition_id)}</strong>
              {stepDef && <> · Step: <strong>{stepDef.name}</strong></>}
              {" · "}Requested by <strong>{inst.requested_by}</strong>
              {" · "}{formatDate(inst.created_at)}
            </div>
            {inst.comment && (
              <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", margin: "4px 0 8px", fontStyle: "italic" }}>
                "{inst.comment}"
              </p>
            )}
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: "var(--space-3)" }}>
              <input
                placeholder="Comment (optional)"
                value={comments[inst.id] ?? ""}
                onChange={(e) => setComments((prev) => ({ ...prev, [inst.id]: e.target.value }))}
                style={{
                  flex: 1, minWidth: 160,
                  padding: "6px 10px",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border)",
                  background: "var(--surface)",
                  color: "var(--text)",
                  fontSize: "var(--text-sm)",
                }}
              />
              <button
                className="btn-primary"
                disabled={actioning === inst.id}
                onClick={() => act(inst.id, "approved")}
                style={{ background: "var(--success)", gap: 4, display: "flex", alignItems: "center" }}
              >
                <Icon name="check" size={14} /> Approve
              </button>
              <button
                className="btn-primary"
                disabled={actioning === inst.id}
                onClick={() => act(inst.id, "rejected")}
                style={{ background: "var(--danger)", gap: 4, display: "flex", alignItems: "center" }}
              >
                <Icon name="close" size={14} /> Reject
              </button>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: My Requests
// ---------------------------------------------------------------------------

function MyRequestsTab({
  instances,
  definitions,
  loading,
  onRefresh,
}: {
  instances: WorkflowInstance[];
  definitions: WorkflowDefinition[];
  loading: boolean;
  onRefresh: () => void;
}) {
  const { user } = useAuth();
  const [cancelling, setCancelling] = useState<string | null>(null);

  const mine = instances.filter((i) => i.requested_by === user?.username);
  const defName = (id: string) => definitions.find((d) => d.id === id)?.name ?? id;

  const cancel = async (id: string) => {
    if (!window.confirm("Cancel this approval request?")) return;
    setCancelling(id);
    try {
      await apiPost(`/workflows/instances/${id}/cancel`, {});
      onRefresh();
    } catch {
      // swallow — the instance may already be completed
    } finally {
      setCancelling(null);
    }
  };

  return (
    <div>
      {loading && <p className="muted">Loading…</p>}

      {!loading && mine.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon"><Icon name="check-circle" size={40} /></div>
          <h3>No requests yet</h3>
          <p>Right-click any file or folder and choose "Request Approval" to start a workflow.</p>
        </div>
      )}

      {mine.map((inst) => (
        <Card key={inst.id}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
            <span style={{ fontWeight: 600, fontSize: "var(--text-base)" }}>
              {inst.resource_name ?? inst.resource_id}
            </span>
            <StatusBadge status={inst.status} />
          </div>
          <div style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", marginBottom: 4 }}>
            Workflow: <strong>{defName(inst.definition_id)}</strong>
            {" · "}{formatDate(inst.created_at)}
            {inst.completed_at && <> · Completed {formatDate(inst.completed_at)}</>}
          </div>
          {inst.comment && (
            <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", margin: "4px 0", fontStyle: "italic" }}>
              "{inst.comment}"
            </p>
          )}
          {inst.status === "in_review" && (
            <button
              className="btn-secondary"
              disabled={cancelling === inst.id}
              onClick={() => cancel(inst.id)}
              style={{ marginTop: "var(--space-3)", fontSize: "var(--text-sm)" }}
            >
              {cancelling === inst.id ? "Cancelling…" : "Cancel request"}
            </button>
          )}
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Designer (admin only)
// ---------------------------------------------------------------------------

function DesignerTab({
  definitions,
  loading,
  onRefresh,
}: {
  definitions: WorkflowDefinition[];
  loading: boolean;
  onRefresh: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [steps, setSteps] = useState<WorkflowStepDef[]>([]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const resetForm = () => {
    setName("");
    setDescription("");
    setSteps([]);
    setFormError(null);
  };

  const addStep = () =>
    setSteps((s) => [...s, { name: "", reviewers: [], required_approvals: 1 }]);

  const updateStep = (i: number, patch: Partial<WorkflowStepDef>) =>
    setSteps((s) => s.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));

  const removeStep = (i: number) =>
    setSteps((s) => s.filter((_, idx) => idx !== i));

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (steps.length === 0) { setFormError("Add at least one step."); return; }
    for (const s of steps) {
      if (!s.name.trim()) { setFormError("All steps need a name."); return; }
      if (s.reviewers.length === 0) { setFormError(`Step "${s.name}" needs at least one reviewer.`); return; }
    }
    setBusy(true);
    setFormError(null);
    try {
      await apiPost("/workflows/definitions", {
        name: name.trim(),
        description: description.trim() || null,
        steps,
      });
      setShowForm(false);
      resetForm();
      onRefresh();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Could not create workflow.");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (def: WorkflowDefinition) => {
    if (!window.confirm(`Delete workflow "${def.name}"? This cannot be undone.`)) return;
    setDeleting(def.id);
    setDeleteError(null);
    try {
      await apiDelete(`/workflows/definitions/${def.id}`);
      onRefresh();
    } catch (err) {
      // A definition with requests still awaiting approval is rejected
      // with 409 (deleting it would freeze those requests in in_review
      // limbo forever) — worth surfacing, not swallowing.
      setDeleteError(err instanceof Error ? err.message : "Could not delete workflow.");
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-4)" }}>
        <span style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
          {definitions.length} workflow definition{definitions.length !== 1 ? "s" : ""}
        </span>
        <button
          className="btn-primary"
          onClick={() => { resetForm(); setShowForm((s) => !s); }}
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          <Icon name="plus" size={14} />
          New workflow
        </button>
      </div>

      {showForm && (
        <form
          onSubmit={handleCreate}
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: "var(--space-4)",
            marginBottom: "var(--space-4)",
          }}
        >
          <h4 style={{ margin: "0 0 12px", fontSize: "var(--text-base)", fontWeight: 600 }}>
            New workflow definition
          </h4>

          <label style={{ display: "block", marginBottom: 10 }}>
            <span style={{ fontSize: "var(--text-sm)", fontWeight: 500, display: "block", marginBottom: 4 }}>Name *</span>
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Document Review"
              style={{ width: "100%", padding: "7px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", fontSize: "var(--text-sm)", boxSizing: "border-box" }}
            />
          </label>

          <label style={{ display: "block", marginBottom: 14 }}>
            <span style={{ fontSize: "var(--text-sm)", fontWeight: 500, display: "block", marginBottom: 4 }}>Description</span>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional"
              style={{ width: "100%", padding: "7px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", fontSize: "var(--text-sm)", boxSizing: "border-box" }}
            />
          </label>

          <div style={{ marginBottom: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>Steps</span>
              <button type="button" className="btn-secondary" style={{ fontSize: 12 }} onClick={addStep}>
                + Add step
              </button>
            </div>

            {steps.length === 0 && (
              <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", margin: 0 }}>
                No steps yet — click "Add step" to begin.
              </p>
            )}

            {steps.map((step, i) => (
              <div
                key={i}
                style={{
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  padding: "10px 12px",
                  marginBottom: 8,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--text-secondary)" }}>
                    Step {i + 1}
                  </span>
                  <button type="button" className="icon-btn" onClick={() => removeStep(i)} title="Remove step">
                    <Icon name="close" size={13} />
                  </button>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr auto", gap: 8, alignItems: "end" }}>
                  <label style={{ margin: 0 }}>
                    <span style={{ fontSize: 11, fontWeight: 500, color: "var(--text-secondary)", display: "block", marginBottom: 3 }}>Step name</span>
                    <input
                      required
                      placeholder="e.g. Manager Review"
                      value={step.name}
                      onChange={(e) => updateStep(i, { name: e.target.value })}
                      style={{ width: "100%", padding: "6px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)", fontSize: "var(--text-sm)", boxSizing: "border-box" }}
                    />
                  </label>
                  <label style={{ margin: 0 }}>
                    <span style={{ fontSize: 11, fontWeight: 500, color: "var(--text-secondary)", display: "block", marginBottom: 3 }}>Reviewers (comma-separated usernames)</span>
                    <input
                      required
                      placeholder="alice, bob, carol"
                      value={step.reviewers.join(", ")}
                      onChange={(e) =>
                        updateStep(i, {
                          reviewers: e.target.value
                            .split(",")
                            .map((s) => s.trim())
                            .filter(Boolean),
                        })
                      }
                      style={{ width: "100%", padding: "6px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)", fontSize: "var(--text-sm)", boxSizing: "border-box" }}
                    />
                  </label>
                  <label style={{ margin: 0 }}>
                    <span style={{ fontSize: 11, fontWeight: 500, color: "var(--text-secondary)", display: "block", marginBottom: 3 }}>Required approvals</span>
                    <input
                      type="number"
                      min={1}
                      max={step.reviewers.length || 1}
                      value={step.required_approvals}
                      onChange={(e) => updateStep(i, { required_approvals: Math.max(1, Number(e.target.value)) })}
                      style={{ width: "100%", padding: "6px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)", fontSize: "var(--text-sm)", boxSizing: "border-box" }}
                    />
                  </label>
                </div>
              </div>
            ))}
          </div>

          {formError && (
            <div style={{ padding: "8px 12px", background: "var(--danger-tint)", color: "var(--danger)", borderRadius: "var(--radius-sm)", fontSize: "var(--text-sm)", marginBottom: 12 }}>
              {formError}
            </div>
          )}

          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? "Creating…" : "Create workflow"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => { setShowForm(false); resetForm(); }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {deleteError && (
        <div className="flash flash-error" style={{ marginBottom: "var(--space-3)" }}>
          {deleteError}
          <button style={{ background: "none", border: "none", marginLeft: 8, cursor: "pointer" }} onClick={() => setDeleteError(null)}>
            <Icon name="close" size={13} />
          </button>
        </div>
      )}

      {loading && <p className="muted">Loading…</p>}

      {!loading && definitions.length === 0 && !showForm && (
        <div className="empty-state">
          <div className="empty-state-icon"><Icon name="check-circle" size={40} /></div>
          <h3>No workflow definitions</h3>
          <p>Click "New workflow" to create an approval workflow with custom steps.</p>
        </div>
      )}

      {definitions.map((def) => (
        <Card key={def.id}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: "var(--text-base)" }}>{def.name}</div>
              {def.description && (
                <div style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", marginTop: 2 }}>{def.description}</div>
              )}
            </div>
            <button
              className="icon-btn"
              style={{ color: "var(--danger)" }}
              disabled={deleting === def.id}
              onClick={() => handleDelete(def)}
              title="Delete definition"
            >
              <Icon name="trash" size={15} />
            </button>
          </div>

          {def.steps.length > 0 && (
            <div style={{ marginTop: 10 }}>
              {def.steps.map((step, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    fontSize: "var(--text-sm)",
                    padding: "4px 0",
                    borderTop: i > 0 ? "1px solid var(--border)" : undefined,
                    marginTop: i > 0 ? 4 : 0,
                  }}
                >
                  <span style={{
                    width: 20, height: 20,
                    borderRadius: "50%",
                    background: "var(--accent-tint)",
                    color: "var(--accent)",
                    display: "inline-flex", alignItems: "center", justifyContent: "center",
                    fontSize: 11, fontWeight: 700, flexShrink: 0,
                  }}>{i + 1}</span>
                  <span style={{ fontWeight: 500 }}>{step.name}</span>
                  <span style={{ color: "var(--text-secondary)" }}>
                    · {step.reviewers.join(", ")}
                    · needs {step.required_approvals} approval{step.required_approvals !== 1 ? "s" : ""}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-tertiary)" }}>
            Created by {def.created_by} · {formatDate(def.created_at)}
          </div>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public export — tabbed wrapper
// ---------------------------------------------------------------------------

type Tab = "inbox" | "requests" | "designer";

export function WorkflowsPanel() {
  const { activeConnectionId } = useConnections();
  const { hasRole } = useAuth();
  const isAdmin = hasRole("admin");

  const [tab, setTab] = useState<Tab>("inbox");
  const [instances, setInstances] = useState<WorkflowInstance[]>([]);
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([]);
  const [loading, setLoading] = useState(false);

  const load = () => {
    if (!activeConnectionId) return;
    setLoading(true);
    Promise.all([
      apiGet<WorkflowInstance[]>("/workflows/instances"),
      apiGet<WorkflowDefinition[]>("/workflows/definitions"),
    ])
      .then(([inst, defs]) => {
        setInstances(inst);
        setDefinitions(defs);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [activeConnectionId]);

  const tabStyle = (t: Tab): React.CSSProperties => ({
    padding: "8px 16px",
    border: "none",
    background: "none",
    cursor: "pointer",
    fontSize: "var(--text-sm)",
    fontWeight: 600,
    color: tab === t ? "var(--accent)" : "var(--text-secondary)",
    borderBottom: tab === t ? "2px solid var(--accent)" : "2px solid transparent",
    transition: "color 120ms, border-color 120ms",
  });

  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: "inbox",    label: "Inbox",      badge: instances.filter((i) => i.status === "in_review").length || undefined },
    { id: "requests", label: "My Requests" },
    ...(isAdmin ? [{ id: "designer" as Tab, label: "Designer" }] : []),
  ];

  return (
    <div style={{ padding: "var(--space-5)", maxWidth: 760 }}>
      <h2 style={{ fontSize: "var(--text-lg)", fontWeight: 700, marginBottom: "var(--space-4)" }}>
        Approval Workflows
      </h2>

      {/* Tab bar */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border)", marginBottom: "var(--space-4)", gap: 0 }}>
        {tabs.map((t) => (
          <button key={t.id} style={tabStyle(t.id)} onClick={() => setTab(t.id)}>
            {t.label}
            {t.badge != null && t.badge > 0 && (
              <span style={{
                marginLeft: 6,
                background: "var(--danger)",
                color: "white",
                borderRadius: 999,
                fontSize: 10,
                fontWeight: 700,
                padding: "1px 6px",
              }}>
                {t.badge}
              </span>
            )}
          </button>
        ))}
        <button
          style={{ marginLeft: "auto", padding: "8px 10px", border: "none", background: "none", cursor: "pointer", color: "var(--text-secondary)" }}
          onClick={load}
          title="Refresh"
        >
          <Icon name="refresh" size={15} />
        </button>
      </div>

      {/* Tab content */}
      {tab === "inbox" && (
        <InboxTab instances={instances} definitions={definitions} loading={loading} onRefresh={load} />
      )}
      {tab === "requests" && (
        <MyRequestsTab instances={instances} definitions={definitions} loading={loading} onRefresh={load} />
      )}
      {tab === "designer" && isAdmin && (
        <DesignerTab definitions={definitions} loading={loading} onRefresh={load} />
      )}
    </div>
  );
}
