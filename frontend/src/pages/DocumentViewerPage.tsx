import { useEffect, useRef, useState, type ReactNode } from "react";
import type { FileItem, WorkflowDefinition } from "../types";
import { Icon, type IconName } from "../icons";
import { formatBytes, formatDate } from "../utils";
import { AiPanel } from "../components/AiPanel";
import { TagsDialogContent } from "../components/TagsDialog";
import { CommentsPanelContent } from "../components/CommentsPanel";
import { ShareLinkDialogContent } from "../components/ShareLinkDialog";
import { VersionHistoryContent } from "../components/VersionHistoryPanel";
import { MetadataEditorContent } from "../components/DocumentClassesPanel";
import { FileApprovalsPanel } from "../components/FileApprovalsPanel";
import { FilePreview } from "../components/FilePreview";

export type ViewerSection = "properties" | "approvals" | "ai" | "tags" | "comments" | "versions" | "share";

const EXT_LABEL: Record<string, string> = {
  pdf: "PDF Document", docx: "Word Document", doc: "Word Document (legacy)",
  xlsx: "Excel Spreadsheet", xls: "Excel Spreadsheet (legacy)", xlsb: "Excel Spreadsheet", ods: "OpenDocument Spreadsheet",
  csv: "CSV Spreadsheet", pptx: "PowerPoint Presentation", ppt: "PowerPoint Presentation (legacy)",
  txt: "Text File", md: "Markdown File", json: "JSON File", xml: "XML File",
  png: "PNG Image", jpg: "JPEG Image", jpeg: "JPEG Image", gif: "GIF Image", svg: "SVG Image", webp: "WebP Image", bmp: "Bitmap Image",
  mp4: "MP4 Video", mov: "QuickTime Video", webm: "WebM Video", mp3: "MP3 Audio", wav: "WAV Audio", flac: "FLAC Audio",
  zip: "ZIP Archive", "7z": "7-Zip Archive", rar: "RAR Archive",
};

function describeFileType(name: string, contentType: string | null): string {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return EXT_LABEL[ext] ?? contentType ?? (ext ? `.${ext.toUpperCase()} file` : "Unknown file type");
}

function ViewerSectionBlock({
  sectionRef,
  icon,
  label,
  open,
  onToggle,
  children,
}: {
  sectionRef: React.RefObject<HTMLDetailsElement | null>;
  icon: IconName;
  label: string;
  open: boolean;
  onToggle: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <details
      className="viewer-section"
      ref={sectionRef}
      open={open}
      onToggle={(e) => onToggle(e.currentTarget.open)}
    >
      <summary className="viewer-section-title">
        <Icon name={icon} size={15} />
        {label}
        <Icon name="chevron-down" size={13} className="viewer-section-chevron" />
      </summary>
      <div className="viewer-section-body">{children}</div>
    </details>
  );
}

