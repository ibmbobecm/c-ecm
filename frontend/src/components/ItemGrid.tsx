import type { DriveItem, Lock, SortKey, SortState, Tag } from "../types";
import { fileTypeIconName, Icon, type IconName } from "../icons";
import { formatBytes, formatDate, keyOf } from "../utils";
import { EmptyState } from "./EmptyState";
import { LockBadge } from "./LockBadge";

function TagDots({ tags }: { tags: Tag[] }) {
  if (tags.length === 0) return null;
  return (
    <span className="tag-dots" title={tags.map((t) => t.name).join(", ")}>
      {tags.slice(0, 4).map((t) => (
        <span key={t.id} className="tag-dot" style={{ background: t.color }} />
      ))}
    </span>
  );
}

function CommentBadge({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <span className="comment-badge" title={`${count} comment${count === 1 ? "" : "s"}`}>
      <Icon name="message" size={11} />
      {count}
    </span>
  );
}

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "modified", label: "Modified" },
  { key: "size", label: "Size" },
];

function itemIcon(item: DriveItem) {
  return item.type === "folder" ? <Icon name="folder" /> : <Icon name={fileTypeIconName(item.content_type, item.name)} />;
}

function sortItems(items: DriveItem[], sort: SortState): DriveItem[] {
  const dir = sort.dir === "asc" ? 1 : -1;
  return [...items].sort((a, b) => {
    if (sort.key === "name") return a.name.localeCompare(b.name) * dir;
    if (sort.key === "size") {
      const sa = a.type === "file" ? a.size_bytes ?? -1 : -1;
      const sb = b.type === "file" ? b.size_bytes ?? -1 : -1;
      return (sa - sb) * dir;
    }
    // modified
    const da = a.type === "file" ? a.updated_at : a.created_at;
    const db = b.type === "file" ? b.updated_at : b.created_at;
    return ((da ?? "") < (db ?? "") ? -1 : (da ?? "") > (db ?? "") ? 1 : 0) * dir;
  });
}

export function ItemGrid({
  folders,
  files,
  layout,
  tagsByResource,
  commentCounts,
  locksByResource,
  selectedIds,
  sort,
  onSortChange,
  onItemClick,
  onCheckboxToggle,
  onOpen,
  onContextMenu,
  onKebabClick,
  emptyIcon,
  emptyTitle,
  emptySubtitle,
  emptyCta,
}: {
  folders: DriveItem[];
  files: DriveItem[];
  layout: "grid" | "list";
  tagsByResource: Record<string, Tag[]>;
  commentCounts: Record<string, number>;
  locksByResource: Record<string, Lock>;
  selectedIds: Set<string>;
  sort: SortState;
  onSortChange: (key: SortKey) => void;
  onItemClick: (e: React.MouseEvent, item: DriveItem, orderedIds: string[]) => void;
  onCheckboxToggle: (item: DriveItem) => void;
  onOpen: (item: DriveItem) => void;
  onContextMenu: (item: DriveItem, x: number, y: number) => void;
  onKebabClick: (item: DriveItem, x: number, y: number) => void;
  emptyIcon: IconName;
  emptyTitle: string;
  emptySubtitle: string;
  emptyCta?: { label: string; onClick: () => void };
}) {
  // Folders always sort first, matching every product studied — only the
  // within-type order follows the active sort.
  const items = [...sortItems(folders, sort), ...sortItems(files, sort)];

  if (items.length === 0) {
    return <EmptyState icon={emptyIcon} title={emptyTitle} subtitle={emptySubtitle} cta={emptyCta} />;
  }

  const orderedIds = items.map(keyOf);

  const openKebab = (e: React.MouseEvent, item: DriveItem) => {
    e.preventDefault();
    e.stopPropagation();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    onKebabClick(item, r.left, r.bottom + 4);
  };

  const handleContextMenu = (e: React.MouseEvent, item: DriveItem) => {
    e.preventDefault();
    onContextMenu(item, e.clientX, e.clientY);
  };

  if (layout === "list") {
    return (
      <div className="item-list-wrap">
        <table className="item-list">
          <thead>
            <tr>
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  className={"sortable" + (sort.key === col.key ? " sorted" : "")}
                  onClick={() => onSortChange(col.key)}
                >
                  <span className="th-inner">
                    {col.label}
                    {sort.key === col.key && <Icon name={sort.dir === "asc" ? "sort-asc" : "sort-desc"} size={12} />}
                  </span>
                </th>
              ))}
              <th className="col-actions" />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const k = keyOf(item);
              const selected = selectedIds.has(k);
              return (
                <tr
                  key={k}
                  className={selected ? "selected" : ""}
                  tabIndex={0}
                  onClick={(e) => onItemClick(e, item, orderedIds)}
                  onDoubleClick={() => onOpen(item)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onOpen(item);
                  }}
                  onContextMenu={(e) => handleContextMenu(e, item)}
                >
                  <td>
                    <div className="item-name-cell">
                      <span className="check-cell">
                        <span className="item-icon">{itemIcon(item)}</span>
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => onCheckboxToggle(item)}
                          onClick={(e) => e.stopPropagation()}
                          aria-label={`Select ${item.name}`}
                        />
                      </span>
                      {item.name}
                      <TagDots tags={tagsByResource[item.id] ?? []} />
                      <CommentBadge count={commentCounts[item.id] ?? 0} />
                      {item.type === "file" && locksByResource[item.id] && (
                        <LockBadge lock={locksByResource[item.id]} />
                      )}
                    </div>
                  </td>
                  <td className="muted">{formatDate(item.type === "file" ? item.updated_at : item.created_at)}</td>
                  <td className="muted">{item.type === "file" ? formatBytes(item.size_bytes) : ""}</td>
                  <td className="col-actions">
                    <button className="row-menu-btn" onClick={(e) => openKebab(e, item)} aria-label={`More actions for ${item.name}`}>
                      <Icon name="more-horizontal" size={16} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="item-grid">
      {items.map((item) => {
        const k = keyOf(item);
        const selected = selectedIds.has(k);
        return (
          <div
            key={k}
            className={"item-tile" + (selected ? " selected" : "")}
            tabIndex={0}
            onClick={(e) => onItemClick(e, item, orderedIds)}
            onDoubleClick={() => onOpen(item)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onOpen(item);
            }}
            onContextMenu={(e) => handleContextMenu(e, item)}
          >
            <input
              type="checkbox"
              className="item-tile-check"
              checked={selected}
              onChange={() => onCheckboxToggle(item)}
              onClick={(e) => e.stopPropagation()}
              aria-label={`Select ${item.name}`}
            />
            <button className="item-tile-menu-btn icon-btn" onClick={(e) => openKebab(e, item)} aria-label={`More actions for ${item.name}`}>
              <Icon name="more-horizontal" size={15} />
            </button>
            <div className="item-tile-icon">{itemIcon(item)}</div>
            <div className="item-tile-name" title={item.name}>
              {item.name}
            </div>
            <div className="item-tile-badges">
              <TagDots tags={tagsByResource[item.id] ?? []} />
              <CommentBadge count={commentCounts[item.id] ?? 0} />
              {item.type === "file" && locksByResource[item.id] && (
                <LockBadge lock={locksByResource[item.id]} />
              )}
            </div>
            {item.type === "file" && <div className="item-tile-meta">{formatBytes(item.size_bytes)}</div>}
          </div>
        );
      })}
    </div>
  );
}
