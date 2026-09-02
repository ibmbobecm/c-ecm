"""NetDocuments — a document/email management system built specifically for
law firms and other regulated professional-services organizations, via its
documented REST API v2 (JSON over HTTPS), OAuth2 authorization-code flow.

UNVERIFIED — written from NetDocuments' published REST API v2 reference,
without a live NetDocuments vault in this environment to test against.
NetDocuments deployments are, more than almost any other backend in this
codebase, heavily customized per law firm (mandatory document "profile"
attributes, per-firm cabinet layout, per-firm workflow rules), so even a
byte-perfect implementation of the documented API surface can still fail
against a specific firm's vault in ways that are impossible to predict
generically. Three areas are the biggest sources of uncertainty, flagged
again inline at the relevant method:

1. Folder-listing query syntax. NetDocuments' REST API is search-first —
   there's no confidently-documented "list the direct children of folder X"
   endpoint the way Box/Google/OneDrive have — so get_children()/
   list_trash()/search() all issue a structured Search query
   (`cabinet:{id} folder-id:{id}`) and parse the response defensively
   (checking several plausible field names for id/name/type) rather than
   assuming one exact response envelope.
2. Document "profile" attributes. Every document created in NetDocuments
   carries profile metadata, and real deployments almost always make
   several profile fields mandatory beyond name/folder (client/matter
   numbers, document type, ...), configured per firm — create_document()
   below sends only the two fields documented as universal (name, folder),
   which will likely be rejected by a firm's actual vault until its
   specific required profile fields are added here.
3. Recycle bin / trash. NetDocuments has a native per-cabinet recycle bin,
   but no confidently-documented endpoint to list or restore from it
   generically, so — the same approach used elsewhere in this codebase for
   backends whose native trash API isn't confidently known (Dropbox,
   Laserfiche, ShareFile) — trash here is emulated by moving items into a
   dedicated hidden "_C-ECM-Trash" folder under this app's own root folder,
   rather than touching NetDocuments' real recycle bin at all.

Versioning is real and native (NetDocuments documents are inherently
versioned), so list_versions/create_version/get_version_content are built
against actual version endpoints. There's no confidently-documented "make
this old version current" endpoint though, so restore_version downloads the
old version's bytes and re-uploads them as a brand-new version instead.

An account can see multiple Cabinets; this provider picks the first one
returned by `GET /Cabinets` and remembers it in creds (`cabinet_id`) — fine
for the common single-cabinet case, but a multi-cabinet account would need
a cabinet picker this app doesn't have yet. NetDocuments' REST API v2 also
has no confidently-documented dedicated "who am I" endpoint, so the
connected cabinet's own name is used as the display identity instead
(good enough to tell connections apart, though it names the vault rather
than the actual signed-in person).
"""

import json
import threading
import time
from datetime import datetime

import requests

from .. import settings_store
from .base import (
    AuthMode,
    BreadcrumbEntry,
    FileInfo,
    FolderContents,
    FolderInfo,
    ProviderError,
    StorageProvider,
    VersionInfo,
)

_APP_ROOT_NAME = "C-ECM"
_TRASH_FOLDER_NAME = "_C-ECM-Trash"


