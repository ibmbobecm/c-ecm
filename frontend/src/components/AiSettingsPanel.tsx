/**
 * AiSettingsPanel — admin UI for choosing and configuring the AI backend
 * used for document summarization, auto-classification, Q&A, and AI Agents.
 * Only renders when the current user has the 'manage_admin_settings'
 * feature (server-side enforced too).
 *
 * Every backend's credentials can be saved independently of which one is
 * currently active — switching the active backend later doesn't lose
 * whatever was already entered for the others.
 */
import { useEffect, useState } from "react";
import { apiGet, apiPut, ApiError } from "../api/client";
import type { AdminSettings } from "../types";

type BackendKey = "none" | "anthropic" | "openai" | "watsonx" | "watson_nlu" | "watson_disco" | "ollama";

const BACKENDS: { key: BackendKey; label: string; blurb: string }[] = [
  { key: "none", label: "Disabled", blurb: "AI features (summarize, auto-classify, Q&A, AI Agents) are turned off." },
  { key: "anthropic", label: "Anthropic (Claude)", blurb: "Claude models via the Anthropic API." },
  { key: "openai", label: "OpenAI (ChatGPT) or compatible", blurb: "OpenAI's API, or any OpenAI-compatible endpoint (Azure OpenAI, a local proxy, etc.)." },
  { key: "watsonx", label: "IBM watsonx.ai", blurb: "IBM's foundation models via watsonx.ai." },
  { key: "watson_nlu", label: "IBM Watson NLU", blurb: "Classification only, via keyword/category matching — no generative summary or Q&A." },
  { key: "watson_disco", label: "IBM Watson Discovery", blurb: "Search + Q&A over an indexed corpus. Falls back to watsonx for a generative answer if that's also configured." },
  { key: "ollama", label: "Ollama (local, self-hosted)", blurb: "A local Ollama instance — no API key, no data leaves this network." },
];

