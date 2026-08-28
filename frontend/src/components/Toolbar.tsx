import { useState, useRef, useEffect, forwardRef } from "react";
import type { BreadcrumbEntry } from "../types";
import { Icon } from "../icons";

function BreadcrumbOverflow({ hidden, onNavigate }: { hidden: BreadcrumbEntry[]; onNavigate: (id: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <span style={{ position: "relative" }} ref={ref}>
      <button className="breadcrumb-overflow-btn" onClick={() => setOpen((o) => !o)} aria-label="Show hidden folders">
        &hellip;
      </button>
      {open && (
        <div className="context-menu" style={{ position: "absolute", left: 0, top: "100%" }}>
          {hidden.map((b) => (
            <button
              key={b.id ?? "root"}
              onClick={() => {
                onNavigate(b.id);
                setOpen(false);
              }}
            >
              {b.name}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

export const Toolbar = forwardRef<
  HTMLInputElement,
  {
    breadcrumb: BreadcrumbEntry[];
    onNavigate: (id: string | null) => void;
    searchQuery: string;
    onSearchChange: (q: string) => void;
    layout: "grid" | "list";
    onLayoutChange: (l: "grid" | "list") => void;
    showBreadcrumb: boolean;
    selectionBar?: React.ReactNode;
  }
>(function Toolbar(
  { breadcrumb, onNavigate, searchQuery, onSearchChange, layout, onLayoutChange, showBreadcrumb, selectionBar },
  searchRef
) {
  // Deep paths (several backends here proxy inherited folder trees, e.g.
  // Alfresco/FileNet) collapse the middle into a "…" dropdown instead of
  // wrapping or scrolling — the crumb bar always stays one line.
  const collapse = breadcrumb.length > 4;
  const visible = collapse
    ? [breadcrumb[0], { __overflow: breadcrumb.slice(1, -2) } as unknown as BreadcrumbEntry, ...breadcrumb.slice(-2)]
    : breadcrumb;

  return (
    <div className="toolbar">
      <div className="toolbar-search">
        <div className="toolbar-search-input-wrap">
          <span className="search-icon">
            <Icon name="search" size={15} />
          </span>
          <input
            ref={searchRef}
            placeholder="Search files and folders"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
          />
          {searchQuery && (
            <button className="search-clear" onClick={() => onSearchChange("")} aria-label="Clear search">
              <Icon name="close" size={14} />
            </button>
          )}
        </div>
        {!searchQuery && <kbd className="search-hint">/</kbd>}
      </div>

      <div className="toolbar-row">
        {selectionBar ? (
          selectionBar
        ) : showBreadcrumb ? (
          <div className="breadcrumb">
            {visible.map((b, i) => {
              const isOverflow = (b as unknown as { __overflow?: BreadcrumbEntry[] }).__overflow;
              if (isOverflow) {
                return (
                  <span key="overflow">
                    <BreadcrumbOverflow hidden={isOverflow} onNavigate={onNavigate} />
                    <span className="breadcrumb-sep">
                      <Icon name="chevron-right" size={13} />
                    </span>
                  </span>
                );
              }
              const isLast = i === visible.length - 1;
              return (
                <span key={b.id ?? "root"}>
                  <button
                    className={"breadcrumb-item" + (isLast ? " current" : "")}
                    onClick={() => !isLast && onNavigate(b.id)}
                  >
                    {b.name}
                  </button>
                  {!isLast && (
                    <span className="breadcrumb-sep">
                      <Icon name="chevron-right" size={13} />
                    </span>
                  )}
                </span>
              );
            })}
          </div>
        ) : (
          <div />
        )}

        <div className="layout-toggle">
          <button
            className={layout === "grid" ? "active" : ""}
            onClick={() => onLayoutChange("grid")}
            aria-label="Grid view"
          >
            <Icon name="grid-view" size={16} />
          </button>
          <button
            className={layout === "list" ? "active" : ""}
            onClick={() => onLayoutChange("list")}
            aria-label="List view"
          >
            <Icon name="list-view" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
});
