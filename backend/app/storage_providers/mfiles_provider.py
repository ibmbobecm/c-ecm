"""M-Files provider, via the M-Files Web Service (MFWS) REST API — the
documented JSON REST interface M-Files servers expose under something like
`http://<host>/REST`, distinct from the older SOAP API and from the M-Files
COM/.NET SDK.

UNVERIFIED — there is no live M-Files server in this environment to test
against. This is written from MFWS's documented REST conventions (the
`server/authenticationtokens` login call, the `vaults/{vault}/objects/...`
resource family, multipart object creation, etc.), cross-checked against my
own recollection of the M-Files SDK's data model. Treat it with meaningfully
*more* suspicion than this codebase's other "unverified" providers
(Alfresco, Box): M-Files' object model is metadata/view-driven rather than
strictly hierarchical-folder-driven the way Alfresco/SharePoint/Box are, so
several of the mappings below onto this app's simple folder/file model are
pragmatic best-effort choices rather than confidently-known API contracts.
Run this against a real vault and expect to adjust the specific points
flagged "UNVERIFIED"/"PLACEHOLDER" below before trusting it in production.

Known soft spots, called out up front:

- **Children-of-a-folder listing.** MFWS's precise "give me the children of
  object X" server-side search syntax is not confidently known here. Rather
  than guess at a query parameter name and silently get it wrong,
  `get_children`/`list_trash` are built on MFWS's simpler, well-documented
  quick-search endpoint (`GET /objects?q=...`), fetching a broad candidate
  set and then filtering client-side (in Python) to objects whose
  parent-folder property matches. This is O(vault size) per listing and
  assumes the vault isn't enormous — a real integration should replace this
  with a proper server-side property filter once the correct query syntax
  is confirmed against the target server.
- **The "Document" object type.** Rather than hardcode a specific object
  type ID for "Document" (which varies by vault, and which I'm not
  confident is universally `0` — some sources call `0` "Document" and
  others use it for "Folder" depending on vault template), documents are
  created and addressed generically through the untyped `/objects` and
  `/objects/{type}/{id}/...` endpoints, reading the actual type back out of
  each object's own `ObjVer.Type` rather than assuming one.
- **"Folder" object type / class IDs, and the parent-folder link property.**
  `_FOLDER_OBJECT_TYPE`, `_FOLDER_CLASS`, `_DOCUMENT_CLASS`, and especially
  `_PD_PARENT_FOLDER` below are PLACEHOLDERS based on common M-Files
  default/demo vault templates, not a universal contract — M-Files lets
  vault admins rename and renumber classes/properties freely in Vault
  Structure. Only `_PD_NAME` (PropertyDef 0, "Name or title") is genuinely
  stable across vaults. A production integration should make the others
  configurable per-vault (e.g. resolved once via the vault's
  `structure/properties` metadata) rather than hardcoded.
- **The PropertyValue JSON shape.** Property values are written here as
  `{"PropertyDef": id, "Value": {"DataType": n, "Value": ...}}`. Some MFWS
  documentation/versions instead wrap the typed value under a `TypedValue`
  key — `_prop_get` reads both shapes defensively, but the *write* helpers
  (`_text_property`/`_lookup_property`) only produce the `Value` shape. If
  writes start failing with a property-validation error against a real
  vault, this is the first thing to check.
- **Trash/restore.** M-Files' own delete is a recoverable "Deleted" flag,
  but there's no confidently-known "list everything currently deleted"
  endpoint here, so trash is *emulated* the same way this codebase emulates
  it for other backends with no confident native trash listing: a
  dedicated `C-ECM-Trash` folder object under the app root. `trash_folder`/
  `trash_file` just re-point the item's parent-folder property at that
  folder; `restore_*` points it back at the app root; `list_trash` is just
  that folder's children. `delete_folder`/`delete_file` (meant to be
  *permanent*) fall back to M-Files' own `DELETE` on the object, which is
  in reality still M-Files' recoverable soft-delete — no confidently-known
  hard-delete call exists in MFWS, so this is a disclosed limitation, not
  an oversight.
- **Version revert.** No "revert to an old version" endpoint name is
  confidently known, so `restore_version` uses the safe, always-correct
  fallback of downloading the target version's content and feeding it back
  through `create_version` (which itself uses MFWS's documented
  latest-version content-replace endpoint, `PUT .../latest/files/{id}/content`,
  which auto-creates a new version).

The server (`base_url`) and target vault (`vault_guid`) are both
per-connection (`config_fields`), since one M-Files server can host many
vaults and this provider instance is shared by every connection to it.
"""

