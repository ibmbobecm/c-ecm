import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPost } from "../api/client";
import type { DriveItem, ShareLink } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";
import { formatDate } from "../utils";

export function ShareLinkDialogContent({ item }: { item: DriveItem }) {
  const [links, setLinks] = useState<ShareLink[]>([]);
  const [role, setRole] = useState<"view" | "comment" | "edit">("view");
  const [expiresInDays, setExpiresInDays] = useState<string>("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const load = () => {
    apiGet<ShareLink[]>(`/resources/${item.id}/share-links`, { resource_type: item.type })
      .then(setLinks)
      .catch(() => {});
  };

  useEffect(load, [item.id]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiPost(`/resources/${item.id}/share-links`, {
        resource_type: item.type,
        role,
        expires_in_days: expiresInDays ? Number(expiresInDays) : null,
        password: password || null,
      });
      setPassword("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create a share link.");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (link: ShareLink) => {
    setError(null);
    try {
      await apiDelete(`/share-links/${item.id}/${link.id}?resource_type=${item.type}`);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't revoke that link.");
    }
  };

  const copy = async (link: ShareLink) => {
    try {
      await navigator.clipboard.writeText(link.url);
      setCopiedId(link.id);
      setTimeout(() => setCopiedId(null), 2000);
    } catch {
      // clipboard access denied — the link is still visible to select/copy manually
    }
  };

  return (
    <>
      {links.length > 0 && (
        <div className="share-link-list">
          {links.map((l) => (
            <div key={l.id} className="share-link-row">
              <div className="share-link-row-main">
                <Icon name="link" size={14} />
                <span className="share-link-url" title={l.url}>
                  {l.url}
                </span>
              </div>
              <div className="share-link-row-meta muted">
                {l.role} {l.password_protected ? "· password protected" : ""}
                {l.expires_at ? ` · expires ${formatDate(l.expires_at)}` : " · no expiry"}
              </div>
              <div className="share-link-row-actions">
                <button className="link-btn" onClick={() => copy(l)}>
                  {copiedId === l.id ? "Copied!" : "Copy"}
                </button>
                <button className="link-btn" onClick={() => revoke(l)}>
                  Revoke
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <form onSubmit={create} className="auth-form" style={{ marginTop: links.length > 0 ? 16 : 0 }}>
        <label>
          Access
          <select value={role} onChange={(e) => setRole(e.target.value as typeof role)}>
            <option value="view">Can view</option>
            <option value="comment">Can comment</option>
            <option value="edit">Can edit</option>
          </select>
        </label>
        <label>
          Expires in (days, optional)
          <input
            type="number"
            min={1}
            max={365}
            value={expiresInDays}
            onChange={(e) => setExpiresInDays(e.target.value)}
            placeholder="Never"
          />
        </label>
        <label>
          Password (optional)
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="auth-error">{error}</div>}
        <button type="submit" disabled={busy}>
          {busy ? "Creating..." : "Create link"}
        </button>
      </form>
    </>
  );
}

export function ShareLinkDialog({ item, onClose }: { item: DriveItem; onClose: () => void }) {
  return (
    <Modal title={`Share "${item.name}"`} onClose={onClose} width={440}>
      <ShareLinkDialogContent item={item} />
    </Modal>
  );
}
