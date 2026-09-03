/**
 * AiPanel — AI document intelligence sidebar panel.
 * Shows a summary of the current file, allows Q&A, displays
 * suggested metadata values, and provides AI-powered workflow routing.
 * Backend-aware: the small badge next to each section header reflects
 * whichever AI provider is active (Admin Settings → AI Provider), not just
 * IBM Watson.
 */
import { useState } from "react";
import { apiPost } from "../api/client";
import type { FileItem } from "../types";
import { Icon } from "../icons";

type AiSummaryOut = { summary: string };
type AiAskOut = { answer: string };
type AiSuggestWorkflowOut = {
  suggested_workflow_id: string | null;
  suggested_workflow_name: string | null;
  confidence: "high" | "medium" | "low";
};

// Single source of truth for backend display names/branding — used to live
// as two independent conditionals (a startsWith("watson") check here, plus
// a separate one for Q&A placeholder copy further down) that had to be kept
// in sync by hand; a future backend name that satisfied one startsWith
// check could silently fall through the other to the wrong label.
const BACKEND_BADGES: Record<string, { label: string; poweredBy: string; bg: string; color: string }> = {
  anthropic: { label: "Claude", poweredBy: "Anthropic Claude", bg: "#d97757", color: "#fff" },
  openai: { label: "ChatGPT", poweredBy: "OpenAI", bg: "#10a37f", color: "#fff" },
  ollama: { label: "Ollama", poweredBy: "a local Ollama model", bg: "#374151", color: "#fff" },
  watsonx: { label: "IBM Watson", poweredBy: "IBM watsonx.ai", bg: "#0f62fe", color: "#fff" },
  watson_nlu: { label: "IBM Watson", poweredBy: "Watson NLU", bg: "#0f62fe", color: "#fff" },
  watson_disco: { label: "IBM Watson", poweredBy: "Watson Discovery", bg: "#0f62fe", color: "#fff" },
};

