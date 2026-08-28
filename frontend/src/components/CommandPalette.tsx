import { useEffect, useMemo, useRef, useState } from "react";
import { Icon, type IconName } from "../icons";

export type PaletteItem = {
  id: string;
  group: string;
  label: string;
  sublabel?: string;
  icon: IconName;
  onSelect: () => void;
};

// One palette, three merged sources (current-folder items, sidebar nav
// destinations, connection-switch actions) — the concrete payoff of
// building this for a multi-backend app specifically: it replaces three
// separate UI surfaces (search box, nav, connection switcher) with one.
export function CommandPalette({ items, onClose }: { items: PaletteItem[]; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((i) => i.label.toLowerCase().includes(q) || i.sublabel?.toLowerCase().includes(q));
  }, [items, query]);

  useEffect(() => setActiveIndex(0), [query]);

  const groups = useMemo(() => {
    const order: string[] = [];
    const byGroup = new Map<string, PaletteItem[]>();
    for (const item of filtered) {
      if (!byGroup.has(item.group)) {
        byGroup.set(item.group, []);
        order.push(item.group);
      }
      byGroup.get(item.group)!.push(item);
    }
    return order.map((g) => ({ group: g, items: byGroup.get(g)! }));
  }, [filtered]);

  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${activeIndex}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  const activate = (item: PaletteItem) => {
    item.onSelect();
    onClose();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = filtered[activeIndex];
      if (item) activate(item);
    }
  };

  let flatIndex = -1;

  return (
    <div className="palette-overlay" onMouseDown={onClose}>
      <div className="palette-card" onMouseDown={(e) => e.stopPropagation()} onKeyDown={onKeyDown}>
        <div className="palette-input-row">
          <Icon name="search" size={16} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search files, jump to a folder, switch connection..."
          />
          <kbd>Esc</kbd>
        </div>
        <div className="palette-results" ref={listRef}>
          {groups.length === 0 && <div className="palette-empty">No matches for "{query}".</div>}
          {groups.map(({ group, items: groupItems }) => (
            <div key={group}>
              <div className="palette-group-label">{group}</div>
              {groupItems.map((item) => {
                flatIndex++;
                const idx = flatIndex;
                return (
                  <button
                    key={item.id}
                    data-idx={idx}
                    className={"palette-item" + (idx === activeIndex ? " active" : "")}
                    onMouseEnter={() => setActiveIndex(idx)}
                    onClick={() => activate(item)}
                  >
                    <span className="palette-item-icon">
                      <Icon name={item.icon} size={16} />
                    </span>
                    <span>
                      {item.label}
                      {item.sublabel && <span className="muted"> — {item.sublabel}</span>}
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <div className="palette-hint">
          <span>
            <kbd>&uarr;</kbd> <kbd>&darr;</kbd> navigate
          </span>
          <span>
            <kbd>&crarr;</kbd> select
          </span>
          <span>
            <kbd>esc</kbd> close
          </span>
        </div>
      </div>
    </div>
  );
}
