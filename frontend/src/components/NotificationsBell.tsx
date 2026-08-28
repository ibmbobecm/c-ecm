import { useEffect, useRef, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import type { NotificationSummary } from "../types";
import { Icon } from "../icons";
import { formatDate } from "../utils";

const POLL_MS = 30000;

export function NotificationsBell() {
  const [summary, setSummary] = useState<NotificationSummary | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const load = () => {
    apiGet<NotificationSummary>("/notifications").then(setSummary).catch(() => {});
  };

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const markAllRead = async () => {
    await apiPost("/notifications/read-all");
    load();
  };

  const markRead = async (id: string) => {
    await apiPost(`/notifications/${id}/read`);
    load();
  };

  const unread = summary?.unread_count ?? 0;

  return (
    <div className="notif-bell-wrap" ref={ref}>
      <button
        className="icon-btn notif-bell-btn"
        onClick={() => {
          setOpen((o) => !o);
          if (!open) load();
        }}
        aria-label={unread > 0 ? `${unread} unread notifications` : "Notifications"}
      >
        <Icon name="bell" size={18} />
        {unread > 0 && <span className="notif-badge">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {open && (
        <div className="notif-panel">
          <div className="notif-panel-header">
            <span>Notifications</span>
            {unread > 0 && (
              <button className="link-btn" onClick={markAllRead}>
                Mark all read
              </button>
            )}
          </div>
          <div className="notif-panel-list">
            {(!summary || summary.notifications.length === 0) && <div className="palette-empty">No notifications yet.</div>}
            {summary?.notifications.map((n) => (
              <button
                key={n.id}
                className={"notif-row" + (n.read_at ? "" : " unread")}
                onClick={() => !n.read_at && markRead(n.id)}
              >
                <span className="notif-dot" />
                <span className="notif-row-body">
                  <span className="notif-row-message">{n.message}</span>
                  <span className="notif-row-time muted">{formatDate(n.created_at)}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
