"""Nextcloud, ownCloud, Synology Drive, and QNAP, via plain WebDAV
(RFC 4918) — all four expose a standard WebDAV endpoint for their file
storage, so one shared implementation covers all four with per-product
differences isolated to `_dav_root` (the base path) and, where a product
has a genuinely documented native trash/versions API, a couple of
overridden methods.

UNVERIFIED — built against the WebDAV RFC and each product's own
published docs for its specific DAV mount path, but there's no live
Nextcloud/ownCloud/Synology/QNAP server in this environment to test
against.

Confidence is HIGH for the WebDAV verbs themselves (PROPFIND/MKCOL/PUT/
GET/DELETE/MOVE/COPY are a real IETF standard, not vendor-specific
guesswork) and for Nextcloud/ownCloud's DAV root and trashbin path
(`remote.php/dav/...`, a stable, long-documented convention shared by
both — ownCloud is the project Nextcloud forked from, and neither has
changed this path since). Confidence is LOWER for the exact default DAV
mount path/port on Synology DSM and QNAP QTS, since both are configurable
per-NAS (a share name and enabled-WebDAV-service port an admin sets up
themselves) — modeled as a `dav_path`/`base_url` config field the admin
fills in for their own NAS, rather than a hardcoded guess.

Nextcloud/ownCloud's server-side trashbin and file-versions WebDAV
collections are used directly where available (a real native feature,
not emulated). Synology/QNAP have no confidently-documented equivalent
WebDAV surface for trash/versions, so those two emulate trash via a
hidden `_C-ECM-Trash` folder (same pattern used elsewhere in this
codebase for backends with uncertain native trash APIs) and report only
the single current version.
"""

import datetime
import threading
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urljoin

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

_DAV_NS = "{DAV:}"
_APP_ROOT_NAME = "C-ECM"
_TRASH_NAME = "_C-ECM-Trash"


