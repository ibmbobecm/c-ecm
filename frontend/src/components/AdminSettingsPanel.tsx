import { useEffect, useState } from "react";
import { apiGet, apiPut, ApiError } from "../api/client";
import type { AdminSettings } from "../types";
import { Modal } from "./Modal";
import { ProviderBadge } from "../icons";

type ExtraField = { key: string; label: string; placeholder?: string };

type OAuthProviderDef = {
  title: string;
  idField: string;
  secretField: string;
  secretSetField: keyof AdminSettings;
  extraFields?: ExtraField[];
  note?: string;
};

// One entry per OAUTH-mode provider — CREDENTIALS-mode providers (FileNet,
// Alfresco, Local Disk, and every per-connection-config backend like
// Documentum/Nuxeo/FileCloud/etc.) don't need an app-level client id/secret
// at all, so they're never routed here.
const OAUTH_PROVIDERS: Record<string, OAuthProviderDef> = {
  google_drive: { title: "Google Drive", idField: "google_client_id", secretField: "google_client_secret", secretSetField: "google_client_secret_set" },
  onedrive_sharepoint: {
    title: "Microsoft 365", idField: "ms_client_id", secretField: "ms_client_secret", secretSetField: "ms_client_secret_set",
    extraFields: [{ key: "ms_tenant", label: "Tenant", placeholder: "common" }],
  },
  box: { title: "Box", idField: "box_client_id", secretField: "box_client_secret", secretSetField: "box_client_secret_set" },
  dropbox: { title: "Dropbox", idField: "dropbox_client_id", secretField: "dropbox_client_secret", secretSetField: "dropbox_client_secret_set" },
  laserfiche: { title: "Laserfiche", idField: "laserfiche_client_id", secretField: "laserfiche_client_secret", secretSetField: "laserfiche_client_secret_set" },
  sharefile: { title: "ShareFile", idField: "sharefile_client_id", secretField: "sharefile_client_secret", secretSetField: "sharefile_client_secret_set" },
  egnyte: {
    title: "Egnyte", idField: "egnyte_client_id", secretField: "egnyte_client_secret", secretSetField: "egnyte_client_secret_set",
    extraFields: [{ key: "egnyte_domain", label: "Egnyte domain", placeholder: "yourcompany" }],
    note: "Egnyte is one domain per C-ECM deployment — this domain is shared by every Egnyte connection.",
  },
  confluence: { title: "Confluence", idField: "confluence_client_id", secretField: "confluence_client_secret", secretSetField: "confluence_client_secret_set" },
  huddle: { title: "Huddle", idField: "huddle_client_id", secretField: "huddle_client_secret", secretSetField: "huddle_client_secret_set" },
  netdocuments: { title: "NetDocuments", idField: "netdocuments_client_id", secretField: "netdocuments_client_secret", secretSetField: "netdocuments_client_secret_set" },
  zoho_workdrive: { title: "Zoho WorkDrive", idField: "zoho_workdrive_client_id", secretField: "zoho_workdrive_client_secret", secretSetField: "zoho_workdrive_client_secret_set" },
  imanage: {
    title: "iManage", idField: "imanage_client_id", secretField: "imanage_client_secret", secretSetField: "imanage_client_secret_set",
    extraFields: [{ key: "imanage_base_url", label: "iManage site URL", placeholder: "https://yourcustomer.imanage.work" }],
    note: "iManage Work Cloud is one site per C-ECM deployment — this URL is shared by every iManage connection.",
  },
  onehub: { title: "Onehub", idField: "onehub_client_id", secretField: "onehub_client_secret", secretSetField: "onehub_client_secret_set" },
  salesforce_files: { title: "Salesforce Files", idField: "salesforce_files_client_id", secretField: "salesforce_files_client_secret", secretSetField: "salesforce_files_client_secret_set" },
  oracle_content_management: {
    title: "Oracle Content Management", idField: "oracle_content_management_client_id",
    secretField: "oracle_content_management_client_secret", secretSetField: "oracle_content_management_client_secret_set",
    extraFields: [
      { key: "oracle_content_management_base_url", label: "OCM instance URL", placeholder: "https://your-instance.oraclecloud.com" },
      { key: "oracle_content_management_idcs_url", label: "Identity Cloud Service URL", placeholder: "https://your-tenant.identity.oraclecloud.com" },
    ],
    note: "One instance per C-ECM deployment — both URLs are shared by every Oracle Content Management connection.",
  },
  kiteworks: {
    title: "Accellion kiteworks", idField: "kiteworks_client_id", secretField: "kiteworks_client_secret", secretSetField: "kiteworks_client_secret_set",
    extraFields: [{ key: "kiteworks_base_url", label: "Kiteworks site URL", placeholder: "https://yourcompany.kiteworks.com" }],
    note: "One site per C-ECM deployment — this URL is shared by every Kiteworks connection.",
  },
  evernote_teams: {
    title: "Evernote Teams", idField: "evernote_teams_client_id", secretField: "evernote_teams_client_secret", secretSetField: "evernote_teams_client_secret_set",
    note: "Registering an app here doesn't make Evernote Teams connectable yet — its API doesn't fit this app's connection flow. See the connect screen for details.",
  },
};

