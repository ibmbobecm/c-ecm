"""Alfresco Content Services provider, via its REST API v1
(`/alfresco/api/-default-/public/alfresco/versions/1`).

UNVERIFIED — written against Alfresco's documented REST API contract (cross
-checked against the published OpenAPI spec and client SDK model
definitions), but there's no live Alfresco server in this environment to
test it against. Run it against a real instance before trusting it the way
FileNet's and local disk's providers are trusted (those were verified live,
this wasn't).

The server is per-connection (`config_fields` collects `base_url`), not a
single global — different connections can point at entirely different
Alfresco instances. Unlike FileNet, Alfresco addresses everything by node
id, not by path — so `folder_id=None` (FileDrive's "root") is resolved once
to a real node id (a dedicated "FileDrive" folder under Company Home,
created on first use) and cached *per base URL*, since this provider
instance is shared across every connection to it.
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

_APP_ROOT_NAME = "FileDrive"
_TRASH_NAME = "$Trash"


class AlfrescoProvider(StorageProvider):
    key = "alfresco"
    display_name = "Alfresco"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Server URL", "http://localhost:8080/alfresco")]

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _api(self, creds: dict) -> str:
        return self._base_url(creds) + "/api/-default-/public/alfresco/versions/1"

    def _search_api(self, creds: dict) -> str:
        return self._base_url(creds) + "/api/-default-/public/search/versions/1"

    def _headers(self, creds: dict) -> dict:
        token = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> dict:
        url = self._api(creds) + path
        try:
            resp = requests.request(method, url, headers=self._headers(creds), timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Alfresco: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("Invalid Alfresco credentials", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Alfresco error {resp.status_code}: {resp.text[:300]}", status_code=502)
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
            self._request(creds, "GET", "/people/-me-")
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    def _find_child_by_name(self, creds: dict, parent_id: str, name: str, is_folder: bool) -> str | None:
        result = self._request(
            creds, "GET", f"/nodes/{parent_id}/children",
            params={"where": f"(name='{name}' AND isFolder={'true' if is_folder else 'false'})"},
        )
        entries = result.get("list", {}).get("entries", [])
        return entries[0]["entry"]["id"] if entries else None

    def _root_id(self, creds: dict) -> str:
        cache_key = self._base_url(creds)
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        # This provider instance is a process-wide singleton shared by
        # every connection to the same Alfresco server, and FastAPI runs
        # sync handlers in a real thread pool — without a lock, concurrent
        # first-requests for a freshly connected server would each find no
        # existing root folder and each create their own duplicate.
        # Double-checked locking: re-test the cache after acquiring the
        # lock, since another thread may have populated it while we waited.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            existing = self._find_child_by_name(creds, "-root-", _APP_ROOT_NAME, is_folder=True)
            if existing:
                self._root_id_cache[cache_key] = existing
                return existing
            created = self._request(
                creds, "POST", "/nodes/-root-/children",
                json={"name": _APP_ROOT_NAME, "nodeType": "cm:folder"},
            )
            root_id = created["entry"]["id"]
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
            existing = self._find_child_by_name(creds, root, _TRASH_NAME, is_folder=True)
            if existing:
                self._trash_id_cache[cache_key] = existing
                return existing
            created = self._request(
                creds, "POST", f"/nodes/{root}/children",
                json={"name": _TRASH_NAME, "nodeType": "cm:folder"},
            )
            trash_id = created["entry"]["id"]
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def _resolve_folder_id(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_id(creds)

    def _node_to_folder(self, entry: dict, root_id: str) -> FolderInfo:
        parent_id = entry.get("parentId")
        if parent_id == root_id:
            parent_id = None
        return FolderInfo(id=entry["id"], name=entry["name"], parent_id=parent_id, created_at=None)

    def _node_to_file(self, entry: dict, root_id: str) -> FileInfo:
        content = entry.get("content") or {}
        parent_id = entry.get("parentId")
        if parent_id == root_id:
            parent_id = None
        return FileInfo(
            id=entry["id"],
            name=entry["name"],
            folder_id=parent_id,
            version_number=1,
            size_bytes=content.get("sizeInBytes"),
            content_type=content.get("mimeType"),
            updated_at=None,
        )

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id if folder_id is not None else root_id
        result = self._request(creds, "GET", f"/nodes/{node_id}/children", params={"maxItems": 1000})
        entries = [e["entry"] for e in result.get("list", {}).get("entries", [])]
        folders = [self._node_to_folder(e, root_id) for e in entries if e.get("isFolder") and e["name"] != _TRASH_NAME]
        files = [self._node_to_file(e, root_id) for e in entries if e.get("isFile")]

        current_folder = None
        if folder_id is not None:
            node = self._request(creds, "GET", f"/nodes/{node_id}")["entry"]
            current_folder = self._node_to_folder(node, root_id)

        return FolderContents(
            folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
            folders=folders, files=files,
        )

    def list_trash(self, creds: dict) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = self._trash_id(creds)
        result = self._request(creds, "GET", f"/nodes/{node_id}/children", params={"maxItems": 1000})
        entries = [e["entry"] for e in result.get("list", {}).get("entries", [])]
        return FolderContents(
            folder=None,
            breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._node_to_folder(e, root_id) for e in entries if e.get("isFolder")],
            files=[self._node_to_file(e, root_id) for e in entries if e.get("isFile")],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        node_id = self._resolve_folder_id(creds, parent_id)
        created = self._request(
            creds, "POST", f"/nodes/{node_id}/children", json={"name": name, "nodeType": "cm:folder"}
        )
        return self._node_to_folder(created["entry"], self._root_id(creds))

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._request(creds, "PUT", f"/nodes/{folder_id}", json={"name": name})
        return self._node_to_folder(updated["entry"], self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve_folder_id(creds, new_parent_id)
        updated = self._request(creds, "POST", f"/nodes/{folder_id}/move", json={"targetParentId": target})
        return self._node_to_folder(updated["entry"], self._root_id(creds))

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "DELETE", f"/nodes/{folder_id}", params={"permanent": "true"})

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        node_id = self._resolve_folder_id(creds, folder_id)
        url = self._api(creds) + f"/nodes/{node_id}/children"
        files = {"filedata": (name, content, content_type)}
        data = {"name": name, "aspectNames": "cm:versionable"}
        try:
            resp = requests.post(url, headers=self._headers(creds), files=files, data=data, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Alfresco: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"Alfresco upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self._node_to_file(resp.json()["entry"], self._root_id(creds))

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        node = self._request(creds, "GET", f"/nodes/{file_id}")["entry"]
        return self._node_to_file(node, self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._request(creds, "PUT", f"/nodes/{file_id}", json={"name": name})
        return self._node_to_file(updated["entry"], self._root_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve_folder_id(creds, new_folder_id)
        updated = self._request(creds, "POST", f"/nodes/{file_id}/move", json={"targetParentId": target})
        return self._node_to_file(updated["entry"], self._root_id(creds))

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "DELETE", f"/nodes/{file_id}", params={"permanent": "true"})

    def get_content(self, creds: dict, file_id: str) -> bytes:
        url = self._api(creds) + f"/nodes/{file_id}/content"
        resp = requests.get(url, headers=self._headers(creds), timeout=60)
        if resp.status_code >= 400:
            raise ProviderError("Couldn't fetch content", status_code=404)
        return resp.content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._request(creds, "GET", f"/nodes/{file_id}/versions")
        entries = [e["entry"] for e in result.get("list", {}).get("entries", [])]
        out = []
        for i, e in enumerate(entries):
            content = e.get("content") or {}
            # Alfresco labels versions "1.0", "2.0", "2.1" — VersionInfo.version_number
            # is a plain int everywhere else, so this keeps the major number and
            # drops minor (the label itself, e["id"], is still the real identifier).
            try:
                version_number = int(float(e["id"]))
            except (TypeError, ValueError):
                version_number = i + 1
            out.append(VersionInfo(
                id=e["id"],
                version_number=version_number,
                size_bytes=content.get("sizeInBytes"),
                content_type=content.get("mimeType"),
                is_current=(i == 0),  # most-recent-first, per Alfresco's documented ordering
                updated_at=self._parse_alfresco_dt(e.get("modifiedAt")),
            ))
        return out

    @staticmethod
    def _parse_alfresco_dt(value: str | None):
        if not value:
            return None
        import datetime
        try:
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        url = self._api(creds) + f"/nodes/{file_id}/content"
        try:
            resp = requests.put(
                url, headers={**self._headers(creds), "Content-Type": content_type},
                params={"majorVersion": "true"}, data=content, timeout=60,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Alfresco: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"Alfresco version upload failed ({resp.status_code})", status_code=502)
        return self._node_to_file(resp.json()["entry"], self._root_id(creds))

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        url = self._api(creds) + f"/nodes/{file_id}/versions/{version_id}/content"
        resp = requests.get(url, headers=self._headers(creds), timeout=60)
        if resp.status_code >= 400:
            raise ProviderError("Version content not found", status_code=404)
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        self._request(creds, "POST", f"/nodes/{file_id}/versions/{version_id}/revert", json={"majorVersion": True})
        return self.get_file(creds, file_id)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._request(creds, "POST", f"/nodes/{folder_id}/move", json={"targetParentId": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        updated = self._request(creds, "POST", f"/nodes/{folder_id}/move", json={"targetParentId": root})
        return self._node_to_folder(updated["entry"], root)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._request(creds, "POST", f"/nodes/{file_id}/move", json={"targetParentId": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        updated = self._request(creds, "POST", f"/nodes/{file_id}/move", json={"targetParentId": root})
        return self._node_to_file(updated["entry"], root)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root = self._root_id(creds)
        body = {
            "query": {"query": f"ANCESTOR:'workspace://SpacesStore/{root}' AND cm:name:*{query}*"},
            "paging": {"maxItems": 100},
        }
        url = self._search_api(creds) + "/search"
        try:
            resp = requests.post(url, headers=self._headers(creds), json=body, timeout=30)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Alfresco: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"Alfresco search failed ({resp.status_code})", status_code=502)
        entries = [e["entry"] for e in resp.json().get("list", {}).get("entries", [])]
        return (
            [self._node_to_folder(e, root) for e in entries if e.get("isFolder")],
            [self._node_to_file(e, root) for e in entries if e.get("isFile")],
        )