/** Tiny badge naming whichever AI provider is currently active */
function BackendBadge({ backend }: { backend: string }) {
  const info = BACKEND_BADGES[backend];
  if (!info) return null;
  return (
    <span
      title={`Powered by ${info.poweredBy}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        padding: "2px 6px",
        borderRadius: 4,
        background: info.bg,
        color: info.color,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.03em",
        marginLeft: 6,
        verticalAlign: "middle",
      }}
    >
      {info.label}
    </span>
  );
}

export function AiPanel({ file, aiBackend }: { file: FileItem; aiBackend?: string }) {
  const backend = aiBackend ?? "";

  const [summary, setSummary] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [answerLoading, setAnswerLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Workflow suggestion
  const [wfSuggestion, setWfSuggestion] = useState<AiSuggestWorkflowOut | null>(null);
  const [wfLoading, setWfLoading] = useState(false);
  const [wfDismissed, setWfDismissed] = useState(false);
  const [wfStarted, setWfStarted] = useState(false);
  const [wfStarting, setWfStarting] = useState(false);
  const [wfStartError, setWfStartError] = useState<string | null>(null);

  const loadSummary = async () => {
    setSummaryLoading(true);
    setError(null);
    try {
      const res = await apiPost<AiSummaryOut>(`/files/${file.id}/ai/summarize`);
      setSummary(res.summary);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "AI unavailable";
      setError(msg.includes("503") ? "AI is not configured on this server." : msg);
    } finally {
      setSummaryLoading(false);
    }
  };

  const askQuestion = async () => {
    if (!question.trim()) return;
    setAnswerLoading(true);
    setAnswer(null);
    setError(null);
    try {
      const res = await apiPost<AiAskOut>(`/files/${file.id}/ai/ask`, { question });
      setAnswer(res.answer);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "AI unavailable";
      setError(msg.includes("503") ? "AI is not configured on this server." : msg);
    } finally {
      setAnswerLoading(false);
    }
  };

  const suggestWorkflow = async () => {
    setWfLoading(true);
    setWfSuggestion(null);
    setWfDismissed(false);
    setWfStarted(false);
    setWfStartError(null);
    try {
      const res = await apiPost<AiSuggestWorkflowOut>(`/files/${file.id}/ai/suggest_workflow`);
      setWfSuggestion(res);
    } catch {
      // silently ignore — workflow suggestion is best-effort
    } finally {
      setWfLoading(false);
    }
  };

  const startWorkflow = async () => {
    if (!wfSuggestion?.suggested_workflow_id) return;
    setWfStarting(true);
    setWfStartError(null);
    try {
      await apiPost("/workflows/instances", {
        definition_id: wfSuggestion.suggested_workflow_id,
        resource_id: file.id,
        resource_type: "file",
        comment: "Started from AI workflow suggestion",
      });
      setWfStarted(true);
    } catch (e: unknown) {
      setWfStartError(e instanceof Error ? e.message : "Couldn't start this workflow.");
    } finally {
      setWfStarting(false);
    }
  };

  const confidenceBadgeStyle = (confidence: string): React.CSSProperties => {
    const colors: Record<string, { bg: string; color: string }> = {
      high: { bg: "#d4edda", color: "#155724" },
      medium: { bg: "#fff3cd", color: "#856404" },
      low: { bg: "#f8d7da", color: "#721c24" },
    };
    const c = colors[confidence] ?? colors.low;
    return {
      display: "inline-block",
      padding: "1px 6px",
      borderRadius: 4,
      fontSize: 10,
      fontWeight: 700,
      background: c.bg,
      color: c.color,
      marginLeft: 6,
    };
  };

  return (
    <div className="ai-panel">
      {/* ---- Summary ---- */}
      <div className="ai-panel-section">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontWeight: 600, fontSize: "var(--text-sm)" }}>
            <Icon name="star" size={14} /> AI Summary
            <BackendBadge backend={backend} />
          </span>
          {!summary && (
            <button className="link-btn" onClick={loadSummary} disabled={summaryLoading}>
              {summaryLoading ? "Generating…" : "Generate"}
            </button>
          )}
        </div>
        {error && <p style={{ color: "var(--danger)", fontSize: "var(--text-xs)" }}>{error}</p>}
        {summary && (
          <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", lineHeight: 1.6, margin: 0 }}>
            {summary}
          </p>
        )}
        {!summary && !summaryLoading && !error && (
          <p style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
            Click Generate to summarize this document with AI.
          </p>
        )}
      </div>

      {/* ---- Q&A ---- */}
      <div className="ai-panel-section">
        <span style={{ fontWeight: 600, fontSize: "var(--text-sm)", display: "block", marginBottom: 8 }}>
          <Icon name="search" size={14} /> Ask about this document
          <BackendBadge backend={backend} />
        </span>
        <div style={{ display: "flex", gap: 6 }}>
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && askQuestion()}
            placeholder={
              backend === "watson_disco"
                ? "Ask Watson Discovery…"
                : "e.g. What is the contract date?"
            }
            style={{
              flex: 1,
              padding: "7px 10px",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border)",
              background: "var(--surface)",
              color: "var(--text)",
              fontSize: "var(--text-sm)",
            }}
          />
          <button className="btn-primary" onClick={askQuestion} disabled={answerLoading || !question.trim()}>
            {answerLoading ? "…" : "Ask"}
          </button>
        </div>
        {answer && (
          <div
            style={{
              marginTop: 10,
              padding: 10,
              background: "var(--bg-secondary)",
              borderRadius: "var(--radius-sm)",
              fontSize: "var(--text-sm)",
              color: "var(--text)",
              lineHeight: 1.6,
            }}
          >
            {answer}
          </div>
        )}
      </div>

      {/* ---- AI Workflow Routing ---- */}
      <div className="ai-panel-section">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontWeight: 600, fontSize: "var(--text-sm)" }}>
            <Icon name="workflow" size={14} /> Workflow Suggestion
            <BackendBadge backend={backend} />
          </span>
          <button className="link-btn" onClick={suggestWorkflow} disabled={wfLoading}>
            {wfLoading ? "Analysing…" : "Suggest"}
          </button>
        </div>

        {wfSuggestion && !wfDismissed && !wfStarted && (
          wfSuggestion.suggested_workflow_name ? (
            <div
              style={{
                padding: 10,
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)",
                fontSize: "var(--text-sm)",
              }}
            >
              <div style={{ marginBottom: 6 }}>
                Detected workflow:{" "}
                <strong>{wfSuggestion.suggested_workflow_name}</strong>
                <span style={confidenceBadgeStyle(wfSuggestion.confidence)}>
                  {wfSuggestion.confidence} confidence
                </span>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  className="btn-primary"
                  style={{ fontSize: "var(--text-xs)", padding: "4px 10px" }}
                  onClick={startWorkflow}
                  disabled={wfStarting}
                >
                  {wfStarting ? "Starting…" : "Start Workflow"}
                </button>
                <button
                  className="btn-secondary"
                  style={{ fontSize: "var(--text-xs)", padding: "4px 10px" }}
                  onClick={() => setWfDismissed(true)}
                  disabled={wfStarting}
                >
                  Dismiss
                </button>
              </div>
              {wfStartError && (
                <p style={{ fontSize: "var(--text-xs)", color: "var(--danger)", margin: "6px 0 0" }}>
                  {wfStartError}
                </p>
              )}
            </div>
          ) : (
            <p style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
              No matching workflow found for this document.
            </p>
          )
        )}

        {wfStarted && wfSuggestion?.suggested_workflow_name && (
          <p style={{ fontSize: "var(--text-xs)", color: "var(--success)" }}>
            ✓ Submitted to &ldquo;{wfSuggestion.suggested_workflow_name}&rdquo; — track its progress in the
            Workflows panel.
          </p>
        )}

        {!wfSuggestion && !wfLoading && (
          <p style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
            Click Suggest to auto-detect the right approval workflow for this document.
          </p>
        )}
      </div>
    </div>
  );
}
