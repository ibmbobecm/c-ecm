"""OpenText Documentum provider, via Documentum REST Services (sometimes
called "D2-REST" or "Documentum REST Services" — a hypermedia HAL+JSON API,
typically mounted at a base URL like `http://<host>:<port>/dctm-rest`,
addressing one repository/docbase at `/repositories/{repository}`).

UNVERIFIED, and more so than Alfresco's provider in this same package.
Alfresco publishes an OpenAPI spec and client SDKs that pin its REST
contract down precisely; Documentum's REST Services API is comparatively
sparse and inconsistent in public documentation, so several choices below
are reconstructed from Documentum's *object model* (decades-stable, solid
ground — dm_cabinet/dm_folder/dm_document, i_chronicle_id, i_folder_id,
r_version_label, DQL) rather than from a confirmed REST contract. Treat
these specifically as best-effort, and verify against a live repository
before trusting them the way FileNet's and local disk's providers (verified
live) are trusted:

  - The exact `_embedded` key name Documentum's HAL list responses use
    (children / folders / documents / entries / ...) isn't something this
    environment could confirm — `_extract_entries()` below is deliberately
    tolerant and takes whichever embedded list it finds, rather than
    hardcoding one name.
  - `move_folder` / `move_file` / trash / restore: Documentum has no single
    -call "move" endpoint the way Alfresco has `/move` — folder membership
    is a many-to-many link (an object can sit in more than one folder), so
    these are implemented as a pair of DQL `UPDATE ... OBJECT UNLINK
    ID(...) / LINK ID(...) WHERE r_object_id = ...` statements. LINK/UNLINK
    is a real, documented DQL capability, but the exact clause syntax below
    is reconstructed from memory of the DQL reference, not confirmed live.
  - `restore_version`: rather than guess at a native "revert to version N"
    endpoint, this downloads the target version's content and checks it
    back in as a new current version via `create_version` — a fallback
    that's always correct from the end user's point of view (the file's
    current content becomes what that old version held), even if it isn't
    Documentum's literal native revert operation.
  - Trash: Documentum has no confidently-documented, listable recycle-bin
    REST endpoint, so it's emulated the same way Alfresco's is — a
    dedicated hidden "C-ECM-Trash" folder under the app root that
    trash/restore move objects into and out of.
  - `a_content_type` on a Documentum document is a *format name*
    (dm_format.name, e.g. "pdf", "msw12"), not a MIME type. Only a handful
    of common formats are mapped to real MIME types (`_FORMAT_TO_MIME`
    below); anything else passes through as the raw format name.
  - Whether a plain `GET` on a folder/document resource returns the full
    property set (including `i_chronicle_id`, `i_folder_id`,
    `r_version_label`) or only a default subset requiring an explicit
    `?properties=...`/view parameter isn't confirmed — this assumes the
    former (a "full representation by default" response), which is common
    for these REST APIs but unverified here.

One Documentum-specific design point below is *not* a guess, because it
follows directly from Documentum's well-established object model: a
Documentum document mints a brand-new `r_object_id` at every checked-in
version, so a version's object id can't double as C-ECM's stable `file_id`
the way Alfresco's stable node id can. Instead, `file_id` here is the
document's `i_chronicle_id` (Documentum's own stable identifier for "this
document across all its versions"), and every file-level operation
resolves it to whichever version currently carries the `CURRENT` label
before touching Documentum's REST surface (see `_current_version_id`). A
`VersionInfo.id`, by contrast, is a specific version's own `r_object_id` —
exactly the "opaque, provider-decided" latitude the base interface grants.
Folders don't version in Documentum, so `folder_id` is simply a folder's
`r_object_id`.

The server *and* repository are both per-connection (`config_fields`
collects `base_url` and `repository`, since one Documentum installation
commonly hosts several repositories/docbases with entirely separate
content) rather than a single global. Like Alfresco, `folder_id=None`
(C-ECM's "root") resolves to a dedicated "C-ECM" cabinet created on first
use and cached *per (base_url, repository) pair*, since this provider
instance is shared across every connection to it.
"""

import base64
import json
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
_TRASH_NAME = "C-ECM-Trash"

# `a_content_type` on a Documentum document is a dm_format *name*, not a
# MIME type (see module docstring). Only the everyday formats are mapped
# here; anything else is passed through as the raw format name.
_FORMAT_TO_MIME = {
    "pdf": "application/pdf",
    "msw12": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "msw8": "application/msword",
    "excel12book": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "excel8book": "application/vnd.ms-excel",
    "ppt12": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt8": "application/vnd.ms-powerpoint",
    "crtext": "text/plain",
    "html": "text/html",
    "jpeg_image": "image/jpeg",
    "gif_image": "image/gif",
    "png_image": "image/png",
}


