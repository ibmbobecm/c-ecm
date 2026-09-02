import { useEffect, useState } from "react";
import { apiGet } from "../api/client";
import { useConnections } from "../contexts/ConnectionsContext";
import type { FolderContents } from "../types";
import { Modal } from "./Modal";
import { Icon, fileTypeIconName } from "../icons";

export type PickedResource = {
  connectionId: string;
  connectionName: string;
  resourceId: string;
  resourceType: "file" | "folder";
  resourceName: string;
};

// A browser dialog for picking one file or folder to scope something to
// (currently: a webhook) -- modeled on MoveDialog's breadcrumb/folder-list
// pattern, extended to also list files (selectable directly) and to let
// the connection itself be chosen, since this isn't tied to whatever
// connection happens to be active in the main Drive view.
export function ResourcePickerDialog({
  onClose,
  onSelect,
}: {
  onClose: () => void;
  onSelect: (picked: PickedResource) => void;
}) {
  const { connections, activeConnectionId } = useConnections();
  const [connectionId, setConnectionId] = useState(activeConnectionId ?? connections[0]?.id ?? "");
  const [folderId, setFolderId] = useState<string | null>(null);
  const [contents, setContents] = useState<FolderContents | null>(null);

  useEffect(() => {
    if (!connectionId) return;
    let cancelled = false;
    setContents(null);
    apiGet<FolderContents>("/folders/contents", { folder_id: folderId ?? undefined }, connectionId)
      .then((c) => {
        if (!cancelled) setContents(c);
      })
      .catch(() => {
        if (!cancelled) setContents(null);
      });
    return () => {
      cancelled = true;
    };
  }, [connectionId, folderId]);

  const connectionName = connections.find((c) => c.id === connectionId)?.display_name ?? "";
  const currentFolderName = contents?.breadcrumb[contents.breadcrumb.length - 1]?.name ?? "My Drive";

  const selectFolder = () => {
    if (!folderId) return;
    onSelect({ connectionId, connectionName, resourceId: folderId, resourceType: "folder", resourceName: currentFolderName });
    onClose();
  };

  const selectFile = (fileId: string, fileName: string) => {
    onSelect({ connectionId, connectionName, resourceId: fileId, resourceType: "file", resourceName: fileName });
    onClose();
  };

  return (
    <Modal title="Select a file or folder" onClose={onClose} width={460}>
      <div className="auth-form" style={{ marginBottom: 14 }}>
        <label>
          Connection
          <select
            value={connectionId}
            onChange={(e) => {
              setConnectionId(e.target.value);
              setFolderId(null);
            }}
          >
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.display_name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="picker-breadcrumb">
        {contents?.breadcrumb.map((b, i) => (
          <span key={b.id ?? "root"} className="picker-breadcrumb-segment">
            {i > 0 && <Icon name="chevron-right" size={12} className="picker-breadcrumb-sep" />}
            <button type="button" className="link-btn" onClick={() => setFolderId(b.id)}>
              {b.name}
            </button>
          </span>
        ))}
      </div>

      <div className="move-folder-list picker-list">
        {!contents && (
          <div className="picker-empty">
            <p className="muted">Loading...</p>
          </div>
        )}
        {contents?.folders.map((f) => (
          <button key={f.id} className="move-folder-row" onClick={() => setFolderId(f.id)}>
            <Icon name="folder" size={18} />
            {f.name}
          </button>
        ))}
        {contents?.files.map((f) => (
          <button key={f.id} className="move-folder-row" onClick={() => selectFile(f.id, f.name)}>
            <Icon name={fileTypeIconName(f.content_type, f.name)} size={18} />
            {f.name}
          </button>
        ))}
        {contents && contents.folders.length === 0 && contents.files.length === 0 && (
          <div className="picker-empty">
            <Icon name="folder" size={28} />
            <p className="muted">This folder is empty.</p>
          </div>
        )}
      </div>

      <div className="modal-actions">
        <button type="button" className="secondary" onClick={onClose}>
          Cancel
        </button>
        <button type="button" onClick={selectFolder} disabled={!folderId} title={folderId ? undefined : "Open a folder first"}>
          Select "{currentFolderName}"
        </button>
      </div>
    </Modal>
  );
}
