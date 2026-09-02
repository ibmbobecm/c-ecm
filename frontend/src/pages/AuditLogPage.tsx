import { useEffect, useState } from "react";
import { API_BASE, apiGet, getAuthToken } from "../api/client";
import type { ActivityEvent, ActivitySummary } from "../types";
import { useConnections } from "../contexts/ConnectionsContext";
import { Icon } from "../icons";
import { formatDate } from "../utils";
import { BarChart, DonutChart } from "../components/MiniCharts";

const EVENT_LABELS: Record<string, string> = {
  login: "User login",
  login_failed: "Failed login",
  logout: "User logout",
  viewed: "Viewed document",
  created: "Created",
  renamed: "Renamed",
  moved: "Moved",
  deleted: "Deleted",
  permanently_deleted: "Permanently deleted",
  restored: "Restored",
  version_created: "New version",
  version_restored: "Version restored",
  checked_out: "Checked out",
  checked_in: "Checked in",
  workflow_started: "Approval requested",
  workflow_step_voted: "Approval step voted",
  workflow_step_advanced: "Approval step advanced",
  workflow_approved: "Approval completed",
  workflow_rejected: "Approval rejected",
  workflow_cancelled: "Approval cancelled",
  commented: "Comment added",
  tagged: "Tag added",
  share_link_created: "Share link created",
  legal_hold_set: "Legal hold set",
  legal_hold_released: "Legal hold released",
  ai_agent_created: "AI Agent created",
  ai_agent_chat: "AI Agent chat",
};

function eventLabel(t: string): string {
  return EVENT_LABELS[t] ?? t;
}

const EVENT_TYPE_GROUPS: { label: string; types: string[] }[] = [
  { label: "Account", types: ["login", "login_failed", "logout"] },
  {
    label: "Documents",
    types: [
      "viewed", "created", "renamed", "moved", "deleted", "permanently_deleted",
      "restored", "version_created", "version_restored", "checked_out", "checked_in",
    ],
  },
  {
    label: "Approvals",
    types: [
      "workflow_started", "workflow_step_voted", "workflow_step_advanced",
      "workflow_approved", "workflow_rejected", "workflow_cancelled",
    ],
  },
  { label: "Collaboration", types: ["commented", "tagged", "share_link_created"] },
  { label: "Compliance", types: ["legal_hold_set", "legal_hold_released"] },
  { label: "AI Agents", types: ["ai_agent_created", "ai_agent_chat"] },
];