class DocumentumProvider(StorageProvider):
    key = "documentum"
    display_name = "OpenText Documentum"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("base_url", "Server URL", "http://localhost:8080/dctm-rest"),
            ConfigField("repository", "Repository", "Repo1"),
        ]

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _api(self, creds: dict) -> str:
        return self._base_url(creds) + f"/repositories/{creds['repository']}"

    def _headers(self, creds: dict) -> dict:
        token = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
        # Documentum REST Services negotiates representation via Accept
        # (it can serve HAL+XML too) — asking explicitly avoids depending
        # on whatever the server's default happens to be.
        return {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> dict:
        url = self._api(creds) + path
        try:
            resp = requests.request(method, url, headers=self._headers(creds), timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Documentum: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("Invalid Documentum credentials", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Documentum error {resp.status_code}: {resp.text[:300]}", status_code=502)
        content_type = resp.headers.get("Content-Type", "")
        if resp.content and "json" in content_type.lower():
            return resp.json()
        return {}

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        repository = (config.get("repository") or "").strip()
        if not base_url:
            raise ProviderError("Server URL is required", status_code=400)
        if not repository:
            raise ProviderError("Repository name is required", status_code=400)
        creds = {"username": username, "password": password, "base_url": base_url, "repository": repository}
        try:
            # The repository's own resource (`/repositories/{repository}`)
            # doubles as a lightweight "can we log in at all" probe.
            self._request(creds, "GET", "")
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    # --- low-level DQL + HAL helpers ---

    @staticmethod
    def _escape_dql_string(value: str) -> str:
        return value.replace("'", "''")

    def _dql(self, creds: dict, query: str) -> list[dict]:
        result = self._request(creds, "POST", "/dql", json={"query": query})
        return self._extract_entries(result)

    @staticmethod
    def _extract_entries(payload) -> list[dict]:
        """Documentum REST Services wraps list results HAL-style under
        `_embedded`, but the exact key for the array of items (type
        -specific like "children"/"folders"/"documents", or a generic
        "entries") isn't confirmed here — take whichever list we find so
        this survives resource/version differences we couldn't verify."""
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        embedded = payload.get("_embedded")
        if isinstance(embedded, dict):
            for value in embedded.values():
                if isinstance(value, list):
                    return value
        entries = payload.get("entries")
        if isinstance(entries, list):
            return entries
        return []

    @staticmethod
    def _props(payload) -> dict:
        if not isinstance(payload, dict):
            return {}
        return payload.get("properties") or {}

    # --- app-root cabinet + trash folder (cached per base_url+repository) ---

    def _cache_key(self, creds: dict) -> str:
        return f"{self._base_url(creds)}|{creds['repository']}"

    def _find_cabinet(self, creds: dict, name: str) -> str | None:
        safe = self._escape_dql_string(name)
        rows = self._dql(creds, f"SELECT r_object_id FROM dm_cabinet WHERE object_name = '{safe}'")
        return self._props(rows[0]).get("r_object_id") if rows else None

    def _find_folder_by_name(self, creds: dict, parent_id: str, name: str) -> str | None:
        safe_name = self._escape_dql_string(name)
        safe_parent = self._escape_dql_string(parent_id)
        rows = self._dql(
            creds,
            f"SELECT r_object_id FROM dm_folder WHERE object_name = '{safe_name}' "
            f"AND FOLDER(ID('{safe_parent}'))",
        )
        return self._props(rows[0]).get("r_object_id") if rows else None

    def _root_id(self, creds: dict) -> str:
        cache_key = self._cache_key(creds)
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        # This provider instance is a process-wide singleton shared by
        # every connection to the same Documentum repository, and FastAPI
        # runs sync handlers in a real thread pool — without a lock,
        # concurrent first-requests for a freshly connected repository
        # would each find no existing root cabinet and each create their
        # own duplicate. Double-checked locking: re-test the cache after
        # acquiring the lock, since another thread may have populated it
        # while we waited.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            existing = self._find_cabinet(creds, _APP_ROOT_NAME)
            if existing:
                self._root_id_cache[cache_key] = existing
                return existing
            created = self._request(
                creds, "POST", "/cabinets", json={"properties": {"object_name": _APP_ROOT_NAME}}
            )
            root_id = self._props(created).get("r_object_id")
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_id(self, creds: dict) -> str:
        cache_key = self._cache_key(creds)
        cached = self._trash_id_cache.get(cache_key)
        if cached:
            return cached
        with self._trash_id_lock:
            cached = self._trash_id_cache.get(cache_key)
            if cached:
                return cached
            root = self._root_id(creds)
            existing = self._find_folder_by_name(creds, root, _TRASH_NAME)
            if existing:
                self._trash_id_cache[cache_key] = existing
                return existing
            created = self._request(
                creds, "POST", f"/folders/{root}/subfolders",
                json={"properties": {"object_name": _TRASH_NAME}},
            )
            trash_id = self._props(created).get("r_object_id")
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def _resolve_folder_id(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_id(creds)

    # --- type / property mapping ---

    @staticmethod
    def _is_folder_type(object_type: str | None) -> bool:
        return bool(object_type) and (object_type == "dm_cabinet" or object_type.startswith("dm_folder"))

    @staticmethod
    def _is_document_type(object_type: str | None) -> bool:
        return bool(object_type) and object_type.startswith("dm_document")

    def _parent_id_from_props(self, props: dict, root_id: str) -> str | None:
        # i_folder_id is a genuine dm_sysobject repeating attribute holding
        # the r_object_id of every folder this object is linked into
        # (Documentum allows multi-parent linking). C-ECM's model is
        # single-parent, so the first linkage is treated as *the* parent.
        folder_ids = props.get("i_folder_id") or []
        if isinstance(folder_ids, str):
            folder_ids = [folder_ids]
        parent = folder_ids[0] if folder_ids else None
        return None if parent == root_id else parent

    @staticmethod
    def _version_number_from_labels(labels) -> int:
        # Documentum labels versions like ["1.0", "CURRENT"] or ["2.1"] —
        # VersionInfo.version_number is a plain int everywhere else in
        # C-ECM, so this keeps the major number off the first numeric
        # -looking label (the label itself is still the real identifier
        # for anything that needs it).
        if isinstance(labels, str):
            labels = [labels]
        for label in labels or []:
            try:
                return int(float(label))
            except (TypeError, ValueError):
                continue
        return 1

    @staticmethod
    def _has_current_label(labels) -> bool:
        if isinstance(labels, str):
            return labels == "CURRENT"
        return "CURRENT" in (labels or [])

    def _format_to_content_type(self, format_name: str | None) -> str | None:
        if not format_name:
            return None
        return _FORMAT_TO_MIME.get(format_name, format_name)

    @staticmethod
    def _parse_dctm_dt(value: str | None):
        if not value:
            return None
        import datetime
        try:
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _entry_to_folder(self, payload: dict, root_id: str) -> FolderInfo:
        props = self._props(payload)
        return FolderInfo(
            id=props.get("r_object_id", ""),
            name=props.get("object_name", ""),
            parent_id=self._parent_id_from_props(props, root_id),
            created_at=self._parse_dctm_dt(props.get("r_creation_date")),
        )

    def _entry_to_file(self, payload: dict, root_id: str) -> FileInfo:
        props = self._props(payload)
        # `id` is the chronicle id, not this particular version's own
        # r_object_id — see the module docstring for why.
        chronicle_id = props.get("i_chronicle_id") or props.get("r_object_id", "")
        return FileInfo(
            id=chronicle_id,
            name=props.get("object_name", ""),
            folder_id=self._parent_id_from_props(props, root_id),
            version_number=self._version_number_from_labels(props.get("r_version_label")),
            size_bytes=props.get("r_full_content_size"),
            content_type=self._format_to_content_type(props.get("a_content_type")),
            updated_at=self._parse_dctm_dt(props.get("r_modify_date")),
        )

    def _current_version_id(self, creds: dict, file_id: str) -> str:
        """`file_id` is a document's `i_chronicle_id` — resolve it to the
        r_object_id of whichever version currently carries the 'CURRENT'
        label, since Documentum's document-level REST endpoints address one
        specific version object, not the chronicle (the whole version tree)
        as a unit."""
        safe = self._escape_dql_string(file_id)
        rows = self._dql(
            creds,
            f"SELECT r_object_id FROM dm_document WHERE i_chronicle_id = '{safe}' AND ANY r_version_label = 'CURRENT'",
        )
        if not rows:
            raise ProviderError("Document not found", status_code=404)
        return self._props(rows[0]).get("r_object_id")

    def _relink(self, creds: dict, object_type: str, object_id: str, old_parent_id: str, new_parent_id: str) -> None:
        """Best-effort move: Documentum has no single documented "move"
        endpoint (folder membership is a many-to-many link, not a single
        parent pointer), so this unlinks the object from its old parent and
        links it into the new one via two DQL UPDATE statements. LINK/
        UNLINK is a real DQL capability; issuing it as two separate
        single-clause statements (rather than one combined statement) is a
        deliberate hedge against uncertainty over the exact combined-clause
        grammar — see the module docstring."""
        safe_obj = self._escape_dql_string(object_id)
        safe_old = self._escape_dql_string(old_parent_id)
        safe_new = self._escape_dql_string(new_parent_id)
        self._dql(creds, f"UPDATE {object_type} OBJECT UNLINK ID('{safe_old}') WHERE r_object_id = '{safe_obj}'")
        self._dql(creds, f"UPDATE {object_type} OBJECT LINK ID('{safe_new}') WHERE r_object_id = '{safe_obj}'")

    # --- folders ---

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id if folder_id is not None else root_id
        result = self._request(
            creds, "GET", f"/folders/{node_id}/children",
            params={"type": "children", "items-per-page": 1000},
        )
        entries = self._extract_entries(result)
        folders, files = [], []
        for e in entries:
            props = self._props(e)
            if props.get("object_name") == _TRASH_NAME:
                continue
            object_type = props.get("r_object_type")
            if self._is_folder_type(object_type):
                folders.append(self._entry_to_folder(e, root_id))
            elif self._is_document_type(object_type):
                files.append(self._entry_to_file(e, root_id))

        current_folder = None
        if folder_id is not None:
            node = self._request(creds, "GET", f"/folders/{node_id}")
            current_folder = self._entry_to_folder(node, root_id)

        return FolderContents(
            folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
            folders=folders, files=files,
        )

    def list_trash(self, creds: dict) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = self._trash_id(creds)
        result = self._request(
            creds, "GET", f"/folders/{node_id}/children",
            params={"type": "children", "items-per-page": 1000},
        )
        entries = self._extract_entries(result)
        folders, files = [], []
        for e in entries:
            props = self._props(e)
            object_type = props.get("r_object_type")
            if self._is_folder_type(object_type):
                folders.append(self._entry_to_folder(e, root_id))
            elif self._is_document_type(object_type):
                files.append(self._entry_to_file(e, root_id))
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=folders, files=files,
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        node_id = self._resolve_folder_id(creds, parent_id)
        created = self._request(
            creds, "POST", f"/folders/{node_id}/subfolders",
            json={"properties": {"object_name": name}},
        )
        return self._entry_to_folder(created, self._root_id(creds))

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._request(
            creds, "PUT", f"/folders/{folder_id}", json={"properties": {"object_name": name}}
        )
        return self._entry_to_folder(updated, self._root_id(creds))

    def _move_folder_to(self, creds: dict, folder_id: str, target_parent_id: str) -> FolderInfo:
        root_id = self._root_id(creds)
        current = self._request(creds, "GET", f"/folders/{folder_id}")
        old_parent = self._parent_id_from_props(self._props(current), root_id) or root_id
        if old_parent != target_parent_id:
            self._relink(creds, "dm_folder", folder_id, old_parent, target_parent_id)
        updated = self._request(creds, "GET", f"/folders/{folder_id}")
        return self._entry_to_folder(updated, root_id)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        return self._move_folder_to(creds, folder_id, self._resolve_folder_id(creds, new_parent_id))

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "DELETE", f"/folders/{folder_id}")

    # --- files ---

    def create_document(
        self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes
    ) -> FileInfo:
        node_id = self._resolve_folder_id(creds, folder_id)
        url = self._api(creds) + f"/folders/{node_id}/documents"
        files = {
            "content": (name, content, content_type),
            "properties": (None, json.dumps({"object_name": name}), "application/json"),
        }
        try:
            resp = requests.post(url, headers=self._headers(creds), files=files, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Documentum: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"Documentum upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self._entry_to_file(resp.json(), self._root_id(creds))

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        obj_id = self._current_version_id(creds, file_id)
        node = self._request(creds, "GET", f"/documents/{obj_id}")
        return self._entry_to_file(node, self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        obj_id = self._current_version_id(creds, file_id)
        updated = self._request(
            creds, "PUT", f"/documents/{obj_id}", json={"properties": {"object_name": name}}
        )
        return self._entry_to_file(updated, self._root_id(creds))

    def _move_file_to(self, creds: dict, file_id: str, target_parent_id: str) -> FileInfo:
        root_id = self._root_id(creds)
        obj_id = self._current_version_id(creds, file_id)
        current = self._request(creds, "GET", f"/documents/{obj_id}")
        old_parent = self._parent_id_from_props(self._props(current), root_id) or root_id
        if old_parent != target_parent_id:
            self._relink(creds, "dm_document", obj_id, old_parent, target_parent_id)
        updated = self._request(creds, "GET", f"/documents/{obj_id}")
        return self._entry_to_file(updated, root_id)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        return self._move_file_to(creds, file_id, self._resolve_folder_id(creds, new_folder_id))

    def delete_file(self, creds: dict, file_id: str) -> None:
        obj_id = self._current_version_id(creds, file_id)
        # `del-all-versions` (best-effort param name, unverified) asks for
        # the whole version tree to go, not just the current version —
        # deliberate, since leaving orphaned older versions behind on a
        # plain delete would be a worse default than a guessed param name.
        self._request(creds, "DELETE", f"/documents/{obj_id}", params={"del-all-versions": "true"})

    def get_content(self, creds: dict, file_id: str) -> bytes:
        obj_id = self._current_version_id(creds, file_id)
        url = self._api(creds) + f"/documents/{obj_id}/content"
        resp = requests.get(url, headers=self._headers(creds), timeout=60)
        if resp.status_code >= 400:
            raise ProviderError("Couldn't fetch content", status_code=404)
        return resp.content

    # --- versions ---

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        obj_id = self._current_version_id(creds, file_id)
        result = self._request(creds, "GET", f"/documents/{obj_id}/versions")
        entries = self._extract_entries(result)
        out = []
        for e in entries:
            props = self._props(e)
            labels = props.get("r_version_label") or []
            out.append(VersionInfo(
                id=props.get("r_object_id", ""),
                version_number=self._version_number_from_labels(labels),
                size_bytes=props.get("r_full_content_size"),
                content_type=self._format_to_content_type(props.get("a_content_type")),
                is_current=self._has_current_label(labels),
                updated_at=self._parse_dctm_dt(props.get("r_modify_date")),
            ))
        out.sort(key=lambda v: v.version_number, reverse=True)
        return out

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        obj_id = self._current_version_id(creds, file_id)
        current = self._request(creds, "GET", f"/documents/{obj_id}")
        name = self._props(current).get("object_name") or "content"
        url = self._api(creds) + f"/documents/{obj_id}/versions"
        files = {"content": (name, content, content_type)}
        try:
            resp = requests.post(url, headers=self._headers(creds), files=files, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Documentum: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"Documentum version upload failed ({resp.status_code})", status_code=502)
        return self._entry_to_file(resp.json(), self._root_id(creds))

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        # version_id is already a specific version's own r_object_id, so it
        # addresses its content directly (unlike file_id, no chronicle
        # -to-current-version resolution needed).
        url = self._api(creds) + f"/documents/{version_id}/content"
        resp = requests.get(url, headers=self._headers(creds), timeout=60)
        if resp.status_code >= 400:
            raise ProviderError("Version content not found", status_code=404)
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        content = self.get_version_content(creds, file_id, version_id)
        version_props = self._props(self._request(creds, "GET", f"/documents/{version_id}"))
        content_type = self._format_to_content_type(version_props.get("a_content_type")) or "application/octet-stream"
        return self.create_version(creds, file_id, content_type, content)

    # --- trash (emulated — see module docstring) ---

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self._move_folder_to(creds, folder_id, self._trash_id(creds))

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        return self._move_folder_to(creds, folder_id, self._root_id(creds))

    def trash_file(self, creds: dict, file_id: str) -> None:
        self._move_file_to(creds, file_id, self._trash_id(creds))

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        return self._move_file_to(creds, file_id, self._root_id(creds))

    # --- search ---

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root = self._root_id(creds)
        safe_query = self._escape_dql_string(query)
        safe_root = self._escape_dql_string(root)
        rows = self._dql(
            creds,
            "SELECT * FROM dm_sysobject WHERE object_name LIKE "
            f"'%{safe_query}%' AND FOLDER(ID('{safe_root}'), DESCEND)",
        )
        folders, files = [], []
        for row in rows:
            props = self._props(row)
            object_type = props.get("r_object_type")
            if self._is_folder_type(object_type):
                folders.append(self._entry_to_folder(row, root))
            elif self._is_document_type(object_type):
                files.append(self._entry_to_file(row, root))
        return folders, files