import json
import mimetypes
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
_TRASH_FOLDER_NAME = "C-ECM-Trash"

# --- Best-guess IDs for M-Files' built-in vault structure -------------------
# See the module docstring's "known soft spots" section. These are the
# values seen in common M-Files default/demo vault templates, used as a
# pragmatic starting point — NOT a value this provider can discover from
# first principles without either querying the vault's own
# `structure/...` metadata endpoints (not attempted here, to keep this
# provider's surface small) or making them user-configurable. If
# folder/document operations fail with a property- or class-validation
# error against a real vault, these are the first values to double check.
_FOLDER_OBJECT_TYPE = 0  # PLACEHOLDER: built-in "Folder" object type ID
_FOLDER_CLASS = 0  # PLACEHOLDER: default "Folder" object class ID
_DOCUMENT_CLASS = 0  # PLACEHOLDER: default "Document" object class ID
_PD_NAME = 0  # built-in "Name or title" property def ID — stable across vaults
_PD_PARENT_FOLDER = 21  # PLACEHOLDER: "parent folder" lookup property def ID

_DT_TEXT = 1  # MFDatatypeText
_DT_LOOKUP = 9  # MFDatatypeLookup


def _guess_content_type(extension: str | None) -> str | None:
    if not extension:
        return None
    guessed, _ = mimetypes.guess_type(f"x.{extension.lstrip('.')}")
    return guessed


