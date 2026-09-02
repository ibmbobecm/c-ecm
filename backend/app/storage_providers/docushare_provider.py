"""Xerox DocuShare provider, via DocuShare's classic "dsweb" HTTP interface
(handle-addressed object URLs like `{base_url}/dsweb/Services/Collection-123`,
plus the `dsweb/Login`, `dsweb/GetFile`, and `dsweb/GetVersion` servlets) —
NOT the older SOAP-style "DSAPI", which this file doesn't touch at all.

======================================================================
CONFIDENCE WARNING — READ BEFORE USE
======================================================================
This adapter is UNVERIFIED (no live DocuShare server was available in this
environment to test against) AND it is written against a system whose
programmatic surface is genuinely less standardized and less publicly
documented than most other providers in this codebase. For several of
C-ECM's backends (Box, Alfresco, Nuxeo, ...) there's a stable, published
REST/OpenAPI contract to check field names and status codes against.
DocuShare does not have an equivalent public reference available here:
historically its integration surface has been a mix of a SOAP-like "DSAPI"
and simpler handle-addressed HTTP object URLs rather than a clean, uniform
modern REST API. This file's endpoint paths, request/response shapes, and
auth mechanics are a best-effort reconstruction from general knowledge of
DocuShare's object model (Collections, Documents, Versions, handles) — NOT
copied from a verified API reference, WSDL/schema, or working install.
This should be read in the same spirit as this codebase's OnBase adapter's
docstring: treat every path and payload shape below as a labeled guess, not
a citation.

Specific things that are lower-confidence than the rest of this file:

  * Whether `?f=json` actually gets a real deployment to return JSON at
    all. DocuShare's dsweb interface has historically defaulted to HTML/XML
    responses; JSON support (and the exact query-param name/value to
    request it) is not confidently known. `_request()` asks for it on
    every call and raises a clear `ProviderError` if the response isn't
    JSON, rather than silently mis-parsing HTML/XML — but a real
    deployment may need a different content-negotiation mechanism
    entirely (an `Accept` header, a different param, a different servlet
    altogether).
  * The exact JSON field names DocuShare's "Services" interface would use
    for an object's title, parent, size, content type, and version number
    (guessed here as `title`/`name`, `parent`/`parentHandle`, `size`/
    `fileSize`, `mimeType`/`contentType`, `versionNumber`/`version`).
    `_get_field()` tries several plausible keys per concept specifically
    because none of them is confidently "the" real one.
  * The children-listing endpoint path (guessed as
    `GET {base_url}/dsweb/Services/{handle}/Children`) and its response
    envelope shape (guessed as either a bare JSON array or a dict wrapping
    it under `children`/`items`/`entries`).
  * The request body shape for creating a new Collection or Document
    (guessed as a JSON/multipart `title` field posted to the parent
    Collection's own handle URL) and for renaming (`PUT` with a `title`
    field) — best-effort guesses, not a verified schema.
  * Move is a deliberate simplification: DocuShare's real model lets an
    object belong to multiple parent Collections at once ("multi-
    parenting"), normally changed via separate link/unlink operations.
    This file doesn't confidently know those operations' endpoint names,
    so instead `move_folder`/`move_file` just overwrite the object's
    `parent` field directly via `PUT` — a strictly single-parent
    simplification of DocuShare's richer capability, not a faithful
    reproduction of it.
  * There is no confidently-known "make version N current again"
    endpoint, so `restore_version` is emulated the safe way: download the
    old version's bytes via `GetVersion`, then upload them as a brand-new
    current version through the same path `create_version` uses.
  * The top-level root handle new Collections are created under is
    guessed as `Collection-1` (a common default-install convention for
    DocuShare's top "Home"/"Content Collection" object) — a real
    deployment's actual top-level handle may differ and isn't confidently
    known here.
  * DocuShare has a native Recycle Bin/Trash concept in most installs, but
    the exact endpoint for listing or restoring from it isn't confidently
    known. Trash is therefore EMULATED (same pattern used elsewhere in
    this codebase for backends with uncertain native trash APIs): a
    dedicated `"C-ECM-Trash"` Collection is created lazily under the app
    root, and `trash_folder`/`trash_file`/`restore_folder`/`restore_file`
    just move the object into/out of it via the same simplified "set
    parent" mechanism as `move_*` above. Only `delete_folder`/
    `delete_file` (permanent delete) call DocuShare's own `DELETE` on the
    object's handle URL directly.
  * DocuShare's own search servlet response envelope isn't confidently
    known, so rather than guess at it, `search()` is implemented as the
    honest fallback explicitly sanctioned for this situation: it
    recursively lists from the app root and filters client-side (in
    Python) for folder/file names containing the query substring, capped
    at 500 visited items and 6 levels deep to stay bounded on a large
    repository.
  * DocuShare's classic `Login` servlet may well return HTTP 200 with an
    HTML error/login page for bad credentials, rather than a clean 4xx —
    a genuinely wrong password that still comes back 200 with a session
    cookie set might not be caught here. `authenticate()`'s check (status
    code plus the presence of a session cookie) is a best-effort signal
    of success, not a guaranteed one, absent a live server to verify
    against.
  * There's no confidently-known dedicated "whoami"/"current user"
    endpoint, so `whoami()` just echoes back the username supplied at
    login rather than round-tripping to the server to confirm it.

Before ANY production use, this file should be checked line-by-line
against a real DocuShare server (or its SDK/admin documentation), more so
than most other providers in this codebase — the more RESTful providers
here are "unverified against a live server but written against a solid
published spec"; this one is "unverified against a live server *and*
written against a considerably less certain spec."

======================================================================
OBJECT MODEL AND AUTH, AS IMPLEMENTED HERE
======================================================================
DocuShare organizes content as "Collections" (folders) containing
"Documents" (files) and other Collections, each addressed by an opaque
handle string — `Collection-1234`, `Document-5678` — and those handle
strings are used directly as this provider's `folder_id`/`file_id`
values, per this codebase's "ids are opaque, each provider decides their
shape" convention. Versions are likewise real objects with their own
`Version-NNNN` handles, linked to a parent Document.

Auth is CREDENTIALS-mode via a classic session cookie rather than a
bearer token: `authenticate()` POSTs the username/password to
`dsweb/Login` and captures whatever cookies come back, joined into one
plain `"name=value; name2=value2"` string stored as `creds["cookie"]`
(deliberately a plain string, not a live `requests.Session`/cookie-jar
object, since `creds` is persisted to SQLite as JSON elsewhere in this
codebase and must stay JSON-serializable). Every subsequent call sends
that string back verbatim as a literal `Cookie:` header.

The server is per-connection (`config_fields` collects `base_url`), not a
single global. `folder_id=None` (C-ECM's "root") is resolved once to a
real Collection handle (a dedicated "C-ECM" Collection under the
best-effort top-level root, created on first use) and cached *per base
URL*, since this provider instance is shared across every connection to
it — same pattern as this codebase's Alfresco provider.
"""

