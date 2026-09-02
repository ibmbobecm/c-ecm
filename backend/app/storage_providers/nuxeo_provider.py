"""Hyland Nuxeo provider, via its REST API v1 (`/nuxeo/api/v1`) plus a
handful of Automation operations invoked through the `@op/{operationId}`
document adapter.

UNVERIFIED — written against Nuxeo's documented REST API v1 contract and
its published Automation operation catalogue (Nuxeo has one of the
better-documented and more standardized REST APIs among the enterprise
ECM systems this app integrates with, so there's more to go on here than
for some other providers), but there's no live Nuxeo server in this
environment to test it against. Run it against a real instance before
trusting it the way FileNet's and local disk's providers are trusted
(those were verified live, this wasn't). Parts most likely to need
adjustment for a specific deployment's actual configuration:

- The automation operation names used for versioning/permanent-delete
  (`Document.CreateVersion`, `Document.RestoreVersion`,
  `Document.PermanentlyDelete`) — these are the documented, conventional
  names, but a deployment could have them disabled, renamed via a
  contribution, or gated behind a permission this connection's user
  doesn't hold.
- The default-domain path assumption (`/default-domain/workspaces`) used
  to anchor the dedicated "C-ECM" app-root folder — standard for an
  out-of-the-box Nuxeo install, but a customized repository tree could
  use a different top-level structure entirely.
- Renaming (`rename_folder`/`rename_file`) only updates `dc:title` via a
  plain PUT. Many Nuxeo deployments keep a document's internal `name`
  (its path segment) in sync with `dc:title` automatically, but that's a
  configurable behavior, not a REST API guarantee — a deployment without
  that sync would see the *display* title change while the underlying
  path segment doesn't.
- `list_versions`' `is_current` flag: Nuxeo's `@versions` adapter doesn't
  document a boolean "this is the live version" field directly, so this
  compares each version's `versionLabel` against the live document's own
  `versionLabel` and, if nothing matches (e.g. unsaved changes since the
  last snapshot), falls back to assuming oldest-to-newest ordering and
  marking the last entry current. That fallback is a guess.
- `version_number` is derived from the `uid:major_version` property alone
  (minor dropped), the same simplification `AlfrescoProvider` makes from
  its "2.1"-style labels — it can collide across versions created with
  `increment: "minor"`, but the real identifier stays each version's own
  `uid` regardless of what int is displayed.

The server is per-connection (`config_fields` collects `base_url`), not a
single global. Documents (folders AND files are both just "documents"
distinguished by their `type` and the presence of the `Folderish` facet)
are addressed everywhere by `uid`, resolved once for "root"
(`folder_id=None`) to a dedicated "C-ECM" folder created under
`/default-domain/workspaces` on first use, and cached *per base URL*
since this provider instance is shared across every connection to it —
same double-checked-locking pattern as Alfresco's `_root_id`.

Unlike Alfresco (which needs a hand-rolled `$Trash` folder), Nuxeo has
genuine native soft-delete built in: a plain `DELETE` flags a document
`ecm:isTrashed = 1` in place (it stays at its original path) rather than
moving it anywhere, so `trash_folder`/`trash_file` are just that DELETE,
`restore_folder`/`restore_file` are the `@undelete` adapter, and
`delete_folder`/`delete_file` (meant to be a genuine permanent delete)
do the DELETE first — Nuxeo requires a document to be trashed before it
can be purged — followed by the `Document.PermanentlyDelete` operation.
"""

import base64
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

_APP_ROOT_NAME = "C-ECM"
_WORKSPACES_PATH = "/default-domain/workspaces"