class MFilesProvider(StorageProvider):
    key = "mfiles"
    display_name = "M-Files"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._root_id_cache: dict[str, int] = {}
        self._trash_id_cache: dict[str, int] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("base_url", "Server URL", "http://localhost/REST"),
            ConfigField("vault_guid", "Vault GUID", "{12345678-1234-1234-1234-123456789012}"),
        ]

    # --- low-level plumbing ---

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _api(self, creds: dict) -> str:
        return f"{self._base_url(creds)}/vaults/{creds['vault_guid']}"

    def _cache_key(self, creds: dict) -> str:
        return f"{self._base_url(creds)}|{creds['vault_guid']}"

    def _headers(self, creds: dict) -> dict:
        return {"X-Authentication": creds["token"]}

    def _obtain_token(self, base_url: str, vault_guid: str, username: str, password: str) -> str:
        url = f"{base_url.rstrip('/')}/server/authenticationtokens"
        try:
            resp = requests.post(
                url,
                json={"Username": username, "Password": password, "VaultGuid": vault_guid},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach M-Files: {exc}", status_code=502)
        if resp.status_code in (401, 403):
            raise ProviderError("Invalid M-Files credentials", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError(
                f"M-Files authentication failed ({resp.status_code}): {resp.text[:300]}", status_code=502
            )
        try:
            token = resp.json().get("Value")
        except ValueError:
            token = None
        if not token:
            raise ProviderError("M-Files did not return an authentication token", status_code=502)
        return token

    def _reauthenticate(self, creds: dict) -> None:
        creds["token"] = self._obtain_token(
            creds["base_url"], creds["vault_guid"], creds["username"], creds["password"]
        )

    def _raw_request(
        self,
        creds: dict,
        method: str,
        path: str,
        *,
        _retry: bool = True,
        extra_headers: dict | None = None,
        **kwargs,
    ) -> requests.Response:
        url = self._api(creds) + path
        headers = {**self._headers(creds), **(extra_headers or {})}
        timeout = kwargs.pop("timeout", 30)
        try:
            resp = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach M-Files: {exc}", status_code=502)
        if resp.status_code == 401 and _retry:
            # The auth token doesn't self-refresh, and this codebase's
            # CREDENTIALS-mode contract gives providers no side-channel to
            # persist a refreshed token back to the connection store
            # mid-call (`refresh_if_needed` is a deliberate no-op for
            # these providers) — so re-authenticate in-memory, for this
            # call only, using the username/password/vault_guid that
            # `authenticate()` already stashed in `creds`, and retry
            # exactly once before giving up.
            self._reauthenticate(creds)
            return self._raw_request(creds, method, path, _retry=False, extra_headers=extra_headers, **kwargs)
        return resp

    def _request(self, creds: dict, method: str, path: str, **kwargs):
        resp = self._raw_request(creds, method, path, **kwargs)
        if resp.status_code == 401:
            raise ProviderError("Invalid M-Files credentials", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"M-Files error {resp.status_code}: {resp.text[:300]}", status_code=502)
        if resp.content and resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {}

    def _content_bytes(self, creds: dict, path: str) -> bytes:
        resp = self._raw_request(creds, "GET", path)
        if resp.status_code == 401:
            raise ProviderError("Invalid M-Files credentials", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError("Couldn't fetch content", status_code=404)
        return resp.content

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        vault_guid = (config.get("vault_guid") or "").strip()
        if not base_url or not vault_guid:
            raise ProviderError("Server URL and Vault GUID are required", status_code=400)
        try:
            token = self._obtain_token(base_url, vault_guid, username, password)
        except ProviderError:
            return None
        return {
            "username": username,
            "password": password,
            "base_url": base_url,
            "vault_guid": vault_guid,
            "token": token,
        }

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    # --- opaque id encoding: "{objectType}:{objectId}" for folders/files,
    # "{objectType}:{objectId}:{version}" for versions. M-Files addresses
    # objects by an (ObjType, ObjID, Version) triple ("ObjVer"), not a
    # single id, so the type has to travel with the id everywhere. ---

    @staticmethod
    def _encode_id(obj_type: int, obj_id: int) -> str:
        return f"{obj_type}:{obj_id}"

    @staticmethod
    def _decode_id(opaque_id: str) -> tuple[int, int]:
        obj_type, obj_id = opaque_id.split(":", 1)
        return int(obj_type), int(obj_id)

    @staticmethod
    def _encode_version_id(obj_type: int, obj_id: int, version: int) -> str:
        return f"{obj_type}:{obj_id}:{version}"

    @staticmethod
    def _decode_version_id(version_id: str) -> tuple[int, int, int]:
        obj_type, obj_id, version = version_id.split(":", 2)
        return int(obj_type), int(obj_id), int(version)

    # --- property value helpers ---

    @staticmethod
    def _text_property(property_def: int, value: str) -> dict:
        return {"PropertyDef": property_def, "Value": {"DataType": _DT_TEXT, "Value": value}}

    @staticmethod
    def _lookup_property(property_def: int, item_id: int) -> dict:
        return {"PropertyDef": property_def, "Value": {"DataType": _DT_LOOKUP, "Value": {"Item": item_id}}}

    @staticmethod
    def _prop_get(properties: list, property_def: int):
        # NOTE (uncertain): reads both the "Value" wrapper this file writes
        # and the "TypedValue" wrapper some MFWS docs/versions use instead,
        # in case a live server actually returns that shape.
        for p in properties or []:
            if p.get("PropertyDef") != property_def:
                continue
            tv = p.get("Value")
            if not isinstance(tv, dict):
                tv = p.get("TypedValue")
            if isinstance(tv, dict):
                return tv.get("Value")
        return None

    @staticmethod
    def _lookup_item_id(value) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, dict):
            item = value.get("Item")
            if isinstance(item, dict):
                return item.get("ID")
            if isinstance(item, int):
                return item
            lookup = value.get("Lookup")
            if isinstance(lookup, dict):
                return lookup.get("Item")
        return None

    @staticmethod
    def _parse_dt(value):
        if not value:
            return None
        import datetime

        try:
            return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _obj_fields(self, obj: dict) -> dict:
        objver = obj.get("ObjVer") or {}
        obj_type = objver.get("Type", obj.get("Type"))
        obj_id = objver.get("ID", obj.get("ID"))
        version = objver.get("Version", obj.get("Version", 1))
        properties = obj.get("Properties") or []
        name = self._prop_get(properties, _PD_NAME) or obj.get("Title") or obj.get("DisplayID") or "Untitled"
        parent_item_id = self._lookup_item_id(self._prop_get(properties, _PD_PARENT_FOLDER))
        return {
            "obj_type": obj_type,
            "obj_id": obj_id,
            "version": version,
            "name": name,
            "parent_item_id": parent_item_id,
            "updated_at": self._parse_dt(obj.get("LastModified")),
            "deleted": bool(obj.get("Deleted", False)),
            "files": obj.get("Files") or [],
        }

    def _to_folder_info(self, obj: dict, root_item_id: int | None) -> FolderInfo:
        f = self._obj_fields(obj)
        parent_id = None
        if f["parent_item_id"] is not None and f["parent_item_id"] != root_item_id:
            parent_id = self._encode_id(_FOLDER_OBJECT_TYPE, f["parent_item_id"])
        return FolderInfo(
            id=self._encode_id(f["obj_type"], f["obj_id"]),
            name=f["name"],
            parent_id=parent_id,
            created_at=None,  # no confidently-known "created" field distinct from LastModified
        )

    def _to_file_info(self, obj: dict, root_item_id: int | None) -> FileInfo:
        f = self._obj_fields(obj)
        parent_id = None
        if f["parent_item_id"] is not None and f["parent_item_id"] != root_item_id:
            parent_id = self._encode_id(_FOLDER_OBJECT_TYPE, f["parent_item_id"])
        files = f["files"]
        size_bytes = files[0].get("Size") if files else None
        content_type = _guess_content_type(files[0].get("Extension")) if files else None
        return FileInfo(
            id=self._encode_id(f["obj_type"], f["obj_id"]),
            name=f["name"],
            folder_id=parent_id,
            version_number=f["version"] or 1,
            size_bytes=size_bytes,
            content_type=content_type,
            updated_at=f["updated_at"],
        )

    @staticmethod
    def _split_name(name: str) -> tuple[str, str]:
        if "." in name:
            title, ext = name.rsplit(".", 1)
            return title, ext
        return name, ""

    def _set_property(self, creds: dict, obj_type: int, obj_id: int, prop_body: dict) -> dict:
        # UNVERIFIED: assumes a per-property sub-resource
        # (".../properties/{propertyDef}") accepts PUT to set a single
        # property value. If this 404s against a real server, the
        # documented alternative is PUT-ing the *entire* PropertyValues
        # array to ".../properties" instead.
        self._request(
            creds,
            "PUT",
            f"/objects/{obj_type}/{obj_id}/latest/properties/{prop_body['PropertyDef']}",
            json=prop_body,
        )
        return self._request(creds, "GET", f"/objects/{obj_type}/{obj_id}/latest")

    def _quick_search(self, creds: dict, query: str) -> list[dict]:
        # See module docstring: the exact "children of this folder"
        # server-side query isn't confidently known, so every folder-aware
        # listing below is built on this one well-documented primitive
        # (MFWS quick search) plus client-side filtering. `query=""` is
        # used where the caller wants "everything" rather than a specific
        # name match — if a real vault's quick search rejects an empty
        # term, substitute a single wildcard character here, or replace
        # this with unfiltered pagination over `/objects`.
        result = self._request(creds, "GET", "/objects", params={"q": query})
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("Items") or result.get("Objects") or []
        return []

    def _find_folder_by_name(self, creds: dict, name: str) -> dict | None:
        for obj in self._quick_search(creds, name):
            f = self._obj_fields(obj)
            if f["obj_type"] == _FOLDER_OBJECT_TYPE and f["name"] == name and not f["deleted"]:
                return obj
        return None

    def _root_object_id(self, creds: dict) -> int:
        cache_key = self._cache_key(creds)
        cached = self._root_id_cache.get(cache_key)
        if cached is not None:
            return cached
        # Process-wide singleton shared by every connection to the same
        # vault, and FastAPI runs sync handlers in a real thread pool —
        # without a lock, concurrent first-requests would each find no
        # existing root folder and each create their own duplicate.
        # Double-checked locking: re-test the cache after acquiring the
        # lock, since another thread may have populated it while we waited.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached is not None:
                return cached
            existing = self._find_folder_by_name(creds, _APP_ROOT_NAME)
            if existing:
                root_id = self._obj_fields(existing)["obj_id"]
            else:
                created = self._request(
                    creds,
                    "POST",
                    f"/objects/{_FOLDER_OBJECT_TYPE}",
                    json={
                        "Class": _FOLDER_CLASS,
                        "PropertyValues": [self._text_property(_PD_NAME, _APP_ROOT_NAME)],
                    },
                )
                root_id = self._obj_fields(created)["obj_id"]
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_object_id(self, creds: dict) -> int:
        cache_key = self._cache_key(creds)
        cached = self._trash_id_cache.get(cache_key)
        if cached is not None:
            return cached
        with self._trash_id_lock:
            cached = self._trash_id_cache.get(cache_key)
            if cached is not None:
                return cached
            root_id = self._root_object_id(creds)
            existing = self._find_folder_by_name(creds, _TRASH_FOLDER_NAME)
            if existing:
                trash_id = self._obj_fields(existing)["obj_id"]
            else:
                created = self._request(
                    creds,
                    "POST",
                    f"/objects/{_FOLDER_OBJECT_TYPE}",
                    json={
                        "Class": _FOLDER_CLASS,
                        "PropertyValues": [
                            self._text_property(_PD_NAME, _TRASH_FOLDER_NAME),
                            self._lookup_property(_PD_PARENT_FOLDER, root_id),
                        ],
                    },
                )
                trash_id = self._obj_fields(created)["obj_id"]
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def _resolve_parent_item_id(self, creds: dict, folder_id: str | None) -> int:
        if folder_id is None:
            return self._root_object_id(creds)
        _, obj_id = self._decode_id(folder_id)
        return obj_id

    # --- folders ---

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_object_id(creds)
        parent_item_id = self._resolve_parent_item_id(creds, folder_id)
        folders: list[FolderInfo] = []
        files: list[FileInfo] = []
        for obj in self._quick_search(creds, ""):
            f = self._obj_fields(obj)
            if f["deleted"] or f["parent_item_id"] != parent_item_id:
                continue
            if f["obj_type"] == _FOLDER_OBJECT_TYPE:
                if parent_item_id == root_id and f["name"] == _TRASH_FOLDER_NAME:
                    continue
                folders.append(self._to_folder_info(obj, root_id))
            else:
                files.append(self._to_file_info(obj, root_id))

        current_folder = None
        if folder_id is not None:
            obj_type, obj_id = self._decode_id(folder_id)
            node = self._request(creds, "GET", f"/objects/{obj_type}/{obj_id}/latest")
            current_folder = self._to_folder_info(node, root_id)

        return FolderContents(
            folder=current_folder,
            breadcrumb=[BreadcrumbEntry(id=None, name="My Vault")],
            folders=folders,
            files=files,
        )

    def list_trash(self, creds: dict) -> FolderContents:
        root_id = self._root_object_id(creds)
        trash_id = self._trash_object_id(creds)
        folders: list[FolderInfo] = []
        files: list[FileInfo] = []
        for obj in self._quick_search(creds, ""):
            f = self._obj_fields(obj)
            if f["deleted"] or f["parent_item_id"] != trash_id:
                continue
            if f["obj_type"] == _FOLDER_OBJECT_TYPE:
                folders.append(self._to_folder_info(obj, root_id))
            else:
                files.append(self._to_file_info(obj, root_id))
        return FolderContents(
            folder=None,
            breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=folders,
            files=files,
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent_item_id = self._resolve_parent_item_id(creds, parent_id)
        root_id = self._root_object_id(creds)
        created = self._request(
            creds,
            "POST",
            f"/objects/{_FOLDER_OBJECT_TYPE}",
            json={
                "Class": _FOLDER_CLASS,
                "PropertyValues": [
                    self._text_property(_PD_NAME, name),
                    self._lookup_property(_PD_PARENT_FOLDER, parent_item_id),
                ],
            },
        )
        return self._to_folder_info(created, root_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        obj_type, obj_id = self._decode_id(folder_id)
        obj = self._set_property(creds, obj_type, obj_id, self._text_property(_PD_NAME, name))
        return self._to_folder_info(obj, self._root_object_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        obj_type, obj_id = self._decode_id(folder_id)
        target = self._resolve_parent_item_id(creds, new_parent_id)
        obj = self._set_property(creds, obj_type, obj_id, self._lookup_property(_PD_PARENT_FOLDER, target))
        return self._to_folder_info(obj, self._root_object_id(creds))

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        # See module docstring: this is M-Files' own (recoverable)
        # soft-delete — no confidently-known hard-delete call exists in
        # MFWS, so it's used here as the closest available action for a
        # "permanent" delete. Disclosed limitation, not an oversight.
        obj_type, obj_id = self._decode_id(folder_id)
        self._request(creds, "DELETE", f"/objects/{obj_type}/{obj_id}/latest")

    # --- files ---

    def create_document(
        self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes
    ) -> FileInfo:
        parent_item_id = self._resolve_parent_item_id(creds, folder_id)
        root_id = self._root_object_id(creds)
        title, ext = self._split_name(name)
        properties = {
            "Class": _DOCUMENT_CLASS,
            "PropertyValues": [
                self._text_property(_PD_NAME, title),
                self._lookup_property(_PD_PARENT_FOLDER, parent_item_id),
            ],
            "Files": [{"Title": title, "Extension": ext}],
        }
        files = {
            "file_0": (name, content, content_type or "application/octet-stream"),
            "properties": (None, json.dumps(properties), "application/json"),
        }
        created = self._request(creds, "POST", "/objects", files=files)
        return self._to_file_info(created, root_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        obj_type, obj_id = self._decode_id(file_id)
        obj = self._request(creds, "GET", f"/objects/{obj_type}/{obj_id}/latest")
        return self._to_file_info(obj, self._root_object_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        obj_type, obj_id = self._decode_id(file_id)
        title, _ext = self._split_name(name)
        obj = self._set_property(creds, obj_type, obj_id, self._text_property(_PD_NAME, title))
        return self._to_file_info(obj, self._root_object_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        obj_type, obj_id = self._decode_id(file_id)
        target = self._resolve_parent_item_id(creds, new_folder_id)
        obj = self._set_property(creds, obj_type, obj_id, self._lookup_property(_PD_PARENT_FOLDER, target))
        return self._to_file_info(obj, self._root_object_id(creds))

    def delete_file(self, creds: dict, file_id: str) -> None:
        obj_type, obj_id = self._decode_id(file_id)
        self._request(creds, "DELETE", f"/objects/{obj_type}/{obj_id}/latest")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        obj_type, obj_id = self._decode_id(file_id)
        obj = self._request(creds, "GET", f"/objects/{obj_type}/{obj_id}/latest")
        files_meta = obj.get("Files") or [{}]
        file_ref = files_meta[0].get("ID", 0)
        return self._content_bytes(creds, f"/objects/{obj_type}/{obj_id}/latest/files/{file_ref}/content")

    # --- versions ---

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        obj_type, obj_id = self._decode_id(file_id)
        current = self._request(creds, "GET", f"/objects/{obj_type}/{obj_id}/latest")
        current_version = (current.get("ObjVer") or {}).get("Version")
        history = self._request(creds, "GET", f"/objects/{obj_type}/{obj_id}/history")
        entries = history if isinstance(history, list) else (history.get("Items") or history.get("Versions") or [])
        out = []
        for e in entries:
            objver = e.get("ObjVer") or e
            version_number = objver.get("Version")
            if version_number is None:
                continue
            files_meta = e.get("Files") or current.get("Files") or []
            size_bytes = files_meta[0].get("Size") if files_meta else None
            content_type = _guess_content_type(files_meta[0].get("Extension")) if files_meta else None
            out.append(
                VersionInfo(
                    id=self._encode_version_id(obj_type, obj_id, version_number),
                    version_number=version_number,
                    size_bytes=size_bytes,
                    content_type=content_type,
                    is_current=(version_number == current_version),
                    updated_at=self._parse_dt(e.get("LastModified")),
                )
            )
        out.sort(key=lambda v: v.version_number, reverse=True)
        return out

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        obj_type, obj_id = self._decode_id(file_id)
        current = self._request(creds, "GET", f"/objects/{obj_type}/{obj_id}/latest")
        files_meta = current.get("Files") or [{}]
        file_ref = files_meta[0].get("ID", 0)
        resp = self._raw_request(
            creds,
            "PUT",
            f"/objects/{obj_type}/{obj_id}/latest/files/{file_ref}/content",
            data=content,
            extra_headers={"Content-Type": content_type or "application/octet-stream"},
        )
        if resp.status_code == 401:
            raise ProviderError("Invalid M-Files credentials", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError(f"M-Files version upload failed ({resp.status_code})", status_code=502)
        obj = self._request(creds, "GET", f"/objects/{obj_type}/{obj_id}/latest")
        return self._to_file_info(obj, self._root_object_id(creds))

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        obj_type, obj_id, version = self._decode_version_id(version_id)
        obj = self._request(creds, "GET", f"/objects/{obj_type}/{obj_id}/{version}")
        files_meta = obj.get("Files") or [{}]
        file_ref = files_meta[0].get("ID", 0)
        return self._content_bytes(creds, f"/objects/{obj_type}/{obj_id}/{version}/files/{file_ref}/content")

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        # No confidently-known "revert to this version" endpoint — the
        # safe, always-correct fallback is to pull the old version's bytes
        # and re-upload them as a brand-new latest version.
        content = self.get_version_content(creds, file_id, version_id)
        obj_type, obj_id, version = self._decode_version_id(version_id)
        obj = self._request(creds, "GET", f"/objects/{obj_type}/{obj_id}/{version}")
        files_meta = obj.get("Files") or [{}]
        content_type = _guess_content_type(files_meta[0].get("Extension")) or "application/octet-stream"
        return self.create_version(creds, file_id, content_type, content)

    # --- trash (emulated — see module docstring) ---

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        obj_type, obj_id = self._decode_id(folder_id)
        trash_id = self._trash_object_id(creds)
        self._set_property(creds, obj_type, obj_id, self._lookup_property(_PD_PARENT_FOLDER, trash_id))

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        obj_type, obj_id = self._decode_id(folder_id)
        root_id = self._root_object_id(creds)
        obj = self._set_property(creds, obj_type, obj_id, self._lookup_property(_PD_PARENT_FOLDER, root_id))
        return self._to_folder_info(obj, root_id)

    def trash_file(self, creds: dict, file_id: str) -> None:
        obj_type, obj_id = self._decode_id(file_id)
        trash_id = self._trash_object_id(creds)
        self._set_property(creds, obj_type, obj_id, self._lookup_property(_PD_PARENT_FOLDER, trash_id))

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        obj_type, obj_id = self._decode_id(file_id)
        root_id = self._root_object_id(creds)
        obj = self._set_property(creds, obj_type, obj_id, self._lookup_property(_PD_PARENT_FOLDER, root_id))
        return self._to_file_info(obj, root_id)

    # --- search ---

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root_id = self._root_object_id(creds)
        folders: list[FolderInfo] = []
        files: list[FileInfo] = []
        for obj in self._quick_search(creds, query):
            f = self._obj_fields(obj)
            if f["deleted"]:
                continue
            if f["obj_type"] == _FOLDER_OBJECT_TYPE:
                if f["name"] in (_APP_ROOT_NAME, _TRASH_FOLDER_NAME):
                    continue
                folders.append(self._to_folder_info(obj, root_id))
            else:
                files.append(self._to_file_info(obj, root_id))
        return folders, files
