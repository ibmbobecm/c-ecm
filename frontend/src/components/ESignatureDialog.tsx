import { useEffect, useState } from "react";
import { apiGet, apiPost, ApiError } from "../api/client";
import type { ESignatureRequest, FileItem } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";
import { formatDate } from "../utils";

type SignerDraft = { name: string; email: string };

const STATUS_LABEL: Record<string, string> = {
  sent: "Sent",
  delivered: "Opened",
  completed: "Completed",
  declined: "Declined",
  voided: "Voided",
};

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "completed" ? "success" : status === "declined" || status === "voided" ? "error" : "progress";
  return <span className={`flash flash-${cls}`} style={{ margin: 0, display: "inline-block", padding: "2px 8px", fontSize: 11 }}>{STATUS_LABEL[status] ?? status}</span>;
}

export function ESignatureDialog({ file, onClose }: { file: FileItem; onClose: () => void }) {
  const [requests, setRequests] = useState<ESignatureRequest[]>([]);
  const [signers, setSigners] = useState<SignerDraft[]>([{ name: "", email: "" }]);
  const [subject, setSubject] = useState(`Please sign: ${file.name}`);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [voidingId, setVoidingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    apiGet<ESignatureRequest[]>(`/files/${file.id}/esignature`)
      .then(setRequests)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Couldn't load signature requests."));
  };

  useEffect(load, [file.id]);

  const updateSigner = (i: number, field: keyof SignerDraft, value: string) => {
    setSigners((prev) => prev.map((s, idx) => (idx === i ? { ...s, [field]: value } : s)));
  };

  const addSigner = () => setSigners((prev) => [...prev, { name: "", email: "" }]);
  const removeSigner = (i: number) => setSigners((prev) => prev.filter((_, idx) => idx !== i));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiPost(`/files/${file.id}/esignature`, {
        resource_type: "file",
        signers: signers
          .filter((s) => s.name.trim() && s.email.trim())
          .map((s, i) => ({ name: s.name.trim(), email: s.email.trim(), routing_order: i + 1 })),
        subject,
        message: message || null,
      });
      setSigners([{ name: "", email: "" }]);
      setMessage("");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't send this document for signature.");
    } finally {
      setBusy(false);
    }
  };

  const voidRequest = async (id: string) => {
    setVoidingId(id);
    setError(null);
    try {
      await apiPost(`/esignature/requests/${id}/void`);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't void that request.");
    } finally {
      setVoidingId(null);
    }
  };

  const canSubmit = signers.some((s) => s.name.trim() && s.email.trim()) && subject.trim();

  return (
    <Modal title={`Send for signature — ${file.name}`} onClose={onClose} width={480}>
      {requests.length > 0 && (
        <div className="version-list" style={{ marginBottom: 18 }}>
          {requests.map((r) => (
            <div key={r.id} className="version-row" style={{ alignItems: "flex-start" }}>
              <div>
                <div className="version-primary">
                  {r.signers.map((s) => s.name).join(", ")}
                  <StatusBadge status={r.status} />
                </div>
                <div className="muted">
                  Sent {formatDate(r.created_at)} by {r.requested_by}
                  {r.completed_at ? ` · completed ${formatDate(r.completed_at)}` : ""}
                  {r.signed_version_number ? ` · saved as version ${r.signed_version_number}` : ""}
                </div>
              </div>
              {!["completed", "declined", "voided"].includes(r.status) && (
                <button className="link-btn" disabled={voidingId === r.id} onClick={() => voidRequest(r.id)}>
                  {voidingId === r.id ? "Voiding..." : "Void"}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <form onSubmit={submit} className="auth-form">
        <label>
          Subject
          <input value={subject} onChange={(e) => setSubject(e.target.value)} required />
        </label>
        <label>
          Message (optional)
          <input value={message} onChange={(e) => setMessage(e.target.value)} />
        </label>

        <p className="muted" style={{ margin: "4px 0 0", fontSize: 12 }}>
          Signers (in order)
        </p>
        {signers.map((s, i) => (
          <div key={i} style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input placeholder="Name" value={s.name} onChange={(e) => updateSigner(i, "name", e.target.value)} style={{ flex: 1 }} />
            <input placeholder="Email" type="email" value={s.email} onChange={(e) => updateSigner(i, "email", e.target.value)} style={{ flex: 1 }} />
            {signers.length > 1 && (
              <button type="button" className="icon-btn" onClick={() => removeSigner(i)} aria-label="Remove signer">
                <Icon name="close" size={14} />
              </button>
            )}
          </div>
        ))}
        <button type="button" className="link-btn" style={{ alignSelf: "flex-start" }} onClick={addSigner}>
          + Add another signer
        </button>

        {error && <div className="auth-error">{error}</div>}
        <button type="submit" disabled={!canSubmit || busy}>
          {busy ? "Sending..." : "Send for signature"}
        </button>
      </form>
    </Modal>
  );
}
