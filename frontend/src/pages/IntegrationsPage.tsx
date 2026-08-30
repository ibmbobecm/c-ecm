import { useState } from "react";
import { useConnections } from "../contexts/ConnectionsContext";
import { AdminSettingsPanel } from "../components/AdminSettingsPanel";
import { ProviderBadge, Icon } from "../icons";

type Selection = { kind: "connection"; id: string } | { kind: "provider"; key: string } | null;

// Short, plain-language description of what each backend actually is —
// shown next to "Connect <Provider>" so picking one isn't a guess from the
// name alone.
const PROVIDER_DESCRIPTION: Record<string, string> = {
  filenet: "Connect to an IBM FileNet Content Engine repository using your object store credentials.",
  ibm_cos: "Store and browse files in an IBM Cloud Object Storage bucket.",
  ibm_i: "Browse the IFS (Integrated File System) on an IBM i (AS/400) server over SSH.",
  ibm_z: "Browse USS files and datasets on an IBM Z mainframe via z/OSMF.",
  local: "Use a folder on this server's local disk as a drive — no external account needed.",
  alfresco: "Connect to an Alfresco Content Services repository.",
  google_drive: "Sign in with Google to store files in a dedicated C-ECM folder in your Google Drive.",
  onedrive_sharepoint: "Sign in with Microsoft to store files in OneDrive or SharePoint via Microsoft Graph.",
  box: "Sign in with Box to store files in a dedicated C-ECM folder in your Box account.",
  aws_s3: "Store and browse files in an Amazon S3 bucket.",
  azure_blob: "Store and browse files in an Azure Blob Storage container.",
};

