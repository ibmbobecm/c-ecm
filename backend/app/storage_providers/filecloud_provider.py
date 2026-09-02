"""FileCloud, via its documented REST API (JSON, session-token auth
against a per-connection FileCloud server URL).

UNVERIFIED — no live FileCloud server in this environment to test
against. Written from FileCloud's documented REST API conventions
(`/core/loginguest` login, `/core/getfilelist` browsing, path-based
addressing under a `/user-username/` root). FileCloud's native trash
("Deleted Items") is used directly where the endpoint shape is
reasonably confidently known; version history uses FileCloud's real
native versioning.
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


class FileCloudProvider(StorageProvider):
    key = "filecloud"
    display_name = "FileCloud"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Server URL", "https://filecloud.example.com")]

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _login(self, creds: dict) -> str:
        resp = requests.post(f"{self._base_url(creds)}/core/loginguest", data={
            "userid": creds["username"], "password": creds["password"],
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Invalid FileCloud credentials", status_code=401)
        result = resp.json()
        token = result.get("token")
        if not token:
            raise ProviderError("Invalid FileCloud credentials", status_code=401)
        return token

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> requests.Response:
        token = creds.get("_token")
        if not token:
            token = self._login(creds)
            creds["_token"] = token
        url = self._base_url(creds) + path
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        try:
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach FileCloud: {exc}", status_code=502)
        if resp.status_code == 401:
            token = self._login(creds)
            creds["_token"] = token
            headers["Authorization"] = f"Bearer {token}"
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"FileCloud error {resp.status_code}: {resp.text[:300]}", status_code=502)
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

    def _user_root(self, creds: dict) -> str:
        return f"/{creds['username']}"

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
            user_root = self._user_root(creds)
            existing = self._list(creds, user_root)
            if not any(e.get("name") == _APP_ROOT_NAME and e.get("isfolder") for e in existing):
                self._request(creds, "POST", "/core/createfolder",
                               data={"path": user_root, "name": _APP_ROOT_NAME})
            root = f"{user_root}/{_APP_ROOT_NAME}"
            self._root_id_cache[cache_key] = root
            return root

    def _list(self, creds: dict, path: str) -> list[dict]:
        result = self._request(creds, "GET", "/core/getfilelist", params={"path": path}).json()
        return result.get("list", {}).get("file", []) if isinstance(result.get("list"), dict) else result.get("files", [])

    @staticmethod
    def _name_from_path(path: str) -> str:
        return path.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _parent_of(path: str) -> str:
        return path.rstrip("/").rsplit("/", 1)[0] or "/"

    def _entry_to_folder(self, e: dict, parent_id: str) -> FolderInfo:
        return FolderInfo(id=f"{parent_id}/{e['name']}", name=e["name"], parent_id=parent_id)

    def _entry_to_file(self, e: dict, parent_id: str) -> FileInfo:
        return FileInfo(id=f"{parent_id}/{e['name']}", name=e["name"], folder_id=parent_id, version_number=1,
                         size_bytes=e.get("size"), content_type=e.get("mime"))

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        node = self._resolve(creds, folder_id)
        entries = self._list(creds, node)
        folders = [self._entry_to_folder(e, node) for e in entries if e.get("isfolder") in (True, "true", "1", 1)]
        files = [self._entry_to_file(e, node) for e in entries if not e.get("isfolder") in (True, "true", "1", 1)]
        current_folder = FolderInfo(id=node, name=self._name_from_path(node), parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        try:
            result = self._request(creds, "GET", "/core/deletedfilelist").json()
            entries = result.get("list", {}).get("file", []) if isinstance(result.get("list"), dict) else result.get("files", [])
        except ProviderError:
            entries = []
        folders, files = [], []
        for e in entries:
            path = e.get("path", "/")
            if e.get("isfolder") in (True, "true", "1", 1):
                folders.append(FolderInfo(id=path, name=e.get("name", self._name_from_path(path)), parent_id=None))
            else:
                files.append(FileInfo(id=path, name=e.get("name", self._name_from_path(path)), folder_id=None,
                                       version_number=1, size_bytes=e.get("size"), content_type=e.get("mime")))
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=folders, files=files)

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        self._request(creds, "POST", "/core/createfolder", data={"path": parent, "name": name})
        return FolderInfo(id=f"{parent}/{name}", name=name, parent_id=parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        parent = self._parent_of(folder_id)
        self._request(creds, "POST", "/core/renamefile", data={"path": folder_id, "name": name})
        return FolderInfo(id=f"{parent}/{name}", name=name, parent_id=None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        name = self._name_from_path(folder_id)
        self._request(creds, "POST", "/core/moveto", data={"srcpath": folder_id, "destpath": target})
        return FolderInfo(id=f"{target}/{name}", name=name, parent_id=new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "POST", "/core/deletefile", data={"path": folder_id})

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        self._request(creds, "POST", "/core/upload", params={"path": parent},
                       files={"file": (name, content, content_type)})
        return FileInfo(id=f"{parent}/{name}", name=name, folder_id=folder_id, version_number=1,
                         size_bytes=len(content), content_type=content_type)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        entries = self._list(creds, self._parent_of(file_id))
        name = self._name_from_path(file_id)
        match = next((e for e in entries if e.get("name") == name), None)
        return self._entry_to_file(match or {"name": name}, self._parent_of(file_id))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        parent = self._parent_of(file_id)
        self._request(creds, "POST", "/core/renamefile", data={"path": file_id, "name": name})
        return FileInfo(id=f"{parent}/{name}", name=name, folder_id=None, version_number=1,
                         size_bytes=None, content_type=None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        name = self._name_from_path(file_id)
        self._request(creds, "POST", "/core/moveto", data={"srcpath": file_id, "destpath": target})
        return FileInfo(id=f"{target}/{name}", name=name, folder_id=new_folder_id, version_number=1,
                         size_bytes=None, content_type=None)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "POST", "/core/deletefile", data={"path": file_id})

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._request(creds, "GET", "/core/downloadfile", params={"path": file_id}).content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        try:
            result = self._request(creds, "GET", "/core/versionhistory", params={"path": file_id}).json()
            versions = result.get("versions", [])
        except ProviderError:
            versions = []
        if not versions:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True)]
        return [
            VersionInfo(id=v.get("versionid", str(i)), version_number=i + 1, size_bytes=v.get("size"),
                        content_type=None, is_current=(i == 0))
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._parent_of(file_id)
        self._request(creds, "POST", "/core/upload", params={"path": parent, "overwrite": "1"},
                       files={"file": (self._name_from_path(file_id), content, content_type)})
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        return self._request(creds, "GET", "/core/downloadversion",
                              params={"path": file_id, "versionid": version_id}).content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        info = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, info.content_type or "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "POST", "/core/deletefile", data={"path": folder_id})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        self._request(creds, "POST", "/core/restore", data={"path": folder_id})
        return FolderInfo(id=folder_id, name=self._name_from_path(folder_id), parent_id=None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "POST", "/core/deletefile", data={"path": file_id})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        self._request(creds, "POST", "/core/restore", data={"path": file_id})
        return self.get_file(creds, file_id)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root = self._resolve(creds, None)
        try:
            result = self._request(creds, "GET", "/core/search", params={"query": query, "path": root}).json()
            entries = result.get("list", {}).get("file", []) if isinstance(result.get("list"), dict) else result.get("files", [])
            folders, files = [], []
            for e in entries:
                parent = self._parent_of(e.get("path", root + "/" + e.get("name", "")))
                if e.get("isfolder") in (True, "true", "1", 1):
                    folders.append(self._entry_to_folder(e, parent))
                else:
                    files.append(self._entry_to_file(e, parent))
            return folders, files
        except ProviderError:
            found_folders: list[FolderInfo] = []
            found_files: list[FileInfo] = []
            q = query.lower()

            def walk(path, depth):
                if depth > 6:
                    return
                for e in self._list(creds, path):
                    name = e.get("name", "")
                    if e.get("isfolder") in (True, "true", "1", 1):
                        if q in name.lower():
                            found_folders.append(self._entry_to_folder(e, path))
                        walk(f"{path}/{name}", depth + 1)
                    elif q in name.lower():
                        found_files.append(self._entry_to_file(e, path))

            walk(root, 0)
            return found_folders, found_files
