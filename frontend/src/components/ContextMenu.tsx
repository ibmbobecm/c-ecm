import { useEffect, useRef } from "react";
import { Icon, type IconName } from "../icons";

export type MenuAction = {
  label: string;
  onClick: () => void;
  icon?: IconName;
  danger?: boolean;
  separatorBefore?: boolean;
};

// One menu, two triggers: right-click and the hover-revealed "⋯" button both
// call this with different anchor coordinates but the exact same item list,
// so there's no functionality that only exists behind a right-click — that
// matters for touch/tablet and keyboard-only users, who have no right-click
// gesture at all.
export function ContextMenu({
  x,
  y,
  actions,
  onClose,
}: {
  x: number;
  y: number;
  actions: MenuAction[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [onClose]);

  const estHeight = actions.length * 36 + actions.filter((a) => a.separatorBefore).length * 9 + 16;
  const maxLeft = Math.min(x, window.innerWidth - 210);
  const maxTop = Math.min(y, window.innerHeight - estHeight - 12);

  return (
    <div className="context-menu" style={{ left: Math.max(8, maxLeft), top: Math.max(8, maxTop) }} ref={ref}>
      {actions.map((a) => (
        <div key={a.label}>
          {a.separatorBefore && <div className="context-menu-sep" />}
          <button
            className={a.danger ? "danger" : ""}
            onClick={() => {
              a.onClick();
              onClose();
            }}
          >
            {a.icon && <Icon name={a.icon} size={16} />}
            {a.label}
          </button>
        </div>
      ))}
    </div>
  );
}
