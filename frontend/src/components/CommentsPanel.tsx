import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPatch, apiPost } from "../api/client";
import type { Comment, DriveItem } from "../types";
import { Modal } from "./Modal";
import { formatDate } from "../utils";
import { Icon } from "../icons";

export function CommentsPanelContent({ item, onChange }: { item: DriveItem; onChange: () => void }) {
  const [comments, setComments] = useState<Comment[]>([]);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    apiGet<Comment[]>(`/resources/${item.id}/comments?resource_type=${item.type}`)
      .then(setComments)
      .catch((err) => setError(err instanceof Error ? err.message : "Couldn't load comments."));
  };

  useEffect(load, [item.id]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = body.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      await apiPost(`/resources/${item.id}/comments`, { resource_type: item.type, body: trimmed });
      setBody("");
      load();
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't post that comment.");
    } finally {
      setBusy(false);
    }
  };

  const toggleResolve = async (c: Comment) => {
    setError(null);
    try {
      await apiPatch(`/comments/${c.id}`, { resolved: !c.resolved_at });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't update that comment.");
    }
  };

  const remove = async (c: Comment) => {
    setError(null);
    try {
      await apiDelete(`/comments/${c.id}`);
      load();
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't delete that comment.");
    }
  };

  return (
    <>
      <div className="comment-list">
        {comments.length === 0 && <p className="muted">No comments yet.</p>}
        {comments.map((c) => (
          <div key={c.id} className={"comment-row" + (c.resolved_at ? " resolved" : "")}>
            <div className="comment-row-header">
              <span className="comment-author">{c.created_by}</span>
              <span className="muted">{formatDate(c.created_at)}</span>
            </div>
            <p className="comment-body">{c.body}</p>
            <div className="comment-row-actions">
              <button className="link-btn" onClick={() => toggleResolve(c)}>
                {c.resolved_at ? "Reopen" : "Resolve"}
              </button>
              <button className="link-btn" onClick={() => remove(c)}>
                Delete
              </button>
              {c.resolved_at && <span className="muted">Resolved</span>}
            </div>
          </div>
        ))}
      </div>

      {error && <div className="auth-error" style={{ marginBottom: 12 }}>{error}</div>}

      <form onSubmit={submit} className="comment-form">
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Add a comment..."
          rows={3}
          className="text-dialog-input"
        />
        <div className="modal-actions">
          <button type="submit" disabled={!body.trim() || busy}>
            <Icon name="message" size={14} />
            Comment
          </button>
        </div>
      </form>
    </>
  );
}

export function CommentsPanel({ item, onClose, onChange }: { item: DriveItem; onClose: () => void; onChange: () => void }) {
  return (
    <Modal title={`Comments — ${item.name}`} onClose={onClose} width={440}>
      <CommentsPanelContent item={item} onChange={onChange} />
    </Modal>
  );
}
