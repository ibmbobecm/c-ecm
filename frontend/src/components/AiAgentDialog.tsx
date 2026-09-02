import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPatch, apiPost } from "../api/client";
import type { AiAgentStats, DriveItem } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";
import { formatDate } from "../utils";

function embedSnippet(agent: AiAgentStats): string {
  return `<iframe src="${agent.chat_url}" style="width:100%;max-width:420px;height:560px;border:1px solid #d0d7de;border-radius:12px;" title="${agent.name}"></iframe>`;
}

/** Fills the freshly-opened blank tab with an immediate loading message —
 * the first-ever open of a test site can take a while server-side (it
 * auto-generates the whole site from the knowledge base right then), and
 * that wait would otherwise show as a stuck-blank "about:blank" tab with
 * no sign anything is happening. Built with DOM APIs rather than an HTML
 * string so the agent's own (user-chosen) name can never be interpreted
 * as markup. This content is simply replaced the moment win.location.href
 * navigates the tab to the real site below. */
function showLoadingPage(win: Window, agentName: string) {
  win.document.title = "Preparing your test site…";
  const style = win.document.createElement("style");
  style.textContent = `
    body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
           font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f8fa; color: #24292f; }
    .cecm-loading { text-align: center; max-width: 360px; padding: 24px; }
    .cecm-spinner { width: 40px; height: 40px; margin: 0 auto 20px; border: 4px solid #d0d7de;
                    border-top-color: #0969da; border-radius: 50%; animation: cecm-spin 0.8s linear infinite; }
    @keyframes cecm-spin { to { transform: rotate(360deg); } }
    .cecm-loading h1 { font-size: 16px; margin: 0 0 8px; }
    .cecm-loading p { font-size: 13px; color: #57606a; margin: 0; line-height: 1.5; }
  `;
  win.document.head.appendChild(style);

  const box = win.document.createElement("div");
  box.className = "cecm-loading";
  const spinner = win.document.createElement("div");
  spinner.className = "cecm-spinner";
  const heading = win.document.createElement("h1");
  heading.textContent = `Creating "${agentName}"'s website…`;
  const message = win.document.createElement("p");
  message.textContent = "Analyzing the knowledge base and generating content — this can take up to a minute the first time.";
  box.append(spinner, heading, message);
  win.document.body.appendChild(box);
}

/** Opens the live test site in a new tab — everything an admin can do
 * (Customize, Manage pages, Manage blog, Generate with AI, Download) lives
 * on that page itself, via its own admin bar, not in this app. */
export async function openTestSite(agent: AiAgentStats) {
  // Open the tab synchronously (before the await) so browsers don't treat
  // it as a popup blocked for happening outside a direct user gesture —
  // it starts blank and gets pointed at the real URL once the edit-token
  // request resolves (falling back to the plain, non-editable URL if that
  // fails, so a broken request never leaves the user with a dead tab).
  // Deliberately no "noopener" here: passing it makes window.open() return
  // null in Chrome/Firefox (the whole point of noopener is that the caller
  // gives up its handle to the new window), which silently defeated both
  // "if (win)" navigations below and left the tab stuck on about:blank.
  const win = window.open("", "_blank");
  if (win) showLoadingPage(win, agent.name);
  try {
    const { edit_token } = await apiPost<{ edit_token: string; expires_at: string }>(`/ai-agents/${agent.id}/edit-token`);
    if (win) win.location.href = `${agent.demo_url}?edit_token=${encodeURIComponent(edit_token)}`;
  } catch {
    if (win) win.location.href = agent.demo_url;
  }
}