class NuxeoProvider(StorageProvider):
    key = "nuxeo"
    display_name = "Hyland Nuxeo"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_cache: dict[str, tuple[str, str]] = {}  # base_url -> (uid, path)
        self._root_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Server URL", "http://localhost:8080/nuxeo")]

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _api(self, creds: dict) -> str:
        return self._base_url(creds) + "/api/v1"

    def _headers(self, creds: dict) -> dict:
        token = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> dict:
        url = self._api(creds) + path
        params = dict(kwargs.pop("params", None) or {})
        # Ask Nuxeo to include every schema's properties (dc:title,
        # dc:created, file:content, uid:major_version, ...) in the JSON —
        # documented as the `properties=*` query parameter. Without it,
        # a document's compact default representation omits most of the
        # fields the converters below rely on.
        params.setdefault("properties", "*")
        try:
            resp = requests.request(method, url, headers=self._headers(creds), params=params, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Nuxeo: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("Invalid Nuxeo credentials", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Nuxeo error {resp.status_code}: {resp.text[:300]}", status_code=502)
        if resp.content and resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {}

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderError("Server URL is required", status_code=400)
        creds = {"username": username, "password": password, "base_url": base_url}
        try:
            self._request(creds, "GET", "/me")
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    # --- app-root resolution ---

    def _find_child(self, creds: dict, parent_uid: str, title: str) -> dict | None:
        result = self._request(creds, "GET", f"/id/{parent_uid}/@children", params={"pageSize": 1000})
        for entry in result.get("entries", []):
            if entry.get("title") == title and self._is_folder(entry):
                return entry
        return None

    def _root(self, creds: dict) -> tuple[str, str]:
        cache_key = self._base_url(creds)
        cached = self._root_cache.get(cache_key)
        if cached:
            return cached
        # This provider instance is a process-wide singleton shared by
        # every connection to the same Nuxeo server, and FastAPI runs
        # sync handlers in a real thread pool — without a lock, concurrent
        # first-requests for a freshly connected server would each find no
        # existing root folder and each create their own duplicate.
        # Double-checked locking: re-test the cache after acquiring the
        # lock, since another thread may have populated it while we waited.
        with self._root_lock:
            cached = self._root_cache.get(cache_key)
            if cached:
                return cached
            workspaces = self._request(creds, "GET", "/path" + _WORKSPACES_PATH)
            workspaces_uid = workspaces["uid"]
            existing = self._find_child(creds, workspaces_uid, _APP_ROOT_NAME)
            if existing:
                result = (existing["uid"], existing.get("path") or f"{_WORKSPACES_PATH}/{_APP_ROOT_NAME}")
                self._root_cache[cache_key] = result
                return result
            created = self._request(
                creds, "POST", f"/id/{workspaces_uid}",
                json={
                    "entity-type": "document",
                    "name": _APP_ROOT_NAME,
                    "type": "Folder",
                    "properties": {"dc:title": _APP_ROOT_NAME},
                },
            )
            result = (created["uid"], created.get("path") or f"{_WORKSPACES_PATH}/{_APP_ROOT_NAME}")
            self._root_cache[cache_key] = result
            return result

    def _resolve_folder_id(self, creds: dict, folder_id: str | None) -> str:
        if folder_id is not None:
            return folder_id
        root_id, _ = self._root(creds)
        return root_id

    def _resolve_parent_id(self, creds: dict, doc: dict) -> str | None:
        """Nuxeo's document JSON doesn't carry a parent-id field directly
        (unlike Alfresco's `parentId`), so when the parent isn't already
        known from context (as it is in get_children/create/move, where we
        supply it ourselves), this derives it from the document's own
        `path` by resolving the parent path via `/path/{path}`."""
        root_id, root_path = self._root(creds)
        path = doc.get("path") or ""
        if not path or path == root_path:
            return None
        parent_path = path.rsplit("/", 1)[0] or "/"
        if parent_path == root_path:
            return None
        try:
            parent = self._request(creds, "GET", "/path" + parent_path)
        except ProviderError:
            return None
        parent_uid = parent.get("uid")
        return None if (not parent_uid or parent_uid == root_id) else parent_uid

    # --- converters ---

    @staticmethod
    def _is_folder(doc: dict) -> bool:
        return "Folderish" in (doc.get("facets") or [])

    @staticmethod
    def _as_int(value) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_dt(value: str | None):
        if not value:
            return None
        import datetime
        try:
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _version_number(props: dict, index: int) -> int:
        major = props.get("uid:major_version")
        if major is not None:
            try:
                return int(major)
            except (TypeError, ValueError):
                pass
        return index + 1

    def _doc_to_folder(self, doc: dict, parent_id: str | None) -> FolderInfo:
        props = doc.get("properties") or {}
        name = doc.get("title") or props.get("dc:title") or ""
        return FolderInfo(
            id=doc["uid"],
            name=name,
            parent_id=parent_id,
            created_at=self._parse_dt(props.get("dc:created")),
        )

    def _doc_to_file(self, doc: dict, parent_id: str | None) -> FileInfo:
        props = doc.get("properties") or {}
        name = doc.get("title") or props.get("dc:title") or ""
        blob = props.get("file:content") or {}
        return FileInfo(
            id=doc["uid"],
            name=name,
            folder_id=parent_id,
            version_number=self._version_number(props, 0),
            size_bytes=self._as_int(blob.get("length")),
            content_type=blob.get("mime-type"),
            updated_at=self._parse_dt(props.get("dc:modified") or doc.get("lastModified")),
        )

    def _folder_info(self, creds: dict, folder_id: str) -> FolderInfo:
        doc = self._request(creds, "GET", f"/id/{folder_id}")
        return self._doc_to_folder(doc, self._resolve_parent_id(creds, doc))

    def _put_blob(self, creds: dict, doc_id: str, filename: str, content_type: str, content: bytes) -> None:
        url = self._api(creds) + f"/id/{doc_id}/@blob/blobholder:0"
        try:
            resp = requests.put(
                url, headers=self._headers(creds),
                files={"content": (filename, content, content_type)}, timeout=60,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Nuxeo: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"Nuxeo blob upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)

    # --- folders ---

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        node_id = self._resolve_folder_id(creds, folder_id)
        result = self._request(creds, "GET", f"/id/{node_id}/@children", params={"pageSize": 1000})
        entries = result.get("entries", [])
        folders = [self._doc_to_folder(e, folder_id) for e in entries if self._is_folder(e)]
        files = [self._doc_to_file(e, folder_id) for e in entries if not self._is_folder(e)]

        current_folder = None
        if folder_id is not None:
            current_folder = self._folder_info(creds, folder_id)

        return FolderContents(
            folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
            folders=folders, files=files,
        )

    def _nxql(self, creds: dict, query: str) -> list[dict]:
        result = self._request(
            creds, "POST", "/search/lang/NXQL/execute",
            params={"query": query, "pageSize": 1000},
        )
        return result.get("entries", [])

    def list_trash(self, creds: dict) -> FolderContents:
        _, root_path = self._root(creds)
        escaped_path = root_path.replace("'", "''")
        query = f"SELECT * FROM Document WHERE ecm:isTrashed = 1 AND ecm:path STARTSWITH '{escaped_path}'"
        entries = self._nxql(creds, query)
        folders, files = [], []
        for e in entries:
            parent_id = self._resolve_parent_id(creds, e)
            if self._is_folder(e):
                folders.append(self._doc_to_folder(e, parent_id))
            else:
                files.append(self._doc_to_file(e, parent_id))
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=folders, files=files,
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        node_id = self._resolve_folder_id(creds, parent_id)
        created = self._request(
            creds, "POST", f"/id/{node_id}",
            json={"entity-type": "document", "name": name, "type": "Folder", "properties": {"dc:title": name}},
        )
        return self._doc_to_folder(created, parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._request(
            creds, "PUT", f"/id/{folder_id}",
            json={"entity-type": "document", "uid": folder_id, "properties": {"dc:title": name}},
        )
        return self._doc_to_folder(updated, self._resolve_parent_id(creds, updated))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve_folder_id(creds, new_parent_id)
        moved = self._request(creds, "POST", f"/id/{folder_id}/@move", params={"destination": target})
        return self._doc_to_folder(moved, new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        # Nuxeo requires a document to be trashed before it can be purged.
        self._request(creds, "DELETE", f"/id/{folder_id}")
        self._request(creds, "POST", f"/id/{folder_id}/@op/Document.PermanentlyDelete", json={})

    # --- files ---

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        node_id = self._resolve_folder_id(creds, folder_id)
        created = self._request(
            creds, "POST", f"/id/{node_id}",
            json={"entity-type": "document", "name": name, "type": "File", "properties": {"dc:title": name}},
        )
        uid = created["uid"]
        self._put_blob(creds, uid, name, content_type, content)
        return self.get_file(creds, uid)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        doc = self._request(creds, "GET", f"/id/{file_id}")
        return self._doc_to_file(doc, self._resolve_parent_id(creds, doc))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._request(
            creds, "PUT", f"/id/{file_id}",
            json={"entity-type": "document", "uid": file_id, "properties": {"dc:title": name}},
        )
        return self._doc_to_file(updated, self._resolve_parent_id(creds, updated))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve_folder_id(creds, new_folder_id)
        moved = self._request(creds, "POST", f"/id/{file_id}/@move", params={"destination": target})
        return self._doc_to_file(moved, new_folder_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        # Nuxeo requires a document to be trashed before it can be purged.
        self._request(creds, "DELETE", f"/id/{file_id}")
        self._request(creds, "POST", f"/id/{file_id}/@op/Document.PermanentlyDelete", json={})

    def get_content(self, creds: dict, file_id: str) -> bytes:
        url = self._api(creds) + f"/id/{file_id}/@blob/blobholder:0"
        try:
            resp = requests.get(url, headers=self._headers(creds), timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Nuxeo: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError("Couldn't fetch content", status_code=404)
        return resp.content

    # --- versions ---

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        live = self._request(creds, "GET", f"/id/{file_id}")
        live_label = live.get("versionLabel")
        result = self._request(creds, "GET", f"/id/{file_id}/@versions")
        entries = result.get("entries", [])
        out = []
        matched_current = False
        for i, e in enumerate(entries):
            props = e.get("properties") or {}
            blob = props.get("file:content") or {}
            is_current = bool(live_label) and e.get("versionLabel") == live_label
            matched_current = matched_current or is_current
            out.append(VersionInfo(
                id=e["uid"],
                version_number=self._version_number(props, i),
                size_bytes=self._as_int(blob.get("length")),
                content_type=blob.get("mime-type"),
                is_current=is_current,
                updated_at=self._parse_dt(props.get("dc:modified") or e.get("lastModified")),
            ))
        # Fallback for when the live document's versionLabel matched no
        # snapshot (e.g. unsaved changes since the last version): assume
        # Nuxeo's documented oldest-to-newest @versions ordering and mark
        # the last entry current. Unconfirmed — see module docstring.
        if out and not matched_current:
            out[-1].is_current = True
        return out

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        self._put_blob(creds, file_id, "content", content_type, content)
        self._request(
            creds, "POST", f"/id/{file_id}/@op/Document.CreateVersion",
            json={"params": {"increment": "minor"}},
        )
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        url = self._api(creds) + f"/id/{version_id}/@blob/blobholder:0"
        try:
            resp = requests.get(url, headers=self._headers(creds), timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Nuxeo: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError("Version content not found", status_code=404)
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        self._request(
            creds, "POST", f"/id/{file_id}/@op/Document.RestoreVersion",
            json={"params": {"value": version_id}},
        )
        return self.get_file(creds, file_id)

    # --- trash (Nuxeo has genuine native soft-delete, so this is a thin
    # wrapper around DELETE/@undelete rather than emulation via a
    # dedicated folder) ---

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "DELETE", f"/id/{folder_id}")

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        self._request(creds, "POST", f"/id/{folder_id}/@undelete")
        return self._folder_info(creds, folder_id)

    def trash_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "DELETE", f"/id/{file_id}")

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        self._request(creds, "POST", f"/id/{file_id}/@undelete")
        return self.get_file(creds, file_id)

    # --- search ---

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        _, root_path = self._root(creds)
        escaped_query = query.replace("'", "''")
        escaped_path = root_path.replace("'", "''")
        nxql = (
            f"SELECT * FROM Document WHERE ecm:fulltext = '{escaped_query}' "
            f"AND ecm:isTrashed = 0 AND ecm:path STARTSWITH '{escaped_path}'"
        )
        entries = self._nxql(creds, nxql)
        folders, files = [], []
        for e in entries:
            parent_id = self._resolve_parent_id(creds, e)
            if self._is_folder(e):
                folders.append(self._doc_to_folder(e, parent_id))
            else:
                files.append(self._doc_to_file(e, parent_id))
        return folders, files