function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}
function daysAgoStr(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

const PAGE_SIZE = 50;

export function AuditLogPage() {
  const { connections } = useConnections();
  const connectionName = (id: string | null) => (id ? connections.find((c) => c.id === id)?.display_name ?? id : "—");

  const [dateFrom, setDateFrom] = useState(daysAgoStr(30));
  const [dateTo, setDateTo] = useState(todayStr());
  const [actor, setActor] = useState("");
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set());
  const [actors, setActors] = useState<string[]>([]);

  const [summary, setSummary] = useState<ActivitySummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [offset, setOffset] = useState(0);

  const since = dateFrom ? `${dateFrom}T00:00:00` : undefined;
  const until = dateTo ? `${dateTo}T23:59:59.999999` : undefined;
  const typesArray = selectedTypes.size > 0 ? Array.from(selectedTypes) : undefined;

  useEffect(() => {
    apiGet<string[]>("/activity/actors").then(setActors).catch(() => {});
  }, []);

  useEffect(() => {
    setSummaryLoading(true);
    apiGet<ActivitySummary>("/activity/summary", { actor: actor || undefined, since, until, event_types: typesArray })
      .then(setSummary)
      .catch(() => setSummary(null))
      .finally(() => setSummaryLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actor, since, until, selectedTypes]);

  useEffect(() => {
    setEventsLoading(true);
    const params = { actor: actor || undefined, since, until, event_types: typesArray, limit: PAGE_SIZE, offset };
    Promise.all([
      apiGet<ActivityEvent[]>("/activity", params),
      apiGet<{ total: number }>("/activity/count", params),
    ])
      .then(([evts, cnt]) => {
        setEvents(evts);
        setTotal(cnt.total);
      })
      .catch(() => {
        setEvents([]);
        setTotal(0);
      })
      .finally(() => setEventsLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actor, since, until, selectedTypes, offset]);

  // Every filter setter below also resets offset back to page 1 in the same
  // event/render — not in a separate effect keyed on the filters, which
  // fired the paginated fetch once with the stale offset and again after
  // the reset landed, wasting a full request pair on every filter change.
  const toggleType = (t: string) => {
    setSelectedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
    setOffset(0);
  };

  const exportCsv = async () => {
    const url = new URL(`${API_BASE}/activity/export.csv`);
    if (actor) url.searchParams.set("actor", actor);
    if (since) url.searchParams.set("since", since);
    if (until) url.searchParams.set("until", until);
    for (const t of typesArray ?? []) url.searchParams.append("event_types", t);
    const token = getAuthToken();
    const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) return;
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = "activity-export.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(blobUrl);
  };

  const maxActorCount = summary?.by_actor[0]?.count || 1;

  return (
    <div className="audit-page">
      <div className="audit-topbar">
        <h2 className="audit-title">Reports</h2>
        <button className="btn-secondary" onClick={exportCsv}>
          <Icon name="download" size={15} />
          Export CSV
        </button>
      </div>

      <div className="audit-body">
        <aside className="audit-filters">
          <div className="audit-filter-group">
            <label>
              From
              <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setOffset(0); }} max={dateTo} />
            </label>
            <label>
              To
              <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setOffset(0); }} min={dateFrom} max={todayStr()} />
            </label>
          </div>

          <div className="audit-filter-group">
            <label>
              User
              <select value={actor} onChange={(e) => { setActor(e.target.value); setOffset(0); }}>
                <option value="">All users</option>
                {actors.map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </select>
            </label>
          </div>

          <div className="audit-filter-group">
            <div className="audit-filter-group-header">
              <span className="audit-filter-group-label">Event types</span>
              {selectedTypes.size > 0 && (
                <button className="link-btn" onClick={() => { setSelectedTypes(new Set()); setOffset(0); }}>Clear</button>
              )}
            </div>
            {EVENT_TYPE_GROUPS.map((g) => {
              const checkedCount = g.types.filter((t) => selectedTypes.has(t)).length;
              return (
                <details key={g.label} className="audit-filter-subgroup">
                  <summary className="audit-filter-subgroup-label">
                    {g.label}
                    {checkedCount > 0 && <span className="audit-filter-subgroup-count">{checkedCount}</span>}
                    <Icon name="chevron-down" size={11} className="audit-filter-subgroup-chevron" />
                  </summary>
                  <div className="audit-filter-subgroup-body">
                    {g.types.map((t) => (
                      <label key={t} className="audit-filter-checkbox">
                        <input type="checkbox" checked={selectedTypes.has(t)} onChange={() => toggleType(t)} />
                        {eventLabel(t)}
                      </label>
                    ))}
                  </div>
                </details>
              );
            })}
          </div>
        </aside>

        <main className="audit-content">
          <div className="audit-summary-cards">
            <div className="audit-card">
              <span className="audit-card-value">{summaryLoading ? "…" : summary?.total_events ?? 0}</span>
              <span className="audit-card-label">Total events</span>
            </div>
            <div className="audit-card">
              <span className="audit-card-value">{summaryLoading ? "…" : summary?.unique_actors ?? 0}</span>
              <span className="audit-card-label">Active users</span>
            </div>
            <div className={"audit-card" + (summary && summary.alerts.length > 0 ? " audit-card-alert" : "")}>
              <span className="audit-card-value">{summaryLoading ? "…" : summary?.alerts.length ?? 0}</span>
              <span className="audit-card-label">Alerts</span>
            </div>
          </div>

          {summary && summary.alerts.length > 0 && (
            <div className="audit-alerts">
              <h3>Alarming Activity</h3>
              {summary.alerts.map((a, i) => (
                <div key={i} className={`audit-alert audit-alert-${a.severity}`}>
                  <Icon name="warning-triangle" size={16} />
                  <div>
                    <div className="audit-alert-title">{a.title}</div>
                    <div className="audit-alert-detail">{a.detail}</div>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="audit-charts-row">
            <div className="audit-chart-card">
              <h3>Events over time</h3>
              <BarChart data={(summary?.by_day ?? []).map((d) => ({ label: d.day.slice(5), value: d.count }))} />
            </div>
            <div className="audit-chart-card">
              <h3>Breakdown by event type</h3>
              <DonutChart data={(summary?.by_type ?? []).slice(0, 8).map((d) => ({ label: eventLabel(d.event_type), value: d.count }))} />
            </div>
          </div>

          <div className="audit-chart-card">
            <h3>Most active users</h3>
            {(summary?.by_actor ?? []).length === 0 ? (
              <p className="muted" style={{ fontSize: "var(--text-sm)" }}>No data for this range.</p>
            ) : (
              (summary?.by_actor ?? []).slice(0, 10).map((a) => (
                <div key={a.actor} className="audit-actor-row">
                  <span className="audit-actor-name">{a.actor}</span>
                  <div className="audit-actor-bar-track">
                    <div className="audit-actor-bar" style={{ width: `${(a.count / maxActorCount) * 100}%` }} />
                  </div>
                  <span className="audit-actor-count muted">{a.count}</span>
                </div>
              ))
            )}
          </div>

          <div className="audit-table-card">
            <div className="audit-table-header">
              <h3>Event Log</h3>
              <span className="muted">{total} event{total !== 1 ? "s" : ""}</span>
            </div>
            {eventsLoading ? (
              <p className="muted" style={{ padding: "0 var(--space-4) var(--space-4)" }}>Loading…</p>
            ) : events.length === 0 ? (
              <p className="muted" style={{ padding: "0 var(--space-4) var(--space-4)" }}>No events match these filters.</p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table className="audit-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Actor</th>
                      <th>Event</th>
                      <th>Resource</th>
                      <th>Connection</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((e) => (
                      <tr key={e.id}>
                        <td className="muted">{formatDate(e.created_at)}</td>
                        <td>{e.actor}</td>
                        <td><span className="audit-event-pill">{eventLabel(e.event_type)}</span></td>
                        <td>{e.resource_name ?? e.resource_id}</td>
                        <td className="muted">{connectionName(e.connection_id)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="audit-pagination">
              <button className="btn-secondary" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}>
                Previous
              </button>
              <span className="muted">
                {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <button className="btn-secondary" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset((o) => o + PAGE_SIZE)}>
                Next
              </button>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
