"""Adapts the existing FileNet CEWS/Java-bridge integration (filenet_client.py)
onto the generic StorageProvider interface. All the actual FileNet-specific
logic (WSI auth, the content-write bridge, path/GUID handling) already lives
in filenet_client.py and is unchanged here — this is purely a translation
layer: opaque folder_id/file_id <-> FileNet paths, and FileNetError <->
ProviderError.

Every connection carries its own server (WSDL/endpoint/IIOP URLs, object
store, root path) in `creds` — nothing here assumes there's only one FileNet
installation. `_conn()` rebuilds a `fnc.FileNetConn` from that on every call.
"""

import threading

from .. import filenet_client as fnc
from ..config import (
    FILENET_ENDPOINT_URL,
    FILENET_IIOP_URI,
    FILENET_OBJECT_STORE,
    FILENET_ROOT_PATH,
    FILENET_WSDL_URL,
)
from ..serializers import _parse_dt as parse_dt
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

_TRASH_NAME = "$Trash"

# (object_store, root_path) pairs already verified/created in this process.
# A connection's stored creds only pin username/password by default — root_path
# falls back to the live FILENET_ROOT_PATH config default on every call. That
# default used to only get ensured once, in authenticate(), so a connection
# created before a config change (e.g. the FileDrive -> C-ECM rebrand moving
# the default from /FileDrive to /C-ECM) silently 404s on every subsequent
# call once the default changes, because the new path was never created for
# it. Ensuring it lazily here, from the one choke point every operation
# resolves a folder path through, makes that self-healing regardless of when
# the connection was created or whether the default changes again later.
_ensured_roots: set[tuple[str, str]] = set()
_ensured_roots_lock = threading.Lock()


def _wrap(exc: fnc.FileNetError, not_found_detail: str = "Not found") -> ProviderError:
    # `detail` here can be a full Java stack trace or SOAP fault dump — never
    # put it straight into the message a user sees. Recognize the specific
    # failure classes this bridge is known to hit and give a short, honest
    # message instead; the raw detail still reaches the server log via
    # access_helpers.to_http(), for actually debugging it.
    detail = exc.detail or str(exc)
    lower = detail.lower()
    if "secj0395e" in lower or ("e_not_authenticated" in lower and "securityserver" in lower):
        return ProviderError(
            "This connection's remote server rejected the content operation at the WebSphere "
            "security layer (SECJ0395E) — its CSIv2/security configuration needs adjustment on "
            "that server; this isn't something a retry fixes.",
            status_code=502, detail=detail,
        )
    if "e_not_authenticated" in lower or "authenticationfailedexception" in lower:
        return ProviderError("Authentication failed for this connection's content operation.", status_code=401, detail=detail)
    if "timed out" in lower:
        return ProviderError("The FileNet server didn't respond in time.", status_code=504, detail=detail)
    if "not found" in lower or "does not exist" in lower:
        return ProviderError(not_found_detail, status_code=404, detail=detail)
    if "access" in lower and ("denied" in lower or "not allowed" in lower):
        return ProviderError("Access denied", status_code=403, detail=detail)
    # Every bridge invocation prints this JVM startup noise line first,
    # regardless of outcome — skip it when picking a one-line summary.
    lines = [ln for ln in detail.strip().splitlines() if "no interval found" not in ln.lower()]
    first_line = lines[0] if lines else "Unknown error"
    return ProviderError(f"FileNet error: {first_line[:200]}", status_code=502, detail=detail)


def _folder_info(row: dict, parent_id: str | None) -> FolderInfo:
    name = row.get("FolderName") or (row.get("PathName") or "").rsplit("/", 1)[-1]
    return FolderInfo(id=row["Id"], name=name, parent_id=parent_id, created_at=parse_dt(row.get("DateCreated")))


def _file_info(row: dict, folder_id: str | None) -> FileInfo:
    size = row.get("ContentSize")
    try:
        size_bytes = int(float(size)) if size is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    try:
        version_number = int(row.get("MajorVersionNumber"))
    except (TypeError, ValueError):
        version_number = 0
    return FileInfo(
        id=row["Id"],
        name=row.get("DocumentTitle") or "Untitled",
        folder_id=folder_id,
        version_number=version_number,
        size_bytes=size_bytes,
        content_type=row.get("MimeType"),
        updated_at=parse_dt(row.get("DateLastModified")),
    )


