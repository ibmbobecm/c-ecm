/**
 * WorkflowsPanel — three-tab approval workflow hub.
 *
 * Inbox      : pending approvals for the current user (approve / reject)
 * My Requests: workflows submitted by the current user (status + cancel)
 * Designer   : admin-only workflow definition builder (create / delete)
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPost } from "../api/client";
import type { AssigneeRef, Group, User, WorkflowInstance, WorkflowDefinition, WorkflowStepDef } from "../types";
import { useAuth } from "../contexts/AuthContext";
import { useConnections } from "../contexts/ConnectionsContext";
import { Icon } from "../icons";
import { formatDate } from "../utils";
import { ResourcePickerDialog, type PickedResource } from "./ResourcePickerDialog";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/** Display name for a user- or group-type assignee. Falls back to the raw
 * id if the user/group can't be found (e.g. a group that's since been
 * deleted — see workflows_store.delete_for_group, which clears the
 * reference from the shared definition but a completed instance's own
 * steps_json snapshot keeps the id as a historical record). */
export function assigneeLabel(a: AssigneeRef, users: User[], groups: Group[]): string {
  if (a.type === "group") {
    // groups may be empty for a non-admin viewer (GET /groups requires
    // 'manage_groups') rather than the group actually being gone -- "a
    // group" is honest in both cases, where "(deleted group)" would not be.
    return groups.find((g) => g.id === a.id)?.name ?? "a group";
  }
  const u = users.find((x) => x.username === a.id);
  return u ? u.display_name || u.username : a.id;
}

/** Is `user` eligible to act on (or be considered "involved in") a step
 * with these assignees — directly named, or a member of a named group.
 * Mirrors the backend's access_control-style matching in
 * routers/workflows.py's act_on_step/_is_involved. */
export function isAssigned(user: User | null | undefined, assignees: AssigneeRef[]): boolean {
  if (!user) return false;
  return assignees.some(
    (a) => (a.type === "user" && a.id === user.username) || (a.type === "group" && user.group_ids.includes(a.id))
  );
}

/** The document(s) a workflow instance covers — one name inline (today's
 * look) or an expandable "N documents" list for a multi-document request. */
export function ResourceList({ instance }: { instance: WorkflowInstance }) {
  const [open, setOpen] = useState(false);
  const resources = instance.resources;
  if (resources.length === 0) return null;
  if (resources.length === 1) {
    return <span style={{ fontWeight: 600, fontSize: "var(--text-base)" }}>{resources[0].resource_name ?? resources[0].resource_id}</span>;
  }
  return (
    <span>
      <button
        type="button"
        className="link-btn"
        style={{ fontWeight: 600, fontSize: "var(--text-base)" }}
        onClick={() => setOpen((o) => !o)}
      >
        {resources.length} documents {open ? "▾" : "▸"}
      </button>
      {open && (
        <ul style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: "var(--text-sm)", fontWeight: 400 }}>
          {resources.map((r) => (
            <li key={r.id}>{r.resource_name ?? r.resource_id}</li>
          ))}
        </ul>
      )}
    </span>
  );
}

/** Client-side mirror of the backend's _is_involved (routers/workflows.py)
 * — who sees the Reassign / Add document controls. The real gate is
 * server-side; this just avoids showing a control that would predictably
 * 403. Deliberately NOT "any authenticated user" even on an open step. */
export function canManageInstance(user: User | null | undefined, isWorkflowAdmin: boolean, inst: WorkflowInstance): boolean {
  if (!user) return false;
  if (isWorkflowAdmin) return true;
  if (inst.requested_by === user.username) return true;
  if (inst.status !== "in_review") return false;
  const step = inst.steps[inst.current_step];
  if (!step || step.assignees.length === 0) return false;
  return isAssigned(user, step.assignees);
}

