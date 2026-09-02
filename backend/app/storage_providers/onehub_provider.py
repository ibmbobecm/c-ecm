"""Onehub, via its documented REST API (JSON, OAuth2 authorization-code
flow, fixed host `onehub.com`).

UNVERIFIED — written from Onehub's documented REST API conventions, but
there's no live Onehub account in this environment to test against.
Onehub organizes content as Workspaces > Folders > Files; this connection
picks the account's first workspace as its scope. The exact field names
for move (`parent_folder_id`) and the versions/search response shapes are
the biggest uncertainties — if a real account's API doesn't match, those
degrade to a single-current-version report / a client-side filtered
listing respectively, rather than raising.
"""

import threading
import time

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
from .. import settings_store

_APP_ROOT_NAME = "C-ECM"
_TRASH_NAME = "_C-ECM-Trash"
_API = "https://onehub.com/api"


class OnehubProvider(StorageProvider):
    key = "onehub"
    display_name = "Onehub"
    auth_mode = AuthMode.OAUTH

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    def _client(self) -> tuple[str, str]:
        return (settings_store.get_setting("onehub_client_id", ""), settings_store.get_setting("onehub_client_secret", ""))

    @property
    def configured(self) -> bool:
        cid, secret = self._client()
        return bool(cid and secret)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        cid, _secret = self._client()
        params = {"client_id": cid, "response_type": "code", "redirect_uri": redirect_uri, "state": state}
        return "https://onehub.com/oauth/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        cid, secret = self._client()
        resp = requests.post("https://onehub.com/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": cid,
            "client_secret": secret, "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Onehub token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                 "expires_at": time.time() + tok.get("expires_in", 3600)}
        workspaces = self._get(creds, f"{_API}/workspaces.json")
        entries = workspaces if isinstance(workspaces, list) else workspaces.get("workspaces", [])
        if not entries:
            raise ProviderError("No Onehub workspace is available on this account", status_code=502)
        first = entries[0]
        creds["workspace_id"] = first.get("id")
        creds["identity"] = first.get("name", "Onehub account")
        return creds

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        if creds.get("expires_at", 0) > time.time() + 30:
            return creds, False
        if not creds.get("refresh_token"):
            raise ProviderError("Onehub session expired — please reconnect", status_code=401)
        cid, secret = self._client()
        resp = requests.post("https://onehub.com/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
            "client_id": cid, "client_secret": secret,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Onehub session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        return creds, True

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"Onehub error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds, _changed = self.refresh_if_needed(creds)
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Onehub error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "Onehub account")

    @staticmethod
    def _parse_dt(s):
        if not s:
            return None
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def _folder_entry(self, e: dict, parent_id) -> FolderInfo:
        return FolderInfo(id=str(e["id"]), name=e.get("name", ""), parent_id=parent_id, created_at=None)

    def _file_entry(self, e: dict, parent_id) -> FileInfo:
        return FileInfo(id=str(e["id"]), name=e.get("filename", e.get("name", "")), folder_id=parent_id,
                         version_number=1, size_bytes=e.get("size"), content_type=e.get("content_type"),
                         updated_at=self._parse_dt(e.get("updated_at")))

    def _find_or_create_child(self, creds: dict, parent_id, name: str) -> str:
        result = self._get(creds, f"{_API}/folders/{parent_id}/folders.json")
        entries = result if isinstance(result, list) else result.get("folders", [])
        for e in entries:
            if e.get("name") == name:
                return str(e["id"])
        created = self._call(creds, "POST", f"{_API}/folders/{parent_id}/folders.json", data={"name": name}).json()
        return str(created.get("id", created.get("folder", {}).get("id")))

    def _root_folder_id(self, creds: dict) -> str:
        ws = self._get(creds, f"{_API}/workspaces/{creds['workspace_id']}.json")
        return str(ws.get("home_folder_id", ws.get("root_folder_id", creds["workspace_id"])))

    def _root_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "")
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            top = self._root_folder_id(creds)
            root_id = self._find_or_create_child(creds, top, _APP_ROOT_NAME)
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "")
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
        node_id = self._resolve(creds, folder_id)
        fres = self._get(creds, f"{_API}/folders/{node_id}/folders.json")
        ffiles = self._get(creds, f"{_API}/folders/{node_id}/files.json")
        f_entries = fres if isinstance(fres, list) else fres.get("folders", [])
        file_entries = ffiles if isinstance(ffiles, list) else ffiles.get("files", [])
        folders = [self._folder_entry(e, folder_id) for e in f_entries if e.get("name") != _TRASH_NAME]
        files = [self._file_entry(e, folder_id) for e in file_entries]
        current_folder = FolderInfo(id=folder_id, name="", parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash_id = self._trash_id(creds)
        fres = self._get(creds, f"{_API}/folders/{trash_id}/folders.json")
        ffiles = self._get(creds, f"{_API}/folders/{trash_id}/files.json")
        f_entries = fres if isinstance(fres, list) else fres.get("folders", [])
        file_entries = ffiles if isinstance(ffiles, list) else ffiles.get("files", [])
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=[self._folder_entry(e, trash_id) for e in f_entries],
                               files=[self._file_entry(e, trash_id) for e in file_entries])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        created = self._call(creds, "POST", f"{_API}/folders/{parent}/folders.json", data={"name": name}).json()
        return self._folder_entry(created, parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "PUT", f"{_API}/folders/{folder_id}.json", data={"name": name}).json()
        return self._folder_entry(updated, None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        updated = self._call(creds, "PUT", f"{_API}/folders/{folder_id}.json",
                              data={"parent_folder_id": target}).json()
        return self._folder_entry(updated, new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{_API}/folders/{folder_id}.json")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        creds2, _ = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{_API}/folders/{parent}/files.json",
            headers={"Authorization": f"Bearer {creds2['access_token']}"},
            files={"file": (name, content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Onehub upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self._file_entry(resp.json(), folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        result = self._get(creds, f"{_API}/files/{file_id}.json")
        return self._file_entry(result, None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "PUT", f"{_API}/files/{file_id}.json", data={"name": name}).json()
        return self._file_entry(updated, None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        updated = self._call(creds, "PUT", f"{_API}/files/{file_id}.json",
                              data={"parent_folder_id": target}).json()
        return self._file_entry(updated, new_folder_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{_API}/files/{file_id}.json")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._call(creds, "GET", f"{_API}/files/{file_id}/download.json").content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        try:
            result = self._get(creds, f"{_API}/files/{file_id}/versions.json")
            entries = result if isinstance(result, list) else result.get("versions", [])
        except ProviderError:
            entries = []
        if not entries:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True, updated_at=info.updated_at)]
        return [
            VersionInfo(id=str(v.get("id", i)), version_number=i + 1, size_bytes=v.get("size"),
                        content_type=None, is_current=bool(v.get("current", i == 0)),
                        updated_at=self._parse_dt(v.get("created_at")))
            for i, v in enumerate(entries)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        creds2, _ = self.refresh_if_needed(creds)
        resp = requests.put(
            f"{_API}/files/{file_id}.json",
            headers={"Authorization": f"Bearer {creds2['access_token']}"},
            files={"file": ("content", content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Onehub version upload failed ({resp.status_code})", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        return self._call(creds, "GET", f"{_API}/files/{file_id}/versions/{version_id}/download.json").content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        return self.create_version(creds, file_id, "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "PUT", f"{_API}/folders/{folder_id}.json", data={"parent_folder_id": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        updated = self._call(creds, "PUT", f"{_API}/folders/{folder_id}.json", data={"parent_folder_id": root}).json()
        return self._folder_entry(updated, None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "PUT", f"{_API}/files/{file_id}.json", data={"parent_folder_id": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        updated = self._call(creds, "PUT", f"{_API}/files/{file_id}.json", data={"parent_folder_id": root}).json()
        return self._file_entry(updated, None)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        try:
            result = self._get(creds, f"{_API}/workspaces/{creds['workspace_id']}/search.json", params={"query": query})
            entries = result if isinstance(result, list) else result.get("results", [])
            folders, files = [], []
            for e in entries:
                if "filename" in e or "content_type" in e:
                    files.append(self._file_entry(e, None))
                else:
                    folders.append(self._folder_entry(e, None))
            return folders, files
        except ProviderError:
            root_id = self._root_id(creds)
            trash_id = self._trash_id(creds)
            found_folders: list[FolderInfo] = []
            found_files: list[FileInfo] = []
            q = query.lower()

            def walk(node_id, depth):
                if depth > 6 or node_id == trash_id:
                    return
                fres = self._get(creds, f"{_API}/folders/{node_id}/folders.json")
                for e in (fres if isinstance(fres, list) else fres.get("folders", [])):
                    if str(e["id"]) == trash_id:
                        continue
                    if q in e.get("name", "").lower():
                        found_folders.append(self._folder_entry(e, node_id))
                    walk(str(e["id"]), depth + 1)
                ffiles = self._get(creds, f"{_API}/folders/{node_id}/files.json")
                for e in (ffiles if isinstance(ffiles, list) else ffiles.get("files", [])):
                    if q in e.get("filename", "").lower():
                        found_files.append(self._file_entry(e, node_id))

            walk(root_id, 0)
            return found_folders, found_files