export function AdminSettingsPanel({
  onClose,
  focusProvider,
}: {
  onClose: () => void;
  focusProvider: string;
}) {
  const provider = OAUTH_PROVIDERS[focusProvider];

  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [idValue, setIdValue] = useState("");
  const [secretValue, setSecretValue] = useState("");
  const [extraValues, setExtraValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadSettings = () => {
    if (!provider) return;
    setLoadError(null);
    apiGet<AdminSettings>("/admin/settings")
      .then((s) => {
        setSettings(s);
        setIdValue((s as unknown as Record<string, string>)[provider.idField] ?? "");
        const extras: Record<string, string> = {};
        for (const f of provider.extraFields ?? []) {
          extras[f.key] = (s as unknown as Record<string, string>)[f.key] ?? "";
        }
        setExtraValues(extras);
      })
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Couldn't load settings."));
  };

  useEffect(loadSettings, [focusProvider]);

  if (!provider) {
    return (
      <Modal title="Unknown provider" onClose={onClose} width={420}>
        <p className="muted">This provider doesn't have OAuth app settings.</p>
      </Modal>
    );
  }

  const secretIsSet = settings ? Boolean((settings as unknown as Record<string, boolean>)[provider.secretSetField]) : false;

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const payload: Record<string, string | undefined> = {
        [provider.idField]: idValue || undefined,
        [provider.secretField]: secretValue || undefined,
      };
      for (const f of provider.extraFields ?? []) {
        payload[f.key] = extraValues[f.key] || undefined;
      }
      const updated = await apiPut<AdminSettings>("/admin/settings", payload);
      setSettings(updated);
      setSecretValue("");
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save settings.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`${provider.title} setup`} onClose={onClose} width={440}>
      <p className="admin-settings-banner">
        {provider.title} requires a real app registered in its own developer console before anyone can sign in
        through it — that's a hard requirement of OAuth itself, not something this form can skip. Register one, paste
        its client ID/secret in below, and every connection to {provider.title} works from then on — nobody has to
        see this screen again.
      </p>
      {provider.note && (
        <p className="muted" style={{ margin: "0 0 12px", fontSize: 12 }}>
          {provider.note}
        </p>
      )}

      {loadError ? (
        <div className="auth-error">
          {loadError}
          <button type="button" className="link-btn" style={{ marginLeft: 8 }} onClick={loadSettings}>
            Retry
          </button>
        </div>
      ) : !settings ? (
        <p className="muted">Loading...</p>
      ) : (
        <form className="auth-form admin-settings-form" onSubmit={save}>
          <fieldset className="admin-fieldset">
            <legend>
              <ProviderBadge providerKey={focusProvider} size={16} />
              <span style={{ marginLeft: 6 }}>{provider.title}</span>
            </legend>
            <label>
              Client ID
              <input value={idValue} onChange={(e) => setIdValue(e.target.value)} autoFocus />
            </label>
            <label>
              Client secret
              <input
                type="password"
                value={secretValue}
                onChange={(e) => setSecretValue(e.target.value)}
                placeholder={secretIsSet ? "•••••••• (set — leave blank to keep)" : ""}
              />
            </label>
            {(provider.extraFields ?? []).map((f) => (
              <label key={f.key}>
                {f.label}
                <input
                  value={extraValues[f.key] ?? ""}
                  onChange={(e) => setExtraValues((prev) => ({ ...prev, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                />
              </label>
            ))}
          </fieldset>

          {error && <div className="auth-error">{error}</div>}
          {saved && <div className="auth-success">Saved.</div>}
          <button type="submit" disabled={busy}>
            {busy ? "Saving..." : "Save"}
          </button>
        </form>
      )}
    </Modal>
  );
}
