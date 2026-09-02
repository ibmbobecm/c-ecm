"""ShareVault (virtual data rooms), via a REST API assumed to follow
ShareVault's general documented conventions (JSON, token auth against a
per-connection ShareVault site URL).

UNVERIFIED and LOW CONFIDENCE — same caveat as this codebase's Firmex
adapter: ShareVault's public API documentation is sparse (data-room
products grant API access per-customer rather than publishing a broad
public reference). Endpoint paths/field names below are a best-effort
reconstruction from the general "vault > folder > document" model common
to this product category, not a verified spec. Trash is emulated via a
hidden folder; version history is emulated as a single current version —
both for the same "no confidently-known native endpoint" reason as
Firmex.
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


class ShareVaultProvider(StorageProvider):
    key = "sharevault"
    display_name = "ShareVault"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "ShareVault site URL", "https://yourcompany.sharevault.com")]

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _login(self, creds: dict) -> str:
        resp = requests.post(f"{self._base_url(creds)}/api/v1/sessions",
                              json={"email": creds["username"], "password": creds["password"]}, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Invalid ShareVault credentials", status_code=401)
        token = resp.json().get("token", resp.json().get("access_token"))
        if not token:
            raise ProviderError("Invalid ShareVault credentials", status_code=401)
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
            raise ProviderError(f"Couldn't reach ShareVault: {exc}", status_code=502)
        if resp.status_code == 401:
            token = self._login(creds)
            creds["_token"] = token
            headers["Authorization"] = f"Bearer {token}"
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"ShareVault error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderError("ShareVault site URL is required", status_code=400)
        creds = {"username": username, "password": password, "base_url": base_url}
        try:
            self._login(creds)
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    def _vault_id(self, creds: dict) -> str:
        vaults = self._request(creds, "GET", "/api/v1/vaults").json()
        entries = vaults if isinstance(vaults, list) else vaults.get("data", [])
        if not entries:
            raise ProviderError("No ShareVault vault is available on this account", status_code=502)
        return str(entries[0]["id"])

    def _entry_to_folder(self, e: dict, parent_id) -> FolderInfo:
        return FolderInfo(id=str(e.get("id")), name=e.get("name", ""), parent_id=parent_id)

    def _entry_to_file(self, e: dict, parent_id) -> FileInfo:
        return FileInfo(id=str(e.get("id")), name=e.get("name", ""), folder_id=parent_id, version_number=1,
                         size_bytes=e.get("size"), content_type=e.get("contentType"))

    def _find_or_create_child(self, creds: dict, vault_id: str, parent_id: str | None, name: str) -> str:
        result = self._request(creds, "GET", f"/api/v1/vaults/{vault_id}/folders",
                                params={"parentId": parent_id or ""}).json()
        entries = result if isinstance(result, list) else result.get("data", [])
        for e in entries:
            if e.get("name") == name:
                return str(e.get("id"))
        created = self._request(creds, "POST", f"/api/v1/vaults/{vault_id}/folders",
                                 json={"name": name, "parentId": parent_id}).json()
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
            vault_id = self._vault_id(creds)
            creds["_vault_id"] = vault_id
            root_id = self._find_or_create_child(creds, vault_id, None, _APP_ROOT_NAME)
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
            trash_id = self._find_or_create_child(creds, creds["_vault_id"], root, _TRASH_NAME)
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def _resolve(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_id(creds)

    def _vault(self, creds: dict) -> str:
        self._root_id(creds)
        return creds["_vault_id"]

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        vault = self._vault(creds)
        node = self._resolve(creds, folder_id)
        fres = self._request(creds, "GET", f"/api/v1/vaults/{vault}/folders", params={"parentId": node}).json()
        ffiles = self._request(creds, "GET", f"/api/v1/vaults/{vault}/documents", params={"folderId": node}).json()
        f_entries = fres if isinstance(fres, list) else fres.get("data", [])
        file_entries = ffiles if isinstance(ffiles, list) else ffiles.get("data", [])
        folders = [self._entry_to_folder(e, folder_id) for e in f_entries if e.get("name") != _TRASH_NAME]
        files = [self._entry_to_file(e, folder_id) for e in file_entries]
        current_folder = FolderInfo(id=node, name="", parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        vault = self._vault(creds)
        trash_id = self._trash_id(creds)
        fres = self._request(creds, "GET", f"/api/v1/vaults/{vault}/folders", params={"parentId": trash_id}).json()
        ffiles = self._request(creds, "GET", f"/api/v1/vaults/{vault}/documents", params={"folderId": trash_id}).json()
        f_entries = fres if isinstance(fres, list) else fres.get("data", [])
        file_entries = ffiles if isinstance(ffiles, list) else ffiles.get("data", [])
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=[self._entry_to_folder(e, trash_id) for e in f_entries],
                               files=[self._entry_to_file(e, trash_id) for e in file_entries])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        vault = self._vault(creds)
        parent = self._resolve(creds, parent_id)
        created = self._request(creds, "POST", f"/api/v1/vaults/{vault}/folders",
                                 json={"name": name, "parentId": parent}).json()
        return self._entry_to_folder(created.get("data", created), parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        vault = self._vault(creds)
        updated = self._request(creds, "PUT", f"/api/v1/vaults/{vault}/folders/{folder_id}",
                                 json={"name": name}).json()
        return self._entry_to_folder(updated.get("data", updated), None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        vault = self._vault(creds)
        target = self._resolve(creds, new_parent_id)
        updated = self._request(creds, "PUT", f"/api/v1/vaults/{vault}/folders/{folder_id}",
                                 json={"parentId": target}).json()
        return self._entry_to_folder(updated.get("data", updated), new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        vault = self._vault(creds)
        self._request(creds, "DELETE", f"/api/v1/vaults/{vault}/folders/{folder_id}")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        vault = self._vault(creds)
        parent = self._resolve(creds, folder_id)
        created = self._request(creds, "POST", f"/api/v1/vaults/{vault}/documents",
                                 data={"folderId": parent, "name": name},
                                 files={"file": (name, content, content_type)}).json()
        return self._entry_to_file(created.get("data", created), folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        vault = self._vault(creds)
        result = self._request(creds, "GET", f"/api/v1/vaults/{vault}/documents/{file_id}").json()
        return self._entry_to_file(result.get("data", result), None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        vault = self._vault(creds)
        updated = self._request(creds, "PUT", f"/api/v1/vaults/{vault}/documents/{file_id}",
                                 json={"name": name}).json()
        return self._entry_to_file(updated.get("data", updated), None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        vault = self._vault(creds)
        target = self._resolve(creds, new_folder_id)
        updated = self._request(creds, "PUT", f"/api/v1/vaults/{vault}/documents/{file_id}",
                                 json={"folderId": target}).json()
        return self._entry_to_file(updated.get("data", updated), new_folder_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        vault = self._vault(creds)
        self._request(creds, "DELETE", f"/api/v1/vaults/{vault}/documents/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        vault = self._vault(creds)
        return self._request(creds, "GET", f"/api/v1/vaults/{vault}/documents/{file_id}/content").content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        info = self.get_file(creds, file_id)
        return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                             content_type=info.content_type, is_current=True)]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        vault = self._vault(creds)
        info = self.get_file(creds, file_id)
        self._request(creds, "POST", f"/api/v1/vaults/{vault}/documents/{file_id}/content",
                       files={"file": (info.name, content, content_type)})
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        return self.get_content(creds, file_id)

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        return self.get_file(creds, file_id)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        vault = self._vault(creds)
        target = self._trash_id(creds)
        self._request(creds, "PUT", f"/api/v1/vaults/{vault}/folders/{folder_id}", json={"parentId": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        vault = self._vault(creds)
        root = self._root_id(creds)
        updated = self._request(creds, "PUT", f"/api/v1/vaults/{vault}/folders/{folder_id}",
                                 json={"parentId": root}).json()
        return self._entry_to_folder(updated.get("data", updated), None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        vault = self._vault(creds)
        target = self._trash_id(creds)
        self._request(creds, "PUT", f"/api/v1/vaults/{vault}/documents/{file_id}", json={"folderId": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        vault = self._vault(creds)
        root = self._root_id(creds)
        updated = self._request(creds, "PUT", f"/api/v1/vaults/{vault}/documents/{file_id}",
                                 json={"folderId": root}).json()
        return self._entry_to_file(updated.get("data", updated), None)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        vault = self._vault(creds)
        root = self._root_id(creds)
        trash = self._trash_id(creds)
        found_folders: list[FolderInfo] = []
        found_files: list[FileInfo] = []
        q = query.lower()

        def walk(node_id, depth):
            if depth > 6 or node_id == trash:
                return
            fres = self._request(creds, "GET", f"/api/v1/vaults/{vault}/folders", params={"parentId": node_id}).json()
            f_entries = fres if isinstance(fres, list) else fres.get("data", [])
            for e in f_entries:
                if str(e.get("id")) == trash:
                    continue
                if q in e.get("name", "").lower():
                    found_folders.append(self._entry_to_folder(e, node_id))
                walk(str(e.get("id")), depth + 1)
            ffiles = self._request(creds, "GET", f"/api/v1/vaults/{vault}/documents", params={"folderId": node_id}).json()
            file_entries = ffiles if isinstance(ffiles, list) else ffiles.get("data", [])
            for e in file_entries:
                if q in e.get("name", "").lower():
                    found_files.append(self._entry_to_file(e, node_id))

        walk(root, 0)
        return found_folders, found_files
