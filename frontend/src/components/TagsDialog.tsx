import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPost } from "../api/client";
import type { DriveItem, Tag } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";

const SWATCHES = ["#5B8DEF", "#1E8E3E", "#E8710A", "#D93025", "#9334E6", "#00838F", "#D6409F", "#5F6368"];

export function TagsDialog({ item, onClose, onChange }: { item: DriveItem; onClose: () => void; onChange: () => void }) {
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [resourceTags, setResourceTags] = useState<Tag[]>([]);
  const [newName, setNewName] = useState("");
  const [newColor, setNewColor] = useState(SWATCHES[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    apiGet<Tag[]>("/tags").then(setAllTags).catch(() => {});
    apiGet<Tag[]>(`/resources/${item.id}/tags`).then(setResourceTags).catch(() => {});
  };

  useEffect(load, [item.id]);

  const attachedIds = new Set(resourceTags.map((t) => t.id));
  const available = allTags.filter((t) => !attachedIds.has(t.id));

  const attach = async (tagId: string) => {
    setBusy(true);
    setError(null);
    try {
      await apiPost(`/resources/${item.id}/tags`, { resource_type: item.type, tag_id: tagId });
      load();
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add that tag.");
    } finally {
      setBusy(false);
    }
  };

  const detach = async (tagId: string) => {
    setBusy(true);
    setError(null);
    try {
      await apiDelete(`/resources/${item.id}/tags/${tagId}`);
      load();
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't remove that tag.");
    } finally {
      setBusy(false);
    }
  };

  const createAndAttach = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    setError(null);
    try {
      const tag = await apiPost<Tag>("/tags", { name, color: newColor });
      await apiPost(`/resources/${item.id}/tags`, { resource_type: item.type, tag_id: tag.id });
      setNewName("");
      load();
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create that tag.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`Tags — ${item.name}`} onClose={onClose} width={380}>
      {resourceTags.length > 0 && (
        <div className="tag-chip-row">
          {resourceTags.map((t) => (
            <span key={t.id} className="tag-chip" style={{ "--tag-color": t.color } as React.CSSProperties}>
              {t.name}
              <button onClick={() => detach(t.id)} disabled={busy} aria-label={`Remove tag ${t.name}`}>
                <Icon name="close" size={11} />
              </button>
            </span>
          ))}
        </div>
      )}

      {available.length > 0 && (
        <>
          <p className="muted" style={{ margin: "14px 0 6px", fontSize: 12 }}>
            Add existing tag
          </p>
          <div className="tag-chip-row">
            {available.map((t) => (
              <button
                key={t.id}
                className="tag-chip tag-chip-add"
                style={{ "--tag-color": t.color } as React.CSSProperties}
                onClick={() => attach(t.id)}
                disabled={busy}
              >
                {t.name}
              </button>
            ))}
          </div>
        </>
      )}

      {error && <div className="auth-error" style={{ marginTop: 12 }}>{error}</div>}

      <form onSubmit={createAndAttach} style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <label>
          New tag
          <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. Contract, Urgent" />
        </label>
        <div className="tag-swatch-row">
          {SWATCHES.map((c) => (
            <button
              key={c}
              type="button"
              className={"tag-swatch" + (c === newColor ? " active" : "")}
              style={{ background: c }}
              onClick={() => setNewColor(c)}
              aria-label={`Color ${c}`}
            />
          ))}
        </div>
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Done
          </button>
          <button type="submit" disabled={!newName.trim() || busy}>
            Create &amp; add
          </button>
        </div>
      </form>
    </Modal>
  );
}
