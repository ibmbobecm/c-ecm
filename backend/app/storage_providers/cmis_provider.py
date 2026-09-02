"""IBM Content Navigator and SAP's Document Management Service, via the
OASIS CMIS 1.1 Browser Binding — a real, standardized protocol (not a
vendor-specific guess) that both products support as their primary
integration surface: IBM Content Navigator is a UI/integration layer that
fronts whatever CMIS-compliant repository sits behind it (FileNet, IBM
Content Manager, etc. all expose a CMIS endpoint IBM Content Navigator
itself talks to), and SAP's Document Management Service (part of SAP BTP)
is documented as CMIS-compliant.

UNVERIFIED — built against the OASIS CMIS 1.1 Browser Binding
specification, but there's no live IBM Content Navigator/SAP DMS
repository in this environment to test against. Confidence in the CMIS
verbs themselves (`cmisselector=children`, `cmisaction=createFolder`,
etc.) is real (it's a ratified, cross-vendor standard), but confidence in
which exact repository id / base path a given IBM Content Navigator or
SAP DMS deployment exposes its CMIS Browser Binding root at is lower,
since that's deployment-specific — collected as a `repository_id`
connection field rather than assumed.
"""

import threading
from urllib.parse import quote

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


class _CMISProvider(StorageProvider):
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    def _browser_url(self, creds: dict) -> str:
        return f"{creds['base_url'].rstrip('/')}/{creds['repository_id']}"

    def _headers(self, creds: dict) -> dict:
        import base64
        token = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _get(self, creds: dict, params: dict) -> dict:
        try:
            resp = requests.get(self._browser_url(creds), headers=self._headers(creds), params=params, timeout=30)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the CMIS repository: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("Invalid credentials", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"CMIS error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _post(self, creds: dict, data: dict, files: dict | None = None) -> dict:
        try:
            resp = requests.post(self._browser_url(creds), headers=self._headers(creds), data=data, files=files, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the CMIS repository: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"CMIS error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("base_url", "CMIS browser binding URL", "https://host/p8cmis/browser"),
            ConfigField("repository_id", "Repository ID"),
        ]

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        repository_id = (config.get("repository_id") or "").strip()
        if not base_url or not repository_id:
            raise ProviderError("CMIS browser URL and repository ID are required", status_code=400)
        creds = {"username": username, "password": password, "base_url": base_url, "repository_id": repository_id}
        try:
            self._get(creds, {"cmisselector": "repositoryInfo"})
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    def _root_folder_id(self, creds: dict) -> str:
        info = self._get(creds, {"cmisselector": "repositoryInfo"})
        # Some servers nest this under the repository id key, some return it flat.
        repo_info = info.get(creds["repository_id"], info)
        return repo_info["rootFolderId"]

    @staticmethod
    def _props(obj: dict) -> dict:
        return obj.get("succinctProperties", obj.get("properties", {}))

    def _obj_to_folder(self, obj: dict, parent_id: str | None) -> FolderInfo:
        p = self._props(obj)
        return FolderInfo(id=p["cmis:objectId"], name=p["cmis:name"], parent_id=parent_id, created_at=None)

    def _obj_to_file(self, obj: dict, parent_id: str | None) -> FileInfo:
        p = self._props(obj)
        return FileInfo(id=p["cmis:objectId"], name=p["cmis:name"], folder_id=parent_id,
                         version_number=1, size_bytes=p.get("cmis:contentStreamLength"),
                         content_type=p.get("cmis:contentStreamMimeType"), updated_at=None)

    def _children(self, creds: dict, folder_id: str) -> list[dict]:
        result = self._get(creds, {"cmisselector": "children", "objectId": folder_id})
        return result.get("objects", [])

    def _find_or_create_child_folder(self, creds: dict, parent_id: str, name: str) -> str:
        for entry in self._children(creds, parent_id):
            obj = entry.get("object", entry)
            p = self._props(obj)
            if p.get("cmis:baseTypeId") == "cmis:folder" and p.get("cmis:name") == name:
                return p["cmis:objectId"]
        created = self._post(creds, {
            "cmisaction": "createFolder", "objectId": parent_id,
            "propertyId[0]": "cmis:name", "propertyValue[0]": name,
            "propertyId[1]": "cmis:objectTypeId", "propertyValue[1]": "cmis:folder",
        })
        return self._props(created)["cmis:objectId"]

    def _root_id(self, creds: dict) -> str:
        cache_key = creds["base_url"] + creds["repository_id"]
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            repo_root = self._root_folder_id(creds)
            root_id = self._find_or_create_child_folder(creds, repo_root, _APP_ROOT_NAME)
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_id(self, creds: dict) -> str:
        cache_key = creds["base_url"] + creds["repository_id"]
        cached = self._trash_id_cache.get(cache_key)
        if cached:
            return cached
        with self._trash_id_lock:
            cached = self._trash_id_cache.get(cache_key)
            if cached:
                return cached
            root = self._root_id(creds)
            trash_id = self._find_or_create_child_folder(creds, root, _TRASH_NAME)
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def _resolve(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_id(creds)

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = self._resolve(creds, folder_id)
        entries = self._children(creds, node_id)
        folders, files = [], []
        for entry in entries:
            obj = entry.get("object", entry)
            p = self._props(obj)
            if p.get("cmis:baseTypeId") == "cmis:folder":
                if p.get("cmis:name") != _TRASH_NAME:
                    folders.append(self._obj_to_folder(obj, folder_id))
            else:
                files.append(self._obj_to_file(obj, folder_id))
        current_folder = None
        if folder_id is not None:
            current_folder = FolderInfo(id=folder_id, name="", parent_id=None)
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash_id = self._trash_id(creds)
        entries = self._children(creds, trash_id)
        folders, files = [], []
        for entry in entries:
            obj = entry.get("object", entry)
            p = self._props(obj)
            (folders if p.get("cmis:baseTypeId") == "cmis:folder" else files).append(
                self._obj_to_folder(obj, trash_id) if p.get("cmis:baseTypeId") == "cmis:folder"
                else self._obj_to_file(obj, trash_id)
            )
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=folders, files=files)

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        created = self._post(creds, {
            "cmisaction": "createFolder", "objectId": parent,
            "propertyId[0]": "cmis:name", "propertyValue[0]": name,
            "propertyId[1]": "cmis:objectTypeId", "propertyValue[1]": "cmis:folder",
        })
        return self._obj_to_folder(created, parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._post(creds, {
            "cmisaction": "update", "objectId": folder_id,
            "propertyId[0]": "cmis:name", "propertyValue[0]": name,
        })
        return self._obj_to_folder(updated, None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        target = self._resolve(creds, new_parent_id)
        updated = self._post(creds, {"cmisaction": "move", "objectId": folder_id, "targetFolderId": target})
        return self._obj_to_folder(updated, new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._post(creds, {"cmisaction": "deleteTree", "objectId": folder_id, "allVersions": "true"})

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        created = self._post(creds, {
            "cmisaction": "createDocument", "objectId": parent,
            "propertyId[0]": "cmis:name", "propertyValue[0]": name,
            "propertyId[1]": "cmis:objectTypeId", "propertyValue[1]": "cmis:document",
        }, files={"content": (name, content, content_type)})
        return self._obj_to_file(created, folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        obj = self._get(creds, {"cmisselector": "object", "objectId": file_id})
        return self._obj_to_file(obj, None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._post(creds, {
            "cmisaction": "update", "objectId": file_id,
            "propertyId[0]": "cmis:name", "propertyValue[0]": name,
        })
        return self._obj_to_file(updated, None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        target = self._resolve(creds, new_folder_id)
        updated = self._post(creds, {"cmisaction": "move", "objectId": file_id, "targetFolderId": target})
        return self._obj_to_file(updated, new_folder_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._post(creds, {"cmisaction": "delete", "objectId": file_id, "allVersions": "true"})

    def get_content(self, creds: dict, file_id: str) -> bytes:
        try:
            resp = requests.get(self._browser_url(creds), headers=self._headers(creds),
                                 params={"cmisselector": "content", "objectId": file_id}, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the CMIS repository: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError("Content not found", status_code=404)
        return resp.content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._get(creds, {"cmisselector": "versions", "objectId": file_id})
        versions = result if isinstance(result, list) else result.get("objects", [])
        out = []
        for i, obj in enumerate(versions):
            v = obj.get("object", obj)
            p = self._props(v)
            out.append(VersionInfo(
                id=p["cmis:objectId"], version_number=i + 1, size_bytes=p.get("cmis:contentStreamLength"),
                content_type=p.get("cmis:contentStreamMimeType"),
                is_current=bool(p.get("cmis:isLatestVersion", i == 0)), updated_at=None,
            ))
        if not out:
            info = self.get_file(creds, file_id)
            out = [VersionInfo(id=file_id, version_number=1, size_bytes=info.size_bytes,
                                content_type=info.content_type, is_current=True, updated_at=None)]
        return out

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        # CMIS's native path is checkOut -> update content on the PWC ->
        # checkIn; simplified here to a direct content update via
        # cmisaction=setContent, which every CMIS 1.1 server is required
        # to support even where check-out/check-in policy isn't enforced.
        updated = self._post(creds, {"cmisaction": "setContent", "objectId": file_id, "overwriteFlag": "true"},
                              files={"content": ("content", content, content_type)})
        return self._obj_to_file(updated, None)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        return self.get_content(creds, version_id)

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        info = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, info.content_type or "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._post(creds, {"cmisaction": "move", "objectId": folder_id, "targetFolderId": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        updated = self._post(creds, {"cmisaction": "move", "objectId": folder_id, "targetFolderId": root})
        return self._obj_to_folder(updated, None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._post(creds, {"cmisaction": "move", "objectId": file_id, "targetFolderId": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        updated = self._post(creds, {"cmisaction": "move", "objectId": file_id, "targetFolderId": root})
        return self._obj_to_file(updated, None)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        # CMIS defines a real SQL-like query language (cmisselector=query)
        # — used here rather than a client-side walk, since unlike several
        # other providers in this codebase, this is a standardized part of
        # the spec every CMIS 1.1 server implements.
        escaped = query.replace("'", "''")
        result = self._post(creds, {
            "cmisaction": "query",
            "statement": f"SELECT * FROM cmis:document WHERE cmis:name LIKE '%{escaped}%'",
            "searchAllVersions": "false",
        })
        entries = result.get("results", result.get("objects", []))
        folders, files = [], []
        for entry in entries:
            obj = entry.get("object", entry)
            p = self._props(obj)
            if p.get("cmis:baseTypeId") == "cmis:folder":
                folders.append(self._obj_to_folder(obj, None))
            else:
                files.append(self._obj_to_file(obj, None))
        return folders, files


class IBMContentNavigatorProvider(_CMISProvider):
    key = "ibm_content_navigator"
    display_name = "IBM Content Navigator"

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("base_url", "CMIS browser binding URL", "https://host:9443/wsi/cmis/browser"),
            ConfigField("repository_id", "Repository/object store ID"),
        ]


class SAPDocumentManagementProvider(_CMISProvider):
    key = "sap_dms"
    display_name = "SAP Document Management System"

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("base_url", "CMIS browser binding URL", "https://host/DocumentManagementService/cmis/browser"),
            ConfigField("repository_id", "Repository ID"),
        ]
