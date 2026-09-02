/**
 * FileApprovalsPanel — the approval history and live actions for ONE
 * resource, embedded in the document viewer's sidebar. WorkflowsPanel's
 * Inbox/My Requests tabs already show this same data, but only in a
 * cross-file list — there was previously no way to see "does this specific
 * file have an approval on it, and can I act on it?" without leaving the
 * viewer and hunting through the Approvals hub.
 */
import { useEffect, useState } from "react";
import { apiGet, apiPost, ApiError } from "../api/client";
import type { WorkflowDefinition, WorkflowInstance } from "../types";
import { useAuth } from "../contexts/AuthContext";
import { StatusBadge, StepTimeline } from "./WorkflowsPanel";
import { Icon } from "../icons";
import { formatDate } from "../utils";

export function FileApprovalsPanel({
  resourceId,
  definitions,
  onChanged = () => {},
}: {
  resourceId: string;
  definitions: WorkflowDefinition[];
  onChanged?: () => void;
}) {
  const { user } = useAuth();
  // Matches the backend's cancel_instance check exactly: the requester or
  // a superadmin can cancel — not a delegable feature, since it's
  // specifically "override anyone's request," the same bypass concept as
  // every other superadmin-only override in this app.
  const isAdmin = Boolean(user?.is_superadmin);
  const [instances, setInstances] = useState<WorkflowInstance[]>([]);
  const [loading, setLoading] = useState(true);
  const [actioning, setActioning] = useState<string | null>(null);
  const [comment, setComment] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    apiGet<WorkflowInstance[]>("/workflows/instances", { resource_id: resourceId })
      .then(setInstances)
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, [resourceId]);

  const act = async (instanceId: string, action: "approved" | "rejected") => {
    setActioning(instanceId);
    setError(null);
    try {
      await apiPost(`/workflows/instances/${instanceId}/action`, { action, comment: comment[instanceId] ?? "" });
      load();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed. Please try again.");
    } finally {
      setActioning(null);
    }
  };

  const cancel = async (instanceId: string) => {
    if (!window.confirm("Cancel this approval request?")) return;
    setActioning(instanceId);
    setError(null);
    try {
      await apiPost(`/workflows/instances/${instanceId}/cancel`, {});
      load();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't cancel this request.");
    } finally {
      setActioning(null);
    }
  };

  if (loading) return <p className="muted" style={{ fontSize: "var(--text-sm)" }}>Loading…</p>;

  if (instances.length === 0) {
    return (
      <p className="muted" style={{ fontSize: "var(--text-sm)" }}>
        No approval requests for this file yet — use "Request Approval" above to start one.
      </p>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {error && <div className="auth-error" style={{ fontSize: "var(--text-sm)" }}>{error}</div>}
      {instances.map((inst) => {
        const def = definitions.find((d) => d.id === inst.definition_id);
        const stepDef = def?.steps[inst.current_step];
        const alreadyActed = inst.step_actions.some(
          (a) => a.reviewer === user?.username && a.step_index === inst.current_step
        );
        const isReviewer =
          inst.status === "in_review" &&
          !!stepDef &&
          (stepDef.reviewers.length === 0 || stepDef.reviewers.includes(user?.username ?? "")) &&
          !alreadyActed;
        const isRequester = inst.requested_by === user?.username;

        return (
          <div key={inst.id} style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
              <strong style={{ fontSize: "var(--text-sm)" }}>{def?.name ?? inst.definition_id}</strong>
              <StatusBadge status={inst.status} />
            </div>
            {inst.status === "in_review" && def && stepDef && (
              <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                Step {inst.current_step + 1} of {def.steps.length}: {stepDef.name}
                {stepDef.reviewers.length > 0 ? ` (${stepDef.reviewers.join(", ")})` : " (any reviewer)"}
              </div>
            )}
            <div className="muted" style={{ fontSize: 12 }}>
              Requested by {inst.requested_by} · {formatDate(inst.created_at)}
              {inst.completed_at && <> · Completed {formatDate(inst.completed_at)}</>}
            </div>
            {inst.comment && (
              <p style={{ fontSize: "var(--text-sm)", fontStyle: "italic", margin: "4px 0" }}>"{inst.comment}"</p>
            )}
            <StepTimeline instance={inst} definition={def} />
            {isReviewer && (
              <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                <input
                  placeholder="Comment (optional)"
                  value={comment[inst.id] ?? ""}
                  onChange={(e) => setComment((prev) => ({ ...prev, [inst.id]: e.target.value }))}
                  style={{
                    flex: 1, minWidth: 120, padding: "5px 8px", borderRadius: "var(--radius-sm)",
                    border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", fontSize: "var(--text-sm)",
                  }}
                />
                <button
                  className="btn-primary"
                  style={{ background: "var(--success)", fontSize: 12, padding: "5px 10px", display: "flex", alignItems: "center", gap: 4 }}
                  disabled={actioning === inst.id}
                  onClick={() => act(inst.id, "approved")}
                >
                  <Icon name="check" size={13} /> Approve
                </button>
                <button
                  className="btn-primary"
                  style={{ background: "var(--danger)", fontSize: 12, padding: "5px 10px", display: "flex", alignItems: "center", gap: 4 }}
                  disabled={actioning === inst.id}
                  onClick={() => act(inst.id, "rejected")}
                >
                  <Icon name="close" size={13} /> Reject
                </button>
              </div>
            )}
            {inst.status === "in_review" && (isRequester || isAdmin) && (
              <button
                className="btn-secondary"
                style={{ fontSize: 12, marginTop: 8 }}
                disabled={actioning === inst.id}
                onClick={() => cancel(inst.id)}
              >
                {actioning === inst.id ? "Cancelling…" : "Cancel request"}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
