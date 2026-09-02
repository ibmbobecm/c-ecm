"""Adobe Experience Manager Assets, via its documented Assets HTTP API
(a REST-ish layer over AEM's underlying JCR repository, JSON responses,
HTTP Basic auth against a per-connection AEM instance URL).

UNVERIFIED — no live AEM instance in this environment to test against.
Written from AEM's documented Assets HTTP API conventions (`.json`/
`.assets.json` selectors, `/api/assets` REST endpoints). AEM addresses
content by real JCR path (like FileNet/Dropbox in this codebase), so
paths under `/content/dam` are used directly as opaque ids. Native
version history and the Assets HTTP API's own versioning selectors are
used directly; trash is emulated (no confidently-known listable AEM
"recycle bin" REST endpoint) via a dedicated hidden folder.
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
_TRASH_NAME = "_C-ECM-Trash"
_DAM_ROOT = "/content/dam"


class AEMAssetsProvider(StorageProvider):
    key = "aem_assets"
    display_name = "Adobe Experience Manager Assets"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "AEM instance URL", "http://localhost:4502")]

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _headers(self, creds: dict) -> dict:
        token = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> requests.Response:
        url = self._base_url(creds) + path
        try:
            resp = requests.request(method, url, headers=self._headers(creds), timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach AEM: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("Invalid AEM credentials", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"AEM error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderError("AEM instance URL is required", status_code=400)
        creds = {"username": username, "password": password, "base_url": base_url}
        try:
            self._request(creds, "GET", f"{_DAM_ROOT}.1.json")
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    @staticmethod
    def _name_from_path(path: str) -> str:
        return path.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _parent_path(path: str) -> str | None:
        idx = path.rstrip("/").rfind("/")
        return path[:idx] if idx > len(_DAM_ROOT) else None

    def _list_children(self, creds: dict, path: str) -> dict:
        return self._request(creds, "GET", f"{path}.1.json").json()

    def _find_or_create_child_folder(self, creds: dict, parent: str, name: str) -> str:
        data = self._list_children(creds, parent)
        for key, val in data.items():
            if isinstance(val, dict) and val.get("jcr:primaryType") == "sling:OrderedFolder" and key == name:
                return f"{parent}/{key}"
        self._request(creds, "POST", parent, data={
            "./name": name, "./jcr:primaryType": "sling:Folder", ":name": name,
        })
        return f"{parent}/{name}"

    def _root_id(self, creds: dict) -> str:
        cache_key = self._base_url(creds)
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            root = self._find_or_create_child_folder(creds, _DAM_ROOT, _APP_ROOT_NAME)
            self._root_id_cache[cache_key] = root
            return root

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
            trash = self._find_or_create_child_folder(creds, root, _TRASH_NAME)
            self._trash_id_cache[cache_key] = trash
            return trash

    def _resolve(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_id(creds)

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        node = self._resolve(creds, folder_id)
        data = self._list_children(creds, node)
        folders, files = [], []
        for key, val in data.items():
            if not isinstance(val, dict) or key.startswith("jcr:"):
                continue
            child_path = f"{node}/{key}"
            ptype = val.get("jcr:primaryType", "")
            if ptype in ("sling:Folder", "sling:OrderedFolder"):
                if key != _TRASH_NAME:
                    folders.append(FolderInfo(id=child_path, name=key, parent_id=folder_id))
            elif ptype == "dam:Asset":
                metadata = (val.get("jcr:content", {}) or {}).get("metadata", {}) or {}
                files.append(FileInfo(
                    id=child_path, name=key, folder_id=folder_id, version_number=1,
                    size_bytes=metadata.get("dam:size"), content_type=metadata.get("dc:format"),
                ))
        current_folder = FolderInfo(id=folder_id, name=self._name_from_path(folder_id), parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash = self._trash_id(creds)
        data = self._list_children(creds, trash)
        folders, files = [], []
        for key, val in data.items():
            if not isinstance(val, dict) or key.startswith("jcr:"):
                continue
            child_path = f"{trash}/{key}"
            ptype = val.get("jcr:primaryType", "")
            if ptype in ("sling:Folder", "sling:OrderedFolder"):
                folders.append(FolderInfo(id=child_path, name=key, parent_id=trash))
            elif ptype == "dam:Asset":
                files.append(FileInfo(id=child_path, name=key, folder_id=trash, version_number=1,
                                       size_bytes=None, content_type=None))
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=folders, files=files)

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        self._request(creds, "POST", parent, data={"./jcr:primaryType": "sling:Folder", ":name": name})
        return FolderInfo(id=f"{parent}/{name}", name=name, parent_id=parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        parent = self._parent_path(folder_id) or _DAM_ROOT
        new_path = f"{parent}/{name}"
        self._request(creds, "POST", folder_id, data={":operation": "move", ":dest": new_path})
        return FolderInfo(id=new_path, name=name, parent_id=None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        name = self._name_from_path(folder_id)
        new_path = f"{target}/{name}"
        self._request(creds, "POST", folder_id, data={":operation": "move", ":dest": new_path})
        return FolderInfo(id=new_path, name=name, parent_id=new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "POST", folder_id, data={":operation": "delete"})

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        self._request(creds, "POST", f"{parent}.createasset.html",
                       files={"file": (name, content, content_type)}, data={"fileName": name})
        return FileInfo(id=f"{parent}/{name}", name=name, folder_id=folder_id, version_number=1,
                         size_bytes=len(content), content_type=content_type)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        data = self._request(creds, "GET", f"{file_id}.1.json").json()
        metadata = (data.get("jcr:content", {}) or {}).get("metadata", {}) or {}
        return FileInfo(id=file_id, name=self._name_from_path(file_id), folder_id=self._parent_path(file_id),
                         version_number=1, size_bytes=metadata.get("dam:size"), content_type=metadata.get("dc:format"))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        parent = self._parent_path(file_id) or _DAM_ROOT
        new_path = f"{parent}/{name}"
        self._request(creds, "POST", file_id, data={":operation": "move", ":dest": new_path})
        return self.get_file(creds, new_path)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        name = self._name_from_path(file_id)
        new_path = f"{target}/{name}"
        self._request(creds, "POST", file_id, data={":operation": "move", ":dest": new_path})
        return self.get_file(creds, new_path)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "POST", file_id, data={":operation": "delete"})

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._request(creds, "GET", f"{file_id}/jcr:content/renditions/original").content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        try:
            result = self._request(creds, "GET", f"{file_id}.versionlist.json").json()
            versions = result.get("versions", [])
        except ProviderError:
            versions = []
        if not versions:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True)]
        return [
            VersionInfo(id=v.get("name", str(i)), version_number=i + 1, size_bytes=None,
                        content_type=None, is_current=(i == 0))
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        self._request(creds, "POST", f"{file_id}/jcr:content/renditions/original",
                       files={"file": ("original", content, content_type)})
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        return self._request(creds, "GET", f"{file_id}.version.{version_id}.json").content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        info = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, info.content_type or "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        name = self._name_from_path(folder_id)
        self._request(creds, "POST", folder_id, data={":operation": "move", ":dest": f"{target}/{name}"})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        name = self._name_from_path(folder_id)
        new_path = f"{root}/{name}"
        self._request(creds, "POST", folder_id, data={":operation": "move", ":dest": new_path})
        return FolderInfo(id=new_path, name=name, parent_id=None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        name = self._name_from_path(file_id)
        self._request(creds, "POST", file_id, data={":operation": "move", ":dest": f"{target}/{name}"})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        name = self._name_from_path(file_id)
        new_path = f"{root}/{name}"
        self._request(creds, "POST", file_id, data={":operation": "move", ":dest": new_path})
        return self.get_file(creds, new_path)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root = self._root_id(creds)
        trash = self._trash_id(creds)
        found_folders: list[FolderInfo] = []
        found_files: list[FileInfo] = []
        q = query.lower()

        def walk(path: str, depth: int):
            if depth > 6 or path == trash:
                return
            data = self._list_children(creds, path)
            for key, val in data.items():
                if not isinstance(val, dict) or key.startswith("jcr:"):
                    continue
                child_path = f"{path}/{key}"
                ptype = val.get("jcr:primaryType", "")
                if ptype in ("sling:Folder", "sling:OrderedFolder"):
                    if q in key.lower():
                        found_folders.append(FolderInfo(id=child_path, name=key, parent_id=path))
                    walk(child_path, depth + 1)
                elif ptype == "dam:Asset" and q in key.lower():
                    found_files.append(FileInfo(id=child_path, name=key, folder_id=path, version_number=1,
                                                 size_bytes=None, content_type=None))

        walk(root, 0)
        return found_folders, found_files
