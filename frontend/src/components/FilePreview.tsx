import { useEffect, useState } from "react";
import { API_BASE, getActiveConnectionId, getAuthToken } from "../api/client";
import type { FileItem } from "../types";
import { formatBytes } from "../utils";
import { Icon } from "../icons";

export type PreviewKind = "image" | "pdf" | "video" | "audio" | "text" | "document" | "spreadsheet" | "other";

const TEXT_EXTENSIONS = new Set([
  "txt", "md", "markdown", "json", "log", "yml", "yaml", "xml", "ini", "conf", "toml",
  "js", "ts", "tsx", "jsx", "py", "java", "c", "cpp", "h", "cs", "go", "rs", "rb", "php",
  "html", "htm", "css", "scss", "sh", "bat", "ps1", "sql",
]);
const SPREADSHEET_EXTENSIONS = new Set(["xlsx", "xls", "xlsb", "ods", "csv"]);
const DOCUMENT_EXTENSIONS = new Set(["docx"]);

function extOf(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

export function detectPreviewKind(contentType: string | null, name: string): PreviewKind {
  const ext = extOf(name);
  if (contentType?.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "svg", "webp", "bmp"].includes(ext)) return "image";
  if (contentType === "application/pdf" || ext === "pdf") return "pdf";
  if (contentType?.startsWith("video/") || /^(mp4|mov|avi|mkv|webm|ogv)$/.test(ext)) return "video";
  if (contentType?.startsWith("audio/") || /^(mp3|wav|flac|aac|ogg|m4a)$/.test(ext)) return "audio";
  if (DOCUMENT_EXTENSIONS.has(ext)) return "document";
  if (SPREADSHEET_EXTENSIONS.has(ext)) return "spreadsheet";
  if (TEXT_EXTENSIONS.has(ext)) return "text";
  return "other";
}

type SheetTable = { name: string; rows: string[][] };

/** Renders a file's content inline where a reasonable in-browser renderer
 * exists — images/PDF/video/audio natively, text/code as monospace, .docx
 * via mammoth (OOXML only — legacy .doc isn't supported), spreadsheets via
 * SheetJS. Anything else (PowerPoint, legacy .doc/.xls) falls back to a
 * "download to view" state rather than guessing. */
export function FilePreview({ file }: { file: FileItem }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [docHtml, setDocHtml] = useState<string | null>(null);
  const [sheets, setSheets] = useState<SheetTable[] | null>(null);
  const [activeSheet, setActiveSheet] = useState(0);
  const kind = detectPreviewKind(file.content_type, file.name);

  useEffect(() => {
    setBlobUrl(null);
    setError(null);
    setTextContent(null);
    setDocHtml(null);
    setSheets(null);
    setActiveSheet(0);
    if (kind === "other") return;
    let cancelled = false;
    let revoke: string | null = null;

    (async () => {
      try {
        const token = getAuthToken();
        const connectionId = getActiveConnectionId();
        const headers: Record<string, string> = {};
        if (token) headers.Authorization = `Bearer ${token}`;
        if (connectionId) headers["X-Connection-Id"] = connectionId;
        const res = await fetch(`${API_BASE}/files/${file.id}/download`, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const b = await res.blob();
        if (cancelled) return;

        if (kind === "text") {
          setTextContent(await b.text());
        } else if (kind === "document") {
          // Loaded on demand — mammoth is only needed by the small subset of
          // users previewing a .docx, not bundled into everyone's initial load.
          const mammoth = await import("mammoth");
          const { value } = await mammoth.convertToHtml({ arrayBuffer: await b.arrayBuffer() });
          if (!cancelled) setDocHtml(value);
        } else if (kind === "spreadsheet") {
          // Same deal for SheetJS, which is a large parser most users never touch.
          const XLSX = await import("xlsx");
          const wb = XLSX.read(await b.arrayBuffer(), { type: "array" });
          const parsed = wb.SheetNames.map((name) => ({
            name,
            rows: XLSX.utils.sheet_to_json<string[]>(wb.Sheets[name], { header: 1, blankrows: false, raw: false }),
          }));
          if (!cancelled) setSheets(parsed);
        } else {
          const url = URL.createObjectURL(b);
          revoke = url;
          setBlobUrl(url);
        }
      } catch {
        if (!cancelled) setError("Couldn't load a preview for this file.");
      }
    })();

    return () => {
      cancelled = true;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [file.id, kind]);

  if (kind === "other") {
    return (
      <div className="preview-body">
        <div className="preview-unsupported">
          <div className="preview-unsupported-icon">
            <Icon name="file-generic" size={48} />
          </div>
          <p>No preview available for this file type.</p>
          <p className="muted">{formatBytes(file.size_bytes)}</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="preview-body">
        <div className="preview-unsupported">{error}</div>
      </div>
    );
  }

  if (kind === "image" || kind === "pdf" || kind === "video" || kind === "audio") {
    if (!blobUrl) return <div className="preview-body"><div className="preview-loading">Loading preview...</div></div>;
    return (
      <div className="preview-body">
        {kind === "image" && <img src={blobUrl} alt={file.name} className="preview-image" />}
        {kind === "pdf" && <iframe src={blobUrl} title={file.name} className="preview-pdf" />}
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        {kind === "video" && <video src={blobUrl} controls className="preview-video" />}
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        {kind === "audio" && <audio src={blobUrl} controls className="preview-audio" />}
      </div>
    );
  }

  if (kind === "text") {
    if (textContent === null) return <div className="preview-body"><div className="preview-loading">Loading preview...</div></div>;
    return (
      <div className="preview-doc-body">
        <pre className="preview-text">{textContent}</pre>
      </div>
    );
  }

  if (kind === "document") {
    if (docHtml === null) return <div className="preview-body"><div className="preview-loading">Loading preview...</div></div>;
    return (
      <div className="preview-doc-body">
        {/* mammoth converts OOXML to a constrained set of tags (p/table/img/etc), not a raw pass-through of attacker HTML */}
        <div className="preview-document-page" dangerouslySetInnerHTML={{ __html: docHtml }} />
      </div>
    );
  }

  // spreadsheet
  if (sheets === null) return <div className="preview-body"><div className="preview-loading">Loading preview...</div></div>;
  const sheet = sheets[activeSheet];
  const MAX_ROWS = 500;
  const rows = sheet?.rows.slice(0, MAX_ROWS) ?? [];
  return (
    <div className="preview-doc-body preview-spreadsheet-body">
      {sheets.length > 1 && (
        <div className="preview-sheet-tabs">
          {sheets.map((s, i) => (
            <button key={s.name} className={i === activeSheet ? "active" : ""} onClick={() => setActiveSheet(i)}>
              {s.name}
            </button>
          ))}
        </div>
      )}
      <div className="preview-spreadsheet-scroll">
        <table className="preview-spreadsheet-table">
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td key={ci}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {sheet && sheet.rows.length > MAX_ROWS && (
          <p className="muted preview-spreadsheet-truncated">Showing the first {MAX_ROWS} rows of {sheet.rows.length}.</p>
        )}
      </div>
    </div>
  );
}