function AgentCard({ agent, onChanged }: { agent: AiAgentStats; onChanged: () => void }) {
  const [copied, setCopied] = useState<"url" | "embed" | null>(null);
  const [busy, setBusy] = useState(false);

  const copy = async (text: string, which: "url" | "embed") => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(which);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // clipboard access denied — the text is still visible to select/copy manually
    }
  };

  const toggleActive = async () => {
    setBusy(true);
    try {
      await apiPatch(`/ai-agents/${agent.id}`, { is_active: !agent.is_active });
      onChanged();
    } catch {
      // surfaced via the list simply not updating; user can retry
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await apiDelete(`/ai-agents/${agent.id}`);
      onChanged();
    } catch {
      setBusy(false);
    }
  };

  return (
    <div className="ai-agent-card">
      <div className="ai-agent-card-header">
        <Icon name="bot" size={18} />
        <div className="ai-agent-card-title">
          <div className="ai-agent-card-name">{agent.name}</div>
          {agent.description && <div className="ai-agent-card-desc muted">{agent.description}</div>}
        </div>
        {!agent.is_active && <span className="ai-agent-badge-inactive">Deactivated</span>}
      </div>

      <div className="ai-agent-card-stats muted">
        {agent.chat_count} chat{agent.chat_count !== 1 ? "s" : ""}
        {" · "}
        {agent.tokens_total.toLocaleString()} tokens
        {agent.last_chat_at ? ` · last used ${formatDate(agent.last_chat_at)}` : " · never used"}
      </div>

      <div className="ai-agent-card-field">
        <span className="ai-agent-card-field-label">Public URL</span>
        <div className="ai-agent-card-field-row">
          <span className="ai-agent-card-url" title={agent.chat_url}>{agent.chat_url}</span>
          <button className="link-btn" onClick={() => copy(agent.chat_url, "url")}>
            {copied === "url" ? "Copied!" : "Copy"}
          </button>
        </div>
      </div>

      <div className="ai-agent-card-field">
        <span className="ai-agent-card-field-label">Embed on your site</span>
        <div className="ai-agent-card-field-row">
          <span className="ai-agent-card-url">{embedSnippet(agent)}</span>
          <button className="link-btn" onClick={() => copy(embedSnippet(agent), "embed")}>
            {copied === "embed" ? "Copied!" : "Copy"}
          </button>
        </div>
      </div>

      <p className="muted" style={{ fontSize: 12, margin: "2px 0 0" }}>
        Customizing the site, managing pages/blog, generating content with AI, and downloading the site all happen on the live test site itself — open it below.
      </p>

      <div className="ai-agent-card-actions">
        <button className="link-btn" onClick={() => openTestSite(agent)}>Open test site</button>
        <button className="link-btn" onClick={toggleActive} disabled={busy}>
          {agent.is_active ? "Deactivate" : "Reactivate"}
        </button>
        <button className="link-btn" onClick={remove} disabled={busy} style={{ color: "var(--danger, #e53e3e)" }}>
          Delete
        </button>
      </div>
    </div>
  );
}

export function AiAgentDialogContent({ item }: { item: DriveItem }) {
  const [agents, setAgents] = useState<AiAgentStats[]>([]);
  const [name, setName] = useState(`${item.name} Assistant`);
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    apiGet<AiAgentStats[]>("/ai-agents", { resource_id: item.id })
      .then(setAgents)
      .catch(() => {});
  };

  useEffect(load, [item.id]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await apiPost("/ai-agents", {
        name: name.trim(),
        description: description.trim(),
        scope_type: item.type,
        resource_id: item.id,
        resource_name: item.name,
      });
      setDescription("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create an AI agent.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        {item.type === "folder"
          ? "Everyone who asks this agent a question gets an answer grounded in every file under this folder — automatically kept in sync as files change."
          : "Everyone who asks this agent a question gets an answer grounded in this file — automatically kept in sync as it's updated."}
      </p>

      {agents.length > 0 && (
        <div className="ai-agent-list">
          {agents.map((a) => (
            <AgentCard key={a.id} agent={a} onChanged={load} />
          ))}
        </div>
      )}

      <form onSubmit={create} className="auth-form" style={{ marginTop: agents.length > 0 ? 16 : 0 }}>
        <label>
          Agent name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required />
        </label>
        <label>
          Description (optional)
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} placeholder="What should people ask this agent about?" />
        </label>
        {error && <div className="auth-error">{error}</div>}
        <button type="submit" disabled={busy}>
          {busy ? "Creating…" : "Create AI Agent"}
        </button>
      </form>
    </>
  );
}

export function AiAgentDialog({ item, onClose }: { item: DriveItem; onClose: () => void }) {
  return (
    <Modal title={`AI Agent — "${item.name}"`} onClose={onClose} width={520}>
      <AiAgentDialogContent item={item} />
    </Modal>
  );
}
