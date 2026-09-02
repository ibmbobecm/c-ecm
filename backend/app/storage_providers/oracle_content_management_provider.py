"""Oracle Content Management (OCM), via its documented Content REST API
(`/documents/api/1.2`), OAuth2 through the tenant's Oracle Identity Cloud
Service (IDCS) — a separate host from OCM itself in most deployments.

UNVERIFIED — no live OCM tenant in this environment to test against.
Both the OCM host and the IDCS host are per-tenant and unknown before the
OAuth redirect, so both are read from admin settings (`..._base_url`,
`..._idcs_url`) the same one-time-per-deployment way the OAuth client id/
secret are — a real limitation of this app's OAuth flow shape shared by
every other single-tenant-host provider added alongside this one.

The exact rename/move/versions/trash endpoint paths below are best-effort
reconstructions from OCM's documented conventions rather than a
line-by-line-confirmed spec — lower confidence than Box/Dropbox/
Salesforce, comparable to this codebase's OnBase/DocuShare adapters.
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


class OracleContentManagementProvider(StorageProvider):
    key = "oracle_content_management"
    display_name = "Oracle Content Management"
    auth_mode = AuthMode.OAUTH

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    def _client(self) -> tuple[str, str, str, str]:
        return (
            settings_store.get_setting("oracle_content_management_client_id", ""),
            settings_store.get_setting("oracle_content_management_client_secret", ""),
            settings_store.get_setting("oracle_content_management_base_url", "").rstrip("/"),
            settings_store.get_setting("oracle_content_management_idcs_url", "").rstrip("/"),
        )

    @property
    def configured(self) -> bool:
        cid, secret, base_url, idcs_url = self._client()
        return bool(cid and secret and base_url and idcs_url)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        cid, _secret, _base_url, idcs_url = self._client()
        params = {"response_type": "code", "client_id": cid, "redirect_uri": redirect_uri, "state": state}
        return f"{idcs_url}/oauth2/v1/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        cid, secret, base_url, idcs_url = self._client()
        resp = requests.post(f"{idcs_url}/oauth2/v1/token",
                              data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
                              auth=(cid, secret), timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"OCM token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {
            "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + tok.get("expires_in", 3600),
            "base_url": base_url, "idcs_url": idcs_url, "identity": "Oracle Content Management account",
        }
        return creds

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        if creds.get("expires_at", 0) > time.time() + 30:
            return creds, False
        if not creds.get("refresh_token"):
            raise ProviderError("OCM session expired — please reconnect", status_code=401)
        cid, secret, _base_url, _idcs_url = self._client()
        resp = requests.post(f"{creds['idcs_url']}/oauth2/v1/token",
                              data={"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]},
                              auth=(cid, secret), timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("OCM session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        return creds, True

    def _api(self, creds: dict) -> str:
        return f"{creds['base_url']}/documents/api/1.2"

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"OCM error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds, _changed = self.refresh_if_needed(creds)
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"OCM error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "Oracle Content Management account")

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
        return FolderInfo(id=e["id"], name=e.get("name", ""), parent_id=parent_id, created_at=None)

    def _entry_to_file(self, e: dict, parent_id) -> FileInfo:
        return FileInfo(id=e["id"], name=e.get("name", ""), folder_id=parent_id, version_number=1,
                         size_bytes=e.get("size"), content_type=None,
                         updated_at=self._parse_dt(e.get("modifiedTime")))

    def _find_or_create_child(self, creds: dict, parent_id: str, name: str) -> str:
        result = self._get(creds, f"{self._api(creds)}/folders/{parent_id}/items")
        for e in result.get("items", []):
            if e.get("type") == "folder" and e.get("name") == name:
                return e["id"]
        created = self._call(creds, "POST", f"{self._api(creds)}/folders/{parent_id}", json={"name": name}).json()
        return created["id"]

    def _root_id(self, creds: dict) -> str:
        cache_key = creds["base_url"]
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            root_id = self._find_or_create_child(creds, "personal", _APP_ROOT_NAME)
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
        result = self._get(creds, f"{self._api(creds)}/folders/{node_id}/items")
        items = result.get("items", [])
        folders = [self._entry_to_folder(e, folder_id) for e in items if e.get("type") == "folder" and e.get("name") != _TRASH_NAME]
        files = [self._entry_to_file(e, folder_id) for e in items if e.get("type") == "file"]
        current_folder = FolderInfo(id=folder_id, name="", parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash_id = self._trash_id(creds)
        result = self._get(creds, f"{self._api(creds)}/folders/{trash_id}/items")
        items = result.get("items", [])
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=[self._entry_to_folder(e, trash_id) for e in items if e.get("type") == "folder"],
                               files=[self._entry_to_file(e, trash_id) for e in items if e.get("type") == "file"])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        created = self._call(creds, "POST", f"{self._api(creds)}/folders/{parent}", json={"name": name}).json()
        return self._entry_to_folder(created, parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "POST", f"{self._api(creds)}/folders/{folder_id}/rename", json={"name": name}).json()
        return self._entry_to_folder(updated, None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        updated = self._call(creds, "POST", f"{self._api(creds)}/folders/{folder_id}/move",
                              json={"destination": target}).json()
        return self._entry_to_folder(updated, new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{self._api(creds)}/folders/{folder_id}")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        import json as _json
        parent = self._resolve(creds, folder_id)
        creds2, _ = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{self._api(creds)}/files/data",
            headers={"Authorization": f"Bearer {creds2['access_token']}"},
            data={"jsonInputParameters": _json.dumps({"parentID": parent})},
            files={"primaryFile": (name, content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"OCM upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self._entry_to_file(resp.json(), folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        result = self._get(creds, f"{self._api(creds)}/files/{file_id}")
        return self._entry_to_file(result, None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "POST", f"{self._api(creds)}/files/{file_id}/rename", json={"name": name}).json()
        return self._entry_to_file(updated, None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        updated = self._call(creds, "POST", f"{self._api(creds)}/files/{file_id}/move",
                              json={"destination": target}).json()
        return self._entry_to_file(updated, new_folder_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._api(creds)}/files/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._call(creds, "GET", f"{self._api(creds)}/files/{file_id}/data").content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        try:
            result = self._get(creds, f"{self._api(creds)}/files/{file_id}/revisions")
            revs = result.get("items", [])
        except ProviderError:
            revs = []
        if not revs:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True, updated_at=info.updated_at)]
        return [
            VersionInfo(id=v.get("revisionId", v.get("id", str(i))), version_number=i + 1,
                        size_bytes=v.get("size"), content_type=None, is_current=(i == 0),
                        updated_at=self._parse_dt(v.get("modifiedTime")))
            for i, v in enumerate(revs)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        info = self.get_file(creds, file_id)
        creds2, _ = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{self._api(creds)}/files/{file_id}/data",
            headers={"Authorization": f"Bearer {creds2['access_token']}"},
            files={"primaryFile": (info.name, content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"OCM version upload failed ({resp.status_code})", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        return self._call(creds, "GET", f"{self._api(creds)}/files/{file_id}/data",
                           params={"revisionId": version_id}).content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        return self.create_version(creds, file_id, "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "POST", f"{self._api(creds)}/folders/{folder_id}/move", json={"destination": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        updated = self._call(creds, "POST", f"{self._api(creds)}/folders/{folder_id}/move",
                              json={"destination": root}).json()
        return self._entry_to_folder(updated, None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "POST", f"{self._api(creds)}/files/{file_id}/move", json={"destination": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        updated = self._call(creds, "POST", f"{self._api(creds)}/files/{file_id}/move",
                              json={"destination": root}).json()
        return self._entry_to_file(updated, None)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        try:
            result = self._get(creds, f"{self._api(creds)}/folders/search/items", params={"fullTextSearch": query})
            items = result.get("items", [])
            return (
                [self._entry_to_folder(e, None) for e in items if e.get("type") == "folder"],
                [self._entry_to_file(e, None) for e in items if e.get("type") == "file"],
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
                items = self._get(creds, f"{self._api(creds)}/folders/{node_id}/items").get("items", [])
                for e in items:
                    if e.get("id") == trash_id:
                        continue
                    name = e.get("name", "")
                    if e.get("type") == "folder":
                        if q in name.lower():
                            found_folders.append(self._entry_to_folder(e, node_id))
                        walk(e["id"], depth + 1)
                    elif q in name.lower():
                        found_files.append(self._entry_to_file(e, node_id))

            walk(root_id, 0)
            return found_folders, found_files