import threading

import requests

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

# LOW CONFIDENCE (see module docstring): best-effort guess at a default
# install's top-level "Home"/"Content Collection" handle.
_TOP_LEVEL_ROOT_HANDLE = "Collection-1"
_APP_ROOT_NAME = "C-ECM"
_TRASH_NAME = "C-ECM-Trash"


class DocuShareProvider(StorageProvider):
    key = "docushare"
    display_name = "Xerox DocuShare"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Server URL", "http://localhost/docushare")]

    # --- plumbing ---

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _headers(self, creds: dict) -> dict:
        return {"Cookie": creds["cookie"]}

    def _request(self, creds: dict, method: str, path: str, params: dict | None = None, **kwargs) -> dict:
        url = self._base_url(creds) + path
        params = dict(params or {})
        # LOW CONFIDENCE (see module docstring): whether this param name/
        # value actually gets a real deployment to return JSON rather than
        # its classic HTML/XML.
        params.setdefault("f", "json")
        try:
            resp = requests.request(method, url, headers=self._headers(creds), params=params, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the DocuShare server: {exc}", status_code=502)
        if resp.status_code in (401, 403):
            raise ProviderError("DocuShare session expired or invalid", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"DocuShare error {resp.status_code}: {resp.text[:300]}", status_code=502)
        if resp.status_code == 204 or not resp.content:
            return {}
        if "json" in resp.headers.get("Content-Type", "").lower():
            try:
                return resp.json()
            except ValueError:
                return {}
        # LOW CONFIDENCE: if the server ignored `f=json` and returned its
        # classic HTML/XML instead, this adapter has no parser for it —
        # surface a clear error instead of silently misbehaving on
        # unexpected content.
        raise ProviderError(
            "DocuShare returned a non-JSON response for this request. This adapter only understands "
            "the `f=json` response shape, and this deployment may not honor that parameter (or may "
            "need a different content-negotiation mechanism) — needs verification against a real "
            "server; see the module docstring.",
            status_code=502,
        )

    @staticmethod
    def _extract_items(result) -> list:
        """The children/versions listing endpoints' exact response envelope
        isn't confidently known — accept either a bare JSON array or a
        dict wrapping it under a plausible key."""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("children", "items", "entries", "results", "value"):
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
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_dt(value: str | None):
        if not value:
            return None
        import datetime as _dt
        try:
            return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _handle_of(self, entry: dict) -> str | None:
        return self._get_field(entry, "handle", "id", "objectId")

    def _entry_to_folder(self, entry: dict, root_id: str) -> FolderInfo:
        handle = self._handle_of(entry)
        name = self._get_field(entry, "title", "name", "displayName", default=handle or "")
        parent = self._get_field(entry, "parent", "parentHandle", "parentId")
        if parent == root_id:
            parent = None
        created = self._parse_dt(self._get_field(entry, "created", "dateCreated", "createDate"))
        return FolderInfo(id=handle, name=name, parent_id=parent, created_at=created)

    def _entry_to_file(self, entry: dict, root_id: str) -> FileInfo:
        handle = self._handle_of(entry)
        name = self._get_field(entry, "title", "name", "displayName", default=handle or "")
        parent = self._get_field(entry, "parent", "parentHandle", "parentId")
        if parent == root_id:
            parent = None
        version_number = self._safe_int(self._get_field(entry, "versionNumber", "version", "currentVersion"), 1)
        size_bytes = self._get_field(entry, "size", "fileSize", "contentLength")
        content_type = self._get_field(entry, "mimeType", "contentType")
        updated = self._parse_dt(self._get_field(entry, "modified", "dateModified", "lastModified"))
        return FileInfo(
            id=handle, name=name, folder_id=parent, version_number=version_number,
            size_bytes=size_bytes, content_type=content_type, updated_at=updated,
        )

    # --- auth ---

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderError("Server URL is required", status_code=400)
        base_url = base_url.rstrip("/")
        url = base_url + "/dsweb/Login"
        try:
            resp = requests.post(url, data={"username": username, "password": password}, timeout=30)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the DocuShare server: {exc}", status_code=502)
        # LOW CONFIDENCE (see module docstring): DocuShare's classic Login
        # servlet may return 200 with an HTML error page for bad
        # credentials rather than a clean 4xx — status code plus a
        # non-empty session cookie is a best-effort success signal here,
        # not a guaranteed one.
        if resp.status_code >= 400:
            return None
        cookie = "; ".join(f"{c.name}={c.value}" for c in resp.cookies)
        if not cookie:
            return None
        return {"base_url": base_url, "username": username, "cookie": cookie}

    def whoami(self, creds: dict) -> str:
        # LOW CONFIDENCE (see module docstring): no confidently-known
        # dedicated "current user" endpoint — echoes back the username
        # supplied at login instead of round-tripping to the server.
        return creds["username"]

    # --- root / trash collection resolution (cached per base_url, thread-safe) ---

    def _find_child_by_title(self, creds: dict, parent_handle: str, title: str) -> str | None:
        result = self._request(creds, "GET", f"/dsweb/Services/{parent_handle}/Children")
        for entry in self._extract_items(result):
            handle = self._handle_of(entry)
            if not handle or not handle.startswith("Collection-"):
                continue
            if self._get_field(entry, "title", "name", "displayName") == title:
                return handle
        return None

    def _root_id(self, creds: dict) -> str:
        cache_key = self._base_url(creds)
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        # This provider instance is a process-wide singleton shared by every
        # connection to the same DocuShare server, and FastAPI runs sync
        # handlers in a real thread pool — without a lock, concurrent
        # first-requests for a freshly connected server would each find no
        # existing root Collection and each create their own duplicate.
        # Double-checked locking: re-test the cache after acquiring the
        # lock, since another thread may have populated it while we waited.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            existing = self._find_child_by_title(creds, _TOP_LEVEL_ROOT_HANDLE, _APP_ROOT_NAME)
            if existing:
                self._root_id_cache[cache_key] = existing
                return existing
            created = self._request(
                creds, "POST", f"/dsweb/Services/{_TOP_LEVEL_ROOT_HANDLE}", json={"title": _APP_ROOT_NAME}
            )
            root_id = self._handle_of(created)
            if not root_id:
                raise ProviderError("DocuShare didn't return a handle for the newly created root collection", status_code=502)
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_id(self, creds: dict) -> str:
        cache_key = self._base_url(creds)
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
            created = self._request(creds, "POST", f"/dsweb/Services/{root}", json={"title": _TRASH_NAME})
            trash_id = self._handle_of(created)
            if not trash_id:
                raise ProviderError("DocuShare didn't return a handle for the newly created trash collection", status_code=502)
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def _resolve_folder_id(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_id(creds)

    def _set_parent(self, creds: dict, handle: str, new_parent_handle: str) -> dict:
        # SIMPLIFICATION (see module docstring): DocuShare's real model lets
        # an object belong to multiple parent Collections at once, normally
        # changed via separate link/unlink operations whose exact endpoint
        # names aren't confidently known here. C-ECM's model is strictly
        # one-parent-per-object, so this overwrites the parent reference
        # directly instead of reproducing DocuShare's richer capability.
        return self._request(creds, "PUT", f"/dsweb/Services/{handle}", json={"parent": new_parent_handle})

    def _get_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        entry = self._request(creds, "GET", f"/dsweb/Services/{folder_id}")
        return self._entry_to_folder(entry, self._root_id(creds))

    # --- folders ---

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id if folder_id is not None else root_id
        result = self._request(creds, "GET", f"/dsweb/Services/{node_id}/Children")
        entries = self._extract_items(result)
        folders: list[FolderInfo] = []
        files: list[FileInfo] = []
        for entry in entries:
            handle = self._handle_of(entry)
            if not handle:
                continue
            if handle.startswith("Collection-"):
                if self._get_field(entry, "title", "name", "displayName") == _TRASH_NAME:
                    continue  # hide the emulated trash collection from normal browsing
                folders.append(self._entry_to_folder(entry, root_id))
            elif handle.startswith("Document-"):
                files.append(self._entry_to_file(entry, root_id))

        current_folder = None
        if folder_id is not None:
            current_folder = self._get_folder(creds, node_id)

        return FolderContents(
            folder=current_folder,
            breadcrumb=[BreadcrumbEntry(id=None, name="Home")],
            folders=folders, files=files,
        )

    def list_trash(self, creds: dict) -> FolderContents:
        root_id = self._root_id(creds)
        trash_id = self._trash_id(creds)
        result = self._request(creds, "GET", f"/dsweb/Services/{trash_id}/Children")
        entries = self._extract_items(result)
        folders = [
            self._entry_to_folder(e, root_id) for e in entries
            if (self._handle_of(e) or "").startswith("Collection-")
        ]
        files = [
            self._entry_to_file(e, root_id) for e in entries
            if (self._handle_of(e) or "").startswith("Document-")
        ]
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")], folders=folders, files=files)

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        node_id = self._resolve_folder_id(creds, parent_id)
        # LOW CONFIDENCE (see module docstring): exact body shape DocuShare
        # expects for creating a subcollection — best-effort POST; may need
        # adjustment against a real server's WSDL/schema.
        created = self._request(creds, "POST", f"/dsweb/Services/{node_id}", json={"title": name})
        return self._entry_to_folder(created, self._root_id(creds))

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._request(creds, "PUT", f"/dsweb/Services/{folder_id}", json={"title": name})
        if not updated:
            updated = self._request(creds, "GET", f"/dsweb/Services/{folder_id}")
        return self._entry_to_folder(updated, self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve_folder_id(creds, new_parent_id)
        updated = self._set_parent(creds, folder_id, target)
        if not updated:
            updated = self._request(creds, "GET", f"/dsweb/Services/{folder_id}")
        return self._entry_to_folder(updated, self._root_id(creds))

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "DELETE", f"/dsweb/Services/{folder_id}")

    # --- files ---

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        node_id = self._resolve_folder_id(creds, folder_id)
        url = self._base_url(creds) + f"/dsweb/Services/{node_id}"
        # LOW CONFIDENCE (see module docstring): exact multipart field
        # names DocuShare's Services interface expects for a metadata part
        # + a content part are a best-effort guess, not verified against a
        # real server.
        files = {"content": (name, content, content_type)}
        data = {"title": name}
        try:
            resp = requests.post(url, headers=self._headers(creds), params={"f": "json"}, files=files, data=data, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the DocuShare server: {exc}", status_code=502)
        if resp.status_code in (401, 403):
            raise ProviderError("DocuShare session expired or invalid", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError(f"DocuShare upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        try:
            entry = resp.json()
        except ValueError:
            raise ProviderError("Unexpected (non-JSON) response from the DocuShare upload endpoint", status_code=502)
        return self._entry_to_file(entry, self._root_id(creds))

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        entry = self._request(creds, "GET", f"/dsweb/Services/{file_id}")
        return self._entry_to_file(entry, self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._request(creds, "PUT", f"/dsweb/Services/{file_id}", json={"title": name})
        if not updated:
            return self.get_file(creds, file_id)
        return self._entry_to_file(updated, self._root_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve_folder_id(creds, new_folder_id)
        updated = self._set_parent(creds, file_id, target)
        if not updated:
            return self.get_file(creds, file_id)
        return self._entry_to_file(updated, self._root_id(creds))

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "DELETE", f"/dsweb/Services/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        # LOW CONFIDENCE-but-relatively-more-confident (see module
        # docstring): DocuShare's classic "GetFile" servlet-style
        # content-download URL is one of the more consistently-remembered
        # parts of its API across versions.
        url = self._base_url(creds) + f"/dsweb/GetFile/{file_id}"
        try:
            resp = requests.get(url, headers=self._headers(creds), timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the DocuShare server: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError("Couldn't fetch content", status_code=404)
        return resp.content

    # --- versions ---

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._request(creds, "GET", f"/dsweb/Services/{file_id}/Versions")
        entries = self._extract_items(result)
        out = []
        for i, entry in enumerate(entries):
            handle = self._handle_of(entry) or f"{file_id}-v{i + 1}"
            version_number = self._safe_int(self._get_field(entry, "versionNumber", "version"), i + 1)
            out.append(VersionInfo(
                id=handle,
                version_number=version_number,
                size_bytes=self._get_field(entry, "size", "fileSize"),
                content_type=self._get_field(entry, "mimeType", "contentType"),
                is_current=bool(self._get_field(entry, "current", "isCurrent", "isLatest", default=(i == 0))),
                updated_at=self._parse_dt(self._get_field(entry, "modified", "dateModified", "lastModified")),
            ))
        return out

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        # Same multipart content-upload call used by create_document, but
        # targeted at the existing Document handle rather than a parent
        # Collection — per the task guidance, assumed to add a new version
        # instead of creating a brand-new object.
        url = self._base_url(creds) + f"/dsweb/Services/{file_id}"
        files = {"content": ("version", content, content_type)}
        try:
            resp = requests.post(url, headers=self._headers(creds), params={"f": "json"}, files=files, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the DocuShare server: {exc}", status_code=502)
        if resp.status_code in (401, 403):
            raise ProviderError("DocuShare session expired or invalid", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError(f"DocuShare version upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        # LOW CONFIDENCE (see module docstring): best-effort guess mirroring
        # the GetFile pattern above.
        url = self._base_url(creds) + f"/dsweb/GetVersion/{version_id}"
        try:
            resp = requests.get(url, headers=self._headers(creds), timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the DocuShare server: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError("Version content not found", status_code=404)
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        # No confidently-known "make version N current again" endpoint (see
        # module docstring) — the safe fallback is to pull the old
        # version's bytes down and lay them back as a brand-new current
        # version.
        old_content = self.get_version_content(creds, file_id, version_id)
        current = self.get_file(creds, file_id)
        content_type = current.content_type or "application/octet-stream"
        return self.create_version(creds, file_id, content_type, old_content)

    # --- trash (emulated via a dedicated Collection — see module docstring) ---

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        trash_id = self._trash_id(creds)
        self._set_parent(creds, folder_id, trash_id)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        self._set_parent(creds, folder_id, root)
        return self._get_folder(creds, folder_id)

    def trash_file(self, creds: dict, file_id: str) -> None:
        trash_id = self._trash_id(creds)
        self._set_parent(creds, file_id, trash_id)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        self._set_parent(creds, file_id, root)
        return self.get_file(creds, file_id)

    # --- search ---

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        # FALLBACK CHOICE (see module docstring): DocuShare's own search
        # servlet response shape isn't confidently known, so rather than
        # guess at it, this recursively walks the app root and filters
        # client-side — capped to stay bounded on a large repository.
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
