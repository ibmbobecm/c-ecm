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
    >
      <td className="gs-row-icon-cell">
        <Icon name={iconName} size={18} />
      </td>
      <td>
        <div className="gs-row-name">{hit.name}</div>
        <div className="gs-row-type muted">{hit.resource_type}</div>
      </td>
      <td className="gs-row-meta muted">
        {hit.size_bytes != null ? formatBytes(hit.size_bytes) : "—"}
      </td>
      <td className="gs-row-meta muted">
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
    <div className="gs-group">
      <button
        className={"gs-group-header" + (collapsed ? " collapsed" : "")}
        onClick={() => setCollapsed((c) => !c)}
      >
        <ProviderBadge providerKey={providerKey} size={20} />
        <span className="gs-group-header-name">{connectionName}</span>
        <span className="gs-group-header-count">
          {error ? "error" : `${hits.length} hit${hits.length !== 1 ? "s" : ""}`}
        </span>
        <Icon name={collapsed ? "chevron-right" : "chevron-down"} size={14} />
      </button>

      {!collapsed && (
        <div className="gs-group-body">
          {error ? (
            <div className="gs-group-error">
              <Icon name="warning-triangle" size={14} />
              {error}
            </div>
          ) : hits.length === 0 ? (
            <div className="gs-group-empty muted">No results in this connection.</div>
          ) : (
            <table className="gs-table">
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
    <div className="gs-page">
      <div className="gs-header">
        <h2>Global Search</h2>
        <p className="muted">
          Search across all your connected backends simultaneously — FileNet, Google Drive, S3, and more.
        </p>
      </div>

      <form onSubmit={runSearch} className="gs-search-form">
        <div className="gs-search-input-wrap">
          <span className="gs-search-icon">
            <Icon name="search" size={16} />
          </span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search all connections…"
            autoFocus
          />
        </div>
        <button type="submit" className="btn-primary" disabled={loading || !query.trim()}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {error && <div className="auth-error" style={{ marginBottom: 16 }}>{error}</div>}

      {result && !loading && (
        <div className="gs-summary">
          <span>
            {totalHits} result{totalHits !== 1 ? "s" : ""} for <em>"{result.query}"</em>
          </span>
          {errorCount > 0 && (
            <span className="gs-summary-error">
              <Icon name="warning-triangle" size={13} />
              {errorCount} connection{errorCount !== 1 ? "s" : ""} failed
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
