"""Zoho WorkDrive, via its documented WorkDrive API v1 (JSON:API-shaped
REST — every resource comes back as `{"id", "type", "attributes"}`,
collections as `{"data": [...]}`) with a standard Zoho OAuth2
authorization-code flow — https://workdrive.zoho.com/apidocs/.

UNVERIFIED — written from Zoho WorkDrive's published API reference, not
exercised against a live Zoho account. The general folder/file/version
CRUD shape (`GET/POST/PATCH/DELETE /files/{id}`, JSON:API request/response
bodies, the OAuth authorize/token exchange under `accounts.zoho.com`) is
taken directly from the documented conventions and is the highest-
confidence part of this file. The pieces below are best-effort
reconstructions where the reference material was genuinely ambiguous;
flagged here so whoever wires this in (and whoever eventually tests it
against a real tenant) knows exactly where to look first:

1. **"My Folders" root discovery.** Zoho WorkDrive organizes everything
   under Teams > Team Folders / "My Folders" > subfolders > files, and
   there's no single, unambiguous documented call for "give me this
   account's My Folders root id" the way Box's `/folders/0` or Google's
   literal `"root"` alias does. `_my_folders_id()` tries `GET
   /privatespace/folders` first (the more specific, and hopefully
   dedicated, private-space listing) and, if that call itself 4xx's,
   falls back to `GET /users/me` and grabs whichever plausible id-shaped
   attribute (`workspace_id`, `myfolder_id`, `home_folder_id`, ...) shows
   up on that response. Both are guesses; a real account may need a
   different call entirely.

2. **Move / restore attribute names and HTTP verb.** Renaming
   (`attributes.name` via `PATCH /files/{id}`) is confidently documented.
   Moving a file/folder to a new parent is assumed to update
   `attributes.parent_id` the same way creation does — some reference
   material for this API uses an oddly-cased `RESOURCE_ID`-style field
   instead for reparenting, which isn't confirmed here, so `parent_id` was
   kept for internal consistency with `create_folder`'s own body shape.
   Restoring a trashed item is implemented as a `PATCH` flipping
   `attributes.status` back to `"0"` (Zoho's documented soft-delete/trash
   pattern uses a status flag), using `PATCH` rather than `POST` to stay
   consistent with how every other attribute update in this API works —
   genuinely possible the real endpoint wants `POST` instead.

3. **Trash listing endpoint.** Deleting via `DELETE /files/{id}` is Zoho
   WorkDrive's real, native, restorable trash (not emulated) — but which
   GET lists what's in it isn't pinned down confidently. `_trash_entries()`
   tries `/privatespace/trash` first, then the flatter `/trash`, and
   returns an empty list rather than raising if neither responds — trash
   is nice-to-have, not something that should break `list_trash()` outright
   on a wrong guess.

4. **Version creation.** The reference material describes listing
   (`GET /files/{id}/versions`), downloading a specific version
   (`GET /download/{id}?version_id=...`), and the general upload endpoint
   (`POST /uploads`), but no distinct "upload a new version of this exact
   file" call. `create_version()` re-uploads to the file's own parent
   folder under its existing filename with `override-name-exist=true` —
   Zoho WorkDrive's documented behavior for an upload that collides with
   an existing name in the same folder is to version it rather than
   duplicate it, so this is a reasonable, but unconfirmed, way to reuse
   that behavior for explicit versioning. `restore_version()` then degrades
   to the same safe download-old-bytes-then-reupload pattern this
   codebase's Google Drive provider already uses for the same reason.

5. **Per-datacenter API host.** Zoho accounts outside the US datacenter
   (.eu, .in, .com.cn, ...) get handed a different API host, returned as
   `api_domain` on every token response — not really a documented
   *uncertainty* so much as a well-known Zoho quirk worth flagging: this
   provider stores it in `creds["api_domain"]` at OAuth time and uses it
   for every later call (falling back to `www.zohoapis.com` only if it's
   ever missing), the same way this codebase already treats Microsoft's
   `ms_tenant` as a small piece of per-connection state rather than a
   hardcoded constant.

`whoami()`/identity comes from `GET /users/me`, which is one of the more
consistently documented parts of this API, so confidence there is higher
than points 1-4 above.
"""

