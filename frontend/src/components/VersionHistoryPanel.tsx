import { useEffect, useState } from "react";
import { apiGet, apiPost, downloadFile, ApiError } from "../api/client";
import type { FileItem, FileVersion } from "../types";
import { Modal } from "./Modal";
import { formatBytes, formatDate } from "../utils";

export function VersionHistoryContent({
  file,
  canEdit,
  onRestored,
}: {
  file: FileItem;
  canEdit: boolean;
  onRestored: () => void;
}) {
  const [versions, setVersions] = useState<FileVersion[]>([]);
  const [busyVersion, setBusyVersion] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    apiGet<FileVersion[]>(`/files/${file.id}/versions`)
      .then(setVersions)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Couldn't load version history."));
  };

  useEffect(load, [file.id]);

  const restore = async (versionId: string) => {
    setBusyVersion(versionId);
    setError(null);
    try {
      await apiPost(`/files/${file.id}/versions/${versionId}/restore`);
      load();
      onRestored();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't restore that version.");
    } finally {
      setBusyVersion(null);
    }
  };

  return (
    <>
      {error && <div className="auth-error" style={{ marginBottom: 12 }}>{error}</div>}
      <div className="version-list">
        {versions.map((v) => (
          <div key={v.id} className="version-row">
            <div>
              <div className="version-primary">
                Version {v.version_number}
                {v.is_current && <span className="version-current-badge">Current</span>}
              </div>
              <div className="muted">
                {formatDate(v.updated_at)} · {formatBytes(v.size_bytes)}
              </div>
            </div>
            <div className="version-actions">
              <button
                className="link-btn"
                onClick={() => downloadFile(`/files/${file.id}/versions/${v.id}/download`, file.name)}
              >
                Download
              </button>
              {canEdit && !v.is_current && (
                <button
                  className="link-btn"
                  disabled={busyVersion === v.id}
                  onClick={() => restore(v.id)}
                >
                  {busyVersion === v.id ? "Restoring..." : "Restore"}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

export function VersionHistoryPanel({
  file,
  canEdit,
  onClose,
  onRestored,
}: {
  file: FileItem;
  canEdit: boolean;
  onClose: () => void;
  onRestored: () => void;
}) {
  return (
    <Modal title={`Version history — ${file.name}`} onClose={onClose} width={480}>
      <VersionHistoryContent file={file} canEdit={canEdit} onRestored={onRestored} />
    </Modal>
  );
}
