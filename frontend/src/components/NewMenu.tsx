import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";

export function NewMenu({
  disabled,
  uploading,
  onNewFolder,
  onUploadFiles,
  onUploadFolder,
}: {
  disabled: boolean;
  uploading: boolean;
  onNewFolder: () => void;
  onUploadFiles: () => void;
  onUploadFolder: () => void;
}) {
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

  const item = (icon: Parameters<typeof Icon>[0]["name"], label: string, onClick: () => void) => (
    <button
      onClick={() => {
        setOpen(false);
        onClick();
      }}
    >
      <Icon name={icon} size={16} />
      {label}
    </button>
  );

  return (
    <div className="new-menu-wrap" ref={ref}>
      <button
        className="new-menu-btn primary new-menu-trigger"
        onClick={() => setOpen((o) => !o)}
        disabled={disabled || uploading}
      >
        <Icon name="plus" size={17} />
        <span className="sidebar-label">{uploading ? "Uploading..." : "New"}</span>
      </button>
      {open && (
        <div className="new-menu-panel">
          {item("upload", "File upload", onUploadFiles)}
          {item("upload", "Folder upload", onUploadFolder)}
          {item("folder-plus", "New folder", onNewFolder)}
        </div>
      )}
    </div>
  );
}
