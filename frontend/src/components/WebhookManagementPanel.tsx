/**
 * WebhookManagementPanel — admin UI to create, view, toggle, and delete
 * outbound webhook registrations.
 */
import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPatch, apiPost, ApiError } from "../api/client";
import type { Webhook } from "../types";
import { Icon, SlackIcon, DiscordIcon } from "../icons";
import { ResourcePickerDialog, type PickedResource } from "./ResourcePickerDialog";
import { WebhookSetupHelp } from "./WebhookSetupHelp";

type DestinationType = "custom" | "slack" | "discord";

const DESTINATIONS: { key: DestinationType; label: string; urlLabel: string; urlPlaceholder: string; urlHint: string }[] = [
  { key: "custom", label: "Custom URL", urlLabel: "Endpoint URL", urlPlaceholder: "https://your-server.com/hook", urlHint: "Every payload is HMAC-signed with your secret so receivers can verify it's genuine." },
  { key: "slack", label: "Slack", urlLabel: "Slack webhook URL", urlPlaceholder: "https://hooks.slack.com/services/...", urlHint: "A Slack incoming-webhook URL — from your Slack app's \"Incoming Webhooks\" page." },
  { key: "discord", label: "Discord", urlLabel: "Discord webhook URL", urlPlaceholder: "https://discord.com/api/webhooks/...", urlHint: "A Discord channel webhook URL — from that channel's Integrations settings." },
];

const ALL_EVENT_TYPES = [
  "login", "login_failed", "logout", "viewed",
  "created", "renamed", "moved", "deleted", "permanently_deleted", "restored",
  "version_created", "version_restored", "checked_out", "checked_in",
  "workflow_started", "workflow_step_voted", "workflow_step_advanced", "workflow_approved", "workflow_rejected", "workflow_cancelled",
  // These two were previously named "comment_added"/"tag_added" here, which
  // don't match what comments.py/tags.py actually emit ("commented"/
  // "tagged") — a webhook subscribed to either literally never fired.
  "commented", "tagged",
];

