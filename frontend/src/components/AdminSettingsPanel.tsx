import { useEffect, useState } from "react";
import { apiGet, apiPut, ApiError } from "../api/client";
import type { AdminSettings } from "../types";
import { Modal } from "./Modal";

type ProviderKey = "google" | "ms" | "box";

const FIELDSET_KEY: Record<string, ProviderKey> = {
  google_drive: "google",
  onedrive_sharepoint: "ms",
  box: "box",
};

// Plain text labels — no emoji to avoid cross-platform rendering gaps.
const TITLES: Record<ProviderKey, string> = {
  google: "Google Drive",
  ms: "Microsoft 365",
  box: "Box",
};

// Small provider icons rendered as inline SVG so they look consistent on all
// platforms without depending on OS emoji support.
function ProviderIcon({ provider }: { provider: ProviderKey }) {
  if (provider === "google") {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true" style={{ marginRight: 5, flexShrink: 0 }}>
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
      </svg>
    );
  }
  if (provider === "ms") {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true" style={{ marginRight: 5, flexShrink: 0 }}>
        <rect x="1" y="1" width="10" height="10" fill="#F25022"/>
        <rect x="13" y="1" width="10" height="10" fill="#7FBA00"/>
        <rect x="1" y="13" width="10" height="10" fill="#00A4EF"/>
        <rect x="13" y="13" width="10" height="10" fill="#FFB900"/>
      </svg>
    );
  }
  // box
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true" style={{ marginRight: 5, flexShrink: 0 }}>
      <rect x="1" y="4" width="22" height="16" rx="2" stroke="#0061D5" strokeWidth="2"/>
      <path d="M1 9h22" stroke="#0061D5" strokeWidth="2"/>
    </svg>
  );
}

