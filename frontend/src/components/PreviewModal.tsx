import { useEffect, useState } from "react";
import { API_BASE, getAuthToken } from "../api/client";
import type { FileItem } from "../types";
import { fileKind, formatBytes } from "../utils";
import { Icon } from "../icons";

// Extend file kind to support video and audio
function extendedKind(contentType: string | null, name: string): "image" | "pdf" | "video" | "audio" | "other" {
  const base = fileKind(contentType, name);
  if (base !== "other") return base;
  if (contentType?.startsWith("video/") || /\.(mp4|mov|avi|mkv|webm|ogv)$/i.test(name)) return "video";
  if (contentType?.startsWith("audio/") || /\.(mp3|wav|flac|aac|ogg|m4a)$/i.test(name)) return "audio";
  return "other";
}

export function PreviewModal({
  file,
  onClose,
  onDownload,
}: {
  file: FileItem;
  onClose: () => void;
  onDownload: () => void;
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const kind = extendedKind(file.content_type, file.name);

  useEffect(() => {
    if (kind === "other") return;
    let revoke: string | null = null;
    let cancelled = false;

    (async () => {
      try {
        const token = getAuthToken();
        const res = await fetch(`${API_BASE}/files/${file.id}/download`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        revoke = url;
        setBlobUrl(url);
      } catch {
        if (!cancelled) setError("Couldn't load a preview for this file.");
      }
    })();

    return () => {
      cancelled = true;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [file.id, kind]);

  return (
    <div className="modal-overlay" onMouseDown={onClose}>
      <div className="preview-card" onMouseDown={(e) => e.stopPropagation()}>
        <div className="preview-header">
          <span className="preview-title" title={file.name}>
            {file.name}
          </span>
          <div className="preview-actions">
            <button onClick={onDownload}>
              <Icon name="download" size={15} />
              Download
            </button>
            <button className="modal-close" onClick={onClose} aria-label="Close">
              <Icon name="close" size={18} />
            </button>
          </div>
        </div>
        <div className="preview-body">
          {kind === "other" && (
            <div className="preview-unsupported">
              <div className="preview-unsupported-icon">
                <Icon name="file-generic" size={48} />
              </div>
              <p>No preview available for this file type.</p>
              <p className="muted">{formatBytes(file.size_bytes)}</p>
            </div>
          )}
          {kind !== "other" && error && <div className="preview-unsupported">{error}</div>}
          {kind === "image" && blobUrl && <img src={blobUrl} alt={file.name} className="preview-image" />}
          {kind === "pdf" && blobUrl && (
            <iframe src={blobUrl} title={file.name} className="preview-pdf" />
          )}
          {kind === "video" && blobUrl && (
            // eslint-disable-next-line jsx-a11y/media-has-caption
            <video src={blobUrl} controls className="preview-video" />
          )}
          {kind === "audio" && blobUrl && (
            // eslint-disable-next-line jsx-a11y/media-has-caption
            <audio src={blobUrl} controls className="preview-audio" />
          )}
          {kind !== "other" && !blobUrl && !error && <div className="preview-loading">Loading preview...</div>}
        </div>
      </div>
    </div>
  );
}
