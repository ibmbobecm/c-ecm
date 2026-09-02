import { useEffect, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { API_BASE, apiGet, ApiError } from "../api/client";

export function Login({ onBack }: { onBack?: () => void }) {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ssoEnabled, setSsoEnabled] = useState(false);

  useEffect(() => {
    apiGet<{ enabled: boolean }>("/saml/status")
      .then((s) => setSsoEnabled(s.enabled))
      .catch(() => setSsoEnabled(false));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Is the backend running?");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        {onBack && (
          <button type="button" className="auth-back-link" onClick={onBack}>
            &larr; Back
          </button>
        )}
        <h1>C-ECM</h1>
        <p className="auth-subtitle">Centralized Enterprise Content Management</p>

        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />
        </label>

        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>

        {error && <div className="auth-error">{error}</div>}

        <button type="submit" disabled={busy}>
          {busy ? "Signing in..." : "Sign in"}
        </button>

        {ssoEnabled && (
          <>
            <div className="auth-divider">or</div>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                // Full top-level navigation, not a fetch — SAML is a
                // browser-redirect protocol, and this leaves the SPA
                // entirely until /sso-complete.html brings the user back.
                window.location.href = `${API_BASE}/saml/login`;
              }}
            >
              Sign in with SSO
            </button>
          </>
        )}
      </form>
    </div>
  );
}
