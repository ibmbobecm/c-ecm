import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";

export function AdminMenu({
  onOpenIntegrations,
  onOpenUsers,
  onOpenDocClasses,
  onOpenWebhooks,
  onOpenRetention,
  onOpenAuditLog,
}: {
  onOpenIntegrations: () => void;
  onOpenUsers: () => void;
  onOpenDocClasses: () => void;
  onOpenWebhooks: () => void;
  onOpenRetention: () => void;
  onOpenAuditLog: () => void;
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
    <div className="admin-menu-wrap" ref={ref}>
      <button
        className="icon-btn"
        onClick={() => setOpen((o) => !o)}
        aria-label="Admin settings"
        title="Admin settings"
      >
        <Icon name="settings" size={18} />
      </button>
      {open && (
        <div className="admin-menu-panel">
          <div className="sidebar-section-label">Admin</div>
          {item("plug", "Connections", onOpenIntegrations)}
          {item("eye", "Users", onOpenUsers)}
          {item("tag", "Doc Classes", onOpenDocClasses)}
          {item("link", "Webhooks", onOpenWebhooks)}
          {item("lock", "Retention", onOpenRetention)}
          {item("bar-chart", "Reports", onOpenAuditLog)}
        </div>
      )}
    </div>
  );
}
