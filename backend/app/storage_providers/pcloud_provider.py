"""pCloud, via its documented JSON API (`api.pcloud.com`), username/
password login exchanged for a long-lived auth token (`auth` param on
every subsequent call, pCloud's own documented convention rather than a
bearer header).

UNVERIFIED — no live pCloud account in this environment to test against.
pCloud addresses folders/files by integer id (`folderid`/`fileid`); real
native trash ("Trash" folder, `folderid=0` implicit special handling) and
real native revisions are used directly since both are confidently
documented pCloud features.
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
_API = "https://api.pcloud.com"


class PCloudProvider(StorageProvider):
    key = "pcloud"
    display_name = "pCloud"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()

    def _call(self, creds: dict, method: str, **params) -> dict:
        if "auth" in creds:
            params["auth"] = creds["auth"]
        try:
            resp = requests.get(f"{_API}/{method}", params=params, timeout=30)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach pCloud: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"pCloud error {resp.status_code}: {resp.text[:300]}", status_code=502)
        result = resp.json()
        if result.get("result") != 0:
            code = result.get("result")
            if code == 2094:
                raise ProviderError("Invalid pCloud credentials", status_code=401)
            if code == 2005 or code == 2009:
                raise ProviderError("Not found", status_code=404)
            raise ProviderError(f"pCloud error {code}: {result.get('error', '')}", status_code=502)
        return result

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        try:
            result = self._call({}, "userinfo", username=username, password=password, getauth=1)
        except ProviderError:
            return None
        return {"username": username, "auth": result["auth"]}

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    def _resolve(self, creds: dict, folder_id: str | None) -> str:
        if folder_id is not None:
            return folder_id
        cache_key = creds["username"]
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            listing = self._call(creds, "listfolder", folderid=0)
            contents = listing.get("metadata", {}).get("contents", [])
            existing = next((c for c in contents if c.get("isfolder") and c.get("name") == _APP_ROOT_NAME), None)
            if existing:
                root_id = str(existing["folderid"])
            else:
                created = self._call(creds, "createfolder", folderid=0, name=_APP_ROOT_NAME)
                root_id = str(created["metadata"]["folderid"])
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _entry_to_folder(self, e: dict, parent_id) -> FolderInfo:
        return FolderInfo(id=str(e["folderid"]), name=e.get("name", ""), parent_id=parent_id)

    def _entry_to_file(self, e: dict, parent_id) -> FileInfo:
        return FileInfo(id=str(e["fileid"]), name=e.get("name", ""), folder_id=parent_id, version_number=1,
                         size_bytes=e.get("size"), content_type=e.get("contenttype"))

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        node = self._resolve(creds, folder_id)
        listing = self._call(creds, "listfolder", folderid=node)
        contents = listing.get("metadata", {}).get("contents", [])
        folders = [self._entry_to_folder(e, folder_id) for e in contents if e.get("isfolder")]
        files = [self._entry_to_file(e, folder_id) for e in contents if not e.get("isfolder")]
        current_folder = FolderInfo(id=node, name=listing.get("metadata", {}).get("name", ""), parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        try:
            result = self._call(creds, "trash_list", folderid=0)
            contents = result.get("metadata", {}).get("contents", [])
        except ProviderError:
            contents = []
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._entry_to_folder(e, None) for e in contents if e.get("isfolder")],
            files=[self._entry_to_file(e, None) for e in contents if not e.get("isfolder")],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        created = self._call(creds, "createfolder", folderid=parent, name=name)
        return self._entry_to_folder(created["metadata"], parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "renamefolder", folderid=folder_id, toname=name)
        return self._entry_to_folder(updated["metadata"], None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        updated = self._call(creds, "renamefolder", folderid=folder_id, tofolderid=target)
        return self._entry_to_folder(updated["metadata"], new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "deletefolderrecursive", folderid=folder_id)

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        resp = requests.post(f"{_API}/uploadfile", params={"auth": creds["auth"], "folderid": parent},
                              files={"file": (name, content, content_type)}, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError(f"pCloud upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        result = resp.json()
        metadata = result.get("metadata", [{}])[0]
        return self._entry_to_file(metadata, folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        result = self._call(creds, "stat", fileid=file_id)
        return self._entry_to_file(result["metadata"], None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "renamefile", fileid=file_id, toname=name)
        return self._entry_to_file(updated["metadata"], None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        updated = self._call(creds, "renamefile", fileid=file_id, tofolderid=target)
        return self._entry_to_file(updated["metadata"], new_folder_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "deletefile", fileid=file_id)

    def get_content(self, creds: dict, file_id: str) -> bytes:
        link = self._call(creds, "getfilelink", fileid=file_id)
        hosts = link.get("hosts", [])
        if not hosts:
            raise ProviderError("Couldn't get a download link", status_code=502)
        url = f"https://{hosts[0]}{link['path']}"
        resp = requests.get(url, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError("File not found", status_code=404)
        return resp.content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        try:
            result = self._call(creds, "listrevisions", fileid=file_id)
            revisions = result.get("revisions", [])
        except ProviderError:
            revisions = []
        if not revisions:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True)]
        return [
            VersionInfo(id=str(r.get("revisionid", i)), version_number=i + 1, size_bytes=r.get("size"),
                        content_type=None, is_current=(i == 0))
            for i, r in enumerate(revisions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        info = self.get_file(creds, file_id)
        resp = requests.post(f"{_API}/uploadfile",
                              params={"auth": creds["auth"], "folderid": info.folder_id or self._resolve(creds, None),
                                      "renameifexists": 0},
                              files={"file": (info.name, content, content_type)}, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError(f"pCloud version upload failed ({resp.status_code})", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        link = self._call(creds, "getfilelink", fileid=file_id, revisionid=version_id)
        hosts = link.get("hosts", [])
        if not hosts:
            raise ProviderError("Version content not found", status_code=404)
        resp = requests.get(f"https://{hosts[0]}{link['path']}", timeout=60)
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        info = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, info.content_type or "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "deletefolderrecursive", folderid=folder_id)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        result = self._call(creds, "trash_restore", folderid=folder_id)
        return self._entry_to_folder(result["metadata"], None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "deletefile", fileid=file_id)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        result = self._call(creds, "trash_restore", fileid=file_id)
        return self._entry_to_file(result["metadata"], None)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root = self._resolve(creds, None)
        found_folders: list[FolderInfo] = []
        found_files: list[FileInfo] = []
        q = query.lower()

        def walk(node_id, depth):
            if depth > 6:
                return
            listing = self._call(creds, "listfolder", folderid=node_id)
            for e in listing.get("metadata", {}).get("contents", []):
                name = e.get("name", "")
                if e.get("isfolder"):
                    if q in name.lower():
                        found_folders.append(self._entry_to_folder(e, node_id))
                    walk(str(e["folderid"]), depth + 1)
                elif q in name.lower():
                    found_files.append(self._entry_to_file(e, node_id))

        walk(root, 0)
        return found_folders, found_files
