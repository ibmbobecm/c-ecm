"""OpenText Content Server (OTCS) provider, via its REST API v2
(`{base_url}/api/v2/...`), with ticket-based auth from API v1
(`{base_url}/api/v1/auth`).

UNVERIFIED — written from OpenText's documented REST API v2 conventions
(node-centric CRUD under `/api/v2/nodes`, ticket auth under `/api/v1/auth`),
but there's no live OTCS server in this environment to test it against. Run
it against a real instance before trusting it the way FileNet's and local
disk's providers are trusted (those were verified live, this wasn't). The
parts most worth independently re-checking before that:

- **Ticket refresh on 401.** OTCS auth tickets are session-scoped and can
  expire mid-session; `_request_with_retry` catches a 401 on any call,
  fetches a fresh ticket via `/api/v1/auth`, and retries once. The retry
  mechanics are straightforward, but the exact conditions under which a
  real OTCS server returns 401 for an expired-vs-invalid ticket aren't
  independently confirmed here.
- **Version restore.** No confidently-known "promote this version to
  current" endpoint name exists in this file's knowledge of the v2 API, so
  `restore_version` uses the always-correct fallback instead: download the
  target version's content, then call `create_version` with it to make it
  the new current version. This works regardless of whether OTCS exposes a
  native promote/restore endpoint, at the cost of one extra version being
  added to the document's history rather than truly "restoring" the old one
  in place.
- **Recycle bin emulation.** OTCS does have a native recycle bin, but its
  REST shape isn't confidently known here, so trash is emulated the same
  way Alfresco's provider emulates it: `trash_*` moves the node into a
  dedicated hidden "C-ECM-Trash" folder under this app's own root folder,
  and `restore_*` moves it back to the root. Nothing is ever sent to OTCS's
  real recycle bin.
- **Response/request body shapes.** The exact JSON shape of v2 responses
  (whether a node's properties come back bare, or wrapped as
  `{"results": {"data": {"properties": {...}}}}`) and of write-endpoint
  request bodies (flat multipart fields vs. some other encoding) is taken
  from documented convention, not confirmed against a live server. Reads are
  parsed defensively (`_extract_props`/`_extract_props_list` tolerate a
  handful of plausible shapes) and every create/rename/move method does a
  follow-up `GET /nodes/{id}` rather than trusting the mutating call's
  response body, since OTCS's own "create node" response is only documented
  to reliably include the new node's bare id, not its full properties.

The server is per-connection (`config_fields` collects `base_url`), not a
single global — different connections can point at entirely different OTCS
instances. Like Alfresco, OTCS addresses everything by a numeric node id
(stringified here), not by path — so `folder_id=None` (C-ECM's "root") is
resolved once to a real node id (a dedicated "C-ECM" folder created under
OTCS's well-known Enterprise Workspace, conventionally node id 2000 in a
default install) and cached *per base URL*, since this provider instance is
shared across every connection to it.
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

_ENTERPRISE_WS_ID = "2000"
_APP_ROOT_NAME = "C-ECM"
_TRASH_NAME = "C-ECM-Trash"
_FOLDER_TYPE = 0
_DOCUMENT_TYPE = 144


class OpenTextContentServerProvider(StorageProvider):
    key = "opentext_content_server"
    display_name = "OpenText Content Server"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Server URL", "http://localhost/otcs/cs.exe")]

    # --- low-level plumbing ---

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _api(self, creds: dict) -> str:
        return self._base_url(creds) + "/api/v2"

    def _auth_url(self, creds: dict) -> str:
        return self._base_url(creds) + "/api/v1/auth"

    def _headers(self, creds: dict) -> dict:
        return {"OTCSTicket": creds["ticket"]}

    def _fetch_ticket(self, creds: dict) -> str:
        try:
            resp = requests.post(
                self._auth_url(creds),
                data={"username": creds["username"], "password": creds["password"]},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach OpenText Content Server: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError("Invalid OpenText Content Server credentials", status_code=401)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        ticket = body.get("ticket")
        if not ticket:
            raise ProviderError("OpenText Content Server didn't return an auth ticket", status_code=401)
        return ticket

    def _request_with_retry(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        timeout = kwargs.pop("timeout", 30)

        def _do() -> requests.Response:
            try:
                return requests.request(method, url, headers=self._headers(creds), timeout=timeout, **kwargs)
            except requests.RequestException as exc:
                raise ProviderError(f"Couldn't reach OpenText Content Server: {exc}", status_code=502)

        resp = _do()
        if resp.status_code == 401:
            # Tickets expire; refresh once and retry rather than failing
            # the caller's request outright. See module docstring — this
            # retry's exact trigger conditions aren't independently
            # verified against a live server.
            creds["ticket"] = self._fetch_ticket(creds)
            resp = _do()
        return resp

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> dict:
        url = self._api(creds) + path
        resp = self._request_with_retry(creds, method, url, **kwargs)
        if resp.status_code == 401:
            raise ProviderError("Invalid OpenText Content Server credentials", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"OpenText Content Server error {resp.status_code}: {resp.text[:300]}", status_code=502)
        if resp.content and resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {}

    def _multipart_request(self, creds: dict, path: str, data: dict, files: dict | None = None) -> dict:
        url = self._api(creds) + path
        resp = self._request_with_retry(creds, "POST", url, data=data, files=files, timeout=60)
        if resp.status_code == 401:
            raise ProviderError("Invalid OpenText Content Server credentials", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError(f"OpenText Content Server error {resp.status_code}: {resp.text[:300]}", status_code=502)
        if resp.content and resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {}

    def _get_content(self, creds: dict, path: str) -> bytes:
        url = self._api(creds) + path
        resp = self._request_with_retry(creds, "GET", url, timeout=60)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError("Couldn't fetch content", status_code=404)
        return resp.content

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderError("Server URL is required", status_code=400)
        creds = {"username": username, "password": password, "base_url": base_url}
        try:
            creds["ticket"] = self._fetch_ticket(creds)
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    # --- response-shape normalization (see module docstring: the exact
    # wrapping of a v2 payload isn't independently confirmed, so this
    # tolerates a bare dict, a `results` envelope holding either a single
    # object or a list, and an optional `data.properties` nesting) ---

    def _extract_props(self, payload) -> dict:
        if not isinstance(payload, dict):
            return {}
        body = payload.get("results", payload)
        if isinstance(body, list):
            body = body[0] if body else {}
        if not isinstance(body, dict):
            return {}
        data = body.get("data", body)
        if isinstance(data, dict) and "properties" in data:
            return data["properties"]
        return data if isinstance(data, dict) else {}

    def _extract_props_list(self, payload) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        body = payload.get("results", payload)
        if isinstance(body, dict):
            body = [body]
        if not isinstance(body, list):
            return []
        out = []
        for entry in body:
            if not isinstance(entry, dict):
                continue
            data = entry.get("data", entry)
            if isinstance(data, dict) and "properties" in data:
                out.append(data["properties"])
            elif isinstance(data, dict):
                out.append(data)
        return out

    def _resolve_created_id(self, payload) -> str:
        props = self._extract_props(payload)
        node_id = props.get("id")
        if node_id is None and isinstance(payload, dict):
            node_id = payload.get("id")
        if node_id is None:
            raise ProviderError("OpenText Content Server didn't return the new node's id", status_code=502)
        return str(node_id)

    @staticmethod
    def _parse_otcs_dt(value):
        if not value:
            return None
        import datetime
        if isinstance(value, (int, float)):
            try:
                return datetime.datetime.fromtimestamp(value)
            except (OverflowError, OSError, ValueError):
                return None
        if isinstance(value, str):
            try:
                return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _node_to_folder(self, props: dict, root_id: str) -> FolderInfo:
        parent_id = props.get("parent_id")
        parent_id = str(parent_id) if parent_id is not None else None
        if parent_id == root_id:
            parent_id = None
        return FolderInfo(
            id=str(props.get("id")),
            name=props.get("name", ""),
            parent_id=parent_id,
            created_at=self._parse_otcs_dt(props.get("create_date")),
        )

    def _node_to_file(self, props: dict, root_id: str) -> FileInfo:
        parent_id = props.get("parent_id")
        parent_id = str(parent_id) if parent_id is not None else None
        if parent_id == root_id:
            parent_id = None
        version_number = props.get("version_number")
        try:
            version_number = int(version_number) if version_number is not None else 1
        except (TypeError, ValueError):
            version_number = 1
        return FileInfo(
            id=str(props.get("id")),
            name=props.get("name", ""),
            folder_id=parent_id,
            version_number=version_number,
            size_bytes=props.get("size"),
            content_type=props.get("mime_type"),
            updated_at=self._parse_otcs_dt(props.get("modify_date")),
        )

    # --- app root / trash folder resolution (cached per base URL) ---

    def _find_child_by_name(self, creds: dict, parent_id: str, name: str, node_type: int) -> str | None:
        result = self._request(creds, "GET", f"/nodes/{parent_id}/nodes", params={"limit": 200})
        for props in self._extract_props_list(result):
            if props.get("name") == name and props.get("type") == node_type:
                return str(props["id"])
        return None

    def _root_id(self, creds: dict) -> str:
        cache_key = self._base_url(creds)
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        # This provider instance is a process-wide singleton shared by
        # every connection to the same OTCS server, and FastAPI runs sync
        # handlers in a real thread pool — without a lock, concurrent
        # first-requests for a freshly connected server would each find no
        # existing root folder and each create their own duplicate.
        # Double-checked locking: re-test the cache after acquiring the
        # lock, since another thread may have populated it while we waited.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            existing = self._find_child_by_name(creds, _ENTERPRISE_WS_ID, _APP_ROOT_NAME, _FOLDER_TYPE)
            if existing:
                self._root_id_cache[cache_key] = existing
                return existing
            created = self._multipart_request(
                creds, "/nodes",
                data={"parent_id": _ENTERPRISE_WS_ID, "type": str(_FOLDER_TYPE), "name": _APP_ROOT_NAME},
            )
            root_id = self._resolve_created_id(created)
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
            existing = self._find_child_by_name(creds, root, _TRASH_NAME, _FOLDER_TYPE)
            if existing:
                self._trash_id_cache[cache_key] = existing
                return existing
            created = self._multipart_request(
                creds, "/nodes",
                data={"parent_id": root, "type": str(_FOLDER_TYPE), "name": _TRASH_NAME},
            )
            trash_id = self._resolve_created_id(created)
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def _resolve_folder_id(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_id(creds)

    # --- folders ---

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id if folder_id is not None else root_id
        result = self._request(creds, "GET", f"/nodes/{node_id}/nodes", params={"limit": 1000})
        entries = self._extract_props_list(result)
        folders = [
            self._node_to_folder(p, root_id) for p in entries
            if p.get("type") == _FOLDER_TYPE and p.get("name") != _TRASH_NAME
        ]
        files = [self._node_to_file(p, root_id) for p in entries if p.get("type") == _DOCUMENT_TYPE]

        current_folder = None
        if folder_id is not None:
            node = self._request(creds, "GET", f"/nodes/{node_id}")
            current_folder = self._node_to_folder(self._extract_props(node), root_id)

        return FolderContents(
            folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
            folders=folders, files=files,
        )

    def list_trash(self, creds: dict) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = self._trash_id(creds)
        result = self._request(creds, "GET", f"/nodes/{node_id}/nodes", params={"limit": 1000})
        entries = self._extract_props_list(result)
        return FolderContents(
            folder=None,
            breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._node_to_folder(p, root_id) for p in entries if p.get("type") == _FOLDER_TYPE],
            files=[self._node_to_file(p, root_id) for p in entries if p.get("type") == _DOCUMENT_TYPE],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        node_id = self._resolve_folder_id(creds, parent_id)
        created = self._multipart_request(
            creds, "/nodes", data={"parent_id": node_id, "type": str(_FOLDER_TYPE), "name": name},
        )
        new_id = self._resolve_created_id(created)
        node = self._request(creds, "GET", f"/nodes/{new_id}")
        return self._node_to_folder(self._extract_props(node), self._root_id(creds))

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        self._request(creds, "PUT", f"/nodes/{folder_id}", json={"name": name})
        node = self._request(creds, "GET", f"/nodes/{folder_id}")
        return self._node_to_folder(self._extract_props(node), self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve_folder_id(creds, new_parent_id)
        self._request(creds, "PUT", f"/nodes/{folder_id}", json={"parent_id": target})
        node = self._request(creds, "GET", f"/nodes/{folder_id}")
        return self._node_to_folder(self._extract_props(node), self._root_id(creds))

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "DELETE", f"/nodes/{folder_id}")

    # --- files ---

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        node_id = self._resolve_folder_id(creds, folder_id)
        files = {"file": (name, content, content_type)}
        data = {"parent_id": node_id, "type": str(_DOCUMENT_TYPE), "name": name}
        created = self._multipart_request(creds, "/nodes", data=data, files=files)
        new_id = self._resolve_created_id(created)
        node = self._request(creds, "GET", f"/nodes/{new_id}")
        return self._node_to_file(self._extract_props(node), self._root_id(creds))

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        node = self._request(creds, "GET", f"/nodes/{file_id}")
        return self._node_to_file(self._extract_props(node), self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        self._request(creds, "PUT", f"/nodes/{file_id}", json={"name": name})
        return self.get_file(creds, file_id)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve_folder_id(creds, new_folder_id)
        self._request(creds, "PUT", f"/nodes/{file_id}", json={"parent_id": target})
        return self.get_file(creds, file_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "DELETE", f"/nodes/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._get_content(creds, f"/nodes/{file_id}/content")

    # --- versions ---

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._request(creds, "GET", f"/nodes/{file_id}/versions")
        entries = self._extract_props_list(result)
        node = self._extract_props(self._request(creds, "GET", f"/nodes/{file_id}"))
        current_number = node.get("version_number")

        out = []
        max_number = None
        for i, e in enumerate(entries):
            number = e.get("version_number")
            try:
                number = int(number) if number is not None else (i + 1)
            except (TypeError, ValueError):
                number = i + 1
            max_number = number if max_number is None else max(max_number, number)
            out.append(VersionInfo(
                id=str(e.get("id", number)),
                version_number=number,
                size_bytes=e.get("size"),
                content_type=e.get("mime_type"),
                is_current=False,
                updated_at=self._parse_otcs_dt(e.get("modify_date")),
            ))

        # Mark whichever entry matches the document's own current-version
        # number as current, falling back to the highest version number
        # seen if the node itself doesn't expose that field.
        try:
            target = int(current_number) if current_number is not None else max_number
        except (TypeError, ValueError):
            target = max_number
        for v in out:
            v.is_current = (v.version_number == target)
        return out

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        files = {"file": ("version", content, content_type)}
        self._multipart_request(creds, f"/nodes/{file_id}/versions", data={}, files=files)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        return self._get_content(creds, f"/nodes/{file_id}/versions/{version_id}/content")

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        # No confidently-known native "promote to current" endpoint (see
        # module docstring) — the always-correct fallback: fetch the old
        # version's bytes and re-upload them as a brand new current version.
        versions = self.list_versions(creds, file_id)
        target = next((v for v in versions if v.id == version_id), None)
        if target is None:
            raise ProviderError("Version not found", status_code=404)
        content = self.get_version_content(creds, file_id, version_id)
        content_type = target.content_type or "application/octet-stream"
        return self.create_version(creds, file_id, content_type, content)

    # --- trash (emulated — see module docstring) ---

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._request(creds, "PUT", f"/nodes/{folder_id}", json={"parent_id": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        self._request(creds, "PUT", f"/nodes/{folder_id}", json={"parent_id": root})
        node = self._request(creds, "GET", f"/nodes/{folder_id}")
        return self._node_to_folder(self._extract_props(node), root)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._request(creds, "PUT", f"/nodes/{file_id}", json={"parent_id": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        self._request(creds, "PUT", f"/nodes/{file_id}", json={"parent_id": root})
        node = self._request(creds, "GET", f"/nodes/{file_id}")
        return self._node_to_file(self._extract_props(node), root)

    # --- search ---

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        # Unlike Alfresco's ANCESTOR-scoped query, there's no independently
        # confirmed way to scope this endpoint to just this app's root
        # folder, so this searches everything the connected account can
        # see across the whole OTCS repository.
        root = self._root_id(creds)
        result = self._request(creds, "GET", "/search", params={"where": query})
        entries = self._extract_props_list(result)
        return (
            [self._node_to_folder(p, root) for p in entries if p.get("type") == _FOLDER_TYPE],
            [self._node_to_file(p, root) for p in entries if p.get("type") == _DOCUMENT_TYPE],
        )