class NetDocumentsProvider(StorageProvider):
    key = "netdocuments"
    display_name = "NetDocuments"
    auth_mode = AuthMode.OAUTH

    # Later NetDocuments deployments are also branded api.netdocuments.com,
    # but api.vault.netvoyage.com is the long-standing documented host and
    # is what's used here per the task's own guidance.
    _AUTH_URL = "https://vault.netvoyage.com/neWeb2/OAuth.aspx"
    _API = "https://api.vault.netvoyage.com/v2"

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("netdocuments_client_id", ""),
            settings_store.get_setting("netdocuments_client_secret", ""),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret = self._client()
        return bool(client_id and client_secret)

    # --- oauth ---
    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret = self._client()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "full",
        }
        return self._AUTH_URL + "?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret = self._client()
        resp = requests.post(f"{self._API}/OAuth/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "client_secret": client_secret, "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"NetDocuments token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + tok.get("expires_in", 3600),
        }
        cabinets = self._get(creds, f"{self._API}/Cabinets")
        cabinet_list = cabinets if isinstance(cabinets, list) else (
            cabinets.get("results") or cabinets.get("Results") or cabinets.get("Cabinets") or cabinets.get("value") or []
        )
        if not cabinet_list:
            raise ProviderError("No NetDocuments cabinet is available on this account", status_code=502)
        first = cabinet_list[0]
        creds["cabinet_id"] = str(first.get("id") or first.get("cabinetId") or first.get("CabinetId"))
        creds["identity"] = first.get("name") or first.get("Name") or "NetDocuments account"
        return creds

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        if creds.get("expires_at", 0) > time.time() + 30:
            return creds, False
        if not creds.get("refresh_token"):
            raise ProviderError("Session expired — please reconnect", status_code=401)
        client_id, client_secret = self._client()
        resp = requests.post(f"{self._API}/OAuth/token", data={
            "grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
            "client_id": client_id, "client_secret": client_secret,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        return creds, True

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "NetDocuments account")

    # --- low-level HTTP helpers ---
    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"NetDocuments error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds, _ = self.refresh_if_needed(creds)
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"NetDocuments error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    @staticmethod
    def _parse_dt(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None

    # --- search-backed listing (see module docstring, point 1) ---
    def _search_raw(self, creds: dict, query: str) -> dict:
        return self._call(creds, "GET", f"{self._API}/Search", params={"q": query, "highlights": "false"}).json()

    @staticmethod
    def _extract_hits(result) -> list[dict]:
        if isinstance(result, list):
            return result
        if not isinstance(result, dict):
            return []
        for key in ("results", "Results", "hits", "Hits", "documents", "Documents", "items", "value"):
            val = result.get(key)
            if isinstance(val, list):
                return val
        return []

    @staticmethod
    def _is_folder_entry(e: dict) -> bool:
        t = str(e.get("type") or e.get("Type") or "").lower()
        if t:
            return t == "folder"
        if "isFolder" in e:
            return bool(e["isFolder"])
        # No explicit type field — fall back to a structural hint: entries
        # with a document/envelope id are documents, everything else is
        # treated as a folder.
        return not any(k in e for k in ("documentId", "docId", "envId"))

    @staticmethod
    def _entry_id(e: dict) -> str:
        return str(e.get("id") or e.get("folderId") or e.get("documentId") or e.get("docId") or e.get("envId"))

    @staticmethod
    def _entry_name(e: dict) -> str:
        return e.get("name") or e.get("Name") or e.get("title") or e.get("docName") or ""

    def _entry_parent_id(self, e: dict, root_id: str) -> str | None:
        parent = e.get("parent") or e.get("parentId") or e.get("folder") or e.get("folderId")
        parent = str(parent) if parent is not None else None
        return None if parent == root_id else parent

    def _entry_to_folder(self, e: dict, root_id: str) -> FolderInfo:
        return FolderInfo(
            id=self._entry_id(e), name=self._entry_name(e), parent_id=self._entry_parent_id(e, root_id),
            created_at=self._parse_dt(e.get("created") or e.get("createdDate")),
        )

    def _entry_to_file(self, e: dict, root_id: str) -> FileInfo:
        size = e.get("size") or e.get("fileSize") or e.get("docSize")
        version = e.get("version") or e.get("versionNumber") or 1
        return FileInfo(
            id=self._entry_id(e), name=self._entry_name(e), folder_id=self._entry_parent_id(e, root_id),
            version_number=int(version) if version else 1,
            size_bytes=int(size) if size else None,
            content_type=e.get("mimeType") or e.get("contentType"),
            updated_at=self._parse_dt(e.get("lastModified") or e.get("modifiedDate")),
        )

    def _list_children_entries(self, creds: dict, folder_id: str) -> list[dict]:
        cabinet_id = creds["cabinet_id"]
        result = self._search_raw(creds, f"cabinet:{cabinet_id} folder-id:{folder_id}")
        return self._extract_hits(result)

    def _folder_meta(self, creds: dict, folder_id: str) -> dict:
        return self._call(creds, "GET", f"{self._API}/folder/{folder_id}").json()

    # --- app-root / trash-root folder bookkeeping ---
    def _root_id(self, creds: dict) -> str:
        cache_key = f"{creds.get('cabinet_id', '')}:{creds.get('identity', '')}"
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        # This provider instance is a process-wide singleton (registry.py)
        # and FastAPI runs sync handlers in a real thread pool, so without a
        # lock several concurrent first-requests for a newly-connected
        # account would each see an empty cache and each create their own
        # duplicate "C-ECM" root folder. Double-checked locking: re-test the
        # cache after acquiring the lock, since another thread may have
        # populated it while this one waited.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            cabinet_id = creds["cabinet_id"]
            for e in self._list_children_entries(creds, cabinet_id):
                if self._is_folder_entry(e) and self._entry_name(e) == _APP_ROOT_NAME:
                    root_id = self._entry_id(e)
                    self._root_id_cache[cache_key] = root_id
                    return root_id
            created = self._call(creds, "POST", f"{self._API}/folder", json={
                "cabinet": cabinet_id, "parent": cabinet_id, "name": _APP_ROOT_NAME,
            }).json()
            root_id = self._entry_id(created)
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_id(self, creds: dict) -> str:
        cache_key = f"{creds.get('cabinet_id', '')}:{creds.get('identity', '')}"
        cached = self._trash_id_cache.get(cache_key)
        if cached:
            return cached
        with self._trash_id_lock:
            cached = self._trash_id_cache.get(cache_key)
            if cached:
                return cached
            root_id = self._root_id(creds)
            for e in self._list_children_entries(creds, root_id):
                if self._is_folder_entry(e) and self._entry_name(e) == _TRASH_FOLDER_NAME:
                    trash_id = self._entry_id(e)
                    self._trash_id_cache[cache_key] = trash_id
                    return trash_id
            created = self._call(creds, "POST", f"{self._API}/folder", json={
                "cabinet": creds["cabinet_id"], "parent": root_id, "name": _TRASH_FOLDER_NAME,
            }).json()
            trash_id = self._entry_id(created)
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    # --- folders ---
    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        trash_id = self._trash_id(creds)
        node_id = folder_id or root_id
        entries = self._list_children_entries(creds, node_id)
        folders = [
            self._entry_to_folder(e, root_id) for e in entries
            if self._is_folder_entry(e) and self._entry_id(e) != trash_id
        ]
        files = [self._entry_to_file(e, root_id) for e in entries if not self._is_folder_entry(e)]
        current_folder = None
        if folder_id is not None:
            current_folder = self._entry_to_folder(self._folder_meta(creds, node_id), root_id)
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Cabinet")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash_id = self._trash_id(creds)
        entries = self._list_children_entries(creds, trash_id)
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._entry_to_folder(e, trash_id) for e in entries if self._is_folder_entry(e)],
            files=[self._entry_to_file(e, trash_id) for e in entries if not self._is_folder_entry(e)],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        root_id = self._root_id(creds)
        parent = parent_id or root_id
        created = self._call(creds, "POST", f"{self._API}/folder", json={
            "cabinet": creds["cabinet_id"], "parent": parent, "name": name,
        }).json()
        return self._entry_to_folder(created, root_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "PATCH", f"{self._API}/folder/{folder_id}", json={"name": name}).json()
        return self._entry_to_folder(updated, self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        root_id = self._root_id(creds)
        target = new_parent_id or root_id
        updated = self._call(creds, "PATCH", f"{self._API}/folder/{folder_id}", json={"parent": target}).json()
        return self._entry_to_folder(updated, root_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{self._API}/folder/{folder_id}")

    # --- files ---
    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        creds, _ = self.refresh_if_needed(creds)
        root_id = self._root_id(creds)
        parent = folder_id or root_id
        # See module docstring, point 2 — only the profile fields
        # documented as universal are sent; a real firm vault will very
        # likely require additional mandatory profile attributes this
        # provider doesn't know about.
        profile = {"cabinet": creds["cabinet_id"], "folder": parent, "name": name}
        resp = requests.post(
            f"{self._API}/Document",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            data={"profile": json.dumps(profile)},
            files={"file": (name, content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"NetDocuments upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self._entry_to_file(resp.json(), root_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        node = self._call(creds, "GET", f"{self._API}/Document/{file_id}").json()
        return self._entry_to_file(node, self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "PATCH", f"{self._API}/Document/{file_id}", json={"name": name}).json()
        return self._entry_to_file(updated, self._root_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        root_id = self._root_id(creds)
        target = new_folder_id or root_id
        updated = self._call(creds, "PATCH", f"{self._API}/Document/{file_id}", json={"folder": target}).json()
        return self._entry_to_file(updated, root_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._API}/Document/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        resp = self._call(creds, "GET", f"{self._API}/Document/{file_id}/Download")
        return resp.content

    # --- versions (native — see module docstring) ---
    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._call(creds, "GET", f"{self._API}/Document/{file_id}/Versions").json()
        entries = result if isinstance(result, list) else (
            result.get("versions") or result.get("Versions") or result.get("value") or result.get("results") or []
        )
        total = len(entries)
        versions: list[VersionInfo] = []
        for i, v in enumerate(entries):
            vnum = v.get("version") or v.get("versionNumber") or (total - i)
            # Whether the API returns newest-first or oldest-first isn't
            # confidently documented — is_current prefers an explicit flag
            # when present, falling back to treating whichever entry comes
            # back first as current.
            is_current = bool(v.get("isCurrent") or v.get("current")) or i == 0
            versions.append(VersionInfo(
                id=str(v.get("id") or v.get("versionId") or vnum),
                version_number=int(vnum),
                size_bytes=v.get("size") or v.get("fileSize"),
                content_type=v.get("mimeType") or v.get("contentType"),
                is_current=is_current,
                updated_at=self._parse_dt(v.get("lastModified") or v.get("modifiedDate")),
            ))
        return versions

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        creds, _ = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{self._API}/Document/{file_id}/Versions",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            files={"file": ("content", content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"NetDocuments version upload failed ({resp.status_code})", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        resp = self._call(creds, "GET", f"{self._API}/Document/{file_id}/Versions/{version_id}/Download")
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        # No confidently-documented "make this old version current"
        # endpoint, so restoring means downloading the old version's bytes
        # and re-uploading them as a brand-new version.
        old_bytes = self.get_version_content(creds, file_id, version_id)
        node = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, node.content_type or "application/octet-stream", old_bytes)

    # --- trash (emulated — see module docstring, point 3) ---
    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "PATCH", f"{self._API}/folder/{folder_id}", json={"parent": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root_id = self._root_id(creds)
        updated = self._call(creds, "PATCH", f"{self._API}/folder/{folder_id}", json={"parent": root_id}).json()
        return self._entry_to_folder(updated, root_id)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "PATCH", f"{self._API}/Document/{file_id}", json={"folder": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root_id = self._root_id(creds)
        updated = self._call(creds, "PATCH", f"{self._API}/Document/{file_id}", json={"folder": root_id}).json()
        return self._entry_to_file(updated, root_id)

    # --- search ---
    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root_id = self._root_id(creds)
        trash_id = self._trash_id(creds)
        cabinet_id = creds["cabinet_id"]
        escaped = query.replace('"', '\\"')
        result = self._search_raw(creds, f'"{escaped}" cabinet:{cabinet_id}')
        entries = self._extract_hits(result)
        folders: list[FolderInfo] = []
        files: list[FileInfo] = []
        for e in entries:
            if self._entry_id(e) == trash_id:
                continue
            if self._is_folder_entry(e):
                folders.append(self._entry_to_folder(e, root_id))
            else:
                files.append(self._entry_to_file(e, root_id))
        return folders, files
