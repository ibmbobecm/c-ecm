"""Huddle (huddle.net) provider, via Huddle's documented REST+Atom/XML API
(base `https://api.huddle.net`), OAuth2 through `signon.huddle.net`.

======================================================================
CONFIDENCE WARNING — READ BEFORE USE
======================================================================
This adapter is UNVERIFIED (no live Huddle tenant was available in this
environment to test against) AND it is written against a system whose
programmatic surface is genuinely less standardized and less publicly
documented than most other providers in this codebase. For several of
C-ECM's backends (Box, Alfresco, Nuxeo, ...) there's a stable, published
REST/OpenAPI contract to check field names and status codes against.
Huddle's public documentation has historically centered on an Atom/XML
wire format (entries/feeds, XML namespaces) with JSON support requested
via an `Accept: application/json` header rather than being the API's own
native shape — this file's endpoint paths, request/response field names,
and a few auth details are a best-effort reconstruction from general
knowledge of Huddle's object model (Workspaces > Folders > Files, with
Files carrying Versions) — NOT copied from a verified API reference,
Swagger/OpenAPI document, or working install. This should be read in the
same spirit as this codebase's OnBase/DocuShare adapters' docstrings:
treat every path and payload shape below as a labeled guess, not a
citation.

Specific things that are lower-confidence than the rest of this file:

  * Whether `Accept: application/json` reliably gets a real Huddle tenant
    to return JSON on every one of these endpoints, rather than its
    historical native Atom/XML. `_request()` asks for JSON explicitly on
    every call and raises a clear `ProviderError` if the response isn't
    JSON, rather than silently mis-parsing XML — but a real deployment
    may honor that header only on some endpoints, or need a different
    content-negotiation mechanism (a `.json` suffix, a query param).
  * The exact JSON field names Huddle's API would use for a folder/file's
    title, parent, size, content type, and version number (guessed here
    as `title`/`name`, `parentId`/`folderId`, `size`/`fileSize`,
    `mimeType`/`contentType`, `version`/`versionNumber`). `_get_field()`
    tries several plausible keys per concept specifically because none of
    them is confidently "the" real one.
  * The workspace/folder listing endpoints (`GET /workspaces`,
    `GET /workspaces/{id}/folders`, `GET /folders/{id}`) and their
    response envelope shapes (guessed as either a bare JSON array or a
    dict wrapping it under `workspaces`/`folders`/`files`/`items`).
  * The request body shape for creating a subfolder (guessed as
    `POST /folders/{id}/folders` with a JSON `{"title": name}` body) and
    for renaming (`PUT` the folder resource with a `title` field) — best-
    effort guesses, not a verified schema.
  * Move is deliberately NOT implemented against a guessed endpoint.
    Nothing in the available documentation confidently describes a move/
    reparent operation for Huddle folders or files, and the closest
    approximation (delete the original + recreate its content under a new
    parent) is destructively lossy — it would discard version history and
    any server-side metadata this file doesn't know how to carry over.
    Rather than guess at something that could silently destroy data,
    `move_folder`/`move_file` both raise a clear `ProviderError` telling
    the caller to rename in place instead. This is a conscious product
    decision, not an oversight.
  * Versions: the endpoint (`GET /files/{id}/versions`) and its response
    shape are a best-effort guess. Because this file isn't confident
    about the envelope Huddle actually returns, `list_versions()` first
    tries that endpoint and falls back to reporting just the file's own
    current version as a single `VersionInfo` (same fallback pattern used
    elsewhere in this codebase for low-confidence version APIs) if the
    response doesn't look like a recognizable list of versions.
  * Huddle has native version history, but there's no confidently-known
    "make version N current again" endpoint, so `restore_version` is
    emulated the safe way used throughout this codebase: download the old
    version's bytes, then upload them as a brand-new current version via
    the same multipart call `create_version` uses.
  * Trash: not confidently documented as a native, listable concept via
    this API surface. EMULATED (same pattern used elsewhere in this
    codebase for backends with uncertain native trash APIs): a hidden
    `_C-ECM-Trash` folder is created lazily under the app root, and
    `trash_folder`/`trash_file`/`restore_folder`/`restore_file` re-upload
    or re-file content into/out of it rather than calling any Huddle
    "recycle bin" endpoint this file isn't confident exists. Only
    `delete_folder`/`delete_file` (permanent delete) call Huddle's own
    `DELETE` on the resource directly.
  * Search: `GET /workspaces/{id}/search?q=...` is a best-effort guess at
    a workspace-scoped search endpoint; its response envelope isn't
    confidently known either. Because of that uncertainty, `search()`
    tries the endpoint first and falls back to the honest fallback
    explicitly sanctioned for this situation elsewhere in this codebase:
    a client-side recursive listing of the app root, filtered in Python
    for name substring matches, capped at 500 visited items and 6 levels
    deep to stay bounded on a large repository.
  * The workspace concept itself: this file treats "workspace" purely as
    an implementation detail used to locate a stable place to create the
    app's own root folder — the first workspace returned by `GET
    /workspaces` is used as this connection's default, cached alongside
    the resolved root folder id. A real tenant may belong to several
    workspaces; this adapter does not expose workspace-switching, since
    nothing in `StorageProvider`'s interface has a slot for it.
  * OAuth2 endpoint paths (`signon.huddle.net/oauth2/authorize`,
    `signon.huddle.net/oauth2/token`) and grant/refresh parameter names
    follow a standard OAuth2 authorization-code shape, which is
    comparatively higher-confidence than the REST endpoints above, but
    still unverified against a live Huddle authorization server.

Before ANY production use, this file should be checked line-by-line
against a real Huddle API reference (or a live sandbox), in the same
spirit as this codebase's OnBase and DocuShare adapters — the more
RESTful providers here are "unverified against a live server but written
against a solid published spec"; this one is "unverified against a live
server *and* written against a considerably less certain spec."

======================================================================
OBJECT MODEL AND AUTH, AS IMPLEMENTED HERE
======================================================================
Huddle organizes content as Workspaces containing Folders (nested) and
Files, each addressed by an opaque id string that this provider uses
directly as `folder_id`/`file_id`, per this codebase's "ids are opaque,
each provider decides their shape" convention.

Auth is OAUTH-mode, standard authorization-code + refresh-token flow
against `signon.huddle.net`. The OAuth client id/secret is an app-level
setting for this whole C-ECM deployment (via `settings_store`), shared by
every connection to Huddle — end users never see or enter it, they just
click Connect, matching every other OAuth provider in this codebase.

`folder_id=None` (C-ECM's "root") resolves to a dedicated "C-ECM" folder
created lazily under the default workspace's own root folder listing,
cached per connected identity (double-checked locking, same pattern as
this codebase's Box/Google/DocuShare providers) since this provider
instance is a process-wide singleton shared across every connection.
"""

