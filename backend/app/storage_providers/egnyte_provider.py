"""Egnyte, via its documented Egnyte Public API v1 (JSON REST, OAuth2
authorization-code flow) — https://developers.egnyte.com/.

UNVERIFIED — written from Egnyte's published Public API v1 reference, not
exercised against a live Egnyte domain. Several things below are best-effort
reconstructions rather than line-by-line-confirmed behavior; flagged inline
and summarized here so whoever wires this in (and whoever eventually tests
it against a real tenant) knows exactly where to look first:

1. **Single-domain limitation.** Egnyte is genuinely per-customer-domain —
   the OAuth authorize/token endpoints AND the API itself all live under
   the customer's own subdomain (`https://{domain}.egnyte.com`), unlike
   Box/Google/Microsoft where one app-level client id/secret works for
   every connected account regardless of which account it is. This app's
   current `StorageProvider` OAuth shape — `get_authorize_url(state,
   redirect_uri)` with no per-connection parameters — has nowhere to
   collect a domain before redirecting the user. As a pragmatic
   workaround (matching how this codebase already treats Microsoft's
   `ms_tenant` as an admin-level setting), the domain is read from
   `settings_store.get_setting("egnyte_domain", "")` alongside the client
   id/secret — a single, one-time-per-deployment admin setting, not
   something an end user supplies per connection. A real multi-tenant
   deployment (this app talking to several different customers' Egnyte
   domains at once) would need a domain field collected before the OAuth
   redirect, which the interface doesn't support yet.

2. **Recycle bin id vs. live path.** Egnyte addresses live items by real
   filesystem PATH (used directly as this provider's opaque
   `folder_id`/`file_id`, the same convention as this codebase's Dropbox
   provider). But Egnyte's native recycle bin (`GET /recyclebin`) — used
   here as-is rather than emulating trash, since it's a real, listable,
   restorable feature — identifies each deleted entry by its OWN
   recycle-bin id, not by path. So the `FolderInfo`/`FileInfo` ids
   returned by `list_trash()` are recycle-bin ids, not paths; they're only
   ever fed back into `restore_folder`/`restore_file` (which is all the
   interface asks of them), never into path-based calls. The exact JSON
   shape of `GET /recyclebin` (top-level key name, whether each entry
   carries an explicit `is_folder` flag) is reconstructed from general
   documentation and handled defensively (`_recyclebin_entries` tries a
   few plausible key names; `_node_is_folder` falls back to a guess when
   the flag is missing) rather than confirmed against a real response.

3. **Delete vs. trash are intentionally identical.** Egnyte's `DELETE
   /fs/{path}` already routes to the native recycle bin (a soft delete),
   and no additional confirmed endpoint exists in the reference material
   here for a hard, unrecoverable purge. Rather than fabricate one,
   `delete_folder`/`delete_file` and `trash_folder`/`trash_file` are the
   same operation — both send the item to Egnyte's real recycle bin. That
   is a deliberate, honest choice, not an oversight.

4. **Version history is a safe fallback, not a full implementation.**
   Egnyte's exact query parameter for listing a file's historical
   versions via `GET /fs/{path}` (candidates seen across different
   documentation revisions include `list_versions` and `version_history`)
   isn't confidently pinned down here, and Egnyte's `fs` endpoint appears
   to silently ignore unrecognized query params rather than erroring —
   which makes a wrong guess fail silently instead of loudly. Rather than
   risk that, `list_versions()` always reports just the single current
   version (which is always correct, if incomplete), and
   `get_version_content`/`restore_version` degenerate accordingly. This
   matches this codebase's existing practice of a safe, honest fallback
   over a guessed endpoint (see e.g. Microsoft Graph's `list_trash` in
   `oauth_providers.py`).

5. **Search parameters are a best guess with a client-side fallback.**
   `GET /search` is called with a `folder`/`query`/`match_case` shape
   that is reconstructed from documentation rather than confirmed; if it
   4xx's, `search()` falls back to a recursive, client-side filtered walk
   of the app's own folder tree so a wrong guess degrades gracefully
   instead of breaking search outright.

6. **`whoami()`** assumes `GET /pubapi/v1/userinfo` returns a `email` or
   `username` field — this endpoint is one of the more consistently
   documented parts of the Public API, so confidence here is higher than
   the points above, but it's still unverified against a live tenant.

Everything else — folder/file CRUD via `POST/GET/DELETE /fs/{path}`,
content via `GET/POST /fs-content/{path}`, and the OAuth
authorize/token exchange under `/puboauth/` — is taken directly from
Egnyte's documented request/response shapes and is the part of this file
with the highest confidence.

Egnyte's own OAuth access tokens are long-lived by design and the
standard Public API v1 authorization-code flow issues no refresh token,
so `refresh_if_needed()` is a permanent no-op here — there is genuinely
nothing to refresh, not a corner this file cut.
"""

