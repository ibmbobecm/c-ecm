import { useEffect, useState } from "react";
import { apiGet } from "../api/client";
import type { DriveItem, FolderContents } from "../types";
import { Modal } from "./Modal";
import { Icon } from "../icons";

export function MoveDialog({
  items,
  onClose,
  onMove,
}: {
  items: DriveItem[];
  onClose: () => void;
  onMove: (targetFolderId: string | null) => void;
}) {
  const [folderId, setFolderId] = useState<string | null>(null);
  const [contents, setContents] = useState<FolderContents | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Guard against out-of-order responses: if the user navigates into a
    // folder, then quickly into another before the first request resolves,
    // an older (slower — this app proxies backends with very different
    // latencies) response arriving after a newer one would silently show
    // the wrong folder's contents while folderId itself has already moved
    // on. A stale response ends up looking like "here's what's inside
    // where you are," and a subsequent click descends from that wrong
    // listing — this is what "Move here" actually targets.
    let cancelled = false;
    apiGet<FolderContents>("/folders/contents", { folder_id: folderId ?? undefined })
      .then((c) => {
        if (!cancelled) setContents(c);
      })
      .catch(() => {
        if (!cancelled) setContents(null);
      });
    return () => {
      cancelled = true;
    };
  }, [folderId]);

  const movedFolderIds = new Set(items.filter((i) => i.type === "folder").map((i) => i.id));
  const isCurrentLocationInvalid = folderId !== null && movedFolderIds.has(folderId);
  const title = items.length === 1 ? `Move "${items[0].name}"` : `Move ${items.length} items`;

  return (
    <Modal title={title} onClose={onClose} width={420}>
      <div className="move-breadcrumb">
        {contents?.breadcrumb.map((b) => (
          <button key={b.id ?? "root"} className="link-btn" onClick={() => setFolderId(b.id)}>
            {b.name} /
          </button>
        ))}
      </div>

      <div className="move-folder-list">
        {contents?.folders
          .filter((f) => !movedFolderIds.has(f.id))
          .map((f) => (
            <button key={f.id} className="move-folder-row" onClick={() => setFolderId(f.id)}>
              <Icon name="folder" size={18} />
              {f.name}
            </button>
          ))}
        {contents && contents.folders.length === 0 && <p className="muted">No subfolders here.</p>}
      </div>

      {error && <div className="auth-error">{error}</div>}

      <div className="modal-actions">
        <button type="button" className="secondary" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          disabled={isCurrentLocationInvalid}
          onClick={() => {
            if (isCurrentLocationInvalid) {
              setError("Can't move a folder into itself or one of the folders being moved.");
              return;
            }
            onMove(folderId);
            onClose();
          }}
        >
          Move here
        </button>
      </div>
    </Modal>
  );
}
