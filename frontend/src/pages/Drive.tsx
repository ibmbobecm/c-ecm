import { useEffect, useRef, useState } from "react";
import { apiDelete, apiGet, apiPatch, apiPost, apiUpload, downloadFile, ApiError } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import { useConnections } from "../contexts/ConnectionsContext";
import type { DriveItem, FileItem, FolderContents, Lock, SearchResult, SortKey, SortState, Tag, ViewMode, WorkflowDefinition, WorkflowInstance } from "../types";
import { Sidebar } from "../components/Sidebar";
import { Toolbar } from "../components/Toolbar";
import { ItemGrid } from "../components/ItemGrid";
import { ContextMenu, type MenuAction } from "../components/ContextMenu";
import { TextInputDialog } from "../components/TextInputDialog";
import { MoveDialog } from "../components/MoveDialog";
import { IntegrationsPage } from "./IntegrationsPage";
import { AuditLogPage } from "./AuditLogPage";
import { DocumentViewerPage, type ViewerSection } from "./DocumentViewerPage";
import { CommandPalette, type PaletteItem } from "../components/CommandPalette";
import { TagsDialog } from "../components/TagsDialog";
import { CommentsPanel } from "../components/CommentsPanel";
import { ShareLinkDialog } from "../components/ShareLinkDialog";
import { WorkflowsPanel } from "../components/WorkflowsPanel";
import { ESignatureDialog } from "../components/ESignatureDialog";
import { GlobalSearchPanel } from "../components/GlobalSearchPanel";
import { UserManagementPanel } from "../components/UserManagementPanel";
import { DocumentClassesPanel } from "../components/DocumentClassesPanel";
import { WebhookManagementPanel } from "../components/WebhookManagementPanel";
import { RetentionPolicyPanel } from "../components/RetentionPolicyPanel";
import { Icon, fileTypeIconName } from "../icons";
import { keyOf } from "../utils";

type Flash = { type: "error" | "success"; message: string };

