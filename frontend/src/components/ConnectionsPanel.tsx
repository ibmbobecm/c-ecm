import { useState } from "react";
import { useConnections } from "../contexts/ConnectionsContext";
import { Modal } from "./Modal";
import { AdminSettingsPanel } from "./AdminSettingsPanel";
import { ProviderBadge } from "../icons";

export function ConnectionsPanel({ onClose }: { onClose: () => void }) {
  const { providers, connections, createConnection, connectOAuth, removeConnection } = useConnections();
  const [addingProvider, setAddingProvider] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adminSettingsFor, setAdminSettingsFor] = useState<string | null>(null);

  const handleRemove = async (id: string) => {
    setRemoveError(null);
    setRemovingId(id);
    try {
      await removeConnection(id);
    } catch (err) {
      setRemoveError(err instanceof Error ? err.message : "Couldn't remove that connection.");
    } finally {
      setRemovingId(null);
    }
  };

  const provider = providers.find((p) => p.key === addingProvider) ?? null;

  const startAdding = (key: string) => {
    const p = providers.find((pp) => pp.key === key);
    setAddingProvider(key);
    setDisplayName("");
    setUsername("");
    setPassword("");
    // Pre-fill with the shown defaults as real, editable values — not just
    // grey placeholder text that silently submits empty if left untouched.
    setFieldValues(Object.fromEntries((p?.config_fields ?? []).map((f) => [f.key, f.placeholder])));
    setError(null);
  };

  const clickTile = (p: (typeof providers)[number]) => {
    if (!p.configured && p.auth_mode === "oauth") {
      // Not a dead end — take them straight to where they can fix it.
      setAdminSettingsFor(p.key);
      return;
    }
    startAdding(p.key);
  };

  const submitCredentials = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!provider) return;
    setBusy(true);
    setError(null);
    try {
      await createConnection(provider.key, displayName || provider.display_name, username, password, fieldValues);
      setAddingProvider(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't connect.");
    } finally {
      setBusy(false);
    }
  };

  const submitOAuth = async () => {
    if (!provider) return;
    setBusy(true);
    setError(null);
    try {
      await connectOAuth(provider.key, displayName || provider.display_name);
      setAddingProvider(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't connect.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Connections" onClose={onClose} width={520}>
      {removeError && <div className="auth-error" style={{ marginBottom: 12 }}>{removeError}</div>}
      <div className="connections-list">
        {connections.length === 0 && <p className="muted">No backends connected yet.</p>}
        {connections.map((c) => {
          const p = providers.find((pp) => pp.key === c.provider_key);
          return (
            <div key={c.id} className="connection-row">
              <div className="connection-row-icon">
                <ProviderBadge providerKey={c.provider_key} size={34} />
              </div>
              <div className="connection-row-body">
                <div className="connection-name">{c.display_name}</div>
                <div className="muted">
                  {p?.display_name ?? c.provider_key}
                  {c.identity ? ` · ${c.identity}` : ""}
                </div>
              </div>
              <button className="link-btn" onClick={() => handleRemove(c.id)} disabled={removingId === c.id}>
                {removingId === c.id ? "Removing..." : "Remove"}
              </button>
            </div>
          );
        })}
      </div>

      <div className="connections-subhead-row">
        <h3 className="connections-subhead">Add a connection</h3>
        <button className="link-btn" onClick={() => setAdminSettingsFor("__all__")}>
          OAuth app settings
        </button>
      </div>

      <div className="provider-grid">
        {providers.map((p) => (
          <button
            key={p.key}
            type="button"
            className={"provider-tile" + (p.key === addingProvider ? " active" : "") + (!p.configured ? " needs-setup" : "")}
            onClick={() => clickTile(p)}
            title={p.configured ? undefined : `${p.display_name} needs an OAuth app registered first — click to set it up`}
          >
            <span className="provider-tile-icon">
              <ProviderBadge providerKey={p.key} size={30} />
            </span>
            <span className="provider-tile-label">{p.display_name}</span>
            {!p.configured && <span className="provider-badge">Set up →</span>}
          </button>
        ))}
      </div>

      {provider && provider.auth_mode === "credentials" && (
        <form className="auth-form" onSubmit={submitCredentials}>
          <label>
            Name this connection
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={provider.display_name}
            />
          </label>
          {provider.config_fields.map((f) => (
            <label key={f.key}>
              {f.label}
              <input
                value={fieldValues[f.key] ?? ""}
                onChange={(e) => setFieldValues((prev) => ({ ...prev, [f.key]: e.target.value }))}
                placeholder={f.placeholder}
                required={f.required}
              />
            </label>
          ))}
          {provider.requires_credentials && (
            <>
              <label>
                {provider.credential_labels[0]}
                <input value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />
              </label>
              <label>
                {provider.credential_labels[1]}
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
              </label>
            </>
          )}
          {error && <div className="auth-error">{error}</div>}
          <button type="submit" disabled={busy}>
            {busy ? "Connecting..." : "Connect"}
          </button>
        </form>
      )}

      {provider && provider.auth_mode === "oauth" && (
        <div className="auth-form">
          <label>
            Name this connection
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={provider.display_name}
            />
          </label>
          <p className="muted">You'll be sent to {provider.display_name} to sign in and grant access.</p>
          {error && <div className="auth-error">{error}</div>}
          <button type="button" onClick={submitOAuth} disabled={busy}>
            {busy ? "Connecting..." : `Connect ${provider.display_name}`}
          </button>
        </div>
      )}

      {adminSettingsFor && (
        <AdminSettingsPanel
          focusProvider={adminSettingsFor === "__all__" ? undefined : adminSettingsFor}
          onClose={() => setAdminSettingsFor(null)}
        />
      )}
    </Modal>
  );
}