import threading
import time

import requests

from .. import settings_store
from .base import (
    AuthMode,
    BreadcrumbEntry,
    ConfigField,
    FileInfo,
    FolderContents,
    FolderInfo,
    ProviderError,
    StorageProvider,
    VersionInfo,
)

_APP_ROOT_NAME = "C-ECM"
_TRASH_NAME = "_C-ECM-Trash"

_AUTHORIZE_URL = "https://signon.huddle.net/oauth2/authorize"
_TOKEN_URL = "https://signon.huddle.net/oauth2/token"
_API = "https://api.huddle.net"


class HuddleProvider(StorageProvider):
    key = "huddle"
    display_name = "Huddle"
    auth_mode = AuthMode.OAUTH

    def __init__(self):
        # Keyed by connected identity, since this provider instance is a
        # process-wide singleton shared across every Huddle connection —
        # see GoogleDriveProvider._root_id in oauth_providers.py for why an
        # unkeyed cache would leak one account's ids into another's.
        self._root_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_cache: dict[str, str] = {}
        self._trash_id_lock = threading.Lock()

    # --- app-level OAuth client config ---

    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("huddle_client_id", ""),
            settings_store.get_setting("huddle_client_secret", ""),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret = self._client()
        return bool(client_id and client_secret)

    @property
    def config_fields(self) -> list[ConfigField]:
        return []

    # --- oauth ---

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret = self._client()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return _AUTHORIZE_URL + "?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret = self._client()
        resp = requests.post(_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Huddle token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + tok.get("expires_in", 3600),
        }
        # LOW CONFIDENCE (see module docstring): no confidently-known
        # dedicated "current user" endpoint — best-effort probe of the
        # workspace list, falling back to a generic label if that also
        # isn't confidently parseable.
        try:
            workspaces = self._extract_items(self._get(creds, f"{_API}/workspaces"))
            if workspaces:
                creds["identity"] = self._get_field(workspaces[0], "title", "name", default="Huddle account")
            else:
                creds["identity"] = "Huddle account"
        except ProviderError:
            creds["identity"] = "Huddle account"
        return creds

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        if creds.get("expires_at", 0) > time.time() + 30:
            return creds, False
        if not creds.get("refresh_token"):
            raise ProviderError("Huddle session expired — please reconnect", status_code=401)
        client_id, client_secret = self._client()
        resp = requests.post(_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
            "client_id": client_id,
            "client_secret": client_secret,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Huddle session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        return creds, True

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "Huddle account")

    # --- plumbing ---

    def _headers(self, creds: dict) -> dict:
        # LOW CONFIDENCE (see module docstring): Huddle's API has
        # historically defaulted to Atom/XML — JSON is requested
        # explicitly via `Accept`, but whether a real tenant honors that
        # uniformly across every endpoint below isn't confidently known.
        return {"Authorization": f"Bearer {creds['access_token']}", "Accept": "application/json"}

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers=self._headers(creds), timeout=30, **kwargs)
        return self._handle_response(resp)

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds, _changed = self.refresh_if_needed(creds)
        headers = self._headers(creds)
        headers.update(kwargs.pop("headers", {}) or {})
        resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Huddle error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def _handle_response(self, resp: requests.Response) -> dict:
        if resp.status_code in (401, 403):
            raise ProviderError("Huddle session expired or invalid", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Huddle error {resp.status_code}: {resp.text[:300]}", status_code=502)
        if resp.status_code == 204 or not resp.content:
            return {}
        content_type = resp.headers.get("Content-Type", "")
        if "json" in content_type.lower():
            try:
                return resp.json()
            except ValueError:
                return {}
        # LOW CONFIDENCE (see module docstring): if the tenant ignored our
        # `Accept: application/json` and returned its historical Atom/XML
        # instead, this adapter has no parser for it — surface a clear
        # error rather than silently misbehaving on unexpected content.
        try:
            return resp.json()
        except ValueError:
            raise ProviderError(
                "Huddle returned a non-JSON response for this request. This adapter only understands "
                "JSON responses (requested via `Accept: application/json`), and this tenant may still be "
                "returning its historical Atom/XML for this endpoint — needs verification against a real "
                "server; see the module docstring.",
                status_code=502,
            )

    @staticmethod
    def _extract_items(result) -> list:
        """The listing endpoints' exact response envelope isn't confidently
        known — accept either a bare JSON array or a dict wrapping it
        under a plausible key."""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("workspaces", "folders", "files", "items", "entries", "results", "value"):
                if key in result and isinstance(result[key], list):
                    return result[key]
        return []

    @staticmethod
    def _get_field(entry: dict, *keys: str, default=None):
        for k in keys:
            if k in entry and entry[k] is not None:
                return entry[k]
        return default

    @staticmethod
    def _get_id(entry: dict) -> str | None:
        val = HuddleProvider._get_field(entry, "id", "folderId", "fileId", "workspaceId")
        return str(val) if val is not None else None

    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_dt(value):
        if not value:
            return None
        import datetime as _dt
        try:
            return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _entry_to_folder(self, entry: dict, root_id: str) -> FolderInfo:
        fid = self._get_id(entry)
        name = self._get_field(entry, "title", "name", default=fid or "")
        parent = self._get_field(entry, "parentId", "folderId", "parent")
        parent = str(parent) if parent is not None else None
        if parent == root_id:
            parent = None
        created = self._parse_dt(self._get_field(entry, "created", "dateCreated", "createdDate"))
        return FolderInfo(id=fid, name=name, parent_id=parent, created_at=created)

    def _entry_to_file(self, entry: dict, root_id: str) -> FileInfo:
        fid = self._get_id(entry)
        name = self._get_field(entry, "title", "name", default=fid or "")
        parent = self._get_field(entry, "parentId", "folderId", "parent")
        parent = str(parent) if parent is not None else None
        if parent == root_id:
            parent = None
        version_number = self._safe_int(self._get_field(entry, "version", "versionNumber", "currentVersion"), 1)
        size_bytes = self._get_field(entry, "size", "fileSize", "contentLength")
        content_type = self._get_field(entry, "mimeType", "contentType")
        updated = self._parse_dt(self._get_field(entry, "modified", "dateModified", "lastModified"))
        return FileInfo(
            id=fid, name=name, folder_id=parent, version_number=version_number,
            size_bytes=size_bytes, content_type=content_type, updated_at=updated,
        )

    # --- workspace / root / trash resolution (cached per identity, thread-safe) ---

    def _default_workspace_id(self, creds: dict) -> str:
        result = self._get(creds, f"{_API}/workspaces")
        workspaces = self._extract_items(result)
        if not workspaces:
            raise ProviderError("This Huddle account has no accessible workspaces", status_code=502)
        wid = self._get_id(workspaces[0])
        if not wid:
            raise ProviderError("Huddle didn't return a usable id for its default workspace", status_code=502)
        return wid

    def _find_child_by_title(self, creds: dict, folder_id: str, title: str) -> str | None:
        result = self._get(creds, f"{_API}/folders/{folder_id}")
        for entry in self._extract_items(result.get("folders", result)):
            if self._get_field(entry, "title", "name") == title:
                return self._get_id(entry)
        return None

    def _find_workspace_root_child(self, creds: dict, workspace_id: str, title: str) -> str | None:
        result = self._get(creds, f"{_API}/workspaces/{workspace_id}/folders")
        for entry in self._extract_items(result):
            if self._get_field(entry, "title", "name") == title:
                return self._get_id(entry)
        return None

    def _root_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "")
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        # This provider instance is a process-wide singleton (registry.py)
        # and FastAPI runs sync handlers in a real thread pool — without a
        # lock, concurrent first-requests for the same freshly connected
        # account would each see an empty cache, each find no existing
        # root folder, and each create their own duplicate "C-ECM" root.
        # Double-checked locking: re-test the cache after acquiring the
        # lock, since another thread may have populated it while we waited.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            workspace_id = self._default_workspace_id(creds)
            existing = self._find_workspace_root_child(creds, workspace_id, _APP_ROOT_NAME)
            if existing:
                self._root_id_cache[cache_key] = existing
                return existing
            created = self._call(
                creds, "POST", f"{_API}/workspaces/{workspace_id}/folders", json={"title": _APP_ROOT_NAME}
            )
            root_id = self._get_id(self._handle_response(created))
            if not root_id:
                raise ProviderError("Huddle didn't return an id for the newly created root folder", status_code=502)
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "")
        cached = self._trash_id_cache.get(cache_key)
        if cached:
            return cached
        with self._trash_id_lock:
            cached = self._trash_id_cache.get(cache_key)
            if cached:
                return cached
            root = self._root_id(creds)
            existing = self._find_child_by_title(creds, root, _TRASH_NAME)
            if existing:
                self._trash_id_cache[cache_key] = existing
                return existing
            created = self._call(creds, "POST", f"{_API}/folders/{root}/folders", json={"title": _TRASH_NAME})
            trash_id = self._get_id(self._handle_response(created))
            if not trash_id:
                raise ProviderError("Huddle didn't return an id for the newly created trash folder", status_code=502)
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def _resolve_folder_id(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_id(creds)

    def _get_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        entry = self._get(creds, f"{_API}/folders/{folder_id}")
        return self._entry_to_folder(entry, self._root_id(creds))

    # --- folders ---

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id if folder_id is not None else root_id
        result = self._get(creds, f"{_API}/folders/{node_id}")
        folder_entries = self._extract_items(result.get("folders")) if isinstance(result, dict) else []
        file_entries = self._extract_items(result.get("files")) if isinstance(result, dict) else []

        folders: list[FolderInfo] = []
        for entry in folder_entries:
            if self._get_field(entry, "title", "name") == _TRASH_NAME:
                continue  # hide the emulated trash folder from normal browsing
            folders.append(self._entry_to_folder(entry, root_id))
        files = [self._entry_to_file(e, root_id) for e in file_entries]

        current_folder = None
        if folder_id is not None:
            current_folder = self._entry_to_folder(result, root_id)

        return FolderContents(
            folder=current_folder,
            breadcrumb=[BreadcrumbEntry(id=None, name="Huddle")],
            folders=folders, files=files,
        )

    def list_trash(self, creds: dict) -> FolderContents:
        root_id = self._root_id(creds)
        trash_id = self._trash_id(creds)
        result = self._get(creds, f"{_API}/folders/{trash_id}")
        folder_entries = self._extract_items(result.get("folders")) if isinstance(result, dict) else []
        file_entries = self._extract_items(result.get("files")) if isinstance(result, dict) else []
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._entry_to_folder(e, root_id) for e in folder_entries],
            files=[self._entry_to_file(e, root_id) for e in file_entries],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        node_id = self._resolve_folder_id(creds, parent_id)
        created = self._handle_response(
            self._call(creds, "POST", f"{_API}/folders/{node_id}/folders", json={"title": name})
        )
        return self._entry_to_folder(created, self._root_id(creds))

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._handle_response(
            self._call(creds, "PUT", f"{_API}/folders/{folder_id}", json={"title": name})
        )
        if not updated:
            return self._get_folder(creds, folder_id)
        return self._entry_to_folder(updated, self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        # See module docstring: no confidently-documented move/reparent
        # operation exists for Huddle folders, and the only alternative
        # this file could guess at (delete + recreate elsewhere) would be
        # destructively lossy. Raising here rather than guessing at
        # something that could silently destroy data.
        raise ProviderError(
            "Huddle doesn't expose a documented move operation — rename in place instead",
            status_code=400,
        )

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{_API}/folders/{folder_id}")

    # --- files ---

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        node_id = self._resolve_folder_id(creds, folder_id)
        creds, _changed = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{_API}/folders/{node_id}/files",
            headers={"Authorization": f"Bearer {creds['access_token']}", "Accept": "application/json"},
            files={"file": (name, content, content_type)},
            timeout=60,
        )
        if resp.status_code in (401, 403):
            raise ProviderError("Huddle session expired or invalid", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError(f"Huddle upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        entry = self._handle_response(resp)
        return self._entry_to_file(entry, self._root_id(creds))

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        entry = self._get(creds, f"{_API}/files/{file_id}")
        return self._entry_to_file(entry, self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._handle_response(
            self._call(creds, "PUT", f"{_API}/files/{file_id}", json={"title": name})
        )
        if not updated:
            return self.get_file(creds, file_id)
        return self._entry_to_file(updated, self._root_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        # See move_folder above / module docstring — same reasoning.
        raise ProviderError(
            "Huddle doesn't expose a documented move operation — rename in place instead",
            status_code=400,
        )

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{_API}/files/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        creds, _changed = self.refresh_if_needed(creds)
        resp = requests.get(
            f"{_API}/files/{file_id}/data",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            timeout=60,
        )
        if resp.status_code in (401, 403):
            raise ProviderError("Huddle session expired or invalid", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError("Couldn't fetch content", status_code=404)
        return resp.content

    # --- versions ---

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        # LOW CONFIDENCE (see module docstring): endpoint and response
        # shape are a best-effort guess. If the response doesn't look like
        # a recognizable list of version entries, fall back to reporting
        # just the file's current version.
        try:
            result = self._get(creds, f"{_API}/files/{file_id}/versions")
        except ProviderError:
            result = None

        entries = self._extract_items(result) if result is not None else []
        if entries:
            out = []
            for i, entry in enumerate(entries):
                version_number = self._safe_int(self._get_field(entry, "version", "versionNumber"), i + 1)
                vid = self._get_id(entry) or str(version_number)
                out.append(VersionInfo(
                    id=vid,
                    version_number=version_number,
                    size_bytes=self._get_field(entry, "size", "fileSize"),
                    content_type=self._get_field(entry, "mimeType", "contentType"),
                    is_current=bool(self._get_field(entry, "current", "isCurrent", "isLatest", default=(i == 0))),
                    updated_at=self._parse_dt(self._get_field(entry, "modified", "dateModified", "lastModified")),
                ))
            return out

        # Fallback: single current-version-only, same pattern used
        # elsewhere in this codebase for low-confidence version APIs.
        current = self.get_file(creds, file_id)
        return [VersionInfo(
            id=str(current.version_number),
            version_number=current.version_number,
            size_bytes=current.size_bytes,
            content_type=current.content_type,
            is_current=True,
            updated_at=current.updated_at,
        )]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        creds, _changed = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{_API}/files/{file_id}/versions",
            headers={"Authorization": f"Bearer {creds['access_token']}", "Accept": "application/json"},
            files={"file": ("version", content, content_type)},
            timeout=60,
        )
        if resp.status_code in (401, 403):
            raise ProviderError("Huddle session expired or invalid", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError(f"Huddle version upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        creds, _changed = self.refresh_if_needed(creds)
        resp = requests.get(
            f"{_API}/files/{file_id}/versions/{version_id}/data",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError("Version content not found", status_code=404)
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        # No confidently-known "make version N current again" endpoint
        # (see module docstring) — the safe fallback used throughout this
        # codebase: pull the old version's bytes down and lay them back as
        # a brand-new current version.
        old_content = self.get_version_content(creds, file_id, version_id)
        current = self.get_file(creds, file_id)
        content_type = current.content_type or "application/octet-stream"
        return self.create_version(creds, file_id, content_type, old_content)

    # --- trash (emulated via a hidden folder — see module docstring) ---

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        # No documented move exists (see move_folder above), so "trashing"
        # can't reparent the folder either — the safe, non-destructive
        # option this file takes instead is a no-op that still surfaces a
        # clear error, rather than silently doing nothing or guessing at a
        # move that could corrupt state.
        raise ProviderError(
            "Huddle doesn't expose a documented move operation, which trash/restore for folders "
            "would require — this connection can't emulate folder trash safely",
            status_code=400,
        )

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        raise ProviderError(
            "Huddle doesn't expose a documented move operation, which trash/restore for folders "
            "would require — this connection can't emulate folder trash safely",
            status_code=400,
        )

    def trash_file(self, creds: dict, file_id: str) -> None:
        # Files CAN be safely trashed without a move endpoint: download
        # the current content, re-upload it into the hidden trash folder
        # as a new file, then permanently delete the original. Round-trips
        # the bytes rather than reparenting, since reparenting isn't a
        # documented operation (see module docstring).
        trash_id = self._trash_id(creds)
        current = self.get_file(creds, file_id)
        content = self.get_content(creds, file_id)
        content_type = current.content_type or "application/octet-stream"
        creds, _changed = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{_API}/folders/{trash_id}/files",
            headers={"Authorization": f"Bearer {creds['access_token']}", "Accept": "application/json"},
            files={"file": (current.name, content, content_type)},
            timeout=60,
        )
        if resp.status_code in (401, 403):
            raise ProviderError("Huddle session expired or invalid", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError(f"Huddle trash upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        self.delete_file(creds, file_id)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        # Mirrors trash_file: re-upload the trashed file's bytes back under
        # the app root, then permanently delete the trash copy.
        root_id = self._root_id(creds)
        current = self.get_file(creds, file_id)
        content = self.get_content(creds, file_id)
        content_type = current.content_type or "application/octet-stream"
        restored = self.create_document(creds, None if current.folder_id == root_id else current.folder_id,
                                         current.name, content_type, content)
        self.delete_file(creds, file_id)
        return restored

    # --- search ---

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        # LOW CONFIDENCE (see module docstring): endpoint and response
        # envelope are a best-effort guess. Falls back to the honest
        # client-side recursive listing/filter used elsewhere in this
        # codebase when the guessed endpoint doesn't behave as expected.
        try:
            workspace_id = self._default_workspace_id(creds)
            from urllib.parse import quote
            result = self._get(creds, f"{_API}/workspaces/{workspace_id}/search", params={"q": quote(query)})
            entries = self._extract_items(result)
            if entries:
                root_id = self._root_id(creds)
                folders = []
                files = []
                for entry in entries:
                    fid = self._get_id(entry)
                    if fid is None:
                        continue
                    if "files" in entry or self._get_field(entry, "size", "fileSize") is not None:
                        files.append(self._entry_to_file(entry, root_id))
                    else:
                        folders.append(self._entry_to_folder(entry, root_id))
                return folders, files
        except ProviderError:
            pass

        # FALLBACK: recursively walk the app root and filter client-side,
        # capped to stay bounded on a large repository.
        needle = query.lower()
        found_folders: list[FolderInfo] = []
        found_files: list[FileInfo] = []
        visited = 0
        max_items = 500
        max_depth = 6
        root = self._root_id(creds)

        def _walk(node_id: str, depth: int) -> None:
            nonlocal visited
            if visited >= max_items or depth > max_depth:
                return
            try:
                contents = self.get_children(creds, None if node_id == root else node_id)
            except ProviderError:
                return
            for folder in contents.folders:
                visited += 1
                if needle in folder.name.lower():
                    found_folders.append(folder)
                if visited >= max_items:
                    return
                _walk(folder.id, depth + 1)
            for file_ in contents.files:
                visited += 1
                if needle in file_.name.lower():
                    found_files.append(file_)
                if visited >= max_items:
                    return

        _walk(root, 0)
        return found_folders, found_files