export function Drive() {
  const { logout } = useAuth();
  const { connections, activeConnectionId, selectConnection, loading: connectionsLoading } = useConnections();
  const [integrationsPageOpen, setIntegrationsPageOpen] = useState(false);
  const [auditLogOpen, setAuditLogOpen] = useState(false);
  const [aiBackend, setAiBackend] = useState<string | undefined>(undefined);

  const [view, setView] = useState<ViewMode>("mine");
  const [folderId, setFolderId] = useState<string | null>(null);
  const [contents, setContents] = useState<FolderContents | null>(null);
  const [loading, setLoading] = useState(true);
  const [layout, setLayout] = useState<"grid" | "list">("grid");
  const [sort, setSort] = useState<SortState>({ key: "name", dir: "asc" });

  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult | null>(null);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [lastClickedId, setLastClickedId] = useState<string | null>(null);

  const [contextMenu, setContextMenu] = useState<{ item: DriveItem; x: number; y: number } | null>(null);
  const [viewer, setViewer] = useState<{ file: FileItem; section?: ViewerSection } | null>(null);
  const [renameItem, setRenameItem] = useState<DriveItem | null>(null);
  const [moveItems, setMoveItems] = useState<DriveItem[] | null>(null);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [tagsItem, setTagsItem] = useState<DriveItem | null>(null);
  const [commentsItem, setCommentsItem] = useState<DriveItem | null>(null);
  const [shareItem, setShareItem] = useState<DriveItem | null>(null);
  const [esignItem, setEsignItem] = useState<FileItem | null>(null);
  const [requestApprovalItem, setRequestApprovalItem] = useState<DriveItem | null>(null);
  const [metadataItem, setMetadataItem] = useState<DriveItem | null>(null);
  const [usersOpen, setUsersOpen] = useState(false);
  const [docClassesOpen, setDocClassesOpen] = useState(false);
  const [webhooksOpen, setWebhooksOpen] = useState(false);
  const [retentionOpen, setRetentionOpen] = useState(false);
  const [tagsByResource, setTagsByResource] = useState<Record<string, Tag[]>>({});
  const [commentCounts, setCommentCounts] = useState<Record<string, number>>({});
  const [locksByResource, setLocksByResource] = useState<Record<string, Lock>>({});
  const [workflowDefs, setWorkflowDefs] = useState<WorkflowDefinition[]>([]);
  const [approvalsRefreshToken, setApprovalsRefreshToken] = useState(0);
  const [pendingApprovalsByResource, setPendingApprovalsByResource] = useState<Record<string, WorkflowInstance>>({});
  const [dragActive, setDragActive] = useState(false);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [uploadStatus, setUploadStatus] = useState<{ current: number; total: number; name: string } | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const showError = (err: unknown, fallback: string) => {
    setFlash({ type: "error", message: err instanceof ApiError ? err.message : fallback });
  };

  const showSuccess = (message: string) => setFlash({ type: "success", message });

  useEffect(() => {
    if (!flash) return;
    // Errors matter more than success toasts — give them longer to be seen,
    // especially since an upload failure can arrive up to ~20s after the
    // click, well after the user's attention may have drifted.
    const t = setTimeout(() => setFlash(null), flash.type === "error" ? 8000 : 4000);
    return () => clearTimeout(t);
  }, [flash]);

  const clearSelection = () => {
    setSelectedIds(new Set());
    setLastClickedId(null);
  };

  const loadResourceMeta = (items: DriveItem[]) => {
    if (items.length === 0) return;
    const ids = items.map((i) => i.id);
    apiPost<Record<string, Tag[]>>("/resources/tags/bulk", { resource_ids: ids })
      .then((byId) => setTagsByResource((prev) => ({ ...prev, ...byId })))
      .catch(() => {});
    apiPost<Record<string, number>>("/resources/comments/counts", { resource_ids: ids })
      .then((counts) => setCommentCounts((prev) => ({ ...prev, ...counts })))
      .catch(() => {});
    // Load lock status for all file items so LockBadge renders
    const fileIds = items.filter((i) => i.type === "file").map((i) => i.id);
    Promise.all(fileIds.map((id) => apiGet<Lock | null>(`/locks/${id}`).catch(() => null))).then((locks) => {
      const map: Record<string, Lock> = {};
      fileIds.forEach((id, idx) => {
        const l = locks[idx];
        if (l) map[id] = l;
      });
      setLocksByResource((prev) => ({ ...prev, ...map }));
    });
  };

  // Load workflow definitions once so "Request Approval" can show a picker
  useEffect(() => {
    if (!activeConnectionId) return;
    apiGet<WorkflowDefinition[]>("/workflows/definitions").then(setWorkflowDefs).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConnectionId]);

  // Pending-approval badges in the grid — same idea as tagsByResource/
  // commentCounts, but there's no bulk-by-resource-ids endpoint for this;
  // in_review instances for the whole connection are typically few, so one
  // list call indexed client-side is simpler than adding one. Re-runs on
  // approvalsRefreshToken so starting/acting on a request updates badges
  // without waiting for an unrelated navigation to trigger a refetch.
  useEffect(() => {
    if (!activeConnectionId) return;
    apiGet<WorkflowInstance[]>("/workflows/instances", { status: "in_review" })
      .then((instances) => {
        const map: Record<string, WorkflowInstance> = {};
        for (const inst of instances) map[inst.resource_id] = inst;
        setPendingApprovalsByResource(map);
      })
      .catch(() => {});
  }, [activeConnectionId, approvalsRefreshToken]);

  // Which AI backend is active, if any -- server-wide, not per-connection,
  // so this only needs to load once. Powers the Watson badge in AiPanel,
  // which otherwise has no way to know what's configured server-side.
  useEffect(() => {
    apiGet<{ enabled: boolean; backend: string }>("/ai/status")
      .then((s) => setAiBackend(s.enabled ? s.backend : undefined))
      .catch(() => {});
  }, []);

  const loadContents = () => {
    if (!activeConnectionId) return;
    // The backend only knows "mine"/"trash" for this endpoint — "workflows"
    // and "global-search" render their own self-contained panels and never
    // needed folder contents at all, but this was still being called for
    // every view change and hitting a 422 (visibly, as an error toast) each
    // time someone opened Approvals or Global Search.
    if (view !== "mine" && view !== "trash") return;
    setLoading(true);
    apiGet<FolderContents>("/folders/contents", { folder_id: view === "mine" ? folderId ?? undefined : undefined, view })
      .then((c) => {
        setContents(c);
        loadResourceMeta([...c.folders, ...c.files]);
      })
      .catch((err) => showError(err, "Couldn't load this folder."))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setView("mine");
    setFolderId(null);
    setSearchQuery("");
    setContents(null);
    clearSelection();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConnectionId]);

  useEffect(() => {
    clearSelection();
    if (searchQuery.trim()) return;
    loadContents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, folderId, searchQuery, activeConnectionId]);

  useEffect(() => {
    const q = searchQuery.trim();
    if (!q) {
      setSearchResults(null);
      return;
    }
    const handle = setTimeout(() => {
      apiGet<SearchResult>("/search", { q })
        .then((r) => {
          setSearchResults(r);
          loadResourceMeta([...r.folders, ...r.files]);
        })
        .catch(() => setSearchResults(null));
    }, 250);
    return () => clearTimeout(handle);
  }, [searchQuery]);

  const goToView = (v: ViewMode) => {
    setView(v);
    setFolderId(null);
    setSearchQuery("");
  };

  const goToFolder = (id: string | null) => {
    setView("mine");
    setFolderId(id);
    setSearchQuery("");
  };

  const handleOpen = (item: DriveItem) => {
    if (item.type === "folder") {
      goToFolder(item.id);
    } else {
      setViewer({ file: item });
    }
  };

  const doUpload = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    let failures = 0;
    for (let i = 0; i < list.length; i++) {
      const f = list[i];
      setUploadStatus({ current: i + 1, total: list.length, name: f.name });
      const form = new FormData();
      form.append("upload", f);
      if (folderId !== null) form.append("folder_id", folderId);
      try {
        await apiUpload("/files", form);
      } catch (err) {
        failures++;
        showError(err, `Couldn't upload "${f.name}".`);
      }
    }
    setUploadStatus(null);
    if (failures === 0 && list.length > 0) {
      showSuccess(list.length === 1 ? `Uploaded "${list[0].name}".` : `Uploaded ${list.length} files.`);
    }
    loadContents();
  };

  const handleCreateFolder = async (name: string) => {
    try {
      await apiPost("/folders", { name, parent_id: folderId });
      loadContents();
    } catch (err) {
      showError(err, "Couldn't create the folder.");
    }
  };

  const handleRename = async (item: DriveItem, name: string) => {
    try {
      if (item.type === "folder") {
        await apiPatch(`/folders/${item.id}`, { name });
      } else {
        await apiPatch(`/files/${item.id}`, { name });
      }
      loadContents();
      // If this file is open in the full-screen viewer, reflect the new
      // name there immediately rather than leaving it stale until reopened.
      setViewer((prev) => (prev && prev.file.id === item.id ? { ...prev, file: { ...prev.file, name } } : prev));
    } catch (err) {
      showError(err, "Couldn't rename that.");
    }
  };

  const handleMoveMany = async (items: DriveItem[], targetFolderId: string | null) => {
    let failures = 0;
    for (const item of items) {
      try {
        if (item.type === "folder") {
          await apiPatch(`/folders/${item.id}`, targetFolderId === null ? { move_to_root: true } : { parent_id: targetFolderId });
        } else {
          await apiPatch(`/files/${item.id}`, targetFolderId === null ? { move_to_root: true } : { folder_id: targetFolderId });
        }
      } catch (err) {
        failures++;
        showError(err, `Couldn't move "${item.name}".`);
      }
    }
    loadContents();
    clearSelection();
    if (failures === 0) showSuccess(items.length === 1 ? `Moved "${items[0].name}".` : `Moved ${items.length} items.`);
  };

  const handleTrashMany = async (items: DriveItem[]) => {
    let failures = 0;
    for (const item of items) {
      try {
        if (item.type === "folder") await apiDelete(`/folders/${item.id}`);
        else await apiDelete(`/files/${item.id}`);
      } catch (err) {
        failures++;
        showError(err, `Couldn't delete "${item.name}".`);
      }
    }
    loadContents();
    clearSelection();
    if (failures === 0) showSuccess(items.length === 1 ? `Deleted "${items[0].name}".` : `Deleted ${items.length} items.`);
  };

  const handleRestoreMany = async (items: DriveItem[]) => {
    for (const item of items) {
      try {
        if (item.type === "folder") await apiPost(`/folders/${item.id}/restore`);
        else await apiPost(`/files/${item.id}/restore`);
      } catch (err) {
        showError(err, `Couldn't restore "${item.name}".`);
      }
    }
    loadContents();
    clearSelection();
  };

  const handlePermanentDeleteMany = async (items: DriveItem[]) => {
    const label = items.length === 1 ? `"${items[0].name}"` : `${items.length} items`;
    if (!window.confirm(`Permanently delete ${label}? This can't be undone.`)) return;
    for (const item of items) {
      try {
        if (item.type === "folder") await apiDelete(`/folders/${item.id}/permanent`);
        else await apiDelete(`/files/${item.id}/permanent`);
      } catch (err) {
        showError(err, `Couldn't permanently delete "${item.name}".`);
      }
    }
    loadContents();
    clearSelection();
  };

  const handleDownloadMany = (items: DriveItem[]) => {
    for (const item of items) {
      if (item.type === "file") downloadFile(`/files/${item.id}/download`, item.name);
    }
  };

  const handleRequestApproval = async (item: DriveItem, defId: string) => {
    try {
      await apiPost("/workflows/instances", {
        definition_id: defId,
        resource_id: item.id,
        resource_type: item.type,
        comment: null,
      });
      showSuccess(`Approval requested for "${item.name}".`);
      // The viewer's Approvals section fetches once on mount, not on a poll —
      // if it's open for this same file, bump its remount key so the request
      // just created actually shows up instead of waiting for a manual
      // collapse/reopen.
      setApprovalsRefreshToken((t) => t + 1);
    } catch (err) {
      showError(err, "Couldn't start the approval workflow.");
    }
  };

  const buildMenuActions = (item: DriveItem): MenuAction[] => {
    if (view === "trash") {
      return [
        { label: "Restore", icon: "refresh", onClick: () => handleRestoreMany([item]) },
        { label: "Delete forever", icon: "trash", onClick: () => handlePermanentDeleteMany([item]), danger: true, separatorBefore: true },
      ];
    }

    const actions: MenuAction[] = [
      { label: item.type === "folder" ? "Open" : "Preview", icon: item.type === "folder" ? "folder-open" : "search", onClick: () => handleOpen(item) },
    ];
    if (item.type === "file") {
      actions.push({ label: "Download", icon: "download", onClick: () => handleDownloadMany([item]) });
      actions.push({ label: "Version history", icon: "list-view", onClick: () => setViewer({ file: item, section: "versions" }) });
    }

    actions.push({ label: "Rename", icon: "rename", onClick: () => setRenameItem(item) });
    actions.push({ label: "Move", icon: "move", onClick: () => setMoveItems([item]) });
    actions.push({
      label: "Get link", icon: "link", separatorBefore: true,
      onClick: () => (item.type === "file" ? setViewer({ file: item, section: "share" }) : setShareItem(item)),
    });
    actions.push({
      label: "Tags", icon: "tag",
      onClick: () => (item.type === "file" ? setViewer({ file: item, section: "tags" }) : setTagsItem(item)),
    });
    actions.push({
      label: "Comments", icon: "message",
      onClick: () => (item.type === "file" ? setViewer({ file: item, section: "comments" }) : setCommentsItem(item)),
    });
    actions.push({
      label: "Set Metadata", icon: "tag",
      onClick: () => (item.type === "file" ? setViewer({ file: item, section: "properties" }) : setMetadataItem(item)),
    });
    if (item.type === "file") {
      actions.push({ label: "AI Insights", icon: "star", onClick: () => setViewer({ file: item, section: "ai" }) });
      actions.push({ label: "Send for signature", icon: "signature", onClick: () => setEsignItem(item) });
    }
    // "Request Approval" appears when at least one workflow definition exists
    if (workflowDefs.length > 0) {
      actions.push({
        label: "Request Approval",
        icon: "check-circle",
        separatorBefore: true,
        onClick: () => setRequestApprovalItem(item),
      });
    }
    actions.push({ label: "Delete", icon: "trash", onClick: () => handleTrashMany([item]), danger: true, separatorBefore: true });
    return actions;
  };

  const displayedFolders = searchQuery.trim() ? searchResults?.folders ?? [] : contents?.folders ?? [];
  const displayedFiles = searchQuery.trim() ? searchResults?.files ?? [] : contents?.files ?? [];
  const allItems: DriveItem[] = [...displayedFolders, ...displayedFiles];
  const breadcrumb = searchQuery.trim()
    ? [{ id: null, name: `Search results for "${searchQuery.trim()}"` }]
    : contents?.breadcrumb ?? [{ id: null, name: "My Drive" }];

  const selectedItems = allItems.filter((i) => selectedIds.has(keyOf(i)));

  // ---- selection ----

  const onItemClick = (e: React.MouseEvent, item: DriveItem, orderedIds: string[]) => {
    const k = keyOf(item);
    if (e.shiftKey && lastClickedId) {
      const a = orderedIds.indexOf(lastClickedId);
      const b = orderedIds.indexOf(k);
      if (a !== -1 && b !== -1) {
        const [lo, hi] = a < b ? [a, b] : [b, a];
        setSelectedIds(new Set(orderedIds.slice(lo, hi + 1)));
      }
    } else if (e.ctrlKey || e.metaKey) {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(k)) next.delete(k);
        else next.add(k);
        return next;
      });
    } else {
      setSelectedIds(new Set([k]));
    }
    setLastClickedId(k);
  };

  const onCheckboxToggle = (item: DriveItem) => {
    const k = keyOf(item);
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
    setLastClickedId(k);
  };

  const onKebabClick = (item: DriveItem, x: number, y: number) => {
    setContextMenu({ item, x, y });
  };

  // ---- keyboard shortcuts ----

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }
      const tag = (e.target as HTMLElement)?.tagName;
      const isEditable = tag === "INPUT" || tag === "TEXTAREA" || (e.target as HTMLElement)?.isContentEditable;
      if (isEditable) return;
      if (e.key === "Escape") {
        if (selectedIds.size) clearSelection();
      } else if ((e.key === "Delete" || e.key === "Backspace") && selectedIds.size && view !== "trash") {
        e.preventDefault();
        handleTrashMany(selectedItems);
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && allItems.length) {
        e.preventDefault();
        setSelectedIds(new Set(allItems.map(keyOf)));
      } else if (e.key === "/") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIds, selectedItems, allItems, view]);

  const onSortChange = (key: SortKey) => {
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  };

  // ---- command palette ----

  const paletteItems: PaletteItem[] = paletteOpen
    ? [
        ...allItems.map((item) => ({
          id: `item-${keyOf(item)}`,
          group: "Files & folders",
          label: item.name,
          icon: item.type === "folder" ? ("folder" as const) : fileTypeIconName(item.content_type, item.name),
          onSelect: () => handleOpen(item),
        })),
        { id: "nav-mine", group: "Navigate", label: "My Drive", icon: "folder" as const, onSelect: () => goToView("mine") },
        { id: "nav-trash", group: "Navigate", label: "Trash", icon: "trash" as const, onSelect: () => goToView("trash") },
        { id: "nav-workflows", group: "Navigate", label: "Approvals", icon: "check-circle" as const, onSelect: () => goToView("workflows") },
        { id: "nav-global-search", group: "Navigate", label: "Global Search", icon: "search" as const, onSelect: () => goToView("global-search") },
        { id: "nav-upload", group: "Navigate", label: "Upload files", icon: "upload" as const, onSelect: () => fileInputRef.current?.click() },
        { id: "nav-new-folder", group: "Navigate", label: "New folder", icon: "folder-plus" as const, onSelect: () => setNewFolderOpen(true) },
        ...connections
          .filter((c) => c.id !== activeConnectionId)
          .map((c) => ({
            id: `conn-${c.id}`,
            group: "Connections",
            label: `Switch to: ${c.display_name}`,
            icon: "plug" as const,
            onSelect: () => selectConnection(c.id),
          })),
      ]
    : [];

  const bulkBar =
    selectedIds.size > 0 ? (
      <div className="bulk-bar">
        <button className="bulk-bar-clear" onClick={clearSelection} aria-label="Clear selection">
          <Icon name="close" size={16} />
        </button>
        <span className="bulk-bar-count">{selectedIds.size} selected</span>
        <div className="bulk-actions">
          {view === "trash" ? (
            <>
              <button onClick={() => handleRestoreMany(selectedItems)}>
                <Icon name="refresh" size={15} />
                Restore
              </button>
              <button className="danger" onClick={() => handlePermanentDeleteMany(selectedItems)}>
                <Icon name="trash" size={15} />
                Delete forever
              </button>
            </>
          ) : (
            <>
              {selectedItems.every((i) => i.type === "file") && (
                <button onClick={() => handleDownloadMany(selectedItems)}>
                  <Icon name="download" size={15} />
                  Download
                </button>
              )}
              <button onClick={() => setMoveItems(selectedItems)}>
                <Icon name="move" size={15} />
                Move
              </button>
              <button className="danger" onClick={() => handleTrashMany(selectedItems)}>
                <Icon name="trash" size={15} />
                Delete
              </button>
            </>
          )}
        </div>
      </div>
    ) : null;

  if (integrationsPageOpen) {
    return <IntegrationsPage onBack={() => setIntegrationsPageOpen(false)} />;
  }

  if (auditLogOpen) {
    return <AuditLogPage onBack={() => setAuditLogOpen(false)} />;
  }

  return (
    <div
      className="drive-app"
      onDragOver={(e) => {
        if (view !== "mine") return;
        e.preventDefault();
        setDragActive(true);
      }}
      onDragLeave={() => setDragActive(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragActive(false);
        if (view === "mine" && !uploadStatus) doUpload(e.dataTransfer.files);
      }}
    >
      <Sidebar
        view={view}
        onViewChange={goToView}
        onNewFolder={() => setNewFolderOpen(true)}
        onUploadClick={() => fileInputRef.current?.click()}
        uploading={!!uploadStatus}
        onOpenIntegrations={() => setIntegrationsPageOpen(true)}
        onOpenUsers={() => setUsersOpen(true)}
        onOpenDocClasses={() => setDocClassesOpen(true)}
        onOpenWebhooks={() => setWebhooksOpen(true)}
        onOpenRetention={() => setRetentionOpen(true)}
        onOpenAuditLog={() => setAuditLogOpen(true)}
        onLogout={logout}
      />

      <input
        ref={fileInputRef}
        type="file"
        multiple
        style={{ display: "none" }}
        onChange={(e) => {
          if (e.target.files) doUpload(e.target.files);
          e.target.value = "";
        }}
      />

      <main className="drive-main">
        <Toolbar
          ref={searchInputRef}
          breadcrumb={breadcrumb}
          onNavigate={goToFolder}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          layout={layout}
          onLayoutChange={setLayout}
          showBreadcrumb={view === "mine" || !!searchQuery.trim()}
          selectionBar={bulkBar}
        />

        {uploadStatus && (
          <div className="flash flash-progress">
            <span className="flash-spinner" />
            Uploading {uploadStatus.name}
            {uploadStatus.total > 1 ? ` (${uploadStatus.current} of ${uploadStatus.total})` : ""}...
          </div>
        )}
        {!uploadStatus && flash && <div className={`flash flash-${flash.type}`}>{flash.message}</div>}

        {view === "workflows" ? (
          <WorkflowsPanel />
        ) : view === "global-search" ? (
          <GlobalSearchPanel
            onSelectHit={(hit) => {
              // Switch to the connection the hit belongs to, then navigate
              // straight to the result. A trailing goToView("mine") used to
              // run unconditionally after goToFolder — goToView resets
              // folderId back to null, so a folder hit always landed back
              // at the connection root instead of the folder just clicked.
              // File hits never opened anything at all: there was no
              // setViewer call in this branch.
              selectConnection(hit.connection_id);
              if (hit.resource_type === "folder") {
                goToFolder(hit.resource_id);
              } else {
                setViewer({
                  file: {
                    type: "file",
                    id: hit.resource_id,
                    name: hit.name,
                    folder_id: null,
                    version_number: 1,
                    size_bytes: hit.size_bytes,
                    content_type: hit.content_type,
                    updated_at: hit.updated_at,
                  },
                });
              }
            }}
          />
        ) : !connectionsLoading && !activeConnectionId ? (
          <div className="empty-state">
            <div className="empty-state-icon">
              <Icon name="plug" size={64} />
            </div>
            <h3>No backend connected yet</h3>
            <p>Connect FileNet, Google Drive, S3, or another backend to start browsing.</p>
            <button className="btn-primary" onClick={() => setIntegrationsPageOpen(true)}>
              Add a connection
            </button>
          </div>
        ) : loading && !contents && !searchResults ? (
          <div className="empty-state">
            <p>Loading&hellip;</p>
          </div>
        ) : (
          <ItemGrid
            folders={displayedFolders}
            files={displayedFiles}
            layout={layout}
            tagsByResource={tagsByResource}
            commentCounts={commentCounts}
            pendingApprovalsByResource={pendingApprovalsByResource}
            locksByResource={locksByResource}
            selectedIds={selectedIds}
            sort={sort}
            onSortChange={onSortChange}
            onItemClick={onItemClick}
            onCheckboxToggle={onCheckboxToggle}
            onOpen={handleOpen}
            onContextMenu={(item, x, y) => setContextMenu({ item, x, y })}
            onKebabClick={onKebabClick}
            emptyIcon={searchQuery.trim() ? "search" : view === "trash" ? "trash" : "folder-open"}
            emptyTitle={searchQuery.trim() ? "No results" : view === "trash" ? "Trash is empty" : "This folder is empty"}
            emptySubtitle={
              searchQuery.trim()
                ? `Nothing matches "${searchQuery.trim()}".`
                : view === "trash"
                  ? "Deleted files and folders show up here."
                  : "Drag files here, or use Upload to add some."
            }
            emptyCta={
              !searchQuery.trim() && view === "mine"
                ? { label: "Upload files", onClick: () => fileInputRef.current?.click() }
                : undefined
            }
          />
        )}

        {dragActive && (
          <div className="drop-overlay">
            <Icon name="upload" size={36} />
            <div>Drop files to upload</div>
          </div>
        )}
      </main>

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          actions={buildMenuActions(contextMenu.item)}
          onClose={() => setContextMenu(null)}
        />
      )}

      {newFolderOpen && (
        <TextInputDialog
          title="New folder"
          placeholder="Untitled folder"
          confirmLabel="Create"
          onConfirm={handleCreateFolder}
          onClose={() => setNewFolderOpen(false)}
        />
      )}

      {renameItem && (
        <TextInputDialog
          title={`Rename "${renameItem.name}"`}
          initialValue={renameItem.name}
          confirmLabel="Rename"
          onConfirm={(name) => handleRename(renameItem, name)}
          onClose={() => setRenameItem(null)}
        />
      )}

      {moveItems && (
        <MoveDialog items={moveItems} onClose={() => setMoveItems(null)} onMove={(target) => handleMoveMany(moveItems, target)} />
      )}

      {viewer && (
        <DocumentViewerPage
          file={viewer.file}
          initialSection={viewer.section}
          aiBackend={aiBackend}
          connectionName={connections.find((c) => c.id === activeConnectionId)?.display_name}
          onClose={() => setViewer(null)}
          onDownload={() => downloadFile(`/files/${viewer.file.id}/download`, viewer.file.name)}
          onResourceChanged={() => loadResourceMeta([viewer.file])}
          onVersionsRestored={loadContents}
          onRename={() => setRenameItem(viewer.file)}
          onMove={() => setMoveItems([viewer.file])}
          onDelete={async () => {
            // Wait for the delete (and the grid reload it triggers) to finish
            // before closing — otherwise the viewer closes onto the *old*
            // grid state, and the file briefly still being there reads as
            // the delete having silently failed.
            await handleTrashMany([viewer.file]);
            setViewer(null);
          }}
          onSendForSignature={() => setEsignItem(viewer.file)}
          canRequestApproval={workflowDefs.length > 0}
          onRequestApproval={() => setRequestApprovalItem(viewer.file)}
          workflowDefs={workflowDefs}
          approvalsRefreshToken={approvalsRefreshToken}
          onApprovalsChanged={() => setApprovalsRefreshToken((t) => t + 1)}
        />
      )}

      {paletteOpen && <CommandPalette items={paletteItems} onClose={() => setPaletteOpen(false)} />}

      {tagsItem && (
        <TagsDialog item={tagsItem} onClose={() => setTagsItem(null)} onChange={() => loadResourceMeta([tagsItem])} />
      )}

      {commentsItem && (
        <CommentsPanel item={commentsItem} onClose={() => setCommentsItem(null)} onChange={() => loadResourceMeta([commentsItem])} />
      )}

      {shareItem && <ShareLinkDialog item={shareItem} onClose={() => setShareItem(null)} />}

      {esignItem && <ESignatureDialog file={esignItem} onClose={() => setEsignItem(null)} />}

      {usersOpen && <UserManagementPanel onClose={() => setUsersOpen(false)} />}
      {docClassesOpen && <DocumentClassesPanel onClose={() => setDocClassesOpen(false)} />}
      {metadataItem && (
        <DocumentClassesPanel
          resourceId={metadataItem.id}
          resourceType={metadataItem.type as "file" | "folder"}
          resourceName={metadataItem.name}
          onClose={() => setMetadataItem(null)}
        />
      )}
      {webhooksOpen && <WebhookManagementPanel onClose={() => setWebhooksOpen(false)} />}
      {retentionOpen && <RetentionPolicyPanel onClose={() => setRetentionOpen(false)} />}

      {requestApprovalItem && (
        <div className="modal-overlay" onMouseDown={() => setRequestApprovalItem(null)}>
          <div className="modal-card" onMouseDown={(e) => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div className="modal-header">
              <h2>Request Approval</h2>
              <button className="modal-close" onClick={() => setRequestApprovalItem(null)} aria-label="Close">
                <Icon name="close" size={18} />
              </button>
            </div>
            <div className="modal-body">
              <p className="muted" style={{ marginBottom: 12 }}>
                Choose a workflow to start for <strong>{requestApprovalItem.name}</strong>:
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {workflowDefs.map((def) => (
                  <button
                    key={def.id}
                    className="btn-secondary"
                    style={{ textAlign: "left" }}
                    onClick={() => {
                      setRequestApprovalItem(null);
                      handleRequestApproval(requestApprovalItem, def.id);
                    }}
                  >
                    <strong>{def.name}</strong>
                    {def.description && <span className="muted" style={{ display: "block", fontSize: 12 }}>{def.description}</span>}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