export function DocumentViewerPage({
  file,
  initialSection,
  aiBackend,
  connectionName,
  onClose,
  onDownload,
  onResourceChanged,
  onVersionsRestored,
  onRename,
  onMove,
  onDelete,
  onSendForSignature,
  canRequestApproval,
  onRequestApproval,
  workflowDefs,
  approvalsRefreshToken,
  onApprovalsChanged,
}: {
  file: FileItem;
  initialSection?: ViewerSection;
  aiBackend?: string;
  connectionName?: string;
  onClose: () => void;
  onDownload: () => void;
  onResourceChanged: () => void;
  onVersionsRestored: () => void;
  onRename: () => void;
  onMove: () => void;
  onDelete: () => void;
  onSendForSignature: () => void;
  canRequestApproval: boolean;
  onRequestApproval: () => void;
  onApprovalsChanged: () => void;
  workflowDefs: WorkflowDefinition[];
  approvalsRefreshToken: number;
}) {
  const propertiesRef = useRef<HTMLDetailsElement>(null);
  const approvalsRef = useRef<HTMLDetailsElement>(null);
  const aiRef = useRef<HTMLDetailsElement>(null);
  const tagsRef = useRef<HTMLDetailsElement>(null);
  const commentsRef = useRef<HTMLDetailsElement>(null);
  const versionsRef = useRef<HTMLDetailsElement>(null);
  const shareRef = useRef<HTMLDetailsElement>(null);
  const sectionRefs: Record<ViewerSection, React.RefObject<HTMLDetailsElement | null>> = {
    properties: propertiesRef,
    approvals: approvalsRef,
    ai: aiRef,
    tags: tagsRef,
    comments: commentsRef,
    versions: versionsRef,
    share: shareRef,
  };

  // Each section opens/closes independently — not an exclusive accordion —
  // so a user can have Tags and Comments open side by side if they want.
  const [expanded, setExpanded] = useState<Set<ViewerSection>>(
    () => new Set([initialSection ?? "properties"])
  );
  // <details open> only controls the native disclosure widget's visual
  // state — it does nothing to stop React from mounting a collapsed
  // section's content, so every one of Tags/Comments/Version History/Share/
  // Approvals was still fetching its data the instant the viewer opened,
  // whether or not the user ever looked at it. This tracks which sections
  // have been opened at least once so their content only mounts (and only
  // then fetches) on first expand — and stays mounted afterward, so
  // toggling a section closed and back open doesn't re-fetch or flicker.
  const [everOpened, setEverOpened] = useState<Set<ViewerSection>>(
    () => new Set([initialSection ?? "properties"])
  );
  const toggleSection = (key: ViewerSection, open: boolean) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (open) next.add(key);
      else next.delete(key);
      return next;
    });
    if (open) setEverOpened((prev) => (prev.has(key) ? prev : new Set(prev).add(key)));
  };

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (initialSection) {
      sectionRefs[initialSection].current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file.id, initialSection]);

  const goToShare = () => {
    // Open the underlying <details> element directly first — toggleSection's
    // state update wouldn't re-render (and actually expand the section) until
    // after this function returns, so scrollIntoView would otherwise measure
    // the still-collapsed layout and land in the wrong place.
    if (shareRef.current) shareRef.current.open = true;
    toggleSection("share", true);
    shareRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="viewer-page">
      <div className="viewer-topbar">
        <button className="icon-btn" onClick={onClose} aria-label="Back to Drive" title="Back to Drive">
          <Icon name="chevron-right" size={16} className="viewer-back-icon" />
        </button>
        <span className="viewer-title" title={file.name}>{file.name}</span>
        <div className="viewer-topbar-actions">
          <button className="btn-secondary" onClick={onDownload}>
            <Icon name="download" size={15} />
            Download
          </button>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <Icon name="close" size={18} />
          </button>
        </div>
      </div>

      <div className="viewer-body">
        <div className="viewer-preview-pane">
          <FilePreview file={file} />
        </div>

        <aside className="viewer-sidebar">
          <div className="viewer-section viewer-actions-section">
            <h3 className="viewer-section-title viewer-actions-title">
              <Icon name="more-horizontal" size={15} /> Actions
            </h3>
            <div className="viewer-actions-list">
              <button className="viewer-action-btn" onClick={onRename}>
                <Icon name="rename" size={16} /> Rename
              </button>
              <button className="viewer-action-btn" onClick={onMove}>
                <Icon name="move" size={16} /> Move
              </button>
              <button className="viewer-action-btn" onClick={goToShare}>
                <Icon name="link" size={16} /> Get link
              </button>
              <button className="viewer-action-btn" onClick={onSendForSignature}>
                <Icon name="signature" size={16} /> Send for signature
              </button>
              {canRequestApproval && (
                <button className="viewer-action-btn" onClick={onRequestApproval}>
                  <Icon name="check-circle" size={16} /> Request Approval
                </button>
              )}
              <button className="viewer-action-btn danger" onClick={onDelete}>
                <Icon name="trash" size={16} /> Delete
              </button>
            </div>
          </div>

          <ViewerSectionBlock
            sectionRef={propertiesRef}
            icon="info"
            label="Properties"
            open={expanded.has("properties")}
            onToggle={(o) => toggleSection("properties", o)}
          >
            <dl className="viewer-properties-list">
              <dt>Type</dt>
              <dd>{describeFileType(file.name, file.content_type)}</dd>
              <dt>Size</dt>
              <dd>{formatBytes(file.size_bytes)}</dd>
              <dt>Version</dt>
              <dd>{file.version_number}</dd>
              <dt>Last modified</dt>
              <dd>{formatDate(file.updated_at) || "—"}</dd>
              {connectionName && (
                <>
                  <dt>Connection</dt>
                  <dd>{connectionName}</dd>
                </>
              )}
            </dl>
            <div className="viewer-metadata-editor">
              <MetadataEditorContent resourceId={file.id} resourceType="file" />
            </div>
          </ViewerSectionBlock>

          <ViewerSectionBlock
            sectionRef={approvalsRef}
            icon="check-circle"
            label="Approvals"
            open={expanded.has("approvals")}
            onToggle={(o) => toggleSection("approvals", o)}
          >
            {everOpened.has("approvals") && (
              <FileApprovalsPanel
                key={approvalsRefreshToken}
                resourceId={file.id}
                definitions={workflowDefs}
                onChanged={onApprovalsChanged}
              />
            )}
          </ViewerSectionBlock>

          <ViewerSectionBlock
            sectionRef={aiRef}
            icon="star"
            label="AI Insights"
            open={expanded.has("ai")}
            onToggle={(o) => toggleSection("ai", o)}
          >
            <AiPanel file={file} aiBackend={aiBackend} />
          </ViewerSectionBlock>

          <ViewerSectionBlock
            sectionRef={tagsRef}
            icon="tag"
            label="Tags"
            open={expanded.has("tags")}
            onToggle={(o) => toggleSection("tags", o)}
          >
            {everOpened.has("tags") && <TagsDialogContent item={file} onChange={onResourceChanged} />}
          </ViewerSectionBlock>

          <ViewerSectionBlock
            sectionRef={commentsRef}
            icon="message"
            label="Comments"
            open={expanded.has("comments")}
            onToggle={(o) => toggleSection("comments", o)}
          >
            {everOpened.has("comments") && <CommentsPanelContent item={file} onChange={onResourceChanged} />}
          </ViewerSectionBlock>

          <ViewerSectionBlock
            sectionRef={versionsRef}
            icon="list-view"
            label="Version History"
            open={expanded.has("versions")}
            onToggle={(o) => toggleSection("versions", o)}
          >
            {everOpened.has("versions") && <VersionHistoryContent file={file} canEdit onRestored={onVersionsRestored} />}
          </ViewerSectionBlock>

          <ViewerSectionBlock
            sectionRef={shareRef}
            icon="link"
            label="Share"
            open={expanded.has("share")}
            onToggle={(o) => toggleSection("share", o)}
          >
            {everOpened.has("share") && <ShareLinkDialogContent item={file} />}
          </ViewerSectionBlock>
        </aside>
      </div>
    </div>
  );
}
