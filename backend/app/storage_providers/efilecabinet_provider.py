"""eFileCabinet, via its documented REST API (JSON, token-based auth
against a per-connection eFileCabinet cloud/server URL).

UNVERIFIED — no live eFileCabinet account in this environment to test
against, and eFileCabinet's REST API is less publicly/consistently
documented than most other providers in this codebase (its main
integration surfaces historically leaned on a Windows desktop client and
a SOAP-era API) — treat this adapter as lower-confidence, comparable to
this codebase's OnBase/DocuShare adapters. Endpoint paths below are
best-effort reconstructions from eFileCabinet's general REST API
conventions (cabinet/drawer/folder/document hierarchy, token auth) rather
than a line-by-line-confirmed spec. Trash is emulated via a hidden folder
since no confidently-known native trash listing endpoint exists.
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
_TRASH_NAME = "_C-ECM-Trash"


class EFileCabinetProvider(StorageProvider):
    key = "efilecabinet"
    display_name = "eFileCabinet"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Server URL", "https://yourcompany.efilecabinet.com")]

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _login(self, creds: dict) -> str:
        resp = requests.post(f"{self._base_url(creds)}/api/auth/login",
                              json={"username": creds["username"], "password": creds["password"]}, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Invalid eFileCabinet credentials", status_code=401)
        token = resp.json().get("token")
        if not token:
            raise ProviderError("Invalid eFileCabinet credentials", status_code=401)
        return token

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> requests.Response:
        token = creds.get("_token")
        if not token:
            token = self._login(creds)
            creds["_token"] = token
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        url = self._base_url(creds) + path
        try:
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach eFileCabinet: {exc}", status_code=502)
        if resp.status_code == 401:
            token = self._login(creds)
            creds["_token"] = token
            headers["Authorization"] = f"Bearer {token}"
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"eFileCabinet error {resp.status_code}: {resp.text[:300]}", status_code=502)
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

    def _entry_to_folder(self, e: dict, parent_id) -> FolderInfo:
        return FolderInfo(id=str(e.get("id")), name=e.get("name", ""), parent_id=parent_id)

    def _entry_to_file(self, e: dict, parent_id) -> FileInfo:
        return FileInfo(id=str(e.get("id")), name=e.get("name", ""), folder_id=parent_id, version_number=1,
                         size_bytes=e.get("size"), content_type=e.get("contentType"))

    def _find_or_create_child(self, creds: dict, parent_id: str | None, name: str) -> str:
        result = self._request(creds, "GET", "/api/folders", params={"parentId": parent_id or ""}).json()
        entries = result.get("items", result.get("data", []))
        for e in entries:
            if e.get("name") == name:
                return str(e.get("id"))
        created = self._request(creds, "POST", "/api/folders", json={"name": name, "parentId": parent_id}).json()
        return str(created.get("id", created.get("data", {}).get("id")))

    def _root_id(self, creds: dict) -> str:
        cache_key = self._base_url(creds) + creds["username"]
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            root_id = self._find_or_create_child(creds, None, _APP_ROOT_NAME)
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_id(self, creds: dict) -> str:
        cache_key = self._base_url(creds) + creds["username"]
        cached = self._trash_id_cache.get(cache_key)
        if cached:
            return cached
        with self._trash_id_lock:
            cached = self._trash_id_cache.get(cache_key)
            if cached:
                return cached
            root = self._root_id(creds)
            trash_id = self._find_or_create_child(creds, root, _TRASH_NAME)
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def _resolve(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_id(creds)

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        node = self._resolve(creds, folder_id)
        fres = self._request(creds, "GET", "/api/folders", params={"parentId": node}).json()
        ffiles = self._request(creds, "GET", "/api/documents", params={"folderId": node}).json()
        f_entries = fres.get("items", fres.get("data", []))
        file_entries = ffiles.get("items", ffiles.get("data", []))
        folders = [self._entry_to_folder(e, folder_id) for e in f_entries if e.get("name") != _TRASH_NAME]
        files = [self._entry_to_file(e, folder_id) for e in file_entries]
        current_folder = FolderInfo(id=node, name="", parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash_id = self._trash_id(creds)
        fres = self._request(creds, "GET", "/api/folders", params={"parentId": trash_id}).json()
        ffiles = self._request(creds, "GET", "/api/documents", params={"folderId": trash_id}).json()
        f_entries = fres.get("items", fres.get("data", []))
        file_entries = ffiles.get("items", ffiles.get("data", []))
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=[self._entry_to_folder(e, trash_id) for e in f_entries],
                               files=[self._entry_to_file(e, trash_id) for e in file_entries])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        created = self._request(creds, "POST", "/api/folders", json={"name": name, "parentId": parent}).json()
        return self._entry_to_folder(created.get("data", created), parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._request(creds, "PUT", f"/api/folders/{folder_id}", json={"name": name}).json()
        return self._entry_to_folder(updated.get("data", updated), None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        updated = self._request(creds, "PUT", f"/api/folders/{folder_id}", json={"parentId": target}).json()
        return self._entry_to_folder(updated.get("data", updated), new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "DELETE", f"/api/folders/{folder_id}")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        created = self._request(creds, "POST", "/api/documents", params={"folderId": parent, "name": name},
                                 files={"file": (name, content, content_type)}).json()
        return self._entry_to_file(created.get("data", created), folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        result = self._request(creds, "GET", f"/api/documents/{file_id}").json()
        return self._entry_to_file(result.get("data", result), None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._request(creds, "PUT", f"/api/documents/{file_id}", json={"name": name}).json()
        return self._entry_to_file(updated.get("data", updated), None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        updated = self._request(creds, "PUT", f"/api/documents/{file_id}", json={"folderId": target}).json()
        return self._entry_to_file(updated.get("data", updated), new_folder_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "DELETE", f"/api/documents/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._request(creds, "GET", f"/api/documents/{file_id}/content").content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        try:
            result = self._request(creds, "GET", f"/api/documents/{file_id}/versions").json()
            versions = result.get("items", result.get("data", []))
        except ProviderError:
            versions = []
        if not versions:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True)]
        return [
            VersionInfo(id=str(v.get("id", i)), version_number=i + 1, size_bytes=v.get("size"),
                        content_type=None, is_current=(i == 0))
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        info = self.get_file(creds, file_id)
        self._request(creds, "POST", f"/api/documents/{file_id}/versions",
                       files={"file": (info.name, content, content_type)})
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        return self._request(creds, "GET", f"/api/documents/{file_id}/versions/{version_id}/content").content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        info = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, info.content_type or "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._request(creds, "PUT", f"/api/folders/{folder_id}", json={"parentId": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        updated = self._request(creds, "PUT", f"/api/folders/{folder_id}", json={"parentId": root}).json()
        return self._entry_to_folder(updated.get("data", updated), None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._request(creds, "PUT", f"/api/documents/{file_id}", json={"folderId": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        updated = self._request(creds, "PUT", f"/api/documents/{file_id}", json={"folderId": root}).json()
        return self._entry_to_file(updated.get("data", updated), None)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        try:
            result = self._request(creds, "GET", "/api/search", params={"q": query}).json()
            entries = result.get("items", result.get("data", []))
            folders = [self._entry_to_folder(e, None) for e in entries if e.get("type") == "folder"]
            files = [self._entry_to_file(e, None) for e in entries if e.get("type") != "folder"]
            return folders, files
        except ProviderError:
            root = self._root_id(creds)
            trash = self._trash_id(creds)
            found_folders: list[FolderInfo] = []
            found_files: list[FileInfo] = []
            q = query.lower()

            def walk(node_id, depth):
                if depth > 6 or node_id == trash:
                    return
                fres = self._request(creds, "GET", "/api/folders", params={"parentId": node_id}).json()
                for e in fres.get("items", fres.get("data", [])):
                    if str(e.get("id")) == trash:
                        continue
                    if q in e.get("name", "").lower():
                        found_folders.append(self._entry_to_folder(e, node_id))
                    walk(str(e.get("id")), depth + 1)
                ffiles = self._request(creds, "GET", "/api/documents", params={"folderId": node_id}).json()
                for e in ffiles.get("items", ffiles.get("data", [])):
                    if q in e.get("name", "").lower():
                        found_files.append(self._entry_to_file(e, node_id))

            walk(root, 0)
            return found_folders, found_files