from datetime import datetime, timezone
from urllib.parse import quote

import requests

from .. import settings_store
from .base import (
    AuthMode,
    BreadcrumbEntry,
    FileInfo,
    FolderContents,
    FolderInfo,
    ProviderError,
    StorageProvider,
    VersionInfo,
)

_APP_ROOT = "/Shared/C-ECM"


class EgnyteProvider(StorageProvider):
    key = "egnyte"
    display_name = "Egnyte"
    auth_mode = AuthMode.OAUTH

    # --- admin-level app config (client id/secret + the one deployment's
    # Egnyte domain — see module docstring point 1) ---
    def _client(self) -> tuple[str, str, str]:
        return (
            settings_store.get_setting("egnyte_client_id", ""),
            settings_store.get_setting("egnyte_client_secret", ""),
            settings_store.get_setting("egnyte_domain", ""),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret, domain = self._client()
        return bool(client_id and client_secret and domain)

    # --- oauth ---
    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret, domain = self._client()
        if not domain:
            raise ProviderError(
                "Egnyte domain isn't configured yet — set it in Admin Settings before connecting",
                status_code=400,
            )
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": "Egnyte.filesystem",
        }
        return f"https://{domain}.egnyte.com/puboauth/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret, domain = self._client()
        if not domain:
            raise ProviderError(
                "Egnyte domain isn't configured yet — set it in Admin Settings before connecting",
                status_code=400,
            )
        resp = requests.post(f"https://{domain}.egnyte.com/puboauth/token", data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Egnyte token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {"access_token": tok["access_token"], "domain": domain}
        me = self._get(creds, f"{self._api(creds)}/userinfo")
        creds["identity"] = me.get("email") or me.get("username") or "Egnyte account"
        return creds

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        # See module docstring — Egnyte's standard OAuth tokens are
        # long-lived and there is no refresh_token to use even if we
        # wanted to. Nothing ever needs refreshing.
        return creds, False

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "Egnyte account")

    # --- low-level HTTP helpers ---
    def _api(self, creds: dict) -> str:
        return f"https://{creds['domain']}.egnyte.com/pubapi/v1"

    def _url(self, creds: dict, path: str, content: bool = False) -> str:
        base = f"{self._api(creds)}/fs-content" if content else f"{self._api(creds)}/fs"
        return f"{base}{quote(path, safe='/')}"

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"Egnyte error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Egnyte error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def _ensure_root(self, creds: dict) -> None:
        """Best-effort find-or-create of the app's dedicated root folder.
        No id caching is needed the way id-based providers (Box/Google/MS)
        need it — Egnyte paths are self-describing, so there's nothing to
        remember between calls. Egnyte's `fs` endpoint has no dedicated
        "does this folder exist" check, so this just attempts the create
        and swallows the "already exists" failure every call after the
        first will get."""
        resp = requests.post(
            self._url(creds, _APP_ROOT),
            headers={"Authorization": f"Bearer {creds['access_token']}", "Content-Type": "application/json"},
            json={"action": "add_folder"}, timeout=30,
        )
        if resp.status_code >= 400 and "exist" not in resp.text.lower():
            raise ProviderError(f"Egnyte error creating app root folder: {resp.text[:300]}", status_code=502)

    # --- path <-> id helpers ---
    def _parent_id(self, path: str) -> str | None:
        idx = path.rstrip("/").rfind("/")
        parent = path[:idx] if idx > 0 else "/"
        if parent in ("", "/") or parent == _APP_ROOT:
            return None
        return parent

    def _entry_path(self, e: dict, parent_path: str | None) -> str:
        p = e.get("path")
        if p:
            return p
        base = (parent_path or _APP_ROOT).rstrip("/")
        return f"{base}/{e['name']}"

    def _node_to_folder(self, e: dict, parent_path: str | None = None) -> FolderInfo:
        path = self._entry_path(e, parent_path)
        return FolderInfo(id=path, name=e.get("name") or path.rsplit("/", 1)[-1], parent_id=self._parent_id(path), created_at=None)

    def _node_to_file(self, e: dict, parent_path: str | None = None) -> FileInfo:
        path = self._entry_path(e, parent_path)
        return FileInfo(
            id=path, name=e.get("name") or path.rsplit("/", 1)[-1], folder_id=self._parent_id(path),
            version_number=int(e.get("num_versions") or 1), size_bytes=e.get("size"), content_type=None,
            updated_at=self._parse_ts(e.get("uploaded")),
        )

    @staticmethod
    def _parse_ts(value) -> datetime | None:
        """Egnyte's fs/recyclebin timestamps show up as epoch milliseconds
        in most documented examples, but handle a plain ISO 8601 string
        too rather than assume — cheap to support both, and silently
        returning None on anything unexpected beats crashing a listing
        over a display timestamp."""
        if value is None:
            return None
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    # --- folders ---
    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        path = folder_id or _APP_ROOT
        if folder_id is None:
            self._ensure_root(creds)
        data = self._get(creds, self._url(creds, path))
        folders = [self._node_to_folder(f, parent_path=path) for f in data.get("folders", [])]
        files = [self._node_to_file(f, parent_path=path) for f in data.get("files", [])]
        current_folder = self._node_to_folder(data) if folder_id is not None else None
        return FolderContents(
            folder=current_folder,
            breadcrumb=[BreadcrumbEntry(id=None, name="C-ECM")],
            folders=folders, files=files,
        )

    def _recyclebin_entries(self, creds: dict) -> list[dict]:
        # Exact top-level key name is unconfirmed (see module docstring
        # point 2) -- try the plausible candidates before giving up.
        data = self._get(creds, f"{self._api(creds)}/recyclebin")
        return data.get("results") or data.get("entries") or data.get("recycle_bin") or []

    @staticmethod
    def _node_is_folder(e: dict) -> bool:
        if "is_folder" in e:
            return bool(e["is_folder"])
        # No explicit flag on this entry -- a present "size" key is the
        # best available signal that this is a file, not a folder.
        return e.get("size") is None

    def list_trash(self, creds: dict) -> FolderContents:
        folders: list[FolderInfo] = []
        files: list[FileInfo] = []
        for e in self._recyclebin_entries(creds):
            path = e.get("path") or e.get("name") or ""
            name = path.rsplit("/", 1)[-1] or path
            entry_id = str(e.get("id"))
            deleted_at = self._parse_ts(e.get("deleted_date") or e.get("deleted"))
            if self._node_is_folder(e):
                folders.append(FolderInfo(id=entry_id, name=name, parent_id=None, created_at=None))
            else:
                files.append(FileInfo(
                    id=entry_id, name=name, folder_id=None, version_number=1,
                    size_bytes=e.get("size"), content_type=None, updated_at=deleted_at,
                ))
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")], folders=folders, files=files)

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = parent_id or _APP_ROOT
        if parent_id is None:
            self._ensure_root(creds)
        path = f"{parent.rstrip('/')}/{name}"
        self._call(creds, "POST", self._url(creds, path), json={"action": "add_folder"})
        data = self._get(creds, self._url(creds, path))
        return self._node_to_folder(data)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        parent = self._parent_id(folder_id) or _APP_ROOT
        new_path = f"{parent.rstrip('/')}/{name}"
        self._call(creds, "POST", self._url(creds, folder_id), json={"action": "move", "destination": new_path})
        data = self._get(creds, self._url(creds, new_path))
        return self._node_to_folder(data)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        name = folder_id.rstrip("/").rsplit("/", 1)[-1]
        new_parent = new_parent_id or _APP_ROOT
        new_path = f"{new_parent.rstrip('/')}/{name}"
        self._call(creds, "POST", self._url(creds, folder_id), json={"action": "move", "destination": new_path})
        data = self._get(creds, self._url(creds, new_path))
        return self._node_to_folder(data)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        # Egnyte's DELETE already routes to its native recycle bin -- see
        # module docstring point 3 for why trash_folder just delegates
        # here rather than this app inventing a separate hard-delete.
        self._call(creds, "DELETE", self._url(creds, folder_id))

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self.delete_folder(creds, folder_id)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        entry = self._find_recyclebin_entry(creds, folder_id)
        self._call(creds, "POST", f"{self._api(creds)}/recyclebin/{folder_id}", json={"action": "restore"})
        path = entry.get("path") or entry.get("name")
        data = self._get(creds, self._url(creds, path))
        return self._node_to_folder(data)

    def _find_recyclebin_entry(self, creds: dict, entry_id: str) -> dict:
        for e in self._recyclebin_entries(creds):
            if str(e.get("id")) == str(entry_id):
                return e
        raise ProviderError("Recycle bin item not found", status_code=404)

    # --- files ---
    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = folder_id or _APP_ROOT
        if folder_id is None:
            self._ensure_root(creds)
        path = f"{parent.rstrip('/')}/{name}"
        resp = requests.post(
            self._url(creds, path, content=True),
            headers={"Authorization": f"Bearer {creds['access_token']}", "Content-Type": content_type},
            data=content, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Egnyte upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        data = self._get(creds, self._url(creds, path))
        return self._node_to_file(data)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        data = self._get(creds, self._url(creds, file_id))
        return self._node_to_file(data)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        parent = self._parent_id(file_id) or _APP_ROOT
        new_path = f"{parent.rstrip('/')}/{name}"
        self._call(creds, "POST", self._url(creds, file_id), json={"action": "move", "destination": new_path})
        data = self._get(creds, self._url(creds, new_path))
        return self._node_to_file(data)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        name = file_id.rstrip("/").rsplit("/", 1)[-1]
        new_parent = new_folder_id or _APP_ROOT
        new_path = f"{new_parent.rstrip('/')}/{name}"
        self._call(creds, "POST", self._url(creds, file_id), json={"action": "move", "destination": new_path})
        data = self._get(creds, self._url(creds, new_path))
        return self._node_to_file(data)

    def delete_file(self, creds: dict, file_id: str) -> None:
        # See delete_folder -- same rationale, Egnyte's DELETE already IS
        # a (recoverable, native-recycle-bin) delete.
        self._call(creds, "DELETE", self._url(creds, file_id))

    def trash_file(self, creds: dict, file_id: str) -> None:
        self.delete_file(creds, file_id)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        entry = self._find_recyclebin_entry(creds, file_id)
        self._call(creds, "POST", f"{self._api(creds)}/recyclebin/{file_id}", json={"action": "restore"})
        path = entry.get("path") or entry.get("name")
        data = self._get(creds, self._url(creds, path))
        return self._node_to_file(data)

    def get_content(self, creds: dict, file_id: str) -> bytes:
        resp = self._call(creds, "GET", self._url(creds, file_id, content=True))
        return resp.content

    # --- versions (see module docstring point 4 for why this is a safe,
    # current-version-only fallback rather than a full implementation) ---
    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        data = self._get(creds, self._url(creds, file_id))
        return [VersionInfo(
            id=file_id, version_number=1, size_bytes=data.get("size"), content_type=None,
            is_current=True, updated_at=self._parse_ts(data.get("uploaded")),
        )]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        resp = requests.post(
            self._url(creds, file_id, content=True),
            headers={"Authorization": f"Bearer {creds['access_token']}", "Content-Type": content_type},
            data=content, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Egnyte version upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        # Only ever called with the single "current version" id that
        # list_versions() above hands out, so this is just the live
        # content -- see module docstring point 4.
        return self.get_content(creds, file_id)

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        # See get_version_content -- there is only ever the current
        # version to "restore" to.
        return self.get_file(creds, file_id)

    # --- search ---
    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        try:
            data = self._get(creds, f"{self._api(creds)}/search", params={
                "folder": _APP_ROOT, "query": query, "match_case": "false",
            })
            entries = data.get("results") or data.get("entries") or []
            folders = [self._node_to_folder(e) for e in entries if self._node_is_folder(e)]
            files = [self._node_to_file(e) for e in entries if not self._node_is_folder(e)]
            return folders, files
        except ProviderError:
            # Unconfirmed query-param shape (module docstring point 5) --
            # degrade to a client-side filtered walk rather than fail.
            return self._search_fallback(creds, _APP_ROOT, query.lower())

    def _search_fallback(self, creds: dict, path: str, needle: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        folders_out: list[FolderInfo] = []
        files_out: list[FileInfo] = []
        try:
            data = self._get(creds, self._url(creds, path))
        except ProviderError:
            return folders_out, files_out
        for f in data.get("folders", []):
            node = self._node_to_folder(f, parent_path=path)
            if needle in node.name.lower():
                folders_out.append(node)
            sub_folders, sub_files = self._search_fallback(creds, node.id, needle)
            folders_out.extend(sub_folders)
            files_out.extend(sub_files)
        for fi in data.get("files", []):
            node = self._node_to_file(fi, parent_path=path)
            if needle in node.name.lower():
                files_out.append(node)
        return folders_out, files_out