def _first_folder_id(obj: dict) -> str | None:
    folders = obj.get("FoldersFiledIn")
    if isinstance(folders, list) and folders:
        return getattr(folders[0], "objectId", None)
    return None


class FileNetProvider(StorageProvider):
    key = "filenet"
    display_name = "IBM FileNet"
    auth_mode = AuthMode.CREDENTIALS

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("wsdl_url", "WSDL URL", FILENET_WSDL_URL),
            ConfigField("endpoint_url", "CEWS endpoint URL", FILENET_ENDPOINT_URL),
            ConfigField("object_store", "Object store", FILENET_OBJECT_STORE),
            ConfigField("root_path", "Root folder path", FILENET_ROOT_PATH, required=False),
            ConfigField("iiop_uri", "IIOP URI (content uploads)", FILENET_IIOP_URI, required=False),
        ]

    def _conn(self, creds: dict) -> fnc.FileNetConn:
        return fnc.FileNetConn(
            username=creds["username"],
            password=creds["password"],
            wsdl_url=creds.get("wsdl_url") or FILENET_WSDL_URL,
            endpoint_url=creds.get("endpoint_url") or FILENET_ENDPOINT_URL,
            object_store=creds.get("object_store") or FILENET_OBJECT_STORE,
            root_path=creds.get("root_path") or FILENET_ROOT_PATH,
            iiop_uri=creds.get("iiop_uri") or FILENET_IIOP_URI,
        )

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        creds = {"username": username, "password": password, **config}
        conn = self._conn(creds)
        if not fnc.authenticate(conn):
            return None
        self._ensure_path(conn, conn.root_path)
        return creds

    def _ensure_path(self, conn: fnc.FileNetConn, path: str) -> None:
        """Creates every missing segment of `path`, so a connection's root
        folder (or any deeper configured path) doesn't have to already
        exist on the target server — pointing a new connection at a fresh
        FileNet installation just works instead of failing on first browse."""
        segments = [s for s in path.strip("/").split("/") if s]
        current = ""
        for seg in segments:
            parent = current or "/"
            current = f"{current}/{seg}"
            try:
                fnc.get_object(conn, "Folder", current, ["Id"])
            except fnc.FileNetError:
                try:
                    fnc.create_folder(conn, parent, seg)
                except fnc.FileNetError as exc:
                    raise _wrap(exc, f"Couldn't create folder '{current}'")

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    def _trash_path(self, creds: dict) -> str:
        return f"{self._conn(creds).root_path}/{_TRASH_NAME}"

    def _ensure_root(self, conn: fnc.FileNetConn) -> None:
        key = (conn.object_store, conn.root_path)
        if key in _ensured_roots:
            return
        with _ensured_roots_lock:
            if key in _ensured_roots:
                return
            self._ensure_path(conn, conn.root_path)
            _ensured_roots.add(key)

    def _folder_path(self, creds: dict, folder_id: str | None) -> str:
        if folder_id is None:
            conn = self._conn(creds)
            self._ensure_root(conn)
            return conn.root_path
        try:
            obj = fnc.get_object(self._conn(creds), "Folder", folder_id, ["Id", "PathName"])
        except fnc.FileNetError as exc:
            raise _wrap(exc, "Folder not found")
        return obj["PathName"]

    def _breadcrumb(self, creds: dict, path: str) -> list[BreadcrumbEntry]:
        root_path = self._conn(creds).root_path
        crumbs = [BreadcrumbEntry(id=None, name="My Drive")]
        if path == root_path:
            return crumbs
        rel = path[len(root_path):].strip("/")
        if not rel:
            return crumbs
        current = root_path
        for seg in rel.split("/"):
            current = f"{current}/{seg}"
            try:
                obj = fnc.get_object(self._conn(creds), "Folder", current, ["Id"])
                crumbs.append(BreadcrumbEntry(id=obj["Id"], name=seg))
            except fnc.FileNetError:
                crumbs.append(BreadcrumbEntry(id=None, name=seg))
        return crumbs

    def _ensure_trash(self, creds: dict) -> str:
        conn = self._conn(creds)
        self._ensure_root(conn)
        try:
            fnc.create_folder(conn, conn.root_path, _TRASH_NAME)
        except fnc.FileNetError:
            pass
        return self._trash_path(creds)

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        path = self._folder_path(creds, folder_id)
        breadcrumb = self._breadcrumb(creds, path)
        current_folder = None
        if folder_id is not None:
            try:
                obj = fnc.get_object(self._conn(creds), "Folder", folder_id, ["Id"])
            except fnc.FileNetError as exc:
                raise _wrap(exc, "Folder not found")
            parent_id = breadcrumb[-2].id if len(breadcrumb) >= 2 else None
            current_folder = _folder_info({"Id": obj["Id"], "PathName": path}, parent_id)

        try:
            folders, docs = fnc.get_children(self._conn(creds), path)
        except fnc.FileNetError as exc:
            raise _wrap(exc)

        listing_id = current_folder.id if current_folder else folder_id
        return FolderContents(
            folder=current_folder,
            breadcrumb=breadcrumb,
            folders=[_folder_info(f, listing_id) for f in folders if f.get("FolderName") != _TRASH_NAME],
            files=[_file_info(d, listing_id) for d in docs],
        )

    def list_trash(self, creds: dict) -> FolderContents:
        trash_path = self._ensure_trash(creds)
        try:
            folders, docs = fnc.get_children(self._conn(creds), trash_path)
        except fnc.FileNetError as exc:
            raise _wrap(exc)
        return FolderContents(
            folder=None,
            breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[_folder_info(f, None) for f in folders],
            files=[_file_info(d, None) for d in docs],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent_path = self._folder_path(creds, parent_id)
        try:
            result = fnc.create_folder(self._conn(creds), parent_path, name)
        except fnc.FileNetError as exc:
            raise _wrap(exc)
        return _folder_info(result, parent_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        path = self._folder_path(creds, folder_id)
        try:
            result = fnc.rename_folder(self._conn(creds), path, name)
        except fnc.FileNetError as exc:
            raise _wrap(exc)
        return _folder_info(result, None)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        path = self._folder_path(creds, folder_id)
        new_parent_path = self._folder_path(creds, new_parent_id)
        try:
            result = fnc.move_folder(self._conn(creds), path, new_parent_path)
        except fnc.FileNetError as exc:
            raise _wrap(exc)
        return _folder_info(result, new_parent_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        path = self._folder_path(creds, folder_id)
        try:
            fnc.delete_folder(self._conn(creds), path)
        except fnc.FileNetError as exc:
            raise _wrap(exc)

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        path = self._folder_path(creds, folder_id)
        try:
            result = fnc.create_document(self._conn(creds), path, name, content_type, content)
        except fnc.FileNetError as exc:
            raise _wrap(exc)
        return _file_info(result, folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        try:
            obj = fnc.get_object(
                self._conn(creds), "Document", file_id,
                ["Id", "DocumentTitle", "MimeType", "ContentSize", "DateLastModified", "MajorVersionNumber", "FoldersFiledIn"],
            )
        except fnc.FileNetError as exc:
            raise _wrap(exc, "File not found")
        return _file_info(obj, _first_folder_id(obj))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        try:
            fnc.rename_document(self._conn(creds), file_id, name)
        except fnc.FileNetError as exc:
            raise _wrap(exc)
        return self.get_file(creds, file_id)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        try:
            obj = fnc.get_object(self._conn(creds), "Document", file_id, ["DocumentTitle", "FoldersFiledIn"])
        except fnc.FileNetError as exc:
            raise _wrap(exc, "File not found")
        current_folder_id = _first_folder_id(obj)
        current_path = self._folder_path(creds, current_folder_id)
        new_path = self._folder_path(creds, new_folder_id)
        name = obj.get("DocumentTitle") or "Untitled"
        try:
            fnc.move_document(self._conn(creds), file_id, current_path, new_path, name)
        except fnc.FileNetError as exc:
            raise _wrap(exc)
        return self.get_file(creds, file_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        try:
            fnc.delete_document(self._conn(creds), file_id)
        except fnc.FileNetError as exc:
            raise _wrap(exc, "File not found")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        try:
            return fnc.get_content(self._conn(creds), file_id)
        except fnc.FileNetError as exc:
            raise _wrap(exc, "File not found")

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        try:
            rows = fnc.list_versions(self._conn(creds), file_id)
        except fnc.FileNetError as exc:
            raise _wrap(exc, "File not found")
        out = []
        for r in rows:
            size = r.get("ContentSize")
            try:
                size_bytes = int(float(size)) if size is not None else None
            except (TypeError, ValueError):
                size_bytes = None
            try:
                version_number = int(r.get("MajorVersionNumber"))
            except (TypeError, ValueError):
                version_number = 0
            out.append(VersionInfo(
                id=r["Id"],
                version_number=version_number,
                size_bytes=size_bytes,
                content_type=r.get("MimeType"),
                is_current=str(r.get("IsCurrentVersion")).lower() in ("true", "1"),
                updated_at=parse_dt(r.get("DateLastModified")),
            ))
        return out

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        try:
            obj = fnc.get_object(self._conn(creds), "Document", file_id, ["DocumentTitle"])
            name = obj.get("DocumentTitle") or "Untitled"
            result = fnc.checkin(self._conn(creds), file_id, name, content_type, content, major=True)
        except fnc.FileNetError as exc:
            raise _wrap(exc)
        return self.get_file(creds, result["Id"])

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        try:
            return fnc.get_content(self._conn(creds), version_id)
        except fnc.FileNetError as exc:
            raise _wrap(exc, "Version not found")

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        try:
            old = fnc.get_object(self._conn(creds), "Document", version_id, ["DocumentTitle", "MimeType"])
            old_bytes = fnc.get_content(self._conn(creds), version_id)
            fnc.checkin(
                self._conn(creds), file_id,
                old.get("DocumentTitle") or "Untitled", old.get("MimeType") or "application/octet-stream",
                old_bytes, major=True,
            )
        except fnc.FileNetError as exc:
            raise _wrap(exc, "Version not found")
        return self.get_file(creds, file_id)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        path = self._folder_path(creds, folder_id)
        trash_path = self._ensure_trash(creds)
        try:
            fnc.move_folder(self._conn(creds), path, trash_path)
        except fnc.FileNetError as exc:
            raise _wrap(exc)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        path = self._folder_path(creds, folder_id)
        try:
            result = fnc.move_folder(self._conn(creds), path, self._conn(creds).root_path)
        except fnc.FileNetError as exc:
            raise _wrap(exc)
        return _folder_info(result, None)

    def trash_file(self, creds: dict, file_id: str) -> None:
        try:
            obj = fnc.get_object(self._conn(creds), "Document", file_id, ["DocumentTitle", "FoldersFiledIn"])
        except fnc.FileNetError as exc:
            raise _wrap(exc, "File not found")
        current_folder_id = _first_folder_id(obj)
        current_path = self._folder_path(creds, current_folder_id)
        trash_path = self._ensure_trash(creds)
        try:
            fnc.move_document(self._conn(creds), file_id, current_path, trash_path, obj.get("DocumentTitle") or "Untitled")
        except fnc.FileNetError as exc:
            raise _wrap(exc)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        try:
            obj = fnc.get_object(self._conn(creds), "Document", file_id, ["DocumentTitle"])
            fnc.move_document(self._conn(creds), file_id, self._trash_path(creds), self._conn(creds).root_path, obj.get("DocumentTitle") or "Untitled")
        except fnc.FileNetError as exc:
            raise _wrap(exc, "File not found")
        return self.get_file(creds, file_id)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root_path = self._conn(creds).root_path
        escaped = query.replace("'", "''")
        folder_sql = (
            "SELECT Id, FolderName, PathName FROM Folder "
            f"WHERE Folder.This INSUBFOLDER('{root_path}') AND FolderName LIKE '%{escaped}%'"
        )
        doc_sql = (
            "SELECT Id, DocumentTitle, MimeType, ContentSize, DateLastModified FROM Document "
            f"WHERE Document.This INSUBFOLDER('{root_path}') AND DocumentTitle LIKE '%{escaped}%'"
        )
        try:
            folders = fnc._raw_search(self._conn(creds), folder_sql)
            docs = fnc._raw_search(self._conn(creds), doc_sql)
        except fnc.FileNetError as exc:
            raise _wrap(exc)
        return (
            [_folder_info(f, None) for f in folders if f.get("FolderName") != _TRASH_NAME],
            [_file_info(d, None) for d in docs],
        )
