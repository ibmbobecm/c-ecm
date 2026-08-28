/**
 * AiPanel — AI document intelligence sidebar panel.
 * Shows a summary of the current file, allows Q&A, and displays
 * suggested metadata values.
 */
import { useState } from "react";
import { apiPost } from "../api/client";
import type { FileItem } from "../types";
import { Icon } from "../icons";

type AiSummaryOut = { summary: string };
type AiAskOut = { answer: string };

export function AiPanel({ file }: { file: FileItem }) {
  const [summary, setSummary] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [answerLoading, setAnswerLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="ai-panel">
      <div className="ai-panel-section">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontWeight: 600, fontSize: "var(--text-sm)" }}>
            <Icon name="star" size={14} /> AI Summary
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

      <div className="ai-panel-section" style={{ borderTop: "1px solid var(--border)", paddingTop: 14 }}>
        <span style={{ fontWeight: 600, fontSize: "var(--text-sm)", display: "block", marginBottom: 8 }}>
          <Icon name="search" size={14} /> Ask about this document
        </span>
        <div style={{ display: "flex", gap: 6 }}>
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && askQuestion()}
            placeholder="e.g. What is the contract date?"
            style={{ flex: 1, padding: "7px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", fontSize: "var(--text-sm)" }}
          />
          <button className="btn-primary" onClick={askQuestion} disabled={answerLoading || !question.trim()}>
            {answerLoading ? "…" : "Ask"}
          </button>
        </div>
        {answer && (
          <div style={{ marginTop: 10, padding: 10, background: "var(--bg-secondary)", borderRadius: "var(--radius-sm)", fontSize: "var(--text-sm)", color: "var(--text)", lineHeight: 1.6 }}>
            {answer}
          </div>
        )}
      </div>
    </div>
  );
}
