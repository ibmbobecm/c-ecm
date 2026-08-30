"""IBM Z (z/OS mainframe) storage provider for C-ECM.

Documents on z/OS can live in three places; this provider handles all three:

  1. z/OS Datasets (PDS members + sequential datasets) — accessed via the
     z/OSMF REST Data Set API (`/zosmf/restfiles/ds`). Browsing requires a
     starting high-level qualifier (`dataset_hlq`) since there's no way to
     list "all datasets" on a mainframe; PDS libraries under that HLQ map to
     folders, their members map to files, and non-partitioned (sequential)
     datasets under it map to files directly. Read-only: allocating or
     rewriting a dataset has attributes (LRECL, DSORG, space) this provider
     has no basis for choosing correctly, so create/rename/move/delete all
     return 501 for dataset-mode ids rather than guess.

  2. USS (Unix System Services) — a POSIX hierarchy accessible through the
     z/OSMF REST USS File API (`/zosmf/restfiles/fs`). Browsed like a regular
     filesystem — identical UX to the IBM i IFS path. This is still the
     default root view; the Datasets library only appears as an extra
     top-level folder when `dataset_hlq` is configured, so existing
     connections that never set it see no change in behaviour.

  3. IBM Content Manager OnDemand (CMOD) — an archive/ECM system. Its
     documented REST Services use base path `/cmod-rest/v1/...`
     (GET /cmod-rest/v1/folders, GET /cmod-rest/v1/folders/{name},
     POST /cmod-rest/v1/document -- confirmed against IBM's own CMOD REST
     Services material). Document-content-retrieval and search endpoints
     are NOT independently confirmed (the fuller API reference wasn't
     reachable while writing this), so those two calls follow the same
     /cmod-rest/v1/ prefix as a best-effort guess, not a verified shape --
     narrower uncertainty than before this file used a prefix
     (`/IBMcmRecordsView/...`) that doesn't appear in any real CMOD
     documentation at all.

Connection credentials collected by `config_fields`:
  - zosmf_url    : z/OSMF base URL (e.g. https://mainframe:10443)
  - system       : z/OS system name (for display)
  - uss_root     : USS root path (default /u)
  - dataset_hlq  : optional high-level qualifier to browse datasets under
  - cm8_url      : optional CMOD REST base URL

UNVERIFIED against a live z/OS system — written against z/OSMF REST
Services documentation (dataset listing, member listing, and content
retrieval endpoints all independently confirmed against IBM's own docs)
and, for CMOD, the partial reference above. Verify against a live system
before production use.
"""

import base64
import logging
import urllib.parse
from datetime import datetime, timezone

logger = logging.getLogger("ibmz_provider")

try:
    import requests as _requests  # type: ignore
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

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

_TRASH_DIR = ".C-ECMTrash"


def _require_requests() -> None:
    if not _HAS_REQUESTS:
        raise ProviderError(
            "requests is required for the IBM Z provider. Install it with: pip install requests", 503
        )


def _basic(creds: dict) -> str:
    return base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()


def _headers(creds: dict) -> dict:
    return {
        "Authorization": f"Basic {_basic(creds)}",
        "Content-Type": "application/json",
        "X-CSRF-ZOSMF-HEADER": "*",
    }


def _zosmf(creds: dict) -> str:
    return creds["zosmf_url"].rstrip("/")


def _uss_root(creds: dict) -> str:
    return (creds.get("uss_root") or "/u").rstrip("/") or "/"


def _cm8_url(creds: dict) -> str | None:
    return (creds.get("cm8_url") or "").strip() or None


def _dt_uss(value: str | None) -> datetime | None:
    """Parse z/OSMF USS timestamps: '2024-01-15T09:00:00'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _dt_ds(value: str | None) -> datetime | None:
    """Parse z/OSMF dataset lastReferredDate 'YYYY/MM/DD'."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y/%m/%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _get(creds: dict, url: str, params: dict | None = None) -> dict:
    _require_requests()
    try:
        resp = _requests.get(url, headers=_headers(creds), params=params, timeout=20, verify=False)
    except _requests.RequestException as exc:
        raise ProviderError(f"z/OSMF unreachable: {exc}", status_code=502)
    if resp.status_code == 401:
        raise ProviderError("Invalid z/OS credentials", status_code=401)
    if resp.status_code == 404:
        raise ProviderError("Not found on z/OS", status_code=404)
    if resp.status_code >= 400:
        raise ProviderError(f"z/OSMF error {resp.status_code}: {resp.text[:300]}", status_code=502)
    return resp.json() if resp.content else {}


