"""Seafile, via its documented Web API v2.1 (JSON, token auth against a
per-connection Seafile server URL).

UNVERIFIED — no live Seafile server in this environment to test against.
Written from Seafile's documented Web API conventions (`/api2/auth-token/`
login, per-library `repo-id` + path-based addressing within a library).
Seafile libraries are picked up as this connection's storage root (first
library returned, or created if none exist); Seafile has real native
trash and snapshot/history for files, used directly.
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


class SeafileProvider(StorageProvider):
    key = "seafile"
    display_name = "Seafile"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Server URL", "https://seafile.example.com")]

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _headers(self, creds: dict) -> dict:
        return {"Authorization": f"Token {creds['token']}"}

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> requests.Response:
        url = self._base_url(creds) + path
        try:
            resp = requests.request(method, url, headers=self._headers(creds), timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Seafile: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("Invalid Seafile credentials", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Seafile error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderError("Server URL is required", status_code=400)
        try:
            resp = requests.post(f"{base_url.rstrip('/')}/api2/auth-token/",
                                  data={"username": username, "password": password}, timeout=30)
        except requests.RequestException:
            return None
        if resp.status_code >= 400:
            return None
        token = resp.json().get("token")
        if not token:
            return None
        return {"username": username, "base_url": base_url, "token": token}

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    def _repo_id(self, creds: dict) -> str:
        cache_key = self._base_url(creds) + creds["username"]
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            repos = self._request(creds, "GET", "/api2/repos/").json()
            if repos:
                repo_id = repos[0]["id"]
            else:
                created = self._request(creds, "POST", "/api2/repos/",
                                         data={"name": _APP_ROOT_NAME}).json()
                repo_id = created["repo_id"]
            self._root_id_cache[cache_key] = repo_id
            return repo_id

    @staticmethod
    def _mkid(repo_id: str, path: str) -> str:
        return f"{repo_id}:{path}"

    @staticmethod
    def _split(node_id: str) -> tuple[str, str]:
        repo_id, path = node_id.split(":", 1)
        return repo_id, path

    def _resolve(self, creds: dict, folder_id: str | None) -> str:
        if folder_id is not None:
            return folder_id
        return self._mkid(self._repo_id(creds), "/")

    @staticmethod
    def _name_from_path(path: str) -> str:
        return path.rstrip("/").rsplit("/", 1)[-1] or "/"

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        node = self._resolve(creds, folder_id)
        repo_id, path = self._split(node)
        entries = self._request(creds, "GET", f"/api2/repos/{repo_id}/dir/", params={"p": path}).json()
        folders = [FolderInfo(id=self._mkid(repo_id, f"{path.rstrip('/')}/{e['name']}"), name=e["name"], parent_id=folder_id)
                   for e in entries if e.get("type") == "dir"]
        files = [FileInfo(id=self._mkid(repo_id, f"{path.rstrip('/')}/{e['name']}"), name=e["name"], folder_id=folder_id,
                           version_number=1, size_bytes=e.get("size"), content_type=None)
                 for e in entries if e.get("type") == "file"]
        current_folder = FolderInfo(id=node, name=self._name_from_path(path), parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        repo_id = self._repo_id(creds)
        try:
            entries = self._request(creds, "GET", f"/api2/repos/{repo_id}/deleted/").json()
        except ProviderError:
            entries = []
        folders, files = [], []
        for e in entries:
            path = e.get("path", "/")
            if e.get("is_dir"):
                folders.append(FolderInfo(id=self._mkid(repo_id, path), name=self._name_from_path(path), parent_id=None))
            else:
                files.append(FileInfo(id=self._mkid(repo_id, path), name=self._name_from_path(path), folder_id=None,
                                       version_number=1, size_bytes=e.get("size"), content_type=None))
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=folders, files=files)

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        repo_id, path = self._split(parent)
        new_path = f"{path.rstrip('/')}/{name}"
        self._request(creds, "POST", f"/api2/repos/{repo_id}/dir/", params={"p": new_path},
                       data={"operation": "mkdir"})
        return FolderInfo(id=self._mkid(repo_id, new_path), name=name, parent_id=parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        repo_id, path = self._split(folder_id)
        self._request(creds, "POST", f"/api2/repos/{repo_id}/dir/", params={"p": path},
                       data={"operation": "rename", "newname": name})
        parent = path.rstrip("/").rsplit("/", 1)[0] or "/"
        new_path = f"{parent.rstrip('/')}/{name}"
        return FolderInfo(id=self._mkid(repo_id, new_path), name=name, parent_id=None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        repo_id, path = self._split(folder_id)
        target = self._resolve(creds, new_parent_id)
        target_repo, target_path = self._split(target)
        name = self._name_from_path(path)
        self._request(creds, "POST", f"/api2/repos/{repo_id}/dir/", params={"p": path},
                       data={"operation": "move", "dst_repo": target_repo, "dst_dir": target_path})
        new_path = f"{target_path.rstrip('/')}/{name}"
        return FolderInfo(id=self._mkid(target_repo, new_path), name=name, parent_id=new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        repo_id, path = self._split(folder_id)
        self._request(creds, "DELETE", f"/api2/repos/{repo_id}/dir/", params={"p": path})

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        repo_id, path = self._split(parent)
        link = self._request(creds, "GET", f"/api2/repos/{repo_id}/upload-link/", params={"p": path}).json()
        resp = requests.post(link, headers=self._headers(creds),
                              data={"parent_dir": path}, files={"file": (name, content, content_type)}, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError(f"Seafile upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        new_path = f"{path.rstrip('/')}/{name}"
        return FileInfo(id=self._mkid(repo_id, new_path), name=name, folder_id=folder_id, version_number=1,
                         size_bytes=len(content), content_type=content_type)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        repo_id, path = self._split(file_id)
        detail = self._request(creds, "GET", f"/api2/repos/{repo_id}/file/detail/", params={"p": path}).json()
        return FileInfo(id=file_id, name=self._name_from_path(path), folder_id=None, version_number=1,
                         size_bytes=detail.get("size"), content_type=None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        repo_id, path = self._split(file_id)
        self._request(creds, "POST", f"/api2/repos/{repo_id}/file/", params={"p": path},
                       data={"operation": "rename", "newname": name})
        parent = path.rstrip("/").rsplit("/", 1)[0] or "/"
        new_path = f"{parent.rstrip('/')}/{name}"
        return FileInfo(id=self._mkid(repo_id, new_path), name=name, folder_id=None, version_number=1,
                         size_bytes=None, content_type=None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        repo_id, path = self._split(file_id)
        target = self._resolve(creds, new_folder_id)
        target_repo, target_path = self._split(target)
        name = self._name_from_path(path)
        self._request(creds, "POST", f"/api2/repos/{repo_id}/file/", params={"p": path},
                       data={"operation": "move", "dst_repo": target_repo, "dst_dir": target_path})
        new_path = f"{target_path.rstrip('/')}/{name}"
        return FileInfo(id=self._mkid(target_repo, new_path), name=name, folder_id=new_folder_id, version_number=1,
                         size_bytes=None, content_type=None)

    def delete_file(self, creds: dict, file_id: str) -> None:
        repo_id, path = self._split(file_id)
        self._request(creds, "DELETE", f"/api2/repos/{repo_id}/file/", params={"p": path})

    def get_content(self, creds: dict, file_id: str) -> bytes:
        repo_id, path = self._split(file_id)
        link_resp = self._request(creds, "GET", f"/api2/repos/{repo_id}/file/", params={"p": path})
        url = link_resp.json()
        resp = requests.get(url, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError("File not found", status_code=404)
        return resp.content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        repo_id, path = self._split(file_id)
        try:
            history = self._request(creds, "GET", f"/api2/repos/{repo_id}/file/history/", params={"p": path}).json()
            commits = history.get("commits", [])
        except ProviderError:
            commits = []
        if not commits:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=None, is_current=True)]
        return [
            VersionInfo(id=c.get("rev_file_id", c.get("id", str(i))), version_number=i + 1,
                        size_bytes=c.get("size"), content_type=None, is_current=(i == 0))
            for i, c in enumerate(commits)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        repo_id, path = self._split(file_id)
        parent = path.rstrip("/").rsplit("/", 1)[0] or "/"
        name = self._name_from_path(path)
        link = self._request(creds, "GET", f"/api2/repos/{repo_id}/update-link/", params={"p": parent}).json()
        resp = requests.post(link, headers=self._headers(creds),
                              data={"target_file": path}, files={"file": (name, content, content_type)}, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError(f"Seafile version upload failed ({resp.status_code})", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        repo_id, path = self._split(file_id)
        link_resp = self._request(creds, "GET", f"/api2/repos/{repo_id}/file/revision/",
                                   params={"p": path, "commit_id": version_id})
        url = link_resp.json()
        resp = requests.get(url, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError("Version content not found", status_code=404)
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        return self.create_version(creds, file_id, "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self.delete_folder(creds, folder_id)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        repo_id, path = self._split(folder_id)
        self._request(creds, "POST", f"/api2/repos/{repo_id}/dir/", params={"p": path},
                       data={"operation": "revert"})
        return FolderInfo(id=folder_id, name=self._name_from_path(path), parent_id=None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        self.delete_file(creds, file_id)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        repo_id, path = self._split(file_id)
        self._request(creds, "POST", f"/api2/repos/{repo_id}/file/", params={"p": path},
                       data={"operation": "revert"})
        return self.get_file(creds, file_id)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        repo_id = self._repo_id(creds)
        try:
            result = self._request(creds, "GET", "/api2/search/", params={"q": query, "search_repo": repo_id}).json()
            entries = result.get("results", [])
            folders, files = [], []
            for e in entries:
                path = e.get("fullpath", "/")
                if e.get("is_dir"):
                    folders.append(FolderInfo(id=self._mkid(repo_id, path), name=self._name_from_path(path), parent_id=None))
                else:
                    files.append(FileInfo(id=self._mkid(repo_id, path), name=self._name_from_path(path), folder_id=None,
                                           version_number=1, size_bytes=e.get("size"), content_type=None))
            return folders, files
        except ProviderError:
            found_folders: list[FolderInfo] = []
            found_files: list[FileInfo] = []
            q = query.lower()

            def walk(path, depth):
                if depth > 6:
                    return
                entries = self._request(creds, "GET", f"/api2/repos/{repo_id}/dir/", params={"p": path}).json()
                for e in entries:
                    name = e.get("name", "")
                    child = f"{path.rstrip('/')}/{name}"
                    if e.get("type") == "dir":
                        if q in name.lower():
                            found_folders.append(FolderInfo(id=self._mkid(repo_id, child), name=name, parent_id=None))
                        walk(child, depth + 1)
                    elif q in name.lower():
                        found_files.append(FileInfo(id=self._mkid(repo_id, child), name=name, folder_id=None,
                                                     version_number=1, size_bytes=e.get("size"), content_type=None))

            walk("/", 0)
            return found_folders, found_files