export function ReassignControl({
  instance,
  users,
  groups,
  onDone,
}: {
  instance: WorkflowInstance;
  users: User[];
  groups: Group[];
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [principalType, setPrincipalType] = useState<"user" | "group">("user");
  const [principalId, setPrincipalId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!principalId) return;
    setBusy(true);
    setError(null);
    try {
      await apiPost(`/workflows/instances/${instance.id}/reassign`, {
        assignees: [{ type: principalType, id: principalId }],
      });
      setOpen(false);
      setPrincipalId("");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't reassign this step.");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className="link-btn" style={{ fontSize: 12 }} onClick={() => setOpen(true)}>
        Reassign
      </button>
    );
  }

  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginTop: 6 }}>
      <select
        value={principalType}
        onChange={(e) => { setPrincipalType(e.target.value as "user" | "group"); setPrincipalId(""); }}
        style={{ fontSize: 12, padding: "3px 4px" }}
      >
        <option value="user">User</option>
        <option value="group">Group</option>
      </select>
      <select value={principalId} onChange={(e) => setPrincipalId(e.target.value)} style={{ fontSize: 12, padding: "3px 4px" }}>
        <option value="">Choose…</option>
        {principalType === "user"
          ? users.map((u) => <option key={u.id} value={u.username}>{u.display_name || u.username}</option>)
          : groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
      </select>
      <button type="button" className="btn-secondary" style={{ fontSize: 12 }} disabled={!principalId || busy} onClick={submit}>
        {busy ? "Reassigning…" : "Confirm"}
      </button>
      <button type="button" className="link-btn" style={{ fontSize: 12 }} onClick={() => setOpen(false)}>Cancel</button>
      {error && <span style={{ color: "var(--danger)", fontSize: 12 }}>{error}</span>}
    </div>
  );
}

