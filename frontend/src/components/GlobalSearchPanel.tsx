/**
 * GlobalSearchPanel — searches across ALL connected backends simultaneously.
 *
 * Results are grouped by connection so users can immediately see which backend
 * each hit came from.  Provider-level errors are shown inline so a single
 * failing backend doesn't mask results from the rest.
 */
import { useState } from "react";
import { apiGet, ApiError } from "../api/client";
import type { GlobalSearchHit, GlobalSearchResult } from "../types";
import { Icon, fileTypeIconName, ProviderBadge } from "../icons";
import { formatBytes, formatDate } from "../utils";
import { useConnections } from "../contexts/ConnectionsContext";

function HitRow({ hit, onSelect }: { hit: GlobalSearchHit; onSelect: (h: GlobalSearchHit) => void }) {
  const iconName = hit.resource_type === "folder"
    ? ("folder" as const)
    : fileTypeIconName(hit.content_type, hit.name);

  return (
    <tr
      className="gs-row"
      tabIndex={0}
      onClick={() => onSelect(hit)}
      onKeyDown={(e) => e.key === "Enter" && onSelect(hit)}
      style={{ cursor: "pointer" }}
    >
      <td style={{ padding: "8px 10px", width: 32 }}>
        <Icon name={iconName} size={18} />
      </td>
      <td style={{ padding: "8px 4px" }}>
        <div style={{ fontWeight: 500, fontSize: 13 }}>{hit.name}</div>
        <div className="muted" style={{ fontSize: 11 }}>{hit.resource_type}</div>
      </td>
      <td style={{ padding: "8px 10px", fontSize: 12 }} className="muted">
        {hit.size_bytes != null ? formatBytes(hit.size_bytes) : "—"}
      </td>
      <td style={{ padding: "8px 10px", fontSize: 12 }} className="muted">
        {formatDate(hit.updated_at)}
      </td>
    </tr>
  );
}

function ConnectionGroup({
  connectionName,
  providerKey,
  hits,
  error,
  onSelect,
}: {
  connectionName: string;
  providerKey: string;
  hits: GlobalSearchHit[];
  error?: string;
  onSelect: (h: GlobalSearchHit) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="gs-group" style={{ marginBottom: 20 }}>
      <button
        className="gs-group-header"
        onClick={() => setCollapsed((c) => !c)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: collapsed ? 8 : "8px 8px 0 0",
          padding: "8px 14px",
          width: "100%",
          cursor: "pointer",
          fontWeight: 600,
          fontSize: 13,
        }}
      >
        <ProviderBadge providerKey={providerKey} size={20} />
        <span style={{ flex: 1, textAlign: "left" }}>{connectionName}</span>
        <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>
          {error ? "error" : `${hits.length} hit${hits.length !== 1 ? "s" : ""}`}
        </span>
        <Icon name={collapsed ? "chevron-right" : "chevron-down"} size={14} />
      </button>

      {!collapsed && (
        <div style={{ border: "1px solid var(--border)", borderTop: "none", borderRadius: "0 0 8px 8px", overflow: "hidden" }}>
          {error ? (
            <div style={{ padding: "10px 14px", fontSize: 13, color: "var(--danger, #e53e3e)", background: "var(--surface)" }}>
              <Icon name="warning-triangle" size={14} />
              {" "}{error}
            </div>
          ) : hits.length === 0 ? (
            <div style={{ padding: "10px 14px", fontSize: 13, color: "var(--text-muted)" }} className="muted">
              No results in this connection.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <tbody>
                {hits.map((h) => (
                  <HitRow key={`${h.connection_id}:${h.resource_type}:${h.resource_id}`} hit={h} onSelect={onSelect} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

export function GlobalSearchPanel({ onSelectHit }: { onSelectHit: (hit: GlobalSearchHit) => void }) {
  const { connections } = useConnections();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<GlobalSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    try {
      const r = await apiGet<GlobalSearchResult>("/search/global", { q });
      setResult(r);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Global search failed.");
    } finally {
      setLoading(false);
    }
  };

  // Group hits by connection
  const groups = result
    ? (() => {
        const map = new Map<string, { connectionName: string; providerKey: string; hits: GlobalSearchHit[] }>();
        for (const hit of result.hits) {
          if (!map.has(hit.connection_id)) {
            map.set(hit.connection_id, { connectionName: hit.connection_name, providerKey: hit.provider_key, hits: [] });
          }
          map.get(hit.connection_id)!.hits.push(hit);
        }
        // Also add connections that errored with no hits — hardcoding
        // providerKey to "local" here used to show the wrong provider
        // badge for e.g. a failed Google Drive/S3 connection, actively
        // misleading about which connection failed. Look up the real
        // name/provider from the connections list instead of guessing.
        for (const [cid, _err] of Object.entries(result.connection_errors)) {
          if (!map.has(cid)) {
            const known = connections.find((c) => c.id === cid);
            map.set(cid, {
              connectionName: known?.display_name ?? cid,
              providerKey: known?.provider_key ?? "local",
              hits: [],
            });
          }
        }
        return map;
      })()
    : null;

  const totalHits = result?.hits.length ?? 0;
  const errorCount = Object.keys(result?.connection_errors ?? {}).length;

  return (
    <div style={{ padding: "24px 32px", maxWidth: 800, margin: "0 auto" }}>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ margin: "0 0 6px", fontSize: 20, fontWeight: 700 }}>Global Search</h2>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Search across all your connected backends simultaneously — FileNet, Google Drive, S3, and more.
        </p>
      </div>

      <form onSubmit={runSearch} style={{ display: "flex", gap: 10, marginBottom: 28 }}>
        <div style={{ flex: 1, position: "relative" }}>
          <span style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }}>
            <Icon name="search" size={16} />
          </span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search all connections…"
            style={{ width: "100%", paddingLeft: 38, boxSizing: "border-box" }}
            autoFocus
          />
        </div>
        <button type="submit" className="btn-primary" disabled={loading || !query.trim()}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {error && (
        <div className="auth-error" style={{ marginBottom: 16 }}>{error}</div>
      )}

      {result && !loading && (
        <div style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 16 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>
            {totalHits} result{totalHits !== 1 ? "s" : ""} for <em>"{result.query}"</em>
          </span>
          {errorCount > 0 && (
            <span style={{ fontSize: 12, color: "var(--danger, #e53e3e)" }}>
              <Icon name="warning-triangle" size={13} />
              {" "}{errorCount} connection{errorCount !== 1 ? "s" : ""} failed
            </span>
          )}
        </div>
      )}

      {groups && (
        <div>
          {[...groups.entries()].map(([cid, { connectionName, providerKey, hits }]) => (
            <ConnectionGroup
              key={cid}
              connectionName={connectionName}
              providerKey={providerKey}
              hits={hits}
              error={result?.connection_errors[cid]}
              onSelect={onSelectHit}
            />
          ))}
        </div>
      )}

      {!result && !loading && (
        <div className="empty-state" style={{ marginTop: 48 }}>
          <div className="empty-state-icon">
            <Icon name="search" size={48} />
          </div>
          <h3>Search all backends at once</h3>
          <p>Enter a query above to find files and folders across every connected provider simultaneously.</p>
        </div>
      )}
    </div>
  );
}
