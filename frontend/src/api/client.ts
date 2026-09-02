// Defaults to whatever hostname/IP the page itself was loaded from, so this
// works both from localhost and from another device hitting this machine's
// LAN IP or hostname -- a hardcoded "127.0.0.1" would resolve to the
// *client's own* machine in that second case, not this server.
const API_BASE = import.meta.env.VITE_API_BASE ?? `http://${window.location.hostname}:8020`;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let authToken: string | null = null;
let activeConnectionId: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setAuthToken(token: string | null) {
  authToken = token;
}

export function getAuthToken(): string | null {
  return authToken;
}

export function setActiveConnectionId(id: string | null) {
  activeConnectionId = id;
}

export function getActiveConnectionId(): string | null {
  return activeConnectionId;
}

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    onUnauthorized?.();
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail)) {
        // FastAPI/pydantic request-validation errors (422) come back as a
        // list of {loc, msg, type} objects, not a plain string. Passing
        // that straight to `new Error(...)` silently stringifies it to
        // the literal text "[object Object]" (Error's constructor calls
        // ToString on a non-string message) — every validation error in
        // the app was surfacing as that instead of a readable message.
        const msgs = body.detail
          .map((e: { loc?: unknown[]; msg?: string }) => {
            if (typeof e?.msg !== "string") return null;
            const field = Array.isArray(e.loc) && e.loc.length ? String(e.loc[e.loc.length - 1]) : null;
            return field && field !== "body" ? `${field}: ${e.msg}` : e.msg;
          })
          .filter((m: string | null): m is string => !!m);
        if (msgs.length) detail = msgs.join("; ");
      } else if (body.detail != null) {
        detail = String(body.detail);
      }
    } catch {
      // non-JSON error body; keep the generic status detail
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  if (activeConnectionId) headers["X-Connection-Id"] = activeConnectionId;
  return headers;
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | string[] | undefined>,
  connectionIdOverride?: string,
): Promise<T> {
  const url = new URL(API_BASE + path);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined) continue;
      // Repeated query params (?k=a&k=b), not one comma-joined value — matches
      // how FastAPI's `list[str] = Query(...)` parses multi-value filters
      // (e.g. the audit log's event-type filter).
      if (Array.isArray(v)) {
        for (const item of v) url.searchParams.append(k, item);
      } else {
        url.searchParams.set(k, String(v));
      }
    }
  }
  const headers = authHeaders();
  // Browsing a connection other than the one active in the main Drive view
  // (e.g. picking a folder to scope a webhook to) without disturbing that
  // global active-connection state.
  if (connectionIdOverride) headers["X-Connection-Id"] = connectionIdOverride;
  const res = await fetch(url, { headers });
  return handle<T>(res);
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return handle<T>(res);
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  return handle<T>(res);
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  return handle<T>(res);
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetch(API_BASE + path, { method: "DELETE", headers: authHeaders() });
  return handle<T>(res);
}

export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: authHeaders(), // no Content-Type — browser sets the multipart boundary
    body: form,
  });
  return handle<T>(res);
}

export async function downloadFile(path: string, filename: string): Promise<void> {
  const res = await fetch(API_BASE + path, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, `Download failed (HTTP ${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export { API_BASE };
