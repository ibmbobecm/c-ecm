/**
 * WebhookManagementPanel — admin UI to create, view, toggle, and delete
 * outbound webhook registrations.
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPatch, apiPost, ApiError } from "../api/client";
import type { Webhook } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";

const ALL_EVENT_TYPES = [
  "created", "renamed", "moved", "deleted", "permanently_deleted", "restored",
  "version_created", "version_restored", "checked_out", "checked_in",
  "workflow_started", "workflow_approved", "workflow_rejected", "workflow_cancelled",
  "comment_added", "tag_added",
];

function StatusDot({ active, lastCode }: { active: boolean; lastCode: number | null }) {
  const colour = !active ? "#57606a" : lastCode == null ? "#57606a" : lastCode < 300 ? "#22a06b" : "#e53e3e";
  const label = !active ? "Inactive" : lastCode == null ? "Never triggered" : `Last: HTTP ${lastCode}`;
  return (
    <span title={label} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: colour }}>
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: colour, display: "inline-block" }} />
      {label}
    </span>
  );
}

export function WebhookManagementPanel({ onClose }: { onClose: () => void }) {
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [newUrl, setNewUrl] = useState("");
  const [newSecret, setNewSecret] = useState("");
  const [newEvents, setNewEvents] = useState<string[]>(["created", "deleted"]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    apiGet<Webhook[]>("/webhooks")
      .then(setWebhooks)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load webhooks."))
      .finally(() => setLoading(false));
  };
  useEffect(reload, []);

  const toggleEvent = (evt: string) =>
    setNewEvents((prev) => (prev.includes(evt) ? prev.filter((x) => x !== evt) : [...prev, evt]));

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      await apiPost("/webhooks", { url: newUrl, secret: newSecret, event_types: newEvents });
      setShowCreate(false);
      setNewUrl("");
      setNewSecret("");
      setNewEvents(["created", "deleted"]);
      reload();
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "Couldn't create webhook.");
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (wh: Webhook) => {
    try {
      await apiPatch(`/webhooks/${wh.id}`, { active: !wh.active });
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't update webhook.");
    }
  };

  const handleDelete = async (wh: Webhook) => {
    if (!window.confirm(`Delete webhook for "${wh.url}"?`)) return;
    try {
      await apiDelete(`/webhooks/${wh.id}`);
      reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't delete webhook.");
    }
  };

  return (
    <Modal title="Webhook Management" onClose={onClose} width={600}>
      <p className="muted" style={{ fontSize: 13, marginBottom: 16 }}>
        Outbound webhooks fire an HTTP POST to your endpoint when selected events occur.
        The payload is signed with HMAC-SHA256 using your secret.
      </p>

      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}>
        <button className="btn-primary" style={{ fontSize: 13 }} onClick={() => setShowCreate((s) => !s)}>
          <Icon name="plus" size={13} /> Add webhook
        </button>
      </div>

      {showCreate && (
        <form className="auth-form" onSubmit={handleCreate} style={{ background: "var(--surface)", padding: 16, borderRadius: 8, marginBottom: 16, border: "1px solid var(--border)" }}>
          <h4 style={{ margin: "0 0 12px" }}>New webhook</h4>
          <label>Endpoint URL <input required type="url" value={newUrl} onChange={(e) => setNewUrl(e.target.value)} placeholder="https://your-server.com/hook" /></label>
          <label>
            Secret (min. 8 characters — every payload is HMAC-signed with it, so receivers can verify it's genuine)
            <input required minLength={8} value={newSecret} onChange={(e) => setNewSecret(e.target.value)} placeholder="A shared signing secret" />
          </label>
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Events to subscribe</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px" }}>
              {ALL_EVENT_TYPES.map((evt) => (
                <label key={evt} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, cursor: "pointer" }}>
                  <input type="checkbox" checked={newEvents.includes(evt)} onChange={() => toggleEvent(evt)} />
                  {evt}
                </label>
              ))}
            </div>
          </div>
          {formError && <div className="auth-error">{formError}</div>}
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" disabled={busy}>{busy ? "Adding…" : "Add"}</button>
            <button type="button" className="btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
        </form>
      )}

      {error && <div className="auth-error" style={{ marginBottom: 12 }}>{error}</div>}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : webhooks.length === 0 ? (
        <p className="muted" style={{ textAlign: "center", padding: 32 }}>No webhooks configured yet.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {webhooks.map((wh) => (
            <div key={wh.id} style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: 12, opacity: wh.active ? 1 : 0.65 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: 13, wordBreak: "break-all" }}>{wh.url}</div>
                  <div style={{ marginTop: 4 }}>
                    <StatusDot active={wh.active} lastCode={wh.last_status_code} />
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, flexShrink: 0, marginLeft: 8 }}>
                  <button
                    className="icon-btn"
                    title={wh.active ? "Disable" : "Enable"}
                    onClick={() => toggleActive(wh)}
                  >
                    <Icon name={wh.active ? "eye-off" : "eye"} size={15} />
                  </button>
                  <button
                    className="icon-btn"
                    title="Delete"
                    style={{ color: "var(--danger, #e53e3e)" }}
                    onClick={() => handleDelete(wh)}
                  >
                    <Icon name="trash" size={15} />
                  </button>
                </div>
              </div>
              <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: "4px 8px" }}>
                {wh.event_types.map((evt) => (
                  <span key={evt} style={{ fontSize: 11, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4, padding: "1px 6px" }}>{evt}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
