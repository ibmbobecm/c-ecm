"""LogicalDOC, via its documented WebService REST/JSON API (session-token
auth against a per-connection LogicalDOC server URL).

UNVERIFIED — no live LogicalDOC server in this environment to test
against. Written from LogicalDOC's documented REST API conventions
(`/services/rest/login/login` session auth, `/services/rest/folder/...`
and `/services/rest/document/...` endpoints, folder/document ids as
integers). LogicalDOC has real native versioning and a native trash
("wastebasket") concept; both used directly where the endpoint shape is
reasonably confidently known.
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

_APP_ROOT_NAME = "C-ECM"


class LogicalDOCProvider(StorageProvider):
    key = "logicaldoc"
    display_name = "LogicalDOC"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Server URL", "https://logicaldoc.example.com")]

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _login(self, creds: dict) -> str:
        resp = requests.get(f"{self._base_url(creds)}/services/rest/login/login",
                             params={"username": creds["username"], "password": creds["password"]}, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Invalid LogicalDOC credentials", status_code=401)
        sid = resp.text.strip().strip('"')
        if not sid or sid == "0":
            raise ProviderError("Invalid LogicalDOC credentials", status_code=401)
        return sid

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> requests.Response:
        sid = creds.get("_sid")
        if not sid:
            sid = self._login(creds)
            creds["_sid"] = sid
        params = kwargs.pop("params", {})
        params["sid"] = sid
        url = self._base_url(creds) + path
        try:
            resp = requests.request(method, url, params=params, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach LogicalDOC: {exc}", status_code=502)
        if resp.status_code == 401:
            sid = self._login(creds)
            creds["_sid"] = sid
            params["sid"] = sid
            resp = requests.request(method, url, params=params, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"LogicalDOC error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderError("Server URL is required", status_code=400)
        creds = {"username": username, "password": password, "base_url": base_url}
        try:
            self._login(creds)
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    def _resolve(self, creds: dict, folder_id: str | None) -> str:
        if folder_id is not None:
            return folder_id
        cache_key = self._base_url(creds) + creds["username"]
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            children = self._request(creds, "GET", "/services/rest/folder/list", params={"folderId": "4"}).json()
            existing = next((c for c in children if c.get("name") == _APP_ROOT_NAME), None)
            if existing:
                root_id = str(existing["id"])
            else:
                created = self._request(creds, "GET", "/services/rest/folder/create",
                                         params={"parentId": "4", "name": _APP_ROOT_NAME}).json()
                root_id = str(created["id"])
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _entry_to_folder(self, e: dict, parent_id) -> FolderInfo:
        return FolderInfo(id=str(e["id"]), name=e.get("name", ""), parent_id=parent_id)

    def _entry_to_file(self, e: dict, parent_id) -> FileInfo:
        return FileInfo(id=str(e["id"]), name=e.get("fileName", e.get("name", "")), folder_id=parent_id,
                         version_number=1, size_bytes=e.get("fileSize"), content_type=None)

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        node = self._resolve(creds, folder_id)
        folders_raw = self._request(creds, "GET", "/services/rest/folder/list", params={"folderId": node}).json()
        docs_raw = self._request(creds, "GET", "/services/rest/document/list", params={"folderId": node}).json()
        folders = [self._entry_to_folder(e, folder_id) for e in folders_raw]
        files = [self._entry_to_file(e, folder_id) for e in docs_raw]
        current_folder = FolderInfo(id=node, name="", parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        try:
            entries = self._request(creds, "GET", "/services/rest/document/wastebasket").json()
        except ProviderError:
            entries = []
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=[], files=[self._entry_to_file(e, None) for e in entries])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        created = self._request(creds, "GET", "/services/rest/folder/create",
                                 params={"parentId": parent, "name": name}).json()
        return self._entry_to_folder(created, parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        self._request(creds, "GET", "/services/rest/folder/rename", params={"folderId": folder_id, "name": name})
        return FolderInfo(id=folder_id, name=name, parent_id=None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        self._request(creds, "GET", "/services/rest/folder/move", params={"folderId": folder_id, "parentId": target})
        return FolderInfo(id=folder_id, name="", parent_id=new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "GET", "/services/rest/folder/delete", params={"folderId": folder_id})

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        resp = self._request(creds, "POST", "/services/rest/document/upload",
                              params={"folderId": parent, "filename": name},
                              files={"content": (name, content, content_type)})
        result = resp.json()
        return self._entry_to_file(result, folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        result = self._request(creds, "GET", "/services/rest/document/getDocument", params={"docId": file_id}).json()
        return self._entry_to_file(result, None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        self._request(creds, "GET", "/services/rest/document/rename", params={"docId": file_id, "name": name})
        return self.get_file(creds, file_id)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        self._request(creds, "GET", "/services/rest/document/move", params={"docId": file_id, "folderId": target})
        return self.get_file(creds, file_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "GET", "/services/rest/document/delete", params={"docId": file_id})

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._request(creds, "GET", "/services/rest/document/download", params={"docId": file_id}).content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        try:
            versions = self._request(creds, "GET", "/services/rest/document/listVersions",
                                      params={"docId": file_id}).json()
        except ProviderError:
            versions = []
        if not versions:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True)]
        return [
            VersionInfo(id=v.get("version", str(i)), version_number=i + 1, size_bytes=v.get("fileSize"),
                        content_type=None, is_current=(i == 0))
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        info = self.get_file(creds, file_id)
        self._request(creds, "POST", "/services/rest/document/checkin",
                       params={"docId": file_id, "filename": info.name},
                       files={"content": (info.name, content, content_type)})
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        return self._request(creds, "GET", "/services/rest/document/downloadVersion",
                              params={"docId": file_id, "version": version_id}).content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        info = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, info.content_type or "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "GET", "/services/rest/folder/delete", params={"folderId": folder_id})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        self._request(creds, "GET", "/services/rest/folder/restore", params={"folderId": folder_id})
        return FolderInfo(id=folder_id, name="", parent_id=None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "GET", "/services/rest/document/delete", params={"docId": file_id})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        self._request(creds, "GET", "/services/rest/document/restore", params={"docId": file_id})
        return self.get_file(creds, file_id)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        try:
            result = self._request(creds, "GET", "/services/rest/search/find",
                                    params={"expression": query}).json()
            return [], [self._entry_to_file(e, None) for e in result]
        except ProviderError:
            root = self._resolve(creds, None)
            found_folders: list[FolderInfo] = []
            found_files: list[FileInfo] = []
            q = query.lower()

            def walk(node_id, depth):
                if depth > 6:
                    return
                for e in self._request(creds, "GET", "/services/rest/folder/list", params={"folderId": node_id}).json():
                    if q in e.get("name", "").lower():
                        found_folders.append(self._entry_to_folder(e, node_id))
                    walk(str(e["id"]), depth + 1)
                for e in self._request(creds, "GET", "/services/rest/document/list", params={"folderId": node_id}).json():
                    if q in e.get("fileName", "").lower():
                        found_files.append(self._entry_to_file(e, node_id))

            walk(root, 0)
            return found_folders, found_files
