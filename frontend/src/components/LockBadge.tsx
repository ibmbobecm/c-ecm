/**
 * LockBadge — shows a lock indicator when a file is checked out.
 * Used inline in ItemGrid and ItemList rows.
 */
import { Icon } from "../icons";
import type { Lock } from "../types";

export function LockBadge({ lock }: { lock: Lock }) {
  return (
    <span
      title={`Checked out by ${lock.locked_by}${lock.comment ? ` — ${lock.comment}` : ""}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        fontSize: 10,
        fontWeight: 600,
        color: "#d97706",
        background: "#fef3c722",
        border: "1px solid #d9770644",
        padding: "2px 6px",
        borderRadius: 999,
        flexShrink: 0,
      }}
    >
      <Icon name="lock" size={10} />
      {lock.locked_by}
    </span>
  );
}
