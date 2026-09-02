"""MediaFire, via its documented Developer API (`www.mediafire.com/api`,
session-token auth from email/password + a registered application id).

UNVERIFIED — no live MediaFire account in this environment to test
against. Written from MediaFire's documented API conventions
(`/user/get_session_token.php`, `/folder/get_content.php`, `/upload/
simple.php`, key-based folder/file addressing). MediaFire has real native
trash ("Files & Folders in Trash") used directly; version history is not
a MediaFire feature (each upload simply becomes the file's current
content), so this provider reports only a single current version.
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
_API = "https://www.mediafire.com/api/1.5"
# MediaFire requires a registered application id for API access; no public
# shared default exists, so a real deployment must obtain its own and set
# it as an admin setting (mirrors this codebase's other one-time-per-
# deployment settings) rather than being hardcoded here.


class MediaFireProvider(StorageProvider):
    key = "mediafire"
    display_name = "MediaFire"
    auth_mode = AuthMode.CREDENTIALS
    credential_labels = ("Email", "Password")

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("app_id", "MediaFire application ID")]

    def _login(self, creds: dict) -> str:
        resp = requests.get(f"{_API}/user/get_session_token.php", params={
            "email": creds["username"], "password": creds["password"],
            "application_id": creds["app_id"], "response_format": "json",
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Couldn't reach MediaFire", status_code=502)
        result = resp.json().get("response", {})
        if result.get("result") != "Success":
            raise ProviderError("Invalid MediaFire credentials", status_code=401)
        return result["session_token"]

    def _call(self, creds: dict, path: str, **params) -> dict:
        token = creds.get("_token")
        if not token:
            token = self._login(creds)
            creds["_token"] = token
        params["session_token"] = token
        params["response_format"] = "json"
        try:
            resp = requests.get(f"{_API}{path}", params=params, timeout=30)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach MediaFire: {exc}", status_code=502)
        result = resp.json().get("response", {})
        if result.get("result") != "Success":
            if result.get("error") in ("116", "142"):
                token = self._login(creds)
                creds["_token"] = token
                params["session_token"] = token
                resp = requests.get(f"{_API}{path}", params=params, timeout=30)
                result = resp.json().get("response", {})
                if result.get("result") == "Success":
                    return result
            raise ProviderError(f"MediaFire error: {result.get('message', result.get('error', ''))}", status_code=502)
        return result

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        app_id = (config.get("app_id") or "").strip()
        if not app_id:
            raise ProviderError("Application ID is required", status_code=400)
        creds = {"username": username, "password": password, "app_id": app_id}
        try:
            self._login(creds)
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    def _resolve(self, creds: dict, folder_id: str | None) -> str | None:
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
            listing = self._call(creds, "/folder/get_content.php", content_type="folders")
            folders = listing.get("folder_content", {}).get("folders", [])
            existing = next((f for f in folders if f.get("name") == _APP_ROOT_NAME), None)
            if existing:
                root_id = existing["folderkey"]
            else:
                created = self._call(creds, "/folder/create.php", foldername=_APP_ROOT_NAME)
                root_id = created["folder_key"]
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _entry_to_folder(self, e: dict, parent_id) -> FolderInfo:
        return FolderInfo(id=e["folderkey"], name=e.get("name", ""), parent_id=parent_id)

    def _entry_to_file(self, e: dict, parent_id) -> FileInfo:
        return FileInfo(id=e["quickkey"], name=e.get("filename", ""), folder_id=parent_id, version_number=1,
                         size_bytes=int(e["size"]) if e.get("size") else None, content_type=e.get("mimetype"))

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        node = self._resolve(creds, folder_id)
        fres = self._call(creds, "/folder/get_content.php", folder_key=node or "", content_type="folders")
        ffiles = self._call(creds, "/folder/get_content.php", folder_key=node or "", content_type="files")
        folders = [self._entry_to_folder(e, folder_id) for e in fres.get("folder_content", {}).get("folders", [])]
        files = [self._entry_to_file(e, folder_id) for e in ffiles.get("folder_content", {}).get("files", [])]
        current_folder = FolderInfo(id=node, name="", parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        try:
            result = self._call(creds, "/folder/get_content.php", content_type="files", filter="trash")
            entries = result.get("folder_content", {}).get("files", [])
        except ProviderError:
            entries = []
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=[], files=[self._entry_to_file(e, None) for e in entries])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        created = self._call(creds, "/folder/create.php", foldername=name, parent_key=parent or "")
        return FolderInfo(id=created["folder_key"], name=name, parent_id=parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        self._call(creds, "/folder/update.php", folder_key=folder_id, foldername=name)
        return FolderInfo(id=folder_id, name=name, parent_id=None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        self._call(creds, "/folder/move.php", folder_key_src=folder_id, folder_key_dst=target or "")
        return FolderInfo(id=folder_id, name="", parent_id=new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "/folder/delete.php", folder_key=folder_id)

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        token = creds.get("_token") or self._login(creds)
        creds["_token"] = token
        parent = self._resolve(creds, folder_id)
        resp = requests.post(f"{_API}/upload/simple.php",
                              params={"session_token": token, "folder_key": parent or "",
                                      "filename": name, "response_format": "json"},
                              data=content, headers={"Content-Type": content_type}, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError(f"MediaFire upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        result = resp.json().get("response", {}).get("doupload", {})
        quickkey = result.get("quickkey")
        return FileInfo(id=quickkey, name=name, folder_id=folder_id, version_number=1,
                         size_bytes=len(content), content_type=content_type)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        result = self._call(creds, "/file/get_info.php", quick_key=file_id)
        info = result.get("file_info", {})
        return self._entry_to_file(info, None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        self._call(creds, "/file/update.php", quick_key=file_id, filename=name)
        return self.get_file(creds, file_id)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        self._call(creds, "/file/move.php", quick_key=file_id, folder_key=target or "")
        info = self.get_file(creds, file_id)
        info.folder_id = new_folder_id
        return info

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "/file/delete.php", quick_key=file_id)

    def get_content(self, creds: dict, file_id: str) -> bytes:
        result = self._call(creds, "/file/get_links.php", quick_key=file_id, link_type="direct_download")
        links = result.get("links", [])
        if not links or not links[0].get("direct_download"):
            raise ProviderError("File content not available", status_code=404)
        resp = requests.get(links[0]["direct_download"], timeout=60)
        if resp.status_code >= 400:
            raise ProviderError("File not found", status_code=404)
        return resp.content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        info = self.get_file(creds, file_id)
        return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                             content_type=info.content_type, is_current=True)]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        info = self.get_file(creds, file_id)
        token = creds.get("_token") or self._login(creds)
        creds["_token"] = token
        resp = requests.post(f"{_API}/upload/simple.php",
                              params={"session_token": token, "quick_key": file_id, "action_on_duplicate": "replace",
                                      "filename": info.name, "response_format": "json"},
                              data=content, headers={"Content-Type": content_type}, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError(f"MediaFire version upload failed ({resp.status_code})", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        return self.get_content(creds, file_id)

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        return self.get_file(creds, file_id)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "/folder/delete.php", folder_key=folder_id)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        self._call(creds, "/folder/restore.php", folder_key=folder_id)
        return FolderInfo(id=folder_id, name="", parent_id=None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "/file/delete.php", quick_key=file_id)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        self._call(creds, "/file/restore.php", quick_key=file_id)
        return self.get_file(creds, file_id)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        try:
            result = self._call(creds, "/file/search.php", search_text=query)
            entries = result.get("results", [])
            folders = [self._entry_to_folder(e, None) for e in entries if "folderkey" in e]
            files = [self._entry_to_file(e, None) for e in entries if "quickkey" in e]
            return folders, files
        except ProviderError:
            root = self._resolve(creds, None)
            found_folders: list[FolderInfo] = []
            found_files: list[FileInfo] = []
            q = query.lower()

            def walk(node_id, depth):
                if depth > 6:
                    return
                fres = self._call(creds, "/folder/get_content.php", folder_key=node_id or "", content_type="folders")
                for e in fres.get("folder_content", {}).get("folders", []):
                    if q in e.get("name", "").lower():
                        found_folders.append(self._entry_to_folder(e, node_id))
                    walk(e["folderkey"], depth + 1)
                ffiles = self._call(creds, "/folder/get_content.php", folder_key=node_id or "", content_type="files")
                for e in ffiles.get("folder_content", {}).get("files", []):
                    if q in e.get("filename", "").lower():
                        found_files.append(self._entry_to_file(e, node_id))

            walk(root, 0)
            return found_folders, found_files
