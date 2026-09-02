import { useState } from "react";
import { useConnections } from "../contexts/ConnectionsContext";
import { AdminSettingsPanel } from "../components/AdminSettingsPanel";
import { Modal } from "../components/Modal";
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
  dropbox: "Sign in with Dropbox to store files in a dedicated C-ECM app folder in your Dropbox account.",
  laserfiche: "Sign in with Laserfiche Cloud to browse and manage files in your repository.",
  sharefile: "Sign in with Citrix ShareFile to browse and manage files in your account.",
  documentum: "Connect to an OpenText Documentum repository via Documentum REST Services.",
  opentext_content_server: "Connect to an OpenText Content Server instance using your credentials.",
  mfiles: "Connect to an M-Files vault using your credentials.",
  onbase: "Connect to a Hyland OnBase repository (flat document list — OnBase has no folder hierarchy).",
  nuxeo: "Connect to a Hyland Nuxeo repository using your credentials.",
  docuware: "Connect to a DocuWare file cabinet using your credentials.",
  docushare: "Connect to a Xerox DocuShare repository using your credentials.",
  aws_s3: "Store and browse files in an Amazon S3 bucket.",
  azure_blob: "Store and browse files in an Azure Blob Storage container.",
  wasabi: "Store and browse files in a Wasabi hot cloud storage bucket (S3-compatible).",
  backblaze_b2: "Store and browse files in a Backblaze B2 bucket (S3-compatible API).",
  gcs: "Store and browse files in a Google Cloud Storage bucket using an HMAC interoperability key.",
  nextcloud: "Connect to a Nextcloud server over WebDAV using your account or an app password.",
  owncloud: "Connect to an ownCloud server over WebDAV using your account or an app password.",
  synology_drive: "Connect to a Synology NAS share over WebDAV.",
  qnap: "Connect to a QNAP NAS share over WebDAV.",
  ibm_content_navigator: "Connect to a CMIS-compliant repository fronted by IBM Content Navigator.",
  sap_dms: "Connect to SAP's Document Management Service via its CMIS-compliant interface.",
  egnyte: "Sign in with Egnyte to browse and manage files in your domain.",
  confluence: "Sign in with Atlassian to browse spaces, pages, and attachments in Confluence Cloud.",
  huddle: "Sign in with Huddle to browse and manage files in your workspace.",
  netdocuments: "Sign in with NetDocuments to browse and manage documents in your cabinet.",
  zoho_workdrive: "Sign in with Zoho WorkDrive to browse and manage files in your team folders.",
  imanage: "Sign in with iManage Work to browse and manage documents in your library.",
  onehub: "Sign in with Onehub to browse and manage files in your workspace.",
  salesforce_files: "Sign in with Salesforce to browse files stored in your Salesforce Files libraries.",
  oracle_content_management: "Sign in with Oracle Content Management to browse and manage your files.",
  kiteworks: "Sign in with Accellion kiteworks to browse and manage files in your folders.",
  aem_assets: "Connect to an Adobe Experience Manager Assets instance using your credentials.",
  filecloud: "Connect to a FileCloud server using your credentials.",
  pcloud: "Sign in with your pCloud account to browse and manage your files.",
  seafile: "Connect to a Seafile server and library using your credentials.",
  logicaldoc: "Connect to a LogicalDOC server using your credentials.",
  veeva_vault: "Connect to a Veeva Vault using your credentials.",
  mediafire: "Sign in with your MediaFire account (and application ID) to browse your files.",
  efilecabinet: "Connect to an eFileCabinet server using your credentials.",
  firmex: "Connect to a Firmex data room using your credentials.",
  sharevault: "Connect to a ShareVault data room using your credentials.",
  intralinks: "Connect to an Intralinks exchange using your credentials.",
  highq: "Connect to a Thomson Reuters HighQ workspace using your credentials.",
  workshare: "Connect to a Workshare project using your credentials.",
  evernote_teams: "Not yet usable — Evernote's API doesn't fit this app's connection flow yet (see details on connect).",
};

