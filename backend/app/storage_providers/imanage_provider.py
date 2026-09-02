"""iManage Work, via its documented REST API v2 (JSON, OAuth2 authorization
-code flow). iManage Work Cloud is per-customer (e.g.
`https://yourcustomer.imanage.work`), so unlike Box/Dropbox/Google — whose
API host is fixed — the customer's own host is read from an admin setting
(`imanage_base_url`) the same one-time-per-deployment way the OAuth client
id/secret are, rather than collected per-connection (this app's OAuth flow
doesn't currently pass a per-connection value into `get_authorize_url`).

UNVERIFIED — written from iManage Work's documented REST API v2
conventions, but there's no live iManage tenant in this environment to
test against. The biggest uncertainties: (a) the base_url-via-settings
limitation above, (b) iManage lists a folder's subfolders and its
documents via two SEPARATE calls rather than one combined listing (unlike
most other providers in this codebase), (c) documents commonly require
firm-specific "profile" fields beyond name/folder that aren't modeled
here, and (d) there's no confidently-known listable recycle-bin endpoint,
so trash is emulated via a hidden folder rather than iManage's own (if it
has one)."""

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


class IManageProvider(StorageProvider):
    key = "imanage"
    display_name = "iManage"
    auth_mode = AuthMode.OAUTH

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    def _client(self) -> tuple[str, str, str]:
        return (
            settings_store.get_setting("imanage_client_id", ""),
            settings_store.get_setting("imanage_client_secret", ""),
            settings_store.get_setting("imanage_base_url", "").rstrip("/"),
        )

    @property
    def configured(self) -> bool:
        cid, secret, base_url = self._client()
        return bool(cid and secret and base_url)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        cid, _secret, base_url = self._client()
        params = {
            "response_type": "code", "client_id": cid, "redirect_uri": redirect_uri,
            "scope": "user.read user.write", "state": state,
        }
        return f"{base_url}/auth/oauth2/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        cid, secret, base_url = self._client()
        resp = requests.post(f"{base_url}/auth/oauth2/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": cid,
            "client_secret": secret, "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"iManage token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {
            "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + tok.get("expires_in", 3600), "base_url": base_url,
        }
        me = self._get(creds, f"{base_url}/work/api/v2/me")
        creds["identity"] = me.get("data", {}).get("email") or me.get("data", {}).get("user_id") or "iManage account"
        creds["customer_id"] = me.get("data", {}).get("customer_id", "")
        creds["library_id"] = self._first_library_id(creds)
        return creds

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        if creds.get("expires_at", 0) > time.time() + 30:
            return creds, False
        if not creds.get("refresh_token"):
            raise ProviderError("iManage session expired — please reconnect", status_code=401)
        cid, secret, _base_url = self._client()
        resp = requests.post(f"{creds['base_url']}/auth/oauth2/token", data={
            "grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
            "client_id": cid, "client_secret": secret,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("iManage session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        return creds, True

    def _headers(self, creds: dict) -> dict:
        return {"Authorization": f"Bearer {creds['access_token']}", "X-Auth-Token": creds["access_token"]}

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers=self._headers(creds), timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"iManage error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds, _changed = self.refresh_if_needed(creds)
        resp = requests.request(method, url, headers=self._headers(creds), timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"iManage error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "iManage account")

    def _first_library_id(self, creds: dict) -> str:
        result = self._get(creds, f"{creds['base_url']}/work/api/v2/customers/{creds['customer_id']}/libraries")
        libs = result.get("data", [])
        if not libs:
            raise ProviderError("No iManage library is available on this account", status_code=502)
        return libs[0]["id"]

    def _lib_api(self, creds: dict) -> str:
        return f"{creds['base_url']}/work/api/v2/customers/{creds['customer_id']}/libraries/{creds['library_id']}"

    @staticmethod
    def _parse_dt(s: str | None):
        if not s:
            return None
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def _folder_entry(self, e: dict, parent_id) -> FolderInfo:
        return FolderInfo(id=e["id"], name=e.get("name", e.get("id", "")), parent_id=parent_id, created_at=None)

    def _doc_entry(self, e: dict, parent_id) -> FileInfo:
        return FileInfo(id=e["id"], name=e.get("name", e.get("id", "")), folder_id=parent_id, version_number=1,
                         size_bytes=e.get("size"), content_type=e.get("extension"),
                         updated_at=self._parse_dt(e.get("edit_date")))

    def _find_or_create_child(self, creds: dict, parent_id: str, name: str) -> str:
        result = self._get(creds, f"{self._lib_api(creds)}/folders/{parent_id}/folders")
        for e in result.get("data", []):
            if e.get("name") == name:
                return e["id"]
        created = self._call(creds, "POST", f"{self._lib_api(creds)}/folders", json={"name": name, "parent_id": parent_id}).json()
        return created.get("data", created)["id"]

    def _root_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "") + creds.get("library_id", "")
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            top = self._get(creds, f"{self._lib_api(creds)}/folders/root/folders")
            top_id = "root"
            root_id = self._find_or_create_child(creds, top_id, _APP_ROOT_NAME)
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "") + creds.get("library_id", "")
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
        root_id = self._root_id(creds)
        node_id = self._resolve(creds, folder_id)
        folders_res = self._get(creds, f"{self._lib_api(creds)}/folders/{node_id}/folders")
        docs_res = self._get(creds, f"{self._lib_api(creds)}/folders/{node_id}/documents")
        folders = [self._folder_entry(e, folder_id) for e in folders_res.get("data", []) if e.get("name") != _TRASH_NAME]
        files = [self._doc_entry(e, folder_id) for e in docs_res.get("data", [])]
        current_folder = None
        if folder_id is not None:
            current_folder = FolderInfo(id=folder_id, name="", parent_id=None)
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash_id = self._trash_id(creds)
        folders_res = self._get(creds, f"{self._lib_api(creds)}/folders/{trash_id}/folders")
        docs_res = self._get(creds, f"{self._lib_api(creds)}/folders/{trash_id}/documents")
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._folder_entry(e, trash_id) for e in folders_res.get("data", [])],
            files=[self._doc_entry(e, trash_id) for e in docs_res.get("data", [])],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        created = self._call(creds, "POST", f"{self._lib_api(creds)}/folders",
                              json={"name": name, "parent_id": parent}).json()
        return self._folder_entry(created.get("data", created), parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "PATCH", f"{self._lib_api(creds)}/folders/{folder_id}", json={"name": name}).json()
        return self._folder_entry(updated.get("data", updated), None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        updated = self._call(creds, "PATCH", f"{self._lib_api(creds)}/folders/{folder_id}",
                              json={"parent_id": target}).json()
        return self._folder_entry(updated.get("data", updated), new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{self._lib_api(creds)}/folders/{folder_id}")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        creds2, _ = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{self._lib_api(creds)}/documents",
            headers=self._headers(creds2),
            data={"name": name, "folder_id": parent},
            files={"file": (name, content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"iManage upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        created = resp.json()
        return self._doc_entry(created.get("data", created), folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        result = self._get(creds, f"{self._lib_api(creds)}/documents/{file_id}")
        return self._doc_entry(result.get("data", result), None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "PATCH", f"{self._lib_api(creds)}/documents/{file_id}", json={"name": name}).json()
        return self._doc_entry(updated.get("data", updated), None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        updated = self._call(creds, "PATCH", f"{self._lib_api(creds)}/documents/{file_id}",
                              json={"folder_id": target}).json()
        return self._doc_entry(updated.get("data", updated), new_folder_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._lib_api(creds)}/documents/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._call(creds, "GET", f"{self._lib_api(creds)}/documents/{file_id}/download").content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._get(creds, f"{self._lib_api(creds)}/documents/{file_id}/versions")
        versions = result.get("data", [])
        if not versions:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id=file_id, version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True, updated_at=info.updated_at)]
        return [
            VersionInfo(id=v.get("id", str(i)), version_number=v.get("version", i + 1),
                        size_bytes=v.get("size"), content_type=None,
                        is_current=bool(v.get("is_current", i == 0)),
                        updated_at=self._parse_dt(v.get("edit_date")))
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        creds2, _ = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{self._lib_api(creds)}/documents/{file_id}/versions",
            headers=self._headers(creds2), files={"file": ("content", content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"iManage version upload failed ({resp.status_code})", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == file_id:
            return self.get_content(creds, file_id)
        return self._call(creds, "GET", f"{self._lib_api(creds)}/documents/{version_id}/download").content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        return self.create_version(creds, file_id, "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "PATCH", f"{self._lib_api(creds)}/folders/{folder_id}", json={"parent_id": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        updated = self._call(creds, "PATCH", f"{self._lib_api(creds)}/folders/{folder_id}",
                              json={"parent_id": root}).json()
        return self._folder_entry(updated.get("data", updated), None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "PATCH", f"{self._lib_api(creds)}/documents/{file_id}", json={"folder_id": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        updated = self._call(creds, "PATCH", f"{self._lib_api(creds)}/documents/{file_id}",
                              json={"folder_id": root}).json()
        return self._doc_entry(updated.get("data", updated), None)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        try:
            result = self._get(creds, f"{self._lib_api(creds)}/search", params={"q": query})
            entries = result.get("data", [])
            folders, files = [], []
            for e in entries:
                if e.get("wstype") == "folder" or "doc_number" not in e:
                    folders.append(self._folder_entry(e, None))
                else:
                    files.append(self._doc_entry(e, None))
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
                for e in self._get(creds, f"{self._lib_api(creds)}/folders/{node_id}/folders").get("data", []):
                    if e["id"] == trash_id:
                        continue
                    if q in e.get("name", "").lower():
                        found_folders.append(self._folder_entry(e, node_id))
                    walk(e["id"], depth + 1)
                for e in self._get(creds, f"{self._lib_api(creds)}/folders/{node_id}/documents").get("data", []):
                    if q in e.get("name", "").lower():
                        found_files.append(self._doc_entry(e, node_id))

            walk(root_id, 0)
            return found_folders, found_files