export function AdminSettingsPanel({
  onClose,
  focusProvider,
}: {
  onClose: () => void;
  focusProvider?: string;
}) {
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [googleId, setGoogleId] = useState("");
  const [googleSecret, setGoogleSecret] = useState("");
  const [msId, setMsId] = useState("");
  const [msSecret, setMsSecret] = useState("");
  const [msTenant, setMsTenant] = useState("");
  const [boxId, setBoxId] = useState("");
  const [boxSecret, setBoxSecret] = useState("");
  const [dsIntegrationKey, setDsIntegrationKey] = useState("");
  const [dsUserId, setDsUserId] = useState("");
  const [dsAccountId, setDsAccountId] = useState("");
  const [dsPrivateKey, setDsPrivateKey] = useState("");
  const [dsEnvironment, setDsEnvironment] = useState("demo");
  const [dsWebhookKey, setDsWebhookKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const initialKey = focusProvider ? FIELDSET_KEY[focusProvider] : undefined;
  const [showAll, setShowAll] = useState(!initialKey);
  const activeKey: ProviderKey = initialKey ?? "google";

  const loadSettings = () => {
    setLoadError(null);
    apiGet<AdminSettings>("/admin/settings")
      .then((s) => {
        setSettings(s);
        setGoogleId(s.google_client_id);
        setMsId(s.ms_client_id);
        setMsTenant(s.ms_tenant);
        setBoxId(s.box_client_id);
        setDsIntegrationKey(s.docusign_integration_key);
        setDsUserId(s.docusign_user_id);
        setDsAccountId(s.docusign_account_id);
        setDsEnvironment(s.docusign_environment || "demo");
      })
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Couldn't load OAuth settings."));
  };

  useEffect(loadSettings, []);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await apiPut<AdminSettings>("/admin/settings", {
        google_client_id: googleId || undefined,
        google_client_secret: googleSecret || undefined,
        ms_client_id: msId || undefined,
        ms_client_secret: msSecret || undefined,
        ms_tenant: msTenant || undefined,
        box_client_id: boxId || undefined,
        box_client_secret: boxSecret || undefined,
        docusign_integration_key: dsIntegrationKey || undefined,
        docusign_user_id: dsUserId || undefined,
        docusign_account_id: dsAccountId || undefined,
        docusign_private_key: dsPrivateKey || undefined,
        docusign_environment: dsEnvironment || undefined,
        docusign_webhook_hmac_key: dsWebhookKey || undefined,
      });
      setSettings(updated);
      setGoogleSecret("");
      setMsSecret("");
      setBoxSecret("");
      setDsPrivateKey("");
      setDsWebhookKey("");
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save settings.");
    } finally {
      setBusy(false);
    }
  };

  const visibleKeys: ProviderKey[] = showAll ? ["google", "ms", "box"] : [activeKey];

  return (
    <Modal title={showAll ? "OAuth app settings" : `${TITLES[activeKey]} setup`} onClose={onClose} width={460}>
      {!showAll && (
        <p className="admin-settings-banner">
          {TITLES[activeKey]} requires a real app registered in its own developer console
          before anyone can sign in through it — that's a hard requirement of OAuth itself, not something this form
          can skip. Register one (steps below), paste its client ID/secret in, and every connection to this provider
          works from then on — nobody has to see this screen again.
        </p>
      )}
      {showAll && (
        <p className="muted admin-settings-intro">
          Register one app per provider (their developer console, not this form) and put its client ID/secret here —
          every connection to that provider shares it. Only needed for Google Drive, Microsoft 365, and Box; FileNet,
          Alfresco, and Local Disk don't use OAuth.
        </p>
      )}

      {!showAll && (
        <button type="button" className="link-btn admin-settings-showall" onClick={() => setShowAll(true)}>
          Show all three providers
        </button>
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
          {visibleKeys.includes("google") && (
            <fieldset className="admin-fieldset">
              <legend><ProviderIcon provider="google" />{TITLES.google}</legend>
              <label>
                Client ID
                <input value={googleId} onChange={(e) => setGoogleId(e.target.value)} />
              </label>
              <label>
                Client secret
                <input
                  type="password"
                  value={googleSecret}
                  onChange={(e) => setGoogleSecret(e.target.value)}
                  placeholder={settings.google_client_secret_set ? "•••••••• (set — leave blank to keep)" : ""}
                />
              </label>
            </fieldset>
          )}

          {visibleKeys.includes("ms") && (
            <fieldset className="admin-fieldset">
              <legend><ProviderIcon provider="ms" />{TITLES.ms}</legend>
              <label>
                Client ID
                <input value={msId} onChange={(e) => setMsId(e.target.value)} />
              </label>
              <label>
                Client secret
                <input
                  type="password"
                  value={msSecret}
                  onChange={(e) => setMsSecret(e.target.value)}
                  placeholder={settings.ms_client_secret_set ? "•••••••• (set — leave blank to keep)" : ""}
                />
              </label>
              <label>
                Tenant
                <input value={msTenant} onChange={(e) => setMsTenant(e.target.value)} placeholder="common" />
              </label>
            </fieldset>
          )}

          {visibleKeys.includes("box") && (
            <fieldset className="admin-fieldset">
              <legend><ProviderIcon provider="box" />{TITLES.box}</legend>
              <label>
                Client ID
                <input value={boxId} onChange={(e) => setBoxId(e.target.value)} />
              </label>
              <label>
                Client secret
                <input
                  type="password"
                  value={boxSecret}
                  onChange={(e) => setBoxSecret(e.target.value)}
                  placeholder={settings.box_client_secret_set ? "•••••••• (set — leave blank to keep)" : ""}
                />
              </label>
            </fieldset>
          )}

          {showAll && (
            <fieldset className="admin-fieldset">
              <legend>DocuSign (e-signature)</legend>
              <p className="muted" style={{ margin: "0 0 4px", fontSize: 12 }}>
                A Service Integration app from your DocuSign developer account — sending a document for signature
                acts as one admin-authorized DocuSign user, shared by the whole deployment.
                {settings.docusign_configured ? " Currently configured." : " Not yet configured."}
              </p>
              <label>
                Integration key
                <input value={dsIntegrationKey} onChange={(e) => setDsIntegrationKey(e.target.value)} />
              </label>
              <label>
                Impersonated user ID
                <input value={dsUserId} onChange={(e) => setDsUserId(e.target.value)} />
              </label>
              <label>
                Account ID
                <input value={dsAccountId} onChange={(e) => setDsAccountId(e.target.value)} />
              </label>
              <label>
                RSA private key
                <textarea
                  className="text-dialog-input"
                  rows={4}
                  value={dsPrivateKey}
                  onChange={(e) => setDsPrivateKey(e.target.value)}
                  placeholder={settings.docusign_private_key_set ? "•••••••• (set — leave blank to keep)" : "-----BEGIN PRIVATE KEY-----..."}
                  style={{ fontFamily: "monospace", fontSize: 11 }}
                />
              </label>
              <label>
                Environment
                <select value={dsEnvironment} onChange={(e) => setDsEnvironment(e.target.value)}>
                  <option value="demo">Demo / sandbox</option>
                  <option value="production">Production</option>
                </select>
              </label>
              <label>
                Webhook HMAC key (optional, recommended)
                <input
                  type="password"
                  value={dsWebhookKey}
                  onChange={(e) => setDsWebhookKey(e.target.value)}
                  placeholder={settings.docusign_webhook_hmac_key_set ? "•••••••• (set — leave blank to keep)" : "Configured on the DocuSign Connect webhook"}
                />
              </label>
            </fieldset>
          )}

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