// The raw event_type strings are the wire format (matched against what
// activity_service actually emits) -- only the display should ever be
// title-cased, never the value itself.
function humanizeEventType(evt: string): string {
  return evt.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

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

export function WebhookManagementPanel() {
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [destination, setDestination] = useState<DestinationType>("custom");
  const [newUrl, setNewUrl] = useState("");
  const [newSecret, setNewSecret] = useState("");
  const [newEvents, setNewEvents] = useState<string[]>(["created", "deleted"]);
  const [scope, setScope] = useState<PickedResource | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
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
    if (!scope) {
      setFormError("Choose a file or folder to scope this webhook to.");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      await apiPost("/webhooks", {
        url: newUrl,
        destination_type: destination,
        secret: destination === "custom" ? newSecret : null,
        event_types: newEvents,
        connection_id: scope.connectionId,
        resource_id: scope.resourceId,
        resource_type: scope.resourceType,
        resource_name: scope.resourceName,
      });
      setShowCreate(false);
      setDestination("custom");
      setNewUrl("");
      setNewSecret("");
      setNewEvents(["created", "deleted"]);
      setScope(null);
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
    <div className="settings-tab-pane">
      <p className="muted" style={{ fontSize: 13, marginBottom: 16 }}>
        Outbound webhooks fire when selected events occur on the file or folder you scope them to — send a signed
        HTTP POST to your own endpoint, or post a message straight to a Slack or Discord channel.
      </p>

      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}>
        <button className="btn-primary" style={{ fontSize: 13 }} onClick={() => setShowCreate((s) => !s)}>
          <Icon name="plus" size={13} /> Add webhook
        </button>
      </div>

      {showCreate && (
        <form className="auth-form" onSubmit={handleCreate} style={{ background: "var(--surface)", padding: 16, borderRadius: 8, marginBottom: 16, border: "1px solid var(--border)" }}>
          <h4 style={{ margin: "0 0 12px" }}>New webhook</h4>
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Destination</div>
            <div style={{ display: "flex", gap: 8 }}>
              {DESTINATIONS.map((d) => (
                <button
                  key={d.key}
                  type="button"
                  className={destination === d.key ? "btn-primary" : "btn-secondary"}
                  style={{ fontSize: 12.5, display: "flex", alignItems: "center", gap: 6 }}
                  onClick={() => setDestination(d.key)}
                >
                  {d.key === "custom" ? <Icon name="link" size={14} /> : d.key === "slack" ? <SlackIcon size={16} /> : <DiscordIcon size={16} />}
                  {d.label}
                </button>
              ))}
            </div>
          </div>
          <label>
            <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {DESTINATIONS.find((d) => d.key === destination)?.urlLabel}
              {destination !== "custom" && (
                <button
                  type="button"
                  className="icon-btn"
                  style={{ padding: 2 }}
                  onClick={() => setHelpOpen(true)}
                  aria-label={`How do I get a ${destination === "slack" ? "Slack" : "Discord"} webhook URL?`}
                  title={`How do I get a ${destination === "slack" ? "Slack" : "Discord"} webhook URL?`}
                >
                  <Icon name="info" size={14} />
                </button>
              )}
            </span>
            <input required type="url" value={newUrl} onChange={(e) => setNewUrl(e.target.value)} placeholder={DESTINATIONS.find((d) => d.key === destination)?.urlPlaceholder} />
            <span className="muted" style={{ fontSize: 11 }}>{DESTINATIONS.find((d) => d.key === destination)?.urlHint}</span>
          </label>
          {destination === "custom" && (
            <label>
              Secret (min. 8 characters — every payload is HMAC-signed with it, so receivers can verify it's genuine)
              <input required minLength={8} value={newSecret} onChange={(e) => setNewSecret(e.target.value)} placeholder="A shared signing secret" />
            </label>
          )}
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Events to subscribe</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "8px 16px" }}>
              {ALL_EVENT_TYPES.map((evt) => (
                <label
                  key={evt}
                  style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 400, color: "var(--text)", cursor: "pointer" }}
                >
                  <input type="checkbox" checked={newEvents.includes(evt)} onChange={() => toggleEvent(evt)} />
                  {humanizeEventType(evt)}
                </label>
              ))}
            </div>
          </div>
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Scope to a file or folder</div>
            {scope ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                <Icon name={scope.resourceType === "folder" ? "folder" : "file-generic"} size={15} />
                <span>
                  {scope.connectionName} / {scope.resourceName}
                </span>
                <button type="button" className="btn-secondary" style={{ fontSize: 11.5 }} onClick={() => setScope(null)}>
                  Change
                </button>
              </div>
            ) : (
              <>
                <button type="button" className="btn-secondary" style={{ fontSize: 12 }} onClick={() => setPickerOpen(true)}>
                  Choose a file or folder...
                </button>
                <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                  Required — fires only for this file/folder, not every event across every connection.
                </div>
              </>
            )}
          </div>
          {formError && <div className="auth-error">{formError}</div>}
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" disabled={busy || !scope} title={scope ? undefined : "Choose a file or folder first"}>
              {busy ? "Adding…" : "Add"}
            </button>
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
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    {wh.destination_type === "slack" && <SlackIcon size={16} />}
                    {wh.destination_type === "discord" && <DiscordIcon size={16} />}
                    <div style={{ fontWeight: 500, fontSize: 13, wordBreak: "break-all" }}>{wh.url}</div>
                  </div>
                  <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <StatusDot active={wh.active} lastCode={wh.last_status_code} />
                    {wh.resource_name && (
                      <span className="muted" style={{ fontSize: 11.5, display: "inline-flex", alignItems: "center", gap: 4 }}>
                        <Icon name={wh.resource_type === "folder" ? "folder" : "file-generic"} size={12} />
                        {wh.resource_name}
                      </span>
                    )}
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
                  <span key={evt} style={{ fontSize: 11, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4, padding: "1px 6px" }}>{humanizeEventType(evt)}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {pickerOpen && (
        <ResourcePickerDialog
          onClose={() => setPickerOpen(false)}
          onSelect={(picked) => setScope(picked)}
        />
      )}

      {helpOpen && (
        <WebhookSetupHelp
          initialPlatform={destination === "discord" ? "discord" : "slack"}
          onClose={() => setHelpOpen(false)}
        />
      )}
    </div>
  );
}