export function AiSettingsPanel() {
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [backend, setBackend] = useState<BackendKey>("none");

  const [anthropicKey, setAnthropicKey] = useState("");
  const [anthropicModel, setAnthropicModel] = useState("");

  const [openaiKey, setOpenaiKey] = useState("");
  const [openaiBaseUrl, setOpenaiBaseUrl] = useState("");
  const [openaiModel, setOpenaiModel] = useState("");

  const [watsonxKey, setWatsonxKey] = useState("");
  const [watsonxProjectId, setWatsonxProjectId] = useState("");
  const [watsonxUrl, setWatsonxUrl] = useState("");
  const [watsonxModel, setWatsonxModel] = useState("");

  const [nluUrl, setNluUrl] = useState("");
  const [nluKey, setNluKey] = useState("");

  const [discoUrl, setDiscoUrl] = useState("");
  const [discoKey, setDiscoKey] = useState("");
  const [discoProjectId, setDiscoProjectId] = useState("");

  const [ollamaUrl, setOllamaUrl] = useState("");
  const [ollamaModel, setOllamaModel] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => {
    setLoadError(null);
    apiGet<AdminSettings>("/admin/settings")
      .then((s) => {
        setSettings(s);
        setBackend((s.ai_backend || "none") as BackendKey);
        setAnthropicModel(s.anthropic_model);
        setOpenaiBaseUrl(s.ai_base_url);
        setOpenaiModel(s.ai_model);
        setWatsonxProjectId(s.watsonx_project_id);
        setWatsonxUrl(s.watsonx_url);
        setWatsonxModel(s.watsonx_model);
        setNluUrl(s.watson_nlu_url);
        setDiscoUrl(s.watson_disco_url);
        setDiscoProjectId(s.watson_disco_project_id);
        setOllamaUrl(s.ollama_url);
        setOllamaModel(s.ollama_model);
      })
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : "Couldn't load AI settings."));
  };

  useEffect(load, []);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await apiPut<AdminSettings>("/admin/settings", {
        ai_backend: backend,
        anthropic_api_key: anthropicKey || undefined,
        anthropic_model: anthropicModel || undefined,
        ai_api_key: openaiKey || undefined,
        ai_base_url: openaiBaseUrl || undefined,
        ai_model: openaiModel || undefined,
        ibm_cloud_api_key: watsonxKey || undefined,
        watsonx_project_id: watsonxProjectId || undefined,
        watsonx_url: watsonxUrl || undefined,
        watsonx_model: watsonxModel || undefined,
        watson_nlu_url: nluUrl || undefined,
        watson_nlu_apikey: nluKey || undefined,
        watson_disco_url: discoUrl || undefined,
        watson_disco_apikey: discoKey || undefined,
        watson_disco_project_id: discoProjectId || undefined,
        ollama_url: ollamaUrl || undefined,
        ollama_model: ollamaModel || undefined,
      });
      setSettings(updated);
      setAnthropicKey("");
      setOpenaiKey("");
      setWatsonxKey("");
      setNluKey("");
      setDiscoKey("");
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save AI settings.");
    } finally {
      setBusy(false);
    }
  };

  if (loadError) {
    return (
      <div className="settings-tab-pane">
        <div className="auth-error">
          {loadError}
          <button type="button" className="link-btn" style={{ marginLeft: 8 }} onClick={load}>Retry</button>
        </div>
      </div>
    );
  }
  if (!settings) {
    return <div className="settings-tab-pane"><p className="muted">Loading…</p></div>;
  }

  const configuredFor = (key: BackendKey): boolean => {
    switch (key) {
      case "anthropic": return settings.anthropic_configured;
      case "openai": return settings.ai_openai_configured;
      case "watsonx": return settings.watsonx_configured;
      case "watson_nlu": return settings.watson_nlu_configured;
      case "watson_disco": return settings.watson_disco_configured;
      case "ollama": return true; // no credential to check — just needs to be reachable
      default: return true;
    }
  };

  return (
    <div className="settings-tab-pane">
      <p className="admin-settings-banner">
        Pick which AI provider powers document summarization, auto-classification, Q&A, and AI Agents. Enter
        credentials for as many providers as you like — switching the active one below doesn't lose the others'
        settings.
      </p>

      <form className="auth-form admin-settings-form" onSubmit={save}>
        <fieldset className="admin-fieldset">
          <legend>Active provider</legend>
          <label>
            AI backend
            <select value={backend} onChange={(e) => setBackend(e.target.value as BackendKey)}>
              {BACKENDS.map((b) => (
                <option key={b.key} value={b.key}>
                  {b.label}{b.key !== "none" && !configuredFor(b.key) ? " (not configured yet)" : ""}
                </option>
              ))}
            </select>
            <span className="muted" style={{ display: "block", fontSize: 12, marginTop: 4 }}>
              {BACKENDS.find((b) => b.key === backend)?.blurb}
            </span>
          </label>
        </fieldset>

        <fieldset className="admin-fieldset">
          <legend>Anthropic (Claude){settings.anthropic_configured && <span className="provider-badge" style={{ marginLeft: 8 }}>Configured</span>}</legend>
          <label>
            API key
            <input
              type="password"
              value={anthropicKey}
              onChange={(e) => setAnthropicKey(e.target.value)}
              placeholder={settings.anthropic_api_key_set ? "•••••••• (set — leave blank to keep)" : "sk-ant-..."}
            />
          </label>
          <label>
            Model
            <input value={anthropicModel} onChange={(e) => setAnthropicModel(e.target.value)} placeholder="claude-sonnet-5" />
          </label>
        </fieldset>

        <fieldset className="admin-fieldset">
          <legend>OpenAI (ChatGPT) or compatible{settings.ai_openai_configured && <span className="provider-badge" style={{ marginLeft: 8 }}>Configured</span>}</legend>
          <label>
            API key
            <input
              type="password"
              value={openaiKey}
              onChange={(e) => setOpenaiKey(e.target.value)}
              placeholder={settings.ai_api_key_set ? "•••••••• (set — leave blank to keep)" : "sk-..."}
            />
          </label>
          <label>
            Base URL
            <input value={openaiBaseUrl} onChange={(e) => setOpenaiBaseUrl(e.target.value)} placeholder="https://api.openai.com/v1" />
          </label>
          <label>
            Model
            <input value={openaiModel} onChange={(e) => setOpenaiModel(e.target.value)} placeholder="gpt-4o-mini" />
          </label>
        </fieldset>

        <fieldset className="admin-fieldset">
          <legend>IBM watsonx.ai{settings.watsonx_configured && <span className="provider-badge" style={{ marginLeft: 8 }}>Configured</span>}</legend>
          <label>
            IBM Cloud API key
            <input
              type="password"
              value={watsonxKey}
              onChange={(e) => setWatsonxKey(e.target.value)}
              placeholder={settings.ibm_cloud_api_key_set ? "•••••••• (set — leave blank to keep)" : ""}
            />
          </label>
          <label>
            Project ID
            <input value={watsonxProjectId} onChange={(e) => setWatsonxProjectId(e.target.value)} />
          </label>
          <label>
            Endpoint URL
            <input value={watsonxUrl} onChange={(e) => setWatsonxUrl(e.target.value)} placeholder="https://us-south.ml.cloud.ibm.com" />
          </label>
          <label>
            Model
            <input value={watsonxModel} onChange={(e) => setWatsonxModel(e.target.value)} placeholder="ibm/granite-4-h-small" />
          </label>
        </fieldset>

        <fieldset className="admin-fieldset">
          <legend>IBM Watson NLU{settings.watson_nlu_configured && <span className="provider-badge" style={{ marginLeft: 8 }}>Configured</span>}</legend>
          <label>
            Instance URL
            <input value={nluUrl} onChange={(e) => setNluUrl(e.target.value)} />
          </label>
          <label>
            API key
            <input
              type="password"
              value={nluKey}
              onChange={(e) => setNluKey(e.target.value)}
              placeholder={settings.watson_nlu_apikey_set ? "•••••••• (set — leave blank to keep)" : ""}
            />
          </label>
        </fieldset>

        <fieldset className="admin-fieldset">
          <legend>IBM Watson Discovery{settings.watson_disco_configured && <span className="provider-badge" style={{ marginLeft: 8 }}>Configured</span>}</legend>
          <label>
            Instance URL
            <input value={discoUrl} onChange={(e) => setDiscoUrl(e.target.value)} />
          </label>
          <label>
            API key
            <input
              type="password"
              value={discoKey}
              onChange={(e) => setDiscoKey(e.target.value)}
              placeholder={settings.watson_disco_apikey_set ? "•••••••• (set — leave blank to keep)" : ""}
            />
          </label>
          <label>
            Project ID
            <input value={discoProjectId} onChange={(e) => setDiscoProjectId(e.target.value)} />
          </label>
        </fieldset>

        <fieldset className="admin-fieldset">
          <legend>Ollama (local)</legend>
          <label>
            Instance URL
            <input value={ollamaUrl} onChange={(e) => setOllamaUrl(e.target.value)} placeholder="http://localhost:11434" />
          </label>
          <label>
            Model
            <input value={ollamaModel} onChange={(e) => setOllamaModel(e.target.value)} placeholder="llama3" />
          </label>
        </fieldset>

        {error && <div className="auth-error">{error}</div>}
        {saved && <div className="auth-success">Saved.</div>}
        <button type="submit" disabled={busy}>
          {busy ? "Saving..." : "Save"}
        </button>
      </form>
    </div>
  );
}