def _put_bytes(creds: dict, url: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    _require_requests()
    hdrs = {**_headers(creds), "Content-Type": content_type}
    try:
        resp = _requests.put(url, headers=hdrs, data=data, timeout=60, verify=False)
    except _requests.RequestException as exc:
        raise ProviderError(f"z/OSMF unreachable: {exc}", status_code=502)
    if resp.status_code >= 400:
        raise ProviderError(f"z/OSMF write error {resp.status_code}: {resp.text[:300]}", status_code=502)


def _delete(creds: dict, url: str) -> None:
    _require_requests()
    try:
        resp = _requests.delete(url, headers=_headers(creds), timeout=20, verify=False)
    except _requests.RequestException as exc:
        raise ProviderError(f"z/OSMF unreachable: {exc}", status_code=502)
    if resp.status_code >= 400:
        raise ProviderError(f"z/OSMF delete error {resp.status_code}", status_code=502)


class IBMZProvider(StorageProvider):
    """IBM Z z/OS provider — USS files + z/OSMF datasets + CM8 ECM."""

    key = "ibm_z"
    display_name = "IBM Z (z/OS Mainframe)"
    auth_mode = AuthMode.CREDENTIALS

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("zosmf_url", "z/OSMF Base URL", "https://mainframe:10443"),
            ConfigField("system", "System Name", "SYSA", required=False),
            ConfigField("uss_root", "USS Root Path", "/u", required=False),
            ConfigField("dataset_hlq", "Dataset High-Level Qualifier (optional)", "", required=False),
            ConfigField("cm8_url", "CMOD REST Base URL (optional)", "", required=False),
        ]

    # --- auth ---

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        _require_requests()
        config = config or {}
        zosmf_url = (config.get("zosmf_url") or "").strip()
        if not zosmf_url:
            raise ProviderError("z/OSMF URL is required", status_code=400)
        creds = {
            "username": username,
            "password": password,
            "zosmf_url": zosmf_url,
            "system": (config.get("system") or "").strip(),
            "uss_root": (config.get("uss_root") or "/u").strip(),
            "dataset_hlq": (config.get("dataset_hlq") or "").strip().upper(),
            "cm8_url": (config.get("cm8_url") or "").strip(),
        }
        try:
            _get(creds, f"{zosmf_url}/zosmf/info")
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        system = creds.get("system") or creds["zosmf_url"]
        return f"{creds['username']}@{system}"

    # =========================================================================
    # Routing: CM8 takes priority when configured; otherwise USS (with an
    # optional Datasets pseudo-folder injected at the USS root when
    # dataset_hlq is configured)
    # =========================================================================

    def _use_cm8(self, creds: dict) -> bool:
        return bool(_cm8_url(creds))

    def _dataset_hlq(self, creds: dict) -> str:
        return (creds.get("dataset_hlq") or "").strip()

    def _use_datasets(self, creds: dict) -> bool:
        return bool(self._dataset_hlq(creds))

    # =========================================================================
    # z/OS Dataset operations (PDS libraries + members, sequential datasets)
    #
    # Read-only by design -- see module docstring. IDs are prefixed "ds:" to
    # distinguish them from USS paths (which always start with "/"):
    #   __datasets__              the pseudo-root folder itself
    #   ds:HLQ.MYLIB              a PDS library (folder) or sequential
    #                             dataset (file) -- same id shape, the
    #                             caller already knows which from whether
    #                             it came back as a FolderInfo or FileInfo
    #   ds:HLQ.MYLIB(MEMBER)      a PDS member (file)
    # =========================================================================

    _DATASETS_ROOT_ID = "__datasets__"

    def _dataset_root_listing(self, creds: dict) -> FolderContents:
        hlq = self._dataset_hlq(creds)
        folders, files = [], []
        data = _get(creds, f"{_zosmf(creds)}/zosmf/restfiles/ds", params={"dslevel": f"{hlq}.*"})
        for item in data.get("items", []):
            dsn = item.get("dsname", "")
            if not dsn:
                continue
            dsorg = item.get("dsorg", "")
            entry_id = f"ds:{dsn}"
            if "PO" in dsorg:
                folders.append(FolderInfo(id=entry_id, name=dsn, parent_id=self._DATASETS_ROOT_ID))
            else:
                files.append(FileInfo(
                    id=entry_id, name=dsn, folder_id=self._DATASETS_ROOT_ID,
                    version_number=1, size_bytes=None, content_type=None,
                    updated_at=_dt_ds(item.get("catnm")),
                ))
        return FolderContents(
            folder=FolderInfo(id=self._DATASETS_ROOT_ID, name="z/OS Datasets", parent_id=None),
            breadcrumb=[BreadcrumbEntry(id=None, name="z/OS Datasets")],
            folders=folders,
            files=files,
        )

    def _dataset_list_members(self, creds: dict, dataset_name: str) -> FolderContents:
        # GET /zosmf/restfiles/ds/{dataset-name}/member -- confirmed against
        # IBM's z/OSMF REST Services documentation.
        encoded = urllib.parse.quote(dataset_name, safe="")
        data = _get(creds, f"{_zosmf(creds)}/zosmf/restfiles/ds/{encoded}/member")
        files = []
        for item in data.get("items", []):
            member = item.get("member", "")
            if not member:
                continue
            files.append(FileInfo(
                id=f"ds:{dataset_name}({member})", name=member,
                folder_id=f"ds:{dataset_name}", version_number=1,
                size_bytes=None, content_type=None, updated_at=None,
            ))
        return FolderContents(
            folder=FolderInfo(id=f"ds:{dataset_name}", name=dataset_name, parent_id=self._DATASETS_ROOT_ID),
            breadcrumb=[
                BreadcrumbEntry(id=None, name="z/OS Datasets"),
                BreadcrumbEntry(id=self._DATASETS_ROOT_ID, name=dataset_name),
            ],
            folders=[],
            files=files,
        )

    def _dataset_read(self, creds: dict, dataset_or_member: str) -> bytes:
        # GET /zosmf/restfiles/ds/{dataset-name}[(member-name)] -- confirmed
        # against IBM's z/OSMF REST Services documentation. Parens are part
        # of the literal path for a PDS member, not URL-encoded away.
        _require_requests()
        resp = _requests.get(
            f"{_zosmf(creds)}/zosmf/restfiles/ds/{dataset_or_member}",
            headers={**_headers(creds), "Content-Type": "application/octet-stream"},
            timeout=60,
            verify=False,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Dataset not found or unreadable: {dataset_or_member}", status_code=404)
        return resp.content

    # =========================================================================
    # USS operations
    # =========================================================================

    def _uss_get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root = _uss_root(creds)
        path = folder_id if folder_id is not None else root
        encoded = urllib.parse.quote(path, safe="")
        data = _get(creds, f"{_zosmf(creds)}/zosmf/restfiles/fs/{encoded}")
        items = data.get("items", [])
        folders, files = [], []
        for item in items:
            name = item.get("name", "")
            if name in (".", ".."):
                continue
            full = f"{path.rstrip('/')}/{name}"
            ftype = item.get("mode", "")
            if ftype.startswith("d"):
                folders.append(FolderInfo(id=full, name=name,
                                           parent_id=None if path == root else path))
            else:
                files.append(FileInfo(
                    id=full, name=name,
                    folder_id=None if path == root else path,
                    version_number=1,
                    size_bytes=item.get("size"),
                    content_type=None,
                    updated_at=_dt_uss(item.get("mtime")),
                ))
        current = None
        if folder_id is not None:
            current = FolderInfo(id=path, name=path.rsplit("/", 1)[-1], parent_id=None)
        # Inject the Datasets pseudo-folder at the true USS root, only when
        # configured -- connections that never set dataset_hlq see no change.
        if folder_id is None and self._use_datasets(creds):
            folders = [FolderInfo(id=self._DATASETS_ROOT_ID, name="z/OS Datasets", parent_id=None), *folders]
        return FolderContents(
            folder=current,
            breadcrumb=[BreadcrumbEntry(id=None, name="z/OS USS")],
            folders=folders,
            files=files,
        )

    def _uss_create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = parent_id if parent_id is not None else _uss_root(creds)
        path = f"{parent.rstrip('/')}/{name}"
        encoded = urllib.parse.quote(path, safe="")
        _require_requests()
        resp = _requests.post(
            f"{_zosmf(creds)}/zosmf/restfiles/fs/{encoded}",
            headers=_headers(creds),
            json={"type": "mkdir"},
            timeout=20,
            verify=False,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"mkdir failed: {resp.text[:200]}", status_code=502)
        return FolderInfo(id=path, name=name,
                          parent_id=None if parent == _uss_root(creds) else parent)

    def _uss_write(self, creds: dict, path: str, content: bytes) -> None:
        encoded = urllib.parse.quote(path, safe="")
        _put_bytes(creds, f"{_zosmf(creds)}/zosmf/restfiles/fs/{encoded}", content, "application/octet-stream")

    def _uss_read(self, creds: dict, path: str) -> bytes:
        encoded = urllib.parse.quote(path, safe="")
        _require_requests()
        resp = _requests.get(
            f"{_zosmf(creds)}/zosmf/restfiles/fs/{encoded}",
            headers={**_headers(creds), "Content-Type": "application/octet-stream"},
            timeout=60,
            verify=False,
        )
        if resp.status_code >= 400:
            raise ProviderError("File not found on USS", status_code=404)
        return resp.content

    def _uss_delete(self, creds: dict, path: str) -> None:
        encoded = urllib.parse.quote(path, safe="")
        _delete(creds, f"{_zosmf(creds)}/zosmf/restfiles/fs/{encoded}")

    def _uss_rename(self, creds: dict, old_path: str, new_path: str) -> None:
        encoded = urllib.parse.quote(new_path, safe="")
        _require_requests()
        resp = _requests.put(
            f"{_zosmf(creds)}/zosmf/restfiles/fs/{encoded}",
            headers=_headers(creds),
            json={"from": old_path, "overwrite": True},
            timeout=20,
            verify=False,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Rename failed: {resp.text[:200]}", status_code=502)

    # =========================================================================
    # CMOD operations (IBM Content Manager OnDemand REST Services)
    #
    # Base path /cmod-rest/v1 and the folders endpoints are confirmed against
    # IBM's own CMOD REST Services material (GET /cmod-rest/v1/folders,
    # GET /cmod-rest/v1/folders/{name}, POST /cmod-rest/v1/document). Content
    # retrieval and search below follow the same prefix as a best-effort,
    # NOT independently confirmed shape -- the fuller API reference wasn't
    # reachable while writing this. Previously this entire section used
    # /IBMcmRecordsView/..., a path that doesn't appear in any real CMOD
    # documentation at all.
    # =========================================================================

    def _cm8_headers(self, creds: dict) -> dict:
        return {"Authorization": f"Basic {_basic(creds)}", "Content-Type": "application/json"}

    def _cm8_get(self, creds: dict, path: str, params: dict | None = None) -> dict:
        _require_requests()
        url = f"{_cm8_url(creds)}{path}"
        try:
            resp = _requests.get(url, headers=self._cm8_headers(creds), params=params, timeout=20, verify=False)
        except _requests.RequestException as exc:
            raise ProviderError(f"CMOD unreachable: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"CMOD error {resp.status_code}: {resp.text[:200]}", status_code=502)
        return resp.json() if resp.content else {}

    def _cm8_get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        path = f"/cmod-rest/v1/folders/{folder_id}" if folder_id else "/cmod-rest/v1/folders"
        data = self._cm8_get(creds, path)
        folders = []
        for f in data.get("folders", []):
            folders.append(FolderInfo(id=str(f["id"]), name=f["name"],
                                      parent_id=str(f.get("parentId")) if f.get("parentId") else None))
        files = []
        for doc in data.get("documents", []):
            files.append(FileInfo(
                id=str(doc["id"]), name=doc["name"],
                folder_id=folder_id,
                version_number=int(doc.get("version", 1)),
                size_bytes=doc.get("fileSize"),
                content_type=doc.get("mimeType"),
                updated_at=None,
            ))
        return FolderContents(
            folder=FolderInfo(id=folder_id or "root", name="Root", parent_id=None) if folder_id else None,
            breadcrumb=[BreadcrumbEntry(id=None, name="CMOD")],
            folders=folders,
            files=files,
        )

    def _cm8_get_content(self, creds: dict, doc_id: str) -> bytes:
        _require_requests()
        url = f"{_cm8_url(creds)}/cmod-rest/v1/documents/{doc_id}/content"
        resp = _requests.get(url, headers=self._cm8_headers(creds), timeout=60, verify=False)
        if resp.status_code >= 400:
            raise ProviderError("CMOD document content not found", status_code=404)
        return resp.content

    def _cm8_search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        data = self._cm8_get(creds, "/cmod-rest/v1/search", {"query": query, "maxResults": 100})
        files = []
        for doc in data.get("documents", []):
            files.append(FileInfo(
                id=str(doc["id"]), name=doc["name"],
                folder_id=str(doc.get("folderId")) if doc.get("folderId") else None,
                version_number=int(doc.get("version", 1)),
                size_bytes=doc.get("fileSize"),
                content_type=doc.get("mimeType"),
                updated_at=None,
            ))
        return [], files

    # =========================================================================
    # StorageProvider interface — delegates to CM8 or USS
    # =========================================================================

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        if self._use_cm8(creds):
            return self._cm8_get_children(creds, folder_id)
        if folder_id == self._DATASETS_ROOT_ID:
            return self._dataset_root_listing(creds)
        if isinstance(folder_id, str) and folder_id.startswith("ds:"):
            return self._dataset_list_members(creds, folder_id[len("ds:"):])
        return self._uss_get_children(creds, folder_id)

    def list_trash(self, creds: dict) -> FolderContents:
        if self._use_cm8(creds):
            # CM8 has its own recycle bin — return empty for now
            return FolderContents(
                folder=None,
                breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                folders=[], files=[],
            )
        trash = f"{_uss_root(creds)}/{_TRASH_DIR}"
        try:
            result = self._uss_get_children(creds, trash)
        except ProviderError:
            result = FolderContents(folder=None, breadcrumb=[], folders=[], files=[])
        result.breadcrumb = [BreadcrumbEntry(id=None, name="Trash")]
        return result

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        if self._use_cm8(creds):
            raise ProviderError("Folder creation via CMOD REST is not supported in this release", status_code=501)
        if parent_id == self._DATASETS_ROOT_ID or (isinstance(parent_id, str) and parent_id.startswith("ds:")):
            raise ProviderError(
                "Allocating a new dataset isn't supported -- it needs attributes "
                "(record format, space, etc.) this provider has no basis for choosing.",
                status_code=501,
            )
        return self._uss_create_folder(creds, parent_id, name)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        if folder_id.startswith("ds:") or folder_id == self._DATASETS_ROOT_ID:
            raise ProviderError("Renaming a dataset is not supported in this release", status_code=501)
        parent = folder_id.rsplit("/", 1)[0] or _uss_root(creds)
        new_path = f"{parent}/{name}"
        self._uss_rename(creds, folder_id, new_path)
        return FolderInfo(id=new_path, name=name,
                          parent_id=None if parent == _uss_root(creds) else parent)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        if folder_id.startswith("ds:") or folder_id == self._DATASETS_ROOT_ID:
            raise ProviderError("Moving a dataset is not supported in this release", status_code=501)
        new_parent = new_parent_id if new_parent_id is not None else _uss_root(creds)
        name = folder_id.rsplit("/", 1)[-1]
        new_path = f"{new_parent.rstrip('/')}/{name}"
        self._uss_rename(creds, folder_id, new_path)
        return FolderInfo(id=new_path, name=name,
                          parent_id=None if new_parent == _uss_root(creds) else new_parent)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        if folder_id.startswith("ds:") or folder_id == self._DATASETS_ROOT_ID:
            raise ProviderError("Deleting a dataset is not supported in this release", status_code=501)
        self._uss_delete(creds, folder_id)

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        if self._use_cm8(creds):
            raise ProviderError("Document upload via CMOD REST is not supported in this release", status_code=501)
        if folder_id == self._DATASETS_ROOT_ID or (isinstance(folder_id, str) and folder_id.startswith("ds:")):
            raise ProviderError(
                "Writing a new dataset member isn't supported -- it needs attributes "
                "(record format, space, etc.) this provider has no basis for choosing.",
                status_code=501,
            )
        parent = folder_id if folder_id is not None else _uss_root(creds)
        path = f"{parent.rstrip('/')}/{name}"
        self._uss_write(creds, path, content)
        return FileInfo(id=path, name=name,
                        folder_id=None if parent == _uss_root(creds) else parent,
                        version_number=1, size_bytes=len(content),
                        content_type=content_type)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        if self._use_cm8(creds):
            data = self._cm8_get(creds, f"/cmod-rest/v1/documents/{file_id}")
            return FileInfo(id=file_id, name=data.get("name", file_id),
                            folder_id=str(data["folderId"]) if data.get("folderId") else None,
                            version_number=int(data.get("version", 1)),
                            size_bytes=data.get("fileSize"),
                            content_type=data.get("mimeType"))
        if file_id.startswith("ds:"):
            raw = file_id[len("ds:"):]
            # "HLQ.LIB(MEMBER)" -> folder_id is the library; a bare
            # "HLQ.SEQDS" sequential dataset's folder is the Datasets root.
            if "(" in raw:
                lib = raw.split("(", 1)[0]
                name = raw
                folder_id = f"ds:{lib}"
            else:
                name = raw
                folder_id = self._DATASETS_ROOT_ID
            return FileInfo(id=file_id, name=name, folder_id=folder_id,
                            version_number=1, size_bytes=None, content_type=None)
        # USS: stat via listdir of parent
        parent = file_id.rsplit("/", 1)[0]
        name = file_id.rsplit("/", 1)[-1]
        encoded = urllib.parse.quote(parent, safe="")
        data = _get(creds, f"{_zosmf(creds)}/zosmf/restfiles/fs/{encoded}")
        for item in data.get("items", []):
            if item.get("name") == name:
                return FileInfo(id=file_id, name=name,
                                folder_id=None if parent == _uss_root(creds) else parent,
                                version_number=1, size_bytes=item.get("size"),
                                content_type=None,
                                updated_at=_dt_uss(item.get("mtime")))
        raise ProviderError("File not found on USS", status_code=404)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        if file_id.startswith("ds:"):
            raise ProviderError("Renaming a dataset member is not supported in this release", status_code=501)
        parent = file_id.rsplit("/", 1)[0]
        new_path = f"{parent}/{name}"
        self._uss_rename(creds, file_id, new_path)
        return FileInfo(id=new_path, name=name,
                        folder_id=None if parent == _uss_root(creds) else parent,
                        version_number=1, size_bytes=None, content_type=None)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        if file_id.startswith("ds:"):
            raise ProviderError("Moving a dataset member is not supported in this release", status_code=501)
        name = file_id.rsplit("/", 1)[-1]
        new_parent = new_folder_id if new_folder_id is not None else _uss_root(creds)
        new_path = f"{new_parent.rstrip('/')}/{name}"
        self._uss_rename(creds, file_id, new_path)
        return FileInfo(id=new_path, name=name,
                        folder_id=None if new_parent == _uss_root(creds) else new_parent,
                        version_number=1, size_bytes=None, content_type=None)

    def delete_file(self, creds: dict, file_id: str) -> None:
        if self._use_cm8(creds):
            raise ProviderError("CMOD delete not supported in this release", status_code=501)
        if file_id.startswith("ds:"):
            raise ProviderError("Deleting a dataset member is not supported in this release", status_code=501)
        self._uss_delete(creds, file_id)

    def get_content(self, creds: dict, file_id: str) -> bytes:
        if self._use_cm8(creds):
            return self._cm8_get_content(creds, file_id)
        if file_id.startswith("ds:"):
            return self._dataset_read(creds, file_id[len("ds:"):])
        return self._uss_read(creds, file_id)

    # --- versions (z/OSMF has no native versioning — report single current) ---

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        info = self.get_file(creds, file_id)
        return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                            content_type=info.content_type, is_current=True, updated_at=info.updated_at)]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        if file_id.startswith("ds:"):
            raise ProviderError("Writing a new version of a dataset member is not supported in this release", status_code=501)
        self._uss_write(creds, file_id, content)
        return FileInfo(id=file_id, name=file_id.rsplit("/", 1)[-1],
                        folder_id=None, version_number=1,
                        size_bytes=len(content), content_type=content_type)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        return self.get_content(creds, file_id)

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        return self.get_file(creds, file_id)

    # --- trash (USS only -- datasets/CMOD have no trash concept here) ---

    def trash_file(self, creds: dict, file_id: str) -> None:
        if file_id.startswith("ds:"):
            raise ProviderError("Trashing a dataset member is not supported in this release", status_code=501)
        trash = f"{_uss_root(creds)}/{_TRASH_DIR}"
        name = file_id.rsplit("/", 1)[-1]
        self._uss_rename(creds, file_id, f"{trash}/{name}")

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = _uss_root(creds)
        name = file_id.rsplit("/", 1)[-1]
        new_path = f"{root}/{name}"
        self._uss_rename(creds, file_id, new_path)
        return FileInfo(id=new_path, name=name, folder_id=None,
                        version_number=1, size_bytes=None, content_type=None)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        if folder_id.startswith("ds:") or folder_id == self._DATASETS_ROOT_ID:
            raise ProviderError("Trashing a dataset is not supported in this release", status_code=501)
        trash = f"{_uss_root(creds)}/{_TRASH_DIR}"
        name = folder_id.rsplit("/", 1)[-1]
        self._uss_rename(creds, folder_id, f"{trash}/{name}")

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = _uss_root(creds)
        name = folder_id.rsplit("/", 1)[-1]
        new_path = f"{root}/{name}"
        self._uss_rename(creds, folder_id, new_path)
        return FolderInfo(id=new_path, name=name, parent_id=None)

    # --- search ---

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        if self._use_cm8(creds):
            return self._cm8_search(creds, query)
        # z/OSMF: search datasets by name pattern. "ds:" prefix matches the
        # id convention used by the dataset-browsing methods above, so a
        # search hit and a browsed entry behave identically once clicked.
        q = query.upper()
        folders: list[FolderInfo] = []
        files: list[FileInfo] = []
        try:
            data = _get(creds, f"{_zosmf(creds)}/zosmf/restfiles/ds",
                        params={"dslevel": f"*.{q}*", "listDetails": "volume"})
            for item in data.get("items", []):
                dsn = item.get("dsname", "")
                dsorg = item.get("dsorg", "")
                if "PO" in dsorg:
                    folders.append(FolderInfo(id=f"ds:{dsn}", name=dsn, parent_id=None))
                else:
                    files.append(FileInfo(id=f"ds:{dsn}", name=dsn, folder_id=None,
                                          version_number=1, size_bytes=None,
                                          content_type=None,
                                          updated_at=_dt_ds(item.get("catnm"))))
        except ProviderError:
            pass
        # Also search USS tree at root
        try:
            root = _uss_root(creds)
            encoded = urllib.parse.quote(root, safe="")
            data = _get(creds, f"{_zosmf(creds)}/zosmf/restfiles/fs/{encoded}",
                        params={"search": query, "maxdepth": "5"})
            for item in data.get("items", []):
                name = item.get("name", "")
                if not name or name in (".", ".."):
                    continue
                full = f"{root}/{name}"
                if item.get("mode", "").startswith("d"):
                    folders.append(FolderInfo(id=full, name=name, parent_id=None))
                else:
                    files.append(FileInfo(id=full, name=name, folder_id=None,
                                          version_number=1, size_bytes=item.get("size"),
                                          content_type=None, updated_at=None))
        except ProviderError:
            pass
        return folders[:100], files[:100]