import threading
import time
from datetime import datetime, timezone

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

_APP_ROOT_NAME = "C-ECM"
_ACCOUNTS_BASE = "https://accounts.zoho.com"


class ZohoWorkDriveProvider(StorageProvider):
    key = "zoho_workdrive"
    display_name = "Zoho WorkDrive"
    auth_mode = AuthMode.OAUTH

    def __init__(self):
        # Keyed by `identity` (not a single shared value), since this
        # provider instance is a process-wide singleton reused across every
        # connection to it -- see GoogleDriveProvider._root_id in
        # oauth_providers.py for the full rationale and why the lookup +
        # create needs a lock (double-checked below) rather than racing two
        # concurrent first-requests into each creating their own "C-ECM"
        # folder.
        self._root_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()

    # --- admin-level app config (the one OAuth client this whole C-ECM
    # deployment registers with Zoho, shared by every connection) ---
    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("zoho_workdrive_client_id", ""),
            settings_store.get_setting("zoho_workdrive_client_secret", ""),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret = self._client()
        return bool(client_id and client_secret)

    # --- oauth ---
    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret = self._client()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "WorkDrive.files.ALL",
            "access_type": "offline",
            "state": state,
        }
        return f"{_ACCOUNTS_BASE}/oauth/v2/auth?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret = self._client()
        resp = requests.post(f"{_ACCOUNTS_BASE}/oauth/v2/token", data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Zoho WorkDrive token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + tok.get("expires_in", 3600),
            # See module docstring point 5 -- the datacenter-specific API
            # host Zoho hands back alongside the tokens.
            "api_domain": tok.get("api_domain") or "https://www.zohoapis.com",
        }
        me = self._get(creds, f"{self._api(creds)}/users/me")
        attrs = (me.get("data") or {}).get("attributes") or {}
        creds["identity"] = attrs.get("email_id") or attrs.get("display_name") or attrs.get("name") or "Zoho WorkDrive account"
        return creds

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        if creds.get("expires_at", 0) > time.time() + 30:
            return creds, False
        if not creds.get("refresh_token"):
            raise ProviderError("Zoho WorkDrive session expired — please reconnect", status_code=401)
        client_id, client_secret = self._client()
        resp = requests.post(f"{_ACCOUNTS_BASE}/oauth/v2/token", data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Zoho WorkDrive session expired — please reconnect", status_code=401)
        tok = resp.json()
        # Mutated in place (not just returned) so a caller that only reads
        # the passed-in dict after this call -- e.g. this provider's own
        # _call() below, which discards the tuple's first element -- still
        # sees the refreshed token, the same pattern this codebase's other
        # OAuth providers rely on.
        creds["access_token"] = tok["access_token"]
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        # Zoho's refresh grant does not reliably return a new refresh_token
        # (they're long-lived and meant to be reused) -- only overwrite it
        # if one actually comes back.
        if tok.get("refresh_token"):
            creds["refresh_token"] = tok["refresh_token"]
        if tok.get("api_domain"):
            creds["api_domain"] = tok["api_domain"]
        return creds, True

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "Zoho WorkDrive account")

    # --- low-level HTTP helpers ---
    def _api(self, creds: dict) -> str:
        domain = creds.get("api_domain") or "https://www.zohoapis.com"
        return f"{domain}/workdrive/api/v1"

    def _headers(self, creds: dict) -> dict:
        # Zoho's own documented scheme -- deliberately not "Bearer".
        return {"Authorization": f"Zoho-oauthtoken {creds['access_token']}"}

    @staticmethod
    def _error_message(resp: requests.Response) -> str:
        try:
            errors = resp.json().get("errors")
            if errors:
                first = errors[0]
                return first.get("detail") or first.get("title") or resp.text[:300]
        except Exception:
            pass
        return resp.text[:300]

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers=self._headers(creds), timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"Zoho WorkDrive error {resp.status_code}: {self._error_message(resp)}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds, _ = self.refresh_if_needed(creds)
        resp = requests.request(method, url, headers=self._headers(creds), timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Zoho WorkDrive error {resp.status_code}: {self._error_message(resp)}", status_code=502)
        return resp

    # --- root discovery / app folder (see module docstring point 1) ---
    def _my_folders_id(self, creds: dict) -> str:
        try:
            result = self._get(creds, f"{self._api(creds)}/privatespace/folders")
            entries = result.get("data") or []
            if entries:
                return entries[0]["id"]
        except ProviderError:
            pass
        me = self._get(creds, f"{self._api(creds)}/users/me")
        attrs = (me.get("data") or {}).get("attributes") or {}
        for candidate_key in ("workspace_id", "myfolder_id", "home_folder_id", "zoho_workdrive_root_id"):
            if attrs.get(candidate_key):
                return attrs[candidate_key]
        raise ProviderError(
            "Could not determine Zoho WorkDrive's 'My Folders' root for this account "
            "(neither /privatespace/folders nor /users/me returned a usable id)",
            status_code=502,
        )

    def _root_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "")
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        # Double-checked locking -- see __init__ for why.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            my_folders_id = self._my_folders_id(creds)
            result = self._get(creds, f"{self._api(creds)}/files/{my_folders_id}/files")
            for e in result.get("data") or []:
                attrs = e.get("attributes") or {}
                if self._is_folder(attrs) and attrs.get("name") == _APP_ROOT_NAME:
                    self._root_id_cache[cache_key] = e["id"]
                    return e["id"]
            created = self._call(creds, "POST", f"{self._api(creds)}/files", json={
                "data": {"attributes": {"name": _APP_ROOT_NAME, "parent_id": my_folders_id}, "type": "files"},
            }).json()
            entry = created.get("data")
            if isinstance(entry, list):
                entry = entry[0] if entry else {}
            new_id = (entry or {}).get("id")
            if not new_id:
                raise ProviderError("Zoho WorkDrive did not return an id for the newly created app root folder", status_code=502)
            self._root_id_cache[cache_key] = new_id
            return new_id

    # --- JSON:API entry <-> dataclass conversion ---
    @staticmethod
    def _is_folder(attrs: dict) -> bool:
        return (attrs.get("type") or "").lower() == "folder"

    @staticmethod
    def _parse_dt(value) -> datetime | None:
        """Zoho's WorkDrive timestamps show up as epoch milliseconds in
        some documented examples and plain ISO 8601 strings in others --
        handle both rather than assume, same defensive approach this
        codebase's Egnyte provider uses for its own ambiguous timestamps."""
        if value in (None, ""):
            return None
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
            s = str(value)
            if s.isdigit():
                return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def _node_to_folder(self, e: dict, root_id: str) -> FolderInfo:
        attrs = e.get("attributes") or {}
        parent_id = attrs.get("parent_id")
        if parent_id == root_id:
            parent_id = None
        return FolderInfo(
            id=e["id"], name=attrs.get("name", ""), parent_id=parent_id,
            created_at=self._parse_dt(attrs.get("created_time")),
        )

    def _node_to_file(self, e: dict, root_id: str) -> FileInfo:
        attrs = e.get("attributes") or {}
        parent_id = attrs.get("parent_id")
        if parent_id == root_id:
            parent_id = None
        storage_info = attrs.get("storage_info")
        size = storage_info.get("size") if isinstance(storage_info, dict) else attrs.get("size")
        try:
            size_bytes = int(size) if size not in (None, "") else None
        except (TypeError, ValueError):
            size_bytes = None
        return FileInfo(
            id=e["id"], name=attrs.get("name", ""), folder_id=parent_id, version_number=1,
            size_bytes=size_bytes, content_type=attrs.get("mime_type"),
            updated_at=self._parse_dt(attrs.get("modified_time")),
        )

    @staticmethod
    def _first_data(body: dict) -> dict:
        entry = body.get("data")
        if isinstance(entry, list):
            return entry[0] if entry else {}
        return entry or {}

    # --- folders ---
    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id or root_id
        result = self._get(creds, f"{self._api(creds)}/files/{node_id}/files")
        entries = result.get("data") or []
        folders = [self._node_to_folder(e, root_id) for e in entries if self._is_folder(e.get("attributes") or {})]
        files = [self._node_to_file(e, root_id) for e in entries if not self._is_folder(e.get("attributes") or {})]
        current_folder = None
        if folder_id is not None:
            node = self._get(creds, f"{self._api(creds)}/files/{node_id}")
            current_folder = self._node_to_folder(self._first_data(node), root_id)
        return FolderContents(
            folder=current_folder,
            breadcrumb=[BreadcrumbEntry(id=None, name="My Folders")],
            folders=folders, files=files,
        )

    def _trash_entries(self, creds: dict) -> list[dict]:
        # See module docstring point 3 -- exact path unconfirmed, tried in
        # order of confidence; degrades to empty rather than raising.
        for path in ("privatespace/trash", "trash"):
            try:
                result = self._get(creds, f"{self._api(creds)}/{path}")
                return result.get("data") or []
            except ProviderError:
                continue
        return []

    def list_trash(self, creds: dict) -> FolderContents:
        root_id = self._root_id(creds)
        entries = self._trash_entries(creds)
        folders = [self._node_to_folder(e, root_id) for e in entries if self._is_folder(e.get("attributes") or {})]
        files = [self._node_to_file(e, root_id) for e in entries if not self._is_folder(e.get("attributes") or {})]
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")], folders=folders, files=files)

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        root_id = self._root_id(creds)
        parent = parent_id or root_id
        resp = self._call(creds, "POST", f"{self._api(creds)}/files", json={
            "data": {"attributes": {"name": name, "parent_id": parent}, "type": "files"},
        }).json()
        return self._node_to_folder(self._first_data(resp), root_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        resp = self._call(creds, "PATCH", f"{self._api(creds)}/files/{folder_id}", json={
            "data": {"attributes": {"name": name}, "id": folder_id, "type": "files"},
        }).json()
        return self._node_to_folder(self._first_data(resp), self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        # See module docstring point 2 -- `parent_id` is the best-effort
        # field name, kept consistent with create_folder's own body shape.
        root_id = self._root_id(creds)
        target = new_parent_id or root_id
        resp = self._call(creds, "PATCH", f"{self._api(creds)}/files/{folder_id}", json={
            "data": {"attributes": {"parent_id": target}, "id": folder_id, "type": "files"},
        }).json()
        return self._node_to_folder(self._first_data(resp), root_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{self._api(creds)}/files/{folder_id}")

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        # Zoho WorkDrive's DELETE already routes to its real, native,
        # restorable trash -- delete and trash are the same call here, the
        # same honest choice this codebase's Egnyte provider makes for the
        # same reason.
        self.delete_folder(creds, folder_id)

    def _restore(self, creds: dict, resource_id: str) -> dict:
        # See module docstring point 2 -- status-flip via PATCH is a
        # best-effort reconstruction of Zoho's documented trash pattern.
        resp = self._call(creds, "PATCH", f"{self._api(creds)}/files/{resource_id}", json={
            "data": {"attributes": {"status": "0"}, "id": resource_id, "type": "files"},
        }).json()
        return self._first_data(resp)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        return self._node_to_folder(self._restore(creds, folder_id), self._root_id(creds))

    # --- files ---
    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        creds, _ = self.refresh_if_needed(creds)
        root_id = self._root_id(creds)
        parent = folder_id or root_id
        resp = requests.post(
            f"{self._api(creds)}/uploads",
            headers=self._headers(creds),
            data={"parent_id": parent, "filename": name},
            files={"content": (name, content, content_type)},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Zoho WorkDrive upload failed ({resp.status_code}): {self._error_message(resp)}", status_code=502)
        entry = self._first_data(resp.json() if resp.content else {})
        attrs = entry.get("attributes") or {}
        file_id = entry.get("id") or attrs.get("resource_id") or attrs.get("RESOURCE_ID")
        if not file_id:
            raise ProviderError("Zoho WorkDrive upload succeeded but returned no file id", status_code=502)
        # Re-fetch canonically rather than trust the upload response's own
        # attribute shape (unconfirmed) -- get_file() below is built on the
        # confidently-documented GET /files/{id}.
        return self.get_file(creds, file_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        node = self._get(creds, f"{self._api(creds)}/files/{file_id}")
        return self._node_to_file(self._first_data(node), self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        resp = self._call(creds, "PATCH", f"{self._api(creds)}/files/{file_id}", json={
            "data": {"attributes": {"name": name}, "id": file_id, "type": "files"},
        }).json()
        return self._node_to_file(self._first_data(resp), self._root_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        root_id = self._root_id(creds)
        target = new_folder_id or root_id
        resp = self._call(creds, "PATCH", f"{self._api(creds)}/files/{file_id}", json={
            "data": {"attributes": {"parent_id": target}, "id": file_id, "type": "files"},
        }).json()
        return self._node_to_file(self._first_data(resp), root_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._api(creds)}/files/{file_id}")

    def trash_file(self, creds: dict, file_id: str) -> None:
        # See trash_folder -- same rationale.
        self.delete_file(creds, file_id)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        return self._node_to_file(self._restore(creds, file_id), self._root_id(creds))

    def get_content(self, creds: dict, file_id: str) -> bytes:
        resp = self._call(creds, "GET", f"{self._api(creds)}/download/{file_id}")
        return resp.content

    # --- versions (see module docstring point 4) ---
    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._get(creds, f"{self._api(creds)}/files/{file_id}/versions")
        entries = result.get("data") or []
        versions = []
        for i, v in enumerate(entries):
            attrs = v.get("attributes") or {}
            storage_info = attrs.get("storage_info")
            size = storage_info.get("size") if isinstance(storage_info, dict) else attrs.get("size")
            try:
                size_bytes = int(size) if size not in (None, "") else None
            except (TypeError, ValueError):
                size_bytes = None
            versions.append(VersionInfo(
                id=v.get("id"), version_number=i + 1, size_bytes=size_bytes,
                content_type=attrs.get("mime_type"),
                # Ordering isn't confirmed by the reference material -- the
                # most recently listed entry is assumed current, the same
                # assumption this codebase's Microsoft Graph provider makes
                # for the same reason (see oauth_providers.py).
                is_current=(i == 0),
                updated_at=self._parse_dt(attrs.get("modified_time")),
            ))
        return versions

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        creds, _ = self.refresh_if_needed(creds)
        node = self.get_file(creds, file_id)
        parent = node.folder_id or self._root_id(creds)
        resp = requests.post(
            f"{self._api(creds)}/uploads",
            params={"override-name-exist": "true"},
            headers=self._headers(creds),
            data={"parent_id": parent, "filename": node.name},
            files={"content": (node.name, content, content_type)},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Zoho WorkDrive version upload failed ({resp.status_code}): {self._error_message(resp)}", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        resp = self._call(creds, "GET", f"{self._api(creds)}/download/{file_id}", params={"version_id": version_id})
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        node = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, node.content_type or "application/octet-stream", old_bytes)

    # --- search ---
    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root_id = self._root_id(creds)
        result = self._get(creds, f"{self._api(creds)}/files/search", params={"query": query})
        entries = result.get("data") or []
        return (
            [self._node_to_folder(e, root_id) for e in entries if self._is_folder(e.get("attributes") or {})],
            [self._node_to_file(e, root_id) for e in entries if not self._is_folder(e.get("attributes") or {})],
        )