export function AddDocumentControl({ instance, onDone }: { instance: WorkflowInstance; onDone: () => void }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const add = async (picked: PickedResource) => {
    setBusy(true);
    setError(null);
    try {
      await apiPost(`/workflows/instances/${instance.id}/resources`, {
        resource_id: picked.resourceId,
        resource_type: picked.resourceType,
      });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add that document.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button type="button" className="link-btn" style={{ fontSize: 12 }} disabled={busy} onClick={() => setPickerOpen(true)}>
        + Add document
      </button>
      {error && <span style={{ color: "var(--danger)", fontSize: 12, marginLeft: 6 }}>{error}</span>}
      {pickerOpen && <ResourcePickerDialog onClose={() => setPickerOpen(false)} onSelect={add} />}
    </>
  );
}

export function StatusBadge({ status }: { status: WorkflowInstance["status"] }) {
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

/** The audit trail behind every instance — who acted on which step, with
 * what comment, when. The backend has always returned this (step_actions),
 * but nothing rendered it: an instance's whole history was invisible once
 * you weren't the one currently holding the ball. */
export function StepTimeline({ instance }: { instance: WorkflowInstance }) {
  const [open, setOpen] = useState(false);
  if (instance.step_actions.length === 0) return null;
  const stepName = (idx: number) => instance.steps[idx]?.name ?? `Step ${idx + 1}`;
  return (
    <div style={{ marginTop: "var(--space-2)" }}>
      <button
        type="button"
        className="link-btn"
        style={{ fontSize: 12 }}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "Hide" : "Show"} history ({instance.step_actions.length})
      </button>
      {open && (
        <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 6 }}>
          {instance.step_actions.map((a) => (
            <div key={a.id} style={{ fontSize: "var(--text-sm)", borderLeft: `2px solid ${a.action === "approved" ? "var(--success)" : "var(--danger)"}`, paddingLeft: 8 }}>
              <div>
                <strong>{a.reviewer}</strong> {a.action === "approved" ? "approved" : "rejected"} · {stepName(a.step_index)}
                <span className="muted" style={{ marginLeft: 6 }}>{formatDate(a.acted_at)}</span>
              </div>
              {a.comment && <div className="muted" style={{ fontStyle: "italic" }}>"{a.comment}"</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Inbox
// ---------------------------------------------------------------------------

function InboxTab({
  instances,
  definitions,
  users,
  groups,
  isWorkflowAdmin,
  loading,
  onRefresh,
}: {
  instances: WorkflowInstance[];
  definitions: WorkflowDefinition[];
  users: User[];
  groups: Group[];
  isWorkflowAdmin: boolean;
  loading: boolean;
  onRefresh: () => void;
}) {
  const { user } = useAuth();
  const [actioning, setActioning] = useState<string | null>(null);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [flash, setFlash] = useState<string | null>(null);

  const defName = (id: string) => definitions.find((d) => d.id === id)?.name ?? id;

  // Instances where the current user is an assigned reviewer (directly or
  // via a group) and hasn't acted yet. Steps come from each instance's OWN
  // snapshot (inst.steps), not the shared definition — a reassigned step
  // only exists there.
  const pending = instances.filter((i) => {
    if (i.status !== "in_review") return false;
    const stepDef = i.steps[i.current_step];
    if (!stepDef) return false;
    const alreadyActed = i.step_actions.some(
      (a) => a.reviewer === user?.username && a.step_index === i.current_step,
    );
    // Empty assignees means the backend allows ANY authenticated user to
    // act on this step (see act_on_step's `if assignees and not any(...)`).
    const isReviewer = stepDef.assignees.length === 0 || isAssigned(user, stepDef.assignees);
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
        const stepDef = inst.steps[inst.current_step];
        return (
          <Card key={inst.id}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
              <ResourceList instance={inst} />
              <StatusBadge status={inst.status} />
            </div>
            <div style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", marginBottom: 4 }}>
              Workflow: <strong>{defName(inst.definition_id)}</strong>
              {stepDef && <> · Step {inst.current_step + 1} of {inst.steps.length}: <strong>{stepDef.name}</strong></>}
              {" · "}Requested by <strong>{inst.requested_by}</strong>
              {" · "}{formatDate(inst.created_at)}
            </div>
            {stepDef && stepDef.assignees.length > 1 && (
              <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 4 }}>
                Needs {stepDef.required_approvals} of {stepDef.assignees.length}: {stepDef.assignees.map((a) => assigneeLabel(a, users, groups)).join(", ")}
              </div>
            )}
            {inst.comment && (
              <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", margin: "4px 0 8px", fontStyle: "italic" }}>
                "{inst.comment}"
              </p>
            )}
            <StepTimeline instance={inst} />
            {canManageInstance(user, isWorkflowAdmin, inst) && (
              <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6 }}>
                <ReassignControl instance={inst} users={users} groups={groups} onDone={onRefresh} />
                <AddDocumentControl instance={inst} onDone={onRefresh} />
              </div>
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
  users,
  groups,
  isWorkflowAdmin,
  loading,
  onRefresh,
}: {
  instances: WorkflowInstance[];
  definitions: WorkflowDefinition[];
  users: User[];
  groups: Group[];
  isWorkflowAdmin: boolean;
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

      {mine.map((inst) => {
        const stepDef = inst.steps[inst.current_step];
        return (
        <Card key={inst.id}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
            <ResourceList instance={inst} />
            <StatusBadge status={inst.status} />
          </div>
          <div style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", marginBottom: 4 }}>
            Workflow: <strong>{defName(inst.definition_id)}</strong>
            {inst.status === "in_review" && stepDef && (
              <> · Awaiting step {inst.current_step + 1} of {inst.steps.length}: <strong>{stepDef.name}</strong>
              {stepDef.assignees.length > 0 ? ` (${stepDef.assignees.map((a) => assigneeLabel(a, users, groups)).join(", ")})` : " (any reviewer)"}</>
            )}
            {" · "}{formatDate(inst.created_at)}
            {inst.completed_at && <> · Completed {formatDate(inst.completed_at)}</>}
          </div>
          {inst.comment && (
            <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", margin: "4px 0", fontStyle: "italic" }}>
              "{inst.comment}"
            </p>
          )}
          <StepTimeline instance={inst} />
          {canManageInstance(user, isWorkflowAdmin, inst) && (
            <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6 }}>
              <ReassignControl instance={inst} users={users} groups={groups} onDone={onRefresh} />
              <AddDocumentControl instance={inst} onDone={onRefresh} />
            </div>
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
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Designer (admin only)
// ---------------------------------------------------------------------------

function DesignerTab({
  definitions,
  users,
  groups,
  loading,
  onRefresh,
}: {
  definitions: WorkflowDefinition[];
  users: User[];
  groups: Group[];
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

  // Assignees used to be a freeform comma-separated username field — a
  // single typo silently created a step nobody could ever act on (an
  // unrecognized username just never matches the current reviewer, so
  // that step — and the whole instance — would sit in_review forever).
  // Picking from real accounts/groups makes that class of mistake impossible.
  const activeUsers = users.filter((u) => u.is_active);

  const resetForm = () => {
    setName("");
    setDescription("");
    setSteps([]);
    setFormError(null);
  };

  const addStep = () =>
    setSteps((s) => [...s, { name: "", assignees: [], required_approvals: 1 }]);

  const updateStep = (i: number, patch: Partial<WorkflowStepDef>) =>
    setSteps((s) => s.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));

  const removeStep = (i: number) =>
    setSteps((s) => s.filter((_, idx) => idx !== i));

  const toggleAssignee = (i: number, type: "user" | "group", id: string, checked: boolean) => {
    setSteps((s) =>
      s.map((x, idx) => {
        if (idx !== i) return x;
        const assignees = checked
          ? [...x.assignees, { type, id }]
          : x.assignees.filter((a) => !(a.type === type && a.id === id));
        return { ...x, assignees };
      })
    );
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (steps.length === 0) { setFormError("Add at least one step."); return; }
    for (const s of steps) {
      if (!s.name.trim()) { setFormError("All steps need a name."); return; }
    }
    // No assignees is a legitimate, supported configuration (any
    // authenticated user may act) — but it's also exactly what an
    // accidentally-cleared checklist looks like, so confirm rather than
    // silently accepting it or hard-blocking a deliberate open step.
    const openSteps = steps.filter((s) => s.assignees.length === 0);
    if (openSteps.length > 0) {
      const names = openSteps.map((s) => `"${s.name}"`).join(", ");
      if (!window.confirm(`${names} ${openSteps.length === 1 ? "has" : "have"} no specific reviewers — any authenticated user will be able to act on it. Continue?`)) {
        return;
      }
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
                <div style={{ display: "grid", gridTemplateColumns: "2fr auto", gap: 8, alignItems: "end", marginBottom: 10 }}>
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
                    <span style={{ fontSize: 11, fontWeight: 500, color: "var(--text-secondary)", display: "block", marginBottom: 3 }}>
                      Required approvals
                    </span>
                    <input
                      type="number"
                      min={1}
                      value={step.required_approvals}
                      onChange={(e) => updateStep(i, { required_approvals: Math.max(1, Number(e.target.value)) })}
                      style={{ width: 90, padding: "6px 8px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)", fontSize: "var(--text-sm)", boxSizing: "border-box" }}
                    />
                  </label>
                </div>

                <div style={{ marginBottom: 10 }}>
                  <span style={{ fontSize: 11, fontWeight: 500, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                    Users
                  </span>
                  {activeUsers.length === 0 ? (
                    <p style={{ fontSize: 12, color: "var(--text-tertiary)", margin: 0 }}>No active users to choose from.</p>
                  ) : (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px" }}>
                      {activeUsers.map((u) => (
                        <label key={u.id} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: "var(--text-sm)" }}>
                          <input
                            type="checkbox"
                            style={{ width: "auto" }}
                            checked={step.assignees.some((a) => a.type === "user" && a.id === u.username)}
                            onChange={(e) => toggleAssignee(i, "user", u.username, e.target.checked)}
                          />
                          {u.display_name || u.username}
                        </label>
                      ))}
                    </div>
                  )}
                </div>

                <div>
                  <span style={{ fontSize: 11, fontWeight: 500, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                    Groups
                  </span>
                  {groups.length === 0 ? (
                    <p style={{ fontSize: 12, color: "var(--text-tertiary)", margin: 0 }}>No groups to choose from.</p>
                  ) : (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px" }}>
                      {groups.map((g) => (
                        <label key={g.id} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: "var(--text-sm)" }}>
                          <input
                            type="checkbox"
                            style={{ width: "auto" }}
                            checked={step.assignees.some((a) => a.type === "group" && a.id === g.id)}
                            onChange={(e) => toggleAssignee(i, "group", g.id, e.target.checked)}
                          />
                          {g.name}
                        </label>
                      ))}
                    </div>
                  )}
                  {step.assignees.length === 0 && (
                    <p style={{ fontSize: 12, color: "var(--warning)", margin: "6px 0 0" }}>
                      No one selected — any authenticated user will be able to act on this step.
                    </p>
                  )}
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
                    · {step.assignees.length > 0
                      ? step.assignees.map((a) => assigneeLabel(a, users, groups)).join(", ")
                      : "any reviewer"}
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
  const { can } = useAuth();
  const isAdmin = can("manage_workflow_definitions");

  const [tab, setTab] = useState<Tab>("inbox");
  const [instances, setInstances] = useState<WorkflowInstance[]>([]);
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
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

  // Best-effort — GET /users and /groups both require an admin-ish
  // feature (manage_users / manage_groups). An ordinary reviewer without
  // either just gets an empty list here and assigneeLabel() falls back to
  // showing the raw username/id, which is still correct, just less pretty.
  useEffect(() => {
    apiGet<User[]>("/users").then(setUsers).catch(() => {});
    apiGet<Group[]>("/groups").then(setGroups).catch(() => {});
  }, []);

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
        <InboxTab
          instances={instances} definitions={definitions} users={users} groups={groups}
          isWorkflowAdmin={isAdmin} loading={loading} onRefresh={load}
        />
      )}
      {tab === "requests" && (
        <MyRequestsTab
          instances={instances} definitions={definitions} users={users} groups={groups}
          isWorkflowAdmin={isAdmin} loading={loading} onRefresh={load}
        />
      )}
      {tab === "designer" && isAdmin && (
        <DesignerTab definitions={definitions} users={users} groups={groups} loading={loading} onRefresh={load} />
      )}
    </div>
  );
}