export function IntegrationsPage({ onBack }: { onBack: () => void }) {
  const { providers, connections, createConnection, connectOAuth, removeConnection } = useConnections();
  const [selection, setSelection] = useState<Selection>(null);
  const [displayName, setDisplayName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adminSettingsFor, setAdminSettingsFor] = useState<string | null>(null);

  const selectedConnection = selection?.kind === "connection" ? (connections.find((c) => c.id === selection.id) ?? null) : null;
  const selectedProvider = selection?.kind === "provider" ? (providers.find((p) => p.key === selection.key) ?? null) : null;

  const selectProvider = (key: string) => {
    const p = providers.find((pp) => pp.key === key);
    if (p && !p.configured && p.auth_mode === "oauth") {
      // Not a dead end — take them straight to where they can fix it.
      setAdminSettingsFor(key);
      return;
    }
    setSelection({ kind: "provider", key });
    setDisplayName("");
    setUsername("");
    setPassword("");
    // Pre-fill with the shown defaults as real, editable values — not just
    // grey placeholder text that silently submits empty if left untouched.
    setFieldValues(Object.fromEntries((p?.config_fields ?? []).map((f) => [f.key, f.placeholder])));
    setError(null);
  };

  const handleRemove = async (id: string) => {
    setRemoveError(null);
    setRemovingId(id);
    try {
      await removeConnection(id);
      if (selection?.kind === "connection" && selection.id === id) setSelection(null);
    } catch (err) {
      setRemoveError(err instanceof Error ? err.message : "Couldn't remove that connection.");
    } finally {
      setRemovingId(null);
    }
  };

  const submitCredentials = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProvider) return;
    setBusy(true);
    setError(null);
    try {
      await createConnection(selectedProvider.key, displayName || selectedProvider.display_name, username, password, fieldValues);
      setSelection(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't connect.");
    } finally {
      setBusy(false);
    }
  };

  const submitOAuth = async () => {
    if (!selectedProvider) return;
    setBusy(true);
    setError(null);
    try {
      await connectOAuth(selectedProvider.key, displayName || selectedProvider.display_name);
      setSelection(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't connect.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="integrations-page">
      <aside className="integrations-nav">
        <button type="button" className="integrations-back" onClick={onBack}>
          <span className="integrations-back-icon">
            <Icon name="chevron-right" size={13} />
          </span>
          Back to Drive
        </button>

        <div className="integrations-nav-heading-row">
          <h1 className="integrations-title">Connections</h1>
          <button type="button" className="link-btn" onClick={() => setAdminSettingsFor("__all__")}>
            OAuth app settings
          </button>
        </div>

        <nav className="integrations-nav-section">
          <div className="sidebar-section-label">Connections</div>
          {connections.length === 0 && <p className="muted integrations-empty-hint">No backends connected yet.</p>}
          {connections.map((c) => {
            const p = providers.find((pp) => pp.key === c.provider_key);
            return (
              <button
                key={c.id}
                type="button"
                className={"integrations-nav-item" + (selection?.kind === "connection" && selection.id === c.id ? " active" : "")}
                onClick={() => setSelection({ kind: "connection", id: c.id })}
              >
                <ProviderBadge providerKey={c.provider_key} size={22} />
                <span className="integrations-nav-item-label">
                  <span className="integrations-nav-item-name">{c.display_name}</span>
                  <span className="integrations-nav-item-sub">{p?.display_name ?? c.provider_key}</span>
                </span>
              </button>
            );
          })}
        </nav>

        <nav className="integrations-nav-section">
          <div className="sidebar-section-label">Add a provider</div>
          {providers.map((p) => (
            <button
              key={p.key}
              type="button"
              className={
                "integrations-nav-item" +
                (selection?.kind === "provider" && selection.key === p.key ? " active" : "") +
                (!p.configured ? " needs-setup" : "")
              }
              onClick={() => selectProvider(p.key)}
              title={p.configured ? undefined : `${p.display_name} needs an OAuth app registered first — click to set it up`}
            >
              <ProviderBadge providerKey={p.key} size={22} />
              <span className="integrations-nav-item-label">
                <span className="integrations-nav-item-name">{p.display_name}</span>
                {!p.configured && <span className="provider-badge">Set up →</span>}
              </span>
            </button>
          ))}
        </nav>
      </aside>

      <main className="integrations-content">
        {removeError && (
          <div className="auth-error" style={{ marginBottom: 12 }}>
            {removeError}
          </div>
        )}

        {!selection && (
          <div className="integrations-placeholder">
            <Icon name="plug" size={48} />
            <h2>Manage your connections</h2>
            <p className="muted">Select a connection on the left to view its details, or add a new provider to connect another backend.</p>
          </div>
        )}

        {selectedConnection && (
          <div className="integrations-detail">
            <div className="integrations-detail-header">
              <ProviderBadge providerKey={selectedConnection.provider_key} size={40} />
              <div>
                <h2>{selectedConnection.display_name}</h2>
                <p className="muted">
                  {providers.find((p) => p.key === selectedConnection.provider_key)?.display_name ?? selectedConnection.provider_key}
                  {selectedConnection.identity ? ` · ${selectedConnection.identity}` : ""}
                </p>
              </div>
            </div>
            {PROVIDER_DESCRIPTION[selectedConnection.provider_key] && (
              <p className="muted integrations-provider-desc">{PROVIDER_DESCRIPTION[selectedConnection.provider_key]}</p>
            )}
            <button
              type="button"
              className="integrations-remove-btn"
              onClick={() => handleRemove(selectedConnection.id)}
              disabled={removingId === selectedConnection.id}
            >
              {removingId === selectedConnection.id ? "Removing..." : "Remove connection"}
            </button>
          </div>
        )}

        {selectedProvider && (
          <div className="integrations-detail">
            <div className="integrations-detail-header">
              <ProviderBadge providerKey={selectedProvider.key} size={40} />
              <h2>Connect {selectedProvider.display_name}</h2>
            </div>
            {PROVIDER_DESCRIPTION[selectedProvider.key] && (
              <p className="muted integrations-provider-desc">{PROVIDER_DESCRIPTION[selectedProvider.key]}</p>
            )}

            {selectedProvider.auth_mode === "credentials" && (
              <form className="auth-form integrations-form" onSubmit={submitCredentials}>
                <label>
                  Name this connection
                  <input
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder={selectedProvider.display_name}
                  />
                </label>
                {selectedProvider.config_fields.map((f) => (
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
                {selectedProvider.requires_credentials && (
                  <>
                    <label>
                      {selectedProvider.credential_labels[0]}
                      <input value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />
                    </label>
                    <label>
                      {selectedProvider.credential_labels[1]}
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

            {selectedProvider.auth_mode === "oauth" && (
              <div className="auth-form integrations-form">
                <label>
                  Name this connection
                  <input
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder={selectedProvider.display_name}
                  />
                </label>
                <p className="muted">You'll be sent to {selectedProvider.display_name} to sign in and grant access.</p>
                {error && <div className="auth-error">{error}</div>}
                <button type="button" onClick={submitOAuth} disabled={busy}>
                  {busy ? "Connecting..." : `Connect ${selectedProvider.display_name}`}
                </button>
              </div>
            )}
          </div>
        )}
      </main>

      {adminSettingsFor && (
        <AdminSettingsPanel
          focusProvider={adminSettingsFor === "__all__" ? undefined : adminSettingsFor}
          onClose={() => setAdminSettingsFor(null)}
        />
      )}
    </div>
  );
}
