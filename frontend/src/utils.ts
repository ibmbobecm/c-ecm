export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, i);
  return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "";
  // Backend timestamps are already offset-aware (e.g. "...+00:00"); only
  // naive strings (no "Z" and no "+HH:MM"/"-HH:MM" suffix) need "Z" added.
  // Blindly appending "Z" to an already-offset string (old bug) produces
  // "...+00:00Z", which Date can't parse and silently yields Invalid Date.
  const hasTz = /(Z|[+-]\d{2}:\d{2})$/.test(iso);
  const d = new Date(hasTz ? iso : iso + "Z");
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) {
    return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function keyOf(item: { type: string; id: string }): string {
  return `${item.type}-${item.id}`;
}
