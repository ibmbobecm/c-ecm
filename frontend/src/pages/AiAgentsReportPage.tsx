import { useEffect, useState } from "react";
import { apiGet } from "../api/client";
import type { AiAgentLead, AiAgentStats } from "../types";
import { useConnections } from "../contexts/ConnectionsContext";
import { formatDate } from "../utils";
import { openTestSite } from "../components/AiAgentDialog";
import { Modal } from "../components/Modal";

function AgentLeadsModal({ agent, onClose }: { agent: AiAgentStats; onClose: () => void }) {
  const [leads, setLeads] = useState<AiAgentLead[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<AiAgentLead[]>(`/ai-agents/${agent.id}/leads`)
      .then(setLeads)
      .catch((err) => setError(err instanceof Error ? err.message : "Couldn't load leads."));
  }, [agent.id]);

  return (
    <Modal title={`Leads — "${agent.name}"`} onClose={onClose} width={640}>
      {error && <div className="auth-error">{error}</div>}
      {leads && leads.length === 0 && (
        <p className="muted">No one has submitted their details through this agent's "Contact us" chat flow yet.</p>
      )}
      {leads && leads.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="audit-table">
            <thead>
              <tr>
                <th>Submitted</th>
                <th>Email</th>
                <th>Phone</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((l) => (
                <tr key={l.id}>
                  <td style={{ whiteSpace: "nowrap" }}>{formatDate(l.created_at)}</td>
                  <td>{l.email ?? "—"}</td>
                  <td>{l.phone ?? "—"}</td>
                  <td>{l.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}

export function AiAgentsReportPage() {
  const { connections } = useConnections();
  const [agents, setAgents] = useState<AiAgentStats[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [leadsAgent, setLeadsAgent] = useState<AiAgentStats | null>(null);

  useEffect(() => {
    apiGet<AiAgentStats[]>("/admin/ai-agents/report")
      .then(setAgents)
      .catch((err) => setError(err instanceof Error ? err.message : "Couldn't load AI agents."));
  }, []);

  const connectionName = (id: string) => connections.find((c) => c.id === id)?.display_name ?? id;

  const totalChats = agents?.reduce((sum, a) => sum + a.chat_count, 0) ?? 0;
  const totalTokens = agents?.reduce((sum, a) => sum + a.tokens_total, 0) ?? 0;
  const activeCount = agents?.filter((a) => a.is_active).length ?? 0;

  return (
    <div className="audit-page">
      <div className="audit-topbar">
        <h2 className="audit-title">AI Agents</h2>
      </div>

      <div className="audit-content" style={{ maxWidth: "none" }}>
        {error && <div className="auth-error">{error}</div>}

        {agents && (
          <div className="audit-summary-cards">
            <div className="audit-card">
              <div className="audit-card-value">{agents.length}</div>
              <div className="audit-card-label">Agents created ({activeCount} active)</div>
            </div>
            <div className="audit-card">
              <div className="audit-card-value">{totalChats.toLocaleString()}</div>
              <div className="audit-card-label">Total chats</div>
            </div>
            <div className="audit-card">
              <div className="audit-card-value">{totalTokens.toLocaleString()}</div>
              <div className="audit-card-label">Total tokens used</div>
            </div>
          </div>
        )}

        {agents && agents.length === 0 && (
          <p className="muted" style={{ marginTop: 24 }}>
            No AI Agents have been created yet. Right-click any folder or file in the Drive to create one.
          </p>
        )}

        {agents && agents.length > 0 && (
          <div style={{ overflowX: "auto", marginTop: 16 }}>
            <table className="audit-table" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Owner</th>
                  <th>Scope</th>
                  <th>Connection</th>
                  <th>Status</th>
                  <th>Chats</th>
                  <th>Tokens</th>
                  <th>Last used</th>
                  <th>Leads</th>
                  <th>Test site</th>
                </tr>
              </thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{a.name}</div>
                      {a.description && <div className="muted" style={{ fontSize: 12 }}>{a.description}</div>}
                    </td>
                    <td>{a.owner}</td>
                    <td>
                      {a.scope_type === "folder" ? "Folder" : "File"}: {a.resource_name}
                    </td>
                    <td>{connectionName(a.connection_id)}</td>
                    <td>{a.is_active ? "Active" : "Deactivated"}</td>
                    <td>{a.chat_count.toLocaleString()}</td>
                    <td>{a.tokens_total.toLocaleString()}</td>
                    <td>{a.last_chat_at ? formatDate(a.last_chat_at) : "Never"}</td>
                    <td>
                      {a.lead_count > 0 ? (
                        <button className="link-btn" onClick={() => setLeadsAgent(a)}>
                          View ({a.lead_count.toLocaleString()})
                        </button>
                      ) : (
                        "0"
                      )}
                    </td>
                    <td>
                      <button className="link-btn" onClick={() => openTestSite(a)}>Open</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {leadsAgent && <AgentLeadsModal agent={leadsAgent} onClose={() => setLeadsAgent(null)} />}
    </div>
  );
}
