"""Veeva Vault, via its documented Vault REST API (JSON, session-id auth
against a per-connection Vault domain — `https://{vault}.veevavault.com`).

UNVERIFIED — no live Vault in this environment to test against. Written
from Veeva's documented Vault REST API conventions (`/api/{version}/auth`
session login, `/api/{version}/objects/binders` and `/api/{version}/objects/
documents` endpoints). Vault's real object model is Documents (versioned
files with structured metadata) organized by Binders (folder-like
containers) — both used directly since Vault's binder/document split
maps cleanly onto this app's folder/file model. Native document
versioning is used directly; Vault's own "Recycle Bin" listing endpoint
isn't confidently known, so trash is emulated via a hidden binder.
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
_API_VERSION = "v23.1"


class VeevaVaultProvider(StorageProvider):
    key = "veeva_vault"
    display_name = "Veeva Vault"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Vault domain URL", "https://yourvault.veevavault.com")]

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _api(self, creds: dict) -> str:
        return f"{self._base_url(creds)}/api/{_API_VERSION}"

    def _login(self, creds: dict) -> str:
        resp = requests.post(f"{self._api(creds)}/auth",
                              data={"username": creds["username"], "password": creds["password"]}, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Invalid Veeva Vault credentials", status_code=401)
        result = resp.json()
        if result.get("responseStatus") != "SUCCESS":
            raise ProviderError("Invalid Veeva Vault credentials", status_code=401)
        return result["sessionId"]

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> requests.Response:
        session_id = creds.get("_session_id")
        if not session_id:
            session_id = self._login(creds)
            creds["_session_id"] = session_id
        url = self._api(creds) + path
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = session_id
        try:
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach Veeva Vault: {exc}", status_code=502)
        if resp.status_code == 401:
            session_id = self._login(creds)
            creds["_session_id"] = session_id
            headers["Authorization"] = session_id
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Veeva Vault error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderError("Vault domain URL is required", status_code=400)
        creds = {"username": username, "password": password, "base_url": base_url}
        try:
            self._login(creds)
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    def _find_or_create_binder(self, creds: dict, parent_id: str | None, name: str) -> str:
        params = {"parent_id__v": parent_id} if parent_id else {}
        result = self._request(creds, "GET", "/objects/binders", params=params).json()
        for b in result.get("binders__v", result.get("data", [])):
            if b.get("name__v") == name:
                return str(b.get("id"))
        created = self._request(creds, "POST", "/objects/binders",
                                 data={"name__v": name, "parent_id__v": parent_id or ""}).json()
        return str(created.get("id"))

    def _root_id(self, creds: dict) -> str:
        cache_key = self._base_url(creds) + creds["username"]
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            root_id = self._find_or_create_binder(creds, None, _APP_ROOT_NAME)
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
            trash_id = self._find_or_create_binder(creds, root, _TRASH_NAME)
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def _resolve(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_id(creds)

    def _binder_entry(self, b: dict, parent_id) -> FolderInfo:
        return FolderInfo(id=str(b.get("id")), name=b.get("name__v", ""), parent_id=parent_id)

    def _doc_entry(self, d: dict, parent_id) -> FileInfo:
        return FileInfo(id=str(d.get("id")), name=d.get("name__v", d.get("filename__v", "")), folder_id=parent_id,
                         version_number=int(d.get("major_version_number__v", 1) or 1),
                         size_bytes=d.get("size__v"), content_type=d.get("format__v"))

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        node = self._resolve(creds, folder_id)
        result = self._request(creds, "GET", "/objects/binders", params={"parent_id__v": node}).json()
        binders = result.get("binders__v", result.get("data", []))
        docs_result = self._request(creds, "GET", "/objects/documents", params={"binder_id__v": node}).json()
        docs = docs_result.get("documents", docs_result.get("data", []))
        folders = [self._binder_entry(b, folder_id) for b in binders if b.get("name__v") != _TRASH_NAME]
        files = [self._doc_entry(d, folder_id) for d in docs]
        current_folder = FolderInfo(id=node, name="", parent_id=None) if folder_id is not None else None
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash_id = self._trash_id(creds)
        result = self._request(creds, "GET", "/objects/binders", params={"parent_id__v": trash_id}).json()
        binders = result.get("binders__v", result.get("data", []))
        docs_result = self._request(creds, "GET", "/objects/documents", params={"binder_id__v": trash_id}).json()
        docs = docs_result.get("documents", docs_result.get("data", []))
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=[self._binder_entry(b, trash_id) for b in binders],
                               files=[self._doc_entry(d, trash_id) for d in docs])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        created = self._request(creds, "POST", "/objects/binders",
                                 data={"name__v": name, "parent_id__v": parent}).json()
        return self._binder_entry(created, parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        self._request(creds, "PUT", f"/objects/binders/{folder_id}", data={"name__v": name})
        return FolderInfo(id=folder_id, name=name, parent_id=None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        self._request(creds, "PUT", f"/objects/binders/{folder_id}", data={"parent_id__v": target})
        return FolderInfo(id=folder_id, name="", parent_id=new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "DELETE", f"/objects/binders/{folder_id}")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        created = self._request(creds, "POST", "/objects/documents",
                                 data={"name__v": name, "type__v": "Unclassified", "binder_id__v": parent},
                                 files={"file": (name, content, content_type)}).json()
        return self._doc_entry(created, folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        result = self._request(creds, "GET", f"/objects/documents/{file_id}").json()
        doc = result.get("document", result)
        return self._doc_entry(doc, None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        self._request(creds, "PUT", f"/objects/documents/{file_id}", data={"name__v": name})
        return self.get_file(creds, file_id)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        self._request(creds, "PUT", f"/objects/documents/{file_id}", data={"binder_id__v": target})
        info = self.get_file(creds, file_id)
        info.folder_id = new_folder_id
        return info

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "DELETE", f"/objects/documents/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._request(creds, "GET", f"/objects/documents/{file_id}/file").content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._request(creds, "GET", f"/objects/documents/{file_id}/versions").json()
        versions = result.get("versions", result.get("data", []))
        if not versions:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True)]
        return [
            VersionInfo(id=v.get("value", str(i)), version_number=i + 1, size_bytes=None,
                        content_type=None, is_current=(i == 0))
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        info = self.get_file(creds, file_id)
        self._request(creds, "POST", f"/objects/documents/{file_id}",
                       files={"file": (info.name, content, content_type)})
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        return self._request(creds, "GET", f"/objects/documents/{file_id}/versions/{version_id}/file").content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        info = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, info.content_type or "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._request(creds, "PUT", f"/objects/binders/{folder_id}", data={"parent_id__v": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        self._request(creds, "PUT", f"/objects/binders/{folder_id}", data={"parent_id__v": root})
        return FolderInfo(id=folder_id, name="", parent_id=None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._request(creds, "PUT", f"/objects/documents/{file_id}", data={"binder_id__v": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        self._request(creds, "PUT", f"/objects/documents/{file_id}", data={"binder_id__v": root})
        return self.get_file(creds, file_id)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        try:
            escaped = query.replace("'", "''")
            result = self._request(creds, "POST", "/query",
                                    data={"q": f"SELECT id, name__v, size__v FROM documents WHERE name__v CONTAINS '{escaped}'"}).json()
            docs = result.get("data", [])
            return [], [self._doc_entry(d, None) for d in docs]
        except ProviderError:
            root = self._root_id(creds)
            trash = self._trash_id(creds)
            found_folders: list[FolderInfo] = []
            found_files: list[FileInfo] = []
            q = query.lower()

            def walk(node_id, depth):
                if depth > 6 or node_id == trash:
                    return
                result = self._request(creds, "GET", "/objects/binders", params={"parent_id__v": node_id}).json()
                for b in result.get("binders__v", result.get("data", [])):
                    if str(b.get("id")) == trash:
                        continue
                    if q in b.get("name__v", "").lower():
                        found_folders.append(self._binder_entry(b, node_id))
                    walk(str(b.get("id")), depth + 1)
                docs_result = self._request(creds, "GET", "/objects/documents", params={"binder_id__v": node_id}).json()
                for d in docs_result.get("documents", docs_result.get("data", [])):
                    if q in d.get("name__v", "").lower():
                        found_files.append(self._doc_entry(d, node_id))

            walk(root, 0)
            return found_folders, found_files
