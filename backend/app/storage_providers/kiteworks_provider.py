"""Accellion Kiteworks, via its documented REST API v3, OAuth2
authorization-code flow. Kiteworks is per-tenant/self-hosted (e.g.
`https://yourcompany.kiteworks.com`), so like this codebase's other
single-tenant-host OAuth providers, the host is read from an admin
setting (`kiteworks_base_url`) rather than collected per-connection.

UNVERIFIED — no live Kiteworks tenant in this environment to test
against. The `X-Accellion-Version` header value, the simplified (non-
chunked) upload — real large-file uploads would need Kiteworks'
documented multi-chunk protocol, not implemented here — and the versions/
trash endpoint paths are the biggest uncertainties.
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


class KiteworksProvider(StorageProvider):
    key = "kiteworks"
    display_name = "Accellion kiteworks"
    auth_mode = AuthMode.OAUTH

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    def _client(self) -> tuple[str, str, str]:
        return (
            settings_store.get_setting("kiteworks_client_id", ""),
            settings_store.get_setting("kiteworks_client_secret", ""),
            settings_store.get_setting("kiteworks_base_url", "").rstrip("/"),
        )

    @property
    def configured(self) -> bool:
        cid, secret, base_url = self._client()
        return bool(cid and secret and base_url)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        cid, _secret, base_url = self._client()
        params = {"response_type": "code", "client_id": cid, "redirect_uri": redirect_uri, "scope": "*", "state": state}
        return f"{base_url}/oauth/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        cid, secret, base_url = self._client()
        resp = requests.post(f"{base_url}/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": cid,
            "client_secret": secret, "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Kiteworks token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        return {
            "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + tok.get("expires_in", 3600),
            "base_url": base_url, "identity": "Kiteworks account",
        }

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        if creds.get("expires_at", 0) > time.time() + 30:
            return creds, False
        if not creds.get("refresh_token"):
            raise ProviderError("Kiteworks session expired — please reconnect", status_code=401)
        cid, secret, _base_url = self._client()
        resp = requests.post(f"{creds['base_url']}/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
            "client_id": cid, "client_secret": secret,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Kiteworks session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        return creds, True

    def _headers(self, creds: dict) -> dict:
        return {"Authorization": f"Bearer {creds['access_token']}", "X-Accellion-Version": "22.0"}

    def _api(self, creds: dict) -> str:
        return f"{creds['base_url']}/rest/v3"

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers=self._headers(creds), timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"Kiteworks error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds, _changed = self.refresh_if_needed(creds)
        resp = requests.request(method, url, headers=self._headers(creds), timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Kiteworks error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "Kiteworks account")

    @staticmethod
    def _parse_dt(s):
        if not s:
            return None
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def _entry_to_folder(self, e: dict, parent_id) -> FolderInfo:
        return FolderInfo(id=str(e["id"]), name=e.get("name", ""), parent_id=parent_id, created_at=None)

    def _entry_to_file(self, e: dict, parent_id) -> FileInfo:
        return FileInfo(id=str(e["id"]), name=e.get("name", ""), folder_id=parent_id,
                         version_number=e.get("currentVersionSum", 1) or 1,
                         size_bytes=e.get("size"), content_type=None,
                         updated_at=self._parse_dt(e.get("clientModified")))

    def _find_or_create_child(self, creds: dict, parent_id: str, name: str) -> str:
        result = self._get(creds, f"{self._api(creds)}/folders/{parent_id}/children")
        for e in result.get("data", []):
            if e.get("type") == "d" and e.get("name") == name:
                return str(e["id"])
        created = self._call(creds, "POST", f"{self._api(creds)}/folders/{parent_id}/folders",
                              json={"name": name}).json()
        return str(created.get("id", created.get("data", {}).get("id")))

    def _root_id(self, creds: dict) -> str:
        cache_key = creds["base_url"]
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            top = self._get(creds, f"{self._api(creds)}/folders/top")
            entries = top.get("data", [])
            top_id = str(entries[0]["id"]) if entries else "0"
            root_id = self._find_or_create_child(creds, top_id, _APP_ROOT_NAME)
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_id(self, creds: dict) -> str:
        cache_key = creds["base_url"]
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
        result = self._get(creds, f"{self._api(creds)}/folders/{node_id}/children")
        entries = result.get("data", [])
        folders = [self._entry_to_folder(e, folder_id) for e in entries if e.get("type") == "d" and e.get("name") != _TRASH_NAME]
        files = [self._entry_to_file(e, folder_id) for e in entries if e.get("type") == "f"]
        current_folder = FolderInfo(id=folder_id, name="", parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash_id = self._trash_id(creds)
        result = self._get(creds, f"{self._api(creds)}/folders/{trash_id}/children")
        entries = result.get("data", [])
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=[self._entry_to_folder(e, trash_id) for e in entries if e.get("type") == "d"],
                               files=[self._entry_to_file(e, trash_id) for e in entries if e.get("type") == "f"])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        created = self._call(creds, "POST", f"{self._api(creds)}/folders/{parent}/folders", json={"name": name}).json()
        return self._entry_to_folder(created.get("data", created), parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "PUT", f"{self._api(creds)}/folders/{folder_id}", json={"name": name}).json()
        return self._entry_to_folder(updated.get("data", updated), None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        updated = self._call(creds, "PUT", f"{self._api(creds)}/folders/{folder_id}",
                              json={"parentId": target}).json()
        return self._entry_to_folder(updated.get("data", updated), new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{self._api(creds)}/folders/{folder_id}")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        ticket = self._call(creds, "POST", f"{self._api(creds)}/folders/{parent}/actions/uploads",
                             json={"filename": name}).json()
        upload_url = ticket.get("uri", ticket.get("data", {}).get("uri"))
        resp = requests.post(upload_url, headers=self._headers(creds),
                              files={"content": (name, content, content_type)}, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError(f"Kiteworks upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        result = resp.json()
        return self._entry_to_file(result.get("data", result), folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        result = self._get(creds, f"{self._api(creds)}/files/{file_id}")
        return self._entry_to_file(result.get("data", result), None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "PUT", f"{self._api(creds)}/files/{file_id}", json={"name": name}).json()
        return self._entry_to_file(updated.get("data", updated), None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        updated = self._call(creds, "PUT", f"{self._api(creds)}/files/{file_id}",
                              json={"parentId": target}).json()
        return self._entry_to_file(updated.get("data", updated), new_folder_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._api(creds)}/files/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._call(creds, "GET", f"{self._api(creds)}/files/{file_id}/content").content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        try:
            result = self._get(creds, f"{self._api(creds)}/files/{file_id}/versions")
            entries = result.get("data", [])
        except ProviderError:
            entries = []
        if not entries:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True, updated_at=info.updated_at)]
        return [
            VersionInfo(id=str(v.get("id", i)), version_number=i + 1, size_bytes=v.get("size"),
                        content_type=None, is_current=(i == 0), updated_at=None)
            for i, v in enumerate(entries)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        info = self.get_file(creds, file_id)
        parent = info.folder_id or self._root_id(creds)
        ticket = self._call(creds, "POST", f"{self._api(creds)}/files/{file_id}/actions/uploads",
                             json={"filename": info.name}).json()
        upload_url = ticket.get("uri", ticket.get("data", {}).get("uri"))
        resp = requests.post(upload_url, headers=self._headers(creds),
                              files={"content": (info.name, content, content_type)}, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError(f"Kiteworks version upload failed ({resp.status_code})", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        return self._call(creds, "GET", f"{self._api(creds)}/files/{file_id}/versions/{version_id}/content").content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        return self.create_version(creds, file_id, "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "PUT", f"{self._api(creds)}/folders/{folder_id}", json={"parentId": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        updated = self._call(creds, "PUT", f"{self._api(creds)}/folders/{folder_id}", json={"parentId": root}).json()
        return self._entry_to_folder(updated.get("data", updated), None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "PUT", f"{self._api(creds)}/files/{file_id}", json={"parentId": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        updated = self._call(creds, "PUT", f"{self._api(creds)}/files/{file_id}", json={"parentId": root}).json()
        return self._entry_to_file(updated.get("data", updated), None)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        try:
            result = self._get(creds, f"{self._api(creds)}/search", params={"q": query, "types": "fd"})
            entries = result.get("data", [])
            return (
                [self._entry_to_folder(e, None) for e in entries if e.get("type") == "d"],
                [self._entry_to_file(e, None) for e in entries if e.get("type") == "f"],
            )
        except ProviderError:
            root_id = self._root_id(creds)
            trash_id = self._trash_id(creds)
            found_folders: list[FolderInfo] = []
            found_files: list[FileInfo] = []
            q = query.lower()

            def walk(node_id, depth):
                if depth > 6 or node_id == trash_id:
                    return
                entries = self._get(creds, f"{self._api(creds)}/folders/{node_id}/children").get("data", [])
                for e in entries:
                    if str(e["id"]) == trash_id:
                        continue
                    name = e.get("name", "")
                    if e.get("type") == "d":
                        if q in name.lower():
                            found_folders.append(self._entry_to_folder(e, node_id))
                        walk(str(e["id"]), depth + 1)
                    elif q in name.lower():
                        found_files.append(self._entry_to_file(e, node_id))

            walk(root_id, 0)
            return found_folders, found_files