class _WebDAVProvider(StorageProvider):
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    # --- per-product overrides ---
    def _dav_root(self, creds: dict) -> str:
        """The DAV collection root for this account — everything this
        provider touches lives under here. Must end in '/'."""
        raise NotImplementedError

    def _supports_native_trash(self) -> bool:
        return False

    def _supports_native_versions(self) -> bool:
        return False

    # --- shared plumbing ---
    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _headers(self, creds: dict) -> dict:
        import base64
        token = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _url(self, creds: dict, path: str) -> str:
        return urljoin(self._base_url(creds) + "/", self._dav_root(creds).lstrip("/")) + path.lstrip("/")

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> requests.Response:
        url = self._url(creds, path)
        try:
            resp = requests.request(method, url, headers={**self._headers(creds), **kwargs.pop("headers", {})},
                                     timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the server: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("Invalid credentials", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Server error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderError("Server URL is required", status_code=400)
        creds = {"username": username, "password": password, "base_url": base_url}
        try:
            self._request(creds, "PROPFIND", "", headers={"Depth": "0"})
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    # --- PROPFIND parsing ---
    def _propfind(self, creds: dict, path: str, depth: str = "1") -> list[dict]:
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<D:propfind xmlns:D="DAV:"><D:prop>'
            "<D:resourcetype/><D:getcontentlength/><D:getcontenttype/><D:getlastmodified/><D:displayname/>"
            "</D:prop></D:propfind>"
        )
        resp = self._request(creds, "PROPFIND", path, headers={"Depth": depth, "Content-Type": "application/xml"},
                              data=body.encode())
        root = ET.fromstring(resp.content)
        entries = []
        for response in root.findall(f"{_DAV_NS}response"):
            href = response.findtext(f"{_DAV_NS}href") or ""
            propstat = response.find(f"{_DAV_NS}propstat")
            if propstat is None:
                continue
            prop = propstat.find(f"{_DAV_NS}prop")
            if prop is None:
                continue
            is_collection = prop.find(f"{_DAV_NS}resourcetype/{_DAV_NS}collection") is not None
            size_text = prop.findtext(f"{_DAV_NS}getcontentlength")
            entries.append({
                "href": unquote(href),
                "is_collection": is_collection,
                "size": int(size_text) if size_text else None,
                "content_type": prop.findtext(f"{_DAV_NS}getcontenttype"),
                "last_modified": prop.findtext(f"{_DAV_NS}getlastmodified"),
                "displayname": prop.findtext(f"{_DAV_NS}displayname"),
            })
        return entries

    @staticmethod
    def _parse_dt(s: str | None):
        if not s:
            return None
        try:
            return datetime.datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %Z")
        except ValueError:
            return None

    def _name_from_href(self, href: str) -> str:
        return href.rstrip("/").rsplit("/", 1)[-1]

    def _entry_to_folder(self, e: dict, parent_path: str | None) -> FolderInfo:
        return FolderInfo(id=e["href"], name=self._name_from_href(e["href"]), parent_id=parent_path, created_at=None)

    def _entry_to_file(self, e: dict, parent_path: str | None) -> FileInfo:
        return FileInfo(id=e["href"], name=self._name_from_href(e["href"]), folder_id=parent_path,
                         version_number=1, size_bytes=e.get("size"), content_type=e.get("content_type"),
                         updated_at=self._parse_dt(e.get("last_modified")))

    # --- app root / trash root, cached per (base_url, username) ---
    def _find_or_create_collection(self, creds: dict, parent_path: str, name: str) -> str:
        entries = self._propfind(creds, parent_path)
        dav_root_path = self._dav_root(creds)
        for e in entries:
            if e["is_collection"] and self._name_from_href(e["href"]) == name and e["href"].rstrip("/") != parent_path.rstrip("/"):
                return e["href"]
        self._request(creds, "MKCOL", f"{parent_path.rstrip('/')}/{name}/")
        return f"{parent_path.rstrip('/')}/{name}/"

    def _root_path(self, creds: dict) -> str:
        cache_key = self._base_url(creds) + creds["username"]
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            root = self._find_or_create_collection(creds, "/", _APP_ROOT_NAME)
            self._root_id_cache[cache_key] = root
            return root

    def _trash_path(self, creds: dict) -> str:
        cache_key = self._base_url(creds) + creds["username"]
        cached = self._trash_id_cache.get(cache_key)
        if cached:
            return cached
        with self._trash_id_lock:
            cached = self._trash_id_cache.get(cache_key)
            if cached:
                return cached
            root = self._root_path(creds)
            trash = self._find_or_create_collection(creds, root, _TRASH_NAME)
            self._trash_id_cache[cache_key] = trash
            return trash

    def _resolve(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_path(creds)

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root = self._root_path(creds)
        path = self._resolve(creds, folder_id)
        entries = self._propfind(creds, path)
        entries = [e for e in entries if e["href"].rstrip("/") != path.rstrip("/")]
        folders = [self._entry_to_folder(e, folder_id) for e in entries
                   if e["is_collection"] and self._name_from_href(e["href"]) != _TRASH_NAME]
        files = [self._entry_to_file(e, folder_id) for e in entries if not e["is_collection"]]
        current_folder = None
        if folder_id is not None:
            current_folder = FolderInfo(id=folder_id, name=self._name_from_href(folder_id), parent_id=None)
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        if self._supports_native_trash():
            return self._native_list_trash(creds)
        trash = self._trash_path(creds)
        entries = [e for e in self._propfind(creds, trash) if e["href"].rstrip("/") != trash.rstrip("/")]
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._entry_to_folder(e, trash) for e in entries if e["is_collection"]],
            files=[self._entry_to_file(e, trash) for e in entries if not e["is_collection"]],
        )

    def _native_list_trash(self, creds: dict) -> FolderContents:
        raise NotImplementedError

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._resolve(creds, parent_id)
        path = f"{parent.rstrip('/')}/{quote(name)}/"
        self._request(creds, "MKCOL", path)
        return FolderInfo(id=path, name=name, parent_id=parent_id)

    def _destination_header(self, creds: dict, dest_path: str) -> dict:
        return {"Destination": self._url(creds, dest_path), "Overwrite": "F"}

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        parent = folder_id.rstrip("/").rsplit("/", 1)
        parent_path = parent[0] + "/" if len(parent) > 1 else "/"
        new_path = f"{parent_path.rstrip('/')}/{quote(name)}/"
        self._request(creds, "MOVE", folder_id, headers=self._destination_header(creds, new_path))
        return FolderInfo(id=new_path, name=name, parent_id=None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        name = self._name_from_href(folder_id)
        target = self._resolve(creds, new_parent_id)
        new_path = f"{target.rstrip('/')}/{quote(name)}/"
        self._request(creds, "MOVE", folder_id, headers=self._destination_header(creds, new_path))
        return FolderInfo(id=new_path, name=name, parent_id=new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._request(creds, "DELETE", folder_id)

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._resolve(creds, folder_id)
        path = f"{parent.rstrip('/')}/{quote(name)}"
        self._request(creds, "PUT", path, data=content, headers={"Content-Type": content_type})
        return FileInfo(id=path, name=name, folder_id=folder_id, version_number=1,
                         size_bytes=len(content), content_type=content_type)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        entries = self._propfind(creds, file_id, depth="0")
        if not entries:
            raise ProviderError("Not found", status_code=404)
        e = entries[0]
        parent = file_id.rstrip("/").rsplit("/", 1)
        parent_path = parent[0] + "/" if len(parent) > 1 else None
        root = self._root_path(creds)
        if parent_path and parent_path.rstrip("/") == root.rstrip("/"):
            parent_path = None
        return self._entry_to_file(e, parent_path)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        parent = file_id.rsplit("/", 1)
        parent_path = parent[0] + "/" if len(parent) > 1 else "/"
        new_path = f"{parent_path.rstrip('/')}/{quote(name)}"
        self._request(creds, "MOVE", file_id, headers=self._destination_header(creds, new_path))
        return self.get_file(creds, new_path)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        name = self._name_from_href(file_id)
        target = self._resolve(creds, new_folder_id)
        new_path = f"{target.rstrip('/')}/{quote(name)}"
        self._request(creds, "MOVE", file_id, headers=self._destination_header(creds, new_path))
        return self.get_file(creds, new_path)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._request(creds, "DELETE", file_id)

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._request(creds, "GET", file_id).content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        if self._supports_native_versions():
            return self._native_list_versions(creds, file_id)
        info = self.get_file(creds, file_id)
        return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                             content_type=info.content_type, is_current=True, updated_at=info.updated_at)]

    def _native_list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        raise NotImplementedError

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        self._request(creds, "PUT", file_id, data=content, headers={"Content-Type": content_type})
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        return self._native_get_version_content(creds, file_id, version_id)

    def _native_get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        raise NotImplementedError

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        info = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, info.content_type or "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        if self._supports_native_trash():
            self._request(creds, "DELETE", folder_id)
            return
        target = self._trash_path(creds)
        name = self._name_from_href(folder_id)
        self._request(creds, "MOVE", folder_id, headers=self._destination_header(creds, f"{target.rstrip('/')}/{quote(name)}/"))

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        if self._supports_native_trash():
            return self._native_restore(creds, folder_id, is_folder=True)
        root = self._root_path(creds)
        name = self._name_from_href(folder_id)
        new_path = f"{root.rstrip('/')}/{quote(name)}/"
        self._request(creds, "MOVE", folder_id, headers=self._destination_header(creds, new_path))
        return FolderInfo(id=new_path, name=name, parent_id=None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        if self._supports_native_trash():
            self._request(creds, "DELETE", file_id)
            return
        target = self._trash_path(creds)
        name = self._name_from_href(file_id)
        self._request(creds, "MOVE", file_id, headers=self._destination_header(creds, f"{target.rstrip('/')}/{quote(name)}"))

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        if self._supports_native_trash():
            return self._native_restore(creds, file_id, is_folder=False)
        root = self._root_path(creds)
        name = self._name_from_href(file_id)
        new_path = f"{root.rstrip('/')}/{quote(name)}"
        self._request(creds, "MOVE", file_id, headers=self._destination_header(creds, new_path))
        return self.get_file(creds, new_path)

    def _native_restore(self, creds: dict, item_id: str, is_folder: bool):
        raise NotImplementedError

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        # No confidently-uniform search DSL across Nextcloud/ownCloud/
        # Synology/QNAP's WebDAV surfaces (Nextcloud has a SEARCH verb with
        # its own XML body, but it isn't something QNAP/Synology are known
        # to implement) — walk the app's own folder tree and filter by
        # name client-side instead, which works identically everywhere.
        root = self._root_path(creds)
        trash = self._trash_path(creds) if not self._supports_native_trash() else None
        found_folders: list[FolderInfo] = []
        found_files: list[FileInfo] = []
        q = query.lower()

        def walk(path: str, parent_id, depth: int):
            if depth > 6:
                return
            entries = [e for e in self._propfind(creds, path) if e["href"].rstrip("/") != path.rstrip("/")]
            for e in entries:
                if trash and e["href"].rstrip("/") == trash.rstrip("/"):
                    continue
                name = self._name_from_href(e["href"])
                if e["is_collection"]:
                    if q in name.lower():
                        found_folders.append(self._entry_to_folder(e, parent_id))
                    walk(e["href"], e["href"], depth + 1)
                elif q in name.lower():
                    found_files.append(self._entry_to_file(e, parent_id))

        walk(root, None, 0)
        return found_folders, found_files


class NextcloudProvider(_WebDAVProvider):
    """Nextcloud's WebDAV mount is a stable, long-documented path
    (`remote.php/dav/files/{username}/`), and its trashbin/versions apps
    each expose their own WebDAV collections the same way — these are
    real Nextcloud server features being used directly, not emulated."""

    key = "nextcloud"
    display_name = "Nextcloud"

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Server URL", "https://cloud.example.com")]

    def _dav_root(self, creds: dict) -> str:
        return f"/remote.php/dav/files/{quote(creds['username'])}/"

    def _supports_native_trash(self) -> bool:
        return True

    def _supports_native_versions(self) -> bool:
        return True

    def _trashbin_path(self, creds: dict) -> str:
        return f"/remote.php/dav/trashbin/{quote(creds['username'])}/trash/"

    def _native_list_trash(self, creds: dict) -> FolderContents:
        # The trashbin collection lives outside this provider's normal DAV
        # root, so PROPFIND against it directly rather than through _url's
        # files-root join.
        base = self._base_url(creds)
        resp = requests.request(
            "PROPFIND", base + self._trashbin_path(creds),
            headers={**self._headers(creds), "Depth": "1", "Content-Type": "application/xml"},
            data=(
                '<?xml version="1.0" encoding="utf-8"?><D:propfind xmlns:D="DAV:"><D:prop>'
                "<D:resourcetype/><D:getcontentlength/><D:getlastmodified/></D:prop></D:propfind>"
            ).encode(),
            timeout=30,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Couldn't list trash ({resp.status_code})", status_code=502)
        root = ET.fromstring(resp.content)
        folders, files = [], []
        trash_path = self._trashbin_path(creds)
        for response in root.findall(f"{_DAV_NS}response"):
            href = unquote(response.findtext(f"{_DAV_NS}href") or "")
            if href.rstrip("/") == trash_path.rstrip("/"):
                continue
            is_collection = response.find(f".//{_DAV_NS}resourcetype/{_DAV_NS}collection") is not None
            name = href.rstrip("/").rsplit("/", 1)[-1]
            if is_collection:
                folders.append(FolderInfo(id=href, name=name, parent_id=None))
            else:
                files.append(FileInfo(id=href, name=name, folder_id=None, version_number=1,
                                       size_bytes=None, content_type=None))
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=folders, files=files)

    def _native_restore(self, creds: dict, item_id: str, is_folder: bool):
        name = item_id.rstrip("/").rsplit("/", 1)[-1]
        base = self._base_url(creds)
        restore_url = base + f"/remote.php/dav/trashbin/{quote(creds['username'])}/restore/{quote(name)}"
        resp = requests.request("MOVE", base + item_id,
                                 headers={**self._headers(creds), "Destination": restore_url}, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Couldn't restore from trash ({resp.status_code})", status_code=502)
        return self.get_file(creds, name) if not is_folder else FolderInfo(id=name, name=name, parent_id=None)

    def _native_list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        # Nextcloud exposes versions under a WebDAV collection keyed by the
        # file's internal fileid, obtainable via a PROPFIND requesting the
        # `oc:fileid` property — a real, documented Nextcloud extension
        # property, but less universally stable across very old server
        # versions than the base DAV properties used elsewhere in this file.
        base = self._base_url(creds)
        resp = requests.request(
            "PROPFIND", base + file_id,
            headers={**self._headers(creds), "Depth": "0", "Content-Type": "application/xml"},
            data=(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<D:propfind xmlns:D="DAV:" xmlns:oc="http://owncloud.org/ns"><D:prop>'
                "<oc:fileid/></D:prop></D:propfind>"
            ).encode(),
            timeout=30,
        )
        if resp.status_code >= 400:
            return super().list_versions(creds, file_id)
        root = ET.fromstring(resp.content)
        fileid = root.findtext(f".//{{http://owncloud.org/ns}}fileid")
        if not fileid:
            return super().list_versions(creds, file_id)
        versions_path = f"/remote.php/dav/versions/{quote(creds['username'])}/versions/{fileid}/"
        resp = requests.request(
            "PROPFIND", base + versions_path,
            headers={**self._headers(creds), "Depth": "1", "Content-Type": "application/xml"},
            data=(
                '<?xml version="1.0" encoding="utf-8"?><D:propfind xmlns:D="DAV:"><D:prop>'
                "<D:getcontentlength/><D:getlastmodified/></D:prop></D:propfind>"
            ).encode(),
            timeout=30,
        )
        if resp.status_code >= 400:
            return super().list_versions(creds, file_id)
        vroot = ET.fromstring(resp.content)
        out = []
        for i, response in enumerate(vroot.findall(f"{_DAV_NS}response")):
            href = response.findtext(f"{_DAV_NS}href") or ""
            if href.rstrip("/") == versions_path.rstrip("/"):
                continue
            vid = href.rstrip("/").rsplit("/", 1)[-1]
            size_text = response.findtext(f".//{_DAV_NS}getcontentlength")
            out.append(VersionInfo(id=vid, version_number=i + 1,
                                    size_bytes=int(size_text) if size_text else None,
                                    content_type=None, is_current=False,
                                    updated_at=self._parse_dt(response.findtext(f".//{_DAV_NS}getlastmodified"))))
        current = self.get_file(creds, file_id)
        out.append(VersionInfo(id="current", version_number=len(out) + 1, size_bytes=current.size_bytes,
                                content_type=current.content_type, is_current=True, updated_at=current.updated_at))
        return out

    def _native_get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        base = self._base_url(creds)
        resp = requests.request(
            "PROPFIND", base + file_id,
            headers={**self._headers(creds), "Depth": "0", "Content-Type": "application/xml"},
            data=(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<D:propfind xmlns:D="DAV:" xmlns:oc="http://owncloud.org/ns"><D:prop>'
                "<oc:fileid/></D:prop></D:propfind>"
            ).encode(),
            timeout=30,
        )
        root = ET.fromstring(resp.content)
        fileid = root.findtext(f".//{{http://owncloud.org/ns}}fileid")
        url = base + f"/remote.php/dav/versions/{quote(creds['username'])}/versions/{fileid}/{version_id}"
        resp = requests.request("GET", url, headers=self._headers(creds), timeout=60)
        if resp.status_code >= 400:
            raise ProviderError("Version content not found", status_code=404)
        return resp.content


class OwnCloudProvider(NextcloudProvider):
    """ownCloud is the project Nextcloud forked from and has kept the same
    `remote.php/dav/...` WebDAV/trashbin/versions paths ever since — this
    subclass exists only so it shows up as its own connection type in the
    UI, the request logic is identical."""

    key = "owncloud"
    display_name = "ownCloud"

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("base_url", "Server URL", "https://owncloud.example.com")]


class SynologyDriveProvider(_WebDAVProvider):
    """Synology DSM's WebDAV Server package exposes a configurable share
    over WebDAV — the share name and port are admin-configured per-NAS
    (not a single well-known path the way Nextcloud's is), so both are
    collected as connection fields rather than guessed."""

    key = "synology_drive"
    display_name = "Synology Drive"

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("base_url", "Server URL (include the WebDAV port)", "https://nas.example.com:5006"),
            ConfigField("dav_path", "WebDAV share path", "/"),
        ]

    def _dav_root(self, creds: dict) -> str:
        path = (creds.get("dav_path") or "/").strip()
        return path if path.endswith("/") else path + "/"


class QNAPProvider(_WebDAVProvider):
    """QNAP QTS's File Station can expose a share over standard WebDAV
    (enabled per-share in Control Panel) — same admin-configured share
    path/port situation as Synology above."""

    key = "qnap"
    display_name = "QNAP"

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("base_url", "Server URL (include the WebDAV port)", "https://nas.example.com:5006"),
            ConfigField("dav_path", "WebDAV share path", "/dav/Public"),
        ]

    def _dav_root(self, creds: dict) -> str:
        path = (creds.get("dav_path") or "/").strip()
        return path if path.endswith("/") else path + "/"