export function IntegrationsPage() {
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
  const [searchQuery, setSearchQuery] = useState("");

  const selectedConnection = selection?.kind === "connection" ? (connections.find((c) => c.id === selection.id) ?? null) : null;
  const selectedProvider = selection?.kind === "provider" ? (providers.find((p) => p.key === selection.key) ?? null) : null;

  const q = searchQuery.trim().toLowerCase();
  const filteredConnections = connections.filter((c) => {
    if (!q) return true;
    const providerName = providers.find((p) => p.key === c.provider_key)?.display_name ?? c.provider_key;
    return c.display_name.toLowerCase().includes(q) || providerName.toLowerCase().includes(q);
  });
  const filteredProviders = providers.filter((p) => !q || p.display_name.toLowerCase().includes(q));

  const selectProvider = (key: string) => {
    const p = providers.find((pp) => pp.key === key);
    if (p?.coming_soon) return; // no adapter exists yet — nothing to open
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
      <div className="integrations-toolbar">
        <div className="integrations-search">
          <Icon name="search" size={15} />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search connections and providers"
          />
        </div>
      </div>

      {removeError && (
        <div className="auth-error" style={{ margin: "0 var(--space-5)" }}>
          {removeError}
        </div>
      )}

      <div className="integrations-body">
        <section className="integrations-section">
          <h2 className="integrations-section-title">Connections</h2>
          {filteredConnections.length === 0 && (
            <p className="muted integrations-empty-hint">
              {connections.length === 0 ? "No backends connected yet." : "No connections match your search."}
            </p>
          )}
          <div className="integrations-grid">
            {filteredConnections.map((c) => {
              const p = providers.find((pp) => pp.key === c.provider_key);
              return (
                <button
                  key={c.id}
                  type="button"
                  className="integrations-card"
                  onClick={() => setSelection({ kind: "connection", id: c.id })}
                >
                  <ProviderBadge providerKey={c.provider_key} size={32} />
                  <span className="integrations-card-name">{c.display_name}</span>
                  <span className="integrations-card-desc">{p?.display_name ?? c.provider_key}</span>
                </button>
              );
            })}
          </div>
        </section>

        <section className="integrations-section">
          <h2 className="integrations-section-title">Add a provider</h2>
          <div className="integrations-grid">
            {filteredProviders.map((p) => (
              <button
                key={p.key}
                type="button"
                className={p.coming_soon ? "integrations-card integrations-card-disabled" : "integrations-card"}
                onClick={() => selectProvider(p.key)}
                disabled={p.coming_soon}
                title={
                  p.coming_soon
                    ? `${p.display_name} isn't connectable yet — it's on the roadmap.`
                    : p.configured
                    ? undefined
                    : `${p.display_name} needs an OAuth app registered first — click to set it up`
                }
              >
                <ProviderBadge providerKey={p.key} size={32} />
                <span className="integrations-card-name">{p.display_name}</span>
                <span className="integrations-card-desc">{PROVIDER_DESCRIPTION[p.key]}</span>
                {p.coming_soon ? (
                  <span className="provider-badge integrations-card-badge integrations-card-badge-soon">Coming soon</span>
                ) : (
                  !p.configured && <span className="provider-badge integrations-card-badge">Set up →</span>
                )}
              </button>
            ))}
          </div>
        </section>
      </div>

      {selectedConnection && (
        <Modal title={selectedConnection.display_name} onClose={() => setSelection(null)} width={480}>
          <div className="integrations-detail">
            <div className="integrations-detail-header">
              <ProviderBadge providerKey={selectedConnection.provider_key} size={40} />
              <div>
                <p className="muted" style={{ margin: 0 }}>
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
        </Modal>
      )}

      {selectedProvider && (
        <Modal title={`Connect ${selectedProvider.display_name}`} onClose={() => setSelection(null)} width={480}>
          <div className="integrations-detail">
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
        </Modal>
      )}

      {adminSettingsFor && (
        <AdminSettingsPanel focusProvider={adminSettingsFor} onClose={() => setAdminSettingsFor(null)} />
      )}
    </div>
  );
}
