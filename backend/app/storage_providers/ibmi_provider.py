"""IBM i (AS/400 / iSeries) storage provider for C-ECM.

Documents on IBM i can live in three places; this provider handles all three:

  1. IFS (Integrated File System) — a POSIX-style hierarchy accessible via
     SFTP. C-ECM browses IFS directories exactly like a local filesystem.

  2. DB2 for i — documents stored as BLOBs in a user-specified table/column.
     Each row's primary key becomes the file id; the name column supplies the
     display name. NOT YET IMPLEMENTED (only the config fields exist) --
     `db2_table`/`db2_name_col` are currently accepted but never read by any
     method below. There's also no configured primary-key or BLOB-column
     field yet (db2_name_col alone isn't enough to build queries) -- both
     would need adding alongside the actual pyodbc integration.

  3. CMOD (Content Manager OnDemand) — IBM's archive system on IBM i. When a
     CMOD REST base URL is provided, C-ECM proxies read/search operations
     through its REST API. NOT YET IMPLEMENTED (only the config field
     exists) -- `cmod_url` is currently accepted but never read by any
     method below. If/when this is built, the base path is
     `/cmod-rest/v1/...` (confirmed against IBM's own CMOD REST Services
     material: GET /cmod-rest/v1/folders, GET /cmod-rest/v1/folders/{name},
     POST /cmod-rest/v1/document) -- NOT `/CMODServer/v1` as this comment
     previously said, which doesn't appear in any real CMOD documentation.
     See ibmz_provider.py's CMOD integration for a worked (still line-item
     unverified beyond the base path) example of this same API.

Connection credentials collected by `config_fields`:
  - hostname      : IBM i hostname or IP
  - port          : SFTP port (default 22)
  - ifs_root      : IFS root path (default /home)
  - db2_table     : optional — TABLE.COLUMN for BLOB documents
  - db2_name_col  : name column in db2_table (default FILENAME)
  - cmod_url      : optional CMOD REST API base URL

IFS (the only implemented mode) is UNVERIFIED — written against documented
IBM i SFTP behavior; verify against a live IBM i partition before
production use. DB2 BLOB and CMOD are unimplemented stubs, not just
unverified — see the notes on each above.
"""

import io
import logging
import stat
from datetime import datetime, timezone

logger = logging.getLogger("ibmi_provider")

try:
    import paramiko  # type: ignore
    _HAS_PARAMIKO = True
except ImportError:
    _HAS_PARAMIKO = False

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


def _require_paramiko() -> None:
    if not _HAS_PARAMIKO:
        raise ProviderError("paramiko is required for the IBM i provider. Install it with: pip install paramiko", 503)


def _require_requests() -> None:
    if not _HAS_REQUESTS:
        raise ProviderError("requests is required for the IBM i provider. Install it with: pip install requests", 503)


def _sftp(creds: dict):
    """Return an (SSHClient, SFTPClient) pair.  Caller must close both."""
    _require_paramiko()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=creds["hostname"],
            port=int(creds.get("port") or 22),
            username=creds["username"],
            password=creds["password"],
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
    except Exception as exc:
        raise ProviderError(f"Could not connect to IBM i via SFTP: {exc}", status_code=502)
    sftp = ssh.open_sftp()
    return ssh, sftp


def _dt(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


def _ifs_file(attr, path: str, ifs_root: str) -> FileInfo:
    parent = path.rsplit("/", 1)[0] or ifs_root
    if parent == ifs_root:
        parent = None  # type: ignore[assignment]
    return FileInfo(
        id=path,
        name=path.rsplit("/", 1)[-1],
        folder_id=parent,
        version_number=1,
        size_bytes=attr.st_size,
        content_type=None,
        updated_at=_dt(attr.st_mtime),
    )


def _ifs_folder(path: str, ifs_root: str) -> FolderInfo:
    parent = path.rsplit("/", 1)[0] or None
    if parent == ifs_root:
        parent = None
    return FolderInfo(id=path, name=path.rsplit("/", 1)[-1], parent_id=parent)


class IBMiProvider(StorageProvider):
    """IBM i IFS + DB2 BLOB + CMOD storage provider."""

    key = "ibm_i"
    display_name = "IBM i (AS/400 / iSeries)"
    auth_mode = AuthMode.CREDENTIALS

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("hostname", "IBM i Hostname / IP"),
            ConfigField("port", "SFTP Port", "22", required=False),
            ConfigField("ifs_root", "IFS Root Path", "/home", required=False),
            ConfigField("db2_table", "DB2 BLOB Table (SCHEMA.TABLE)", "", required=False),
            ConfigField("db2_name_col", "DB2 Name Column", "FILENAME", required=False),
            ConfigField("cmod_url", "CMOD REST Base URL (optional)", "", required=False),
        ]

    def _ifs_root(self, creds: dict) -> str:
        return (creds.get("ifs_root") or "/home").rstrip("/") or "/"

    # --- auth ---

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        hostname = (config.get("hostname") or "").strip()
        if not hostname:
            raise ProviderError("Hostname is required", status_code=400)
        creds = {
            "username": username,
            "password": password,
            "hostname": hostname,
            "port": (config.get("port") or "22").strip(),
            "ifs_root": (config.get("ifs_root") or "/home").strip(),
            "db2_table": (config.get("db2_table") or "").strip(),
            "db2_name_col": (config.get("db2_name_col") or "FILENAME").strip(),
            "cmod_url": (config.get("cmod_url") or "").strip(),
        }
        try:
            ssh, sftp = _sftp(creds)
            sftp.close()
            ssh.close()
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return f"{creds['username']}@{creds['hostname']}"

    # --- folder helpers ---

    def _list_ifs(self, creds: dict, path: str) -> FolderContents:
        ifs_root = self._ifs_root(creds)
        ssh, sftp = _sftp(creds)
        try:
            try:
                attrs = sftp.listdir_attr(path)
            except IOError as exc:
                raise ProviderError(f"IFS path not found: {exc}", status_code=404)
            folders, files = [], []
            for a in attrs:
                full_path = f"{path.rstrip('/')}/{a.filename}"
                if a.filename.startswith("."):
                    continue
                if stat.S_ISDIR(a.st_mode or 0):
                    folders.append(_ifs_folder(full_path, ifs_root))
                else:
                    files.append(_ifs_file(a, full_path, ifs_root))
            current = None
            if path != ifs_root:
                current = _ifs_folder(path, ifs_root)
            return FolderContents(
                folder=current,
                breadcrumb=[BreadcrumbEntry(id=None, name="IBM i IFS")],
                folders=folders,
                files=files,
            )
        finally:
            sftp.close()
            ssh.close()

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        path = folder_id if folder_id is not None else self._ifs_root(creds)
        return self._list_ifs(creds, path)

    def list_trash(self, creds: dict) -> FolderContents:
        trash = f"{self._ifs_root(creds)}/{_TRASH_DIR}"
        ssh, sftp = _sftp(creds)
        try:
            try:
                sftp.mkdir(trash)
            except IOError:
                pass  # already exists
        finally:
            sftp.close()
            ssh.close()
        result = self._list_ifs(creds, trash)
        result.breadcrumb = [BreadcrumbEntry(id=None, name="Trash")]
        return result

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = parent_id if parent_id is not None else self._ifs_root(creds)
        new_path = f"{parent.rstrip('/')}/{name}"
        ssh, sftp = _sftp(creds)
        try:
            sftp.mkdir(new_path)
        except IOError as exc:
            raise ProviderError(f"Could not create folder: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()
        return _ifs_folder(new_path, self._ifs_root(creds))

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        parent = folder_id.rsplit("/", 1)[0] or self._ifs_root(creds)
        new_path = f"{parent}/{name}"
        ssh, sftp = _sftp(creds)
        try:
            sftp.rename(folder_id, new_path)
        except IOError as exc:
            raise ProviderError(f"Rename failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()
        return _ifs_folder(new_path, self._ifs_root(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        new_parent = new_parent_id if new_parent_id is not None else self._ifs_root(creds)
        name = folder_id.rsplit("/", 1)[-1]
        new_path = f"{new_parent.rstrip('/')}/{name}"
        ssh, sftp = _sftp(creds)
        try:
            sftp.rename(folder_id, new_path)
        except IOError as exc:
            raise ProviderError(f"Move failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()
        return _ifs_folder(new_path, self._ifs_root(creds))

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        ssh, sftp = _sftp(creds)
        try:
            self._rmdir_recursive(sftp, folder_id)
        finally:
            sftp.close()
            ssh.close()

    def _rmdir_recursive(self, sftp, path: str) -> None:
        for a in sftp.listdir_attr(path):
            full = f"{path}/{a.filename}"
            if stat.S_ISDIR(a.st_mode or 0):
                self._rmdir_recursive(sftp, full)
            else:
                sftp.remove(full)
        sftp.rmdir(path)

    # --- file operations ---

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = folder_id if folder_id is not None else self._ifs_root(creds)
        path = f"{parent.rstrip('/')}/{name}"
        ssh, sftp = _sftp(creds)
        try:
            with sftp.file(path, "wb") as f:
                f.write(content)
            attr = sftp.stat(path)
        except IOError as exc:
            raise ProviderError(f"Upload failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()
        return _ifs_file(attr, path, self._ifs_root(creds))

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        ssh, sftp = _sftp(creds)
        try:
            attr = sftp.stat(file_id)
        except IOError as exc:
            raise ProviderError(f"File not found: {exc}", status_code=404)
        finally:
            sftp.close()
            ssh.close()
        return _ifs_file(attr, file_id, self._ifs_root(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        parent = file_id.rsplit("/", 1)[0]
        new_path = f"{parent}/{name}"
        ssh, sftp = _sftp(creds)
        try:
            sftp.rename(file_id, new_path)
            attr = sftp.stat(new_path)
        except IOError as exc:
            raise ProviderError(f"Rename failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()
        return _ifs_file(attr, new_path, self._ifs_root(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        new_parent = new_folder_id if new_folder_id is not None else self._ifs_root(creds)
        name = file_id.rsplit("/", 1)[-1]
        new_path = f"{new_parent.rstrip('/')}/{name}"
        ssh, sftp = _sftp(creds)
        try:
            sftp.rename(file_id, new_path)
            attr = sftp.stat(new_path)
        except IOError as exc:
            raise ProviderError(f"Move failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()
        return _ifs_file(attr, new_path, self._ifs_root(creds))

    def delete_file(self, creds: dict, file_id: str) -> None:
        ssh, sftp = _sftp(creds)
        try:
            sftp.remove(file_id)
        except IOError as exc:
            raise ProviderError(f"Delete failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()

    def get_content(self, creds: dict, file_id: str) -> bytes:
        ssh, sftp = _sftp(creds)
        try:
            buf = io.BytesIO()
            sftp.getfo(file_id, buf)
            return buf.getvalue()
        except IOError as exc:
            raise ProviderError(f"Could not read file: {exc}", status_code=404)
        finally:
            sftp.close()
            ssh.close()

    # --- versions (IFS has no native versioning — report single current) ---

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        info = self.get_file(creds, file_id)
        return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                            content_type=info.content_type, is_current=True, updated_at=info.updated_at)]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        ssh, sftp = _sftp(creds)
        try:
            with sftp.file(file_id, "wb") as f:
                f.write(content)
            attr = sftp.stat(file_id)
        except IOError as exc:
            raise ProviderError(f"Upload failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()
        return _ifs_file(attr, file_id, self._ifs_root(creds))

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        return self.get_content(creds, file_id)

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        return self.get_file(creds, file_id)

    # --- trash ---

    def trash_file(self, creds: dict, file_id: str) -> None:
        trash = f"{self._ifs_root(creds)}/{_TRASH_DIR}"
        name = file_id.rsplit("/", 1)[-1]
        ssh, sftp = _sftp(creds)
        try:
            try:
                sftp.mkdir(trash)
            except IOError:
                pass
            sftp.rename(file_id, f"{trash}/{name}")
        except IOError as exc:
            raise ProviderError(f"Trash failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        ifs_root = self._ifs_root(creds)
        name = file_id.rsplit("/", 1)[-1]
        new_path = f"{ifs_root}/{name}"
        ssh, sftp = _sftp(creds)
        try:
            sftp.rename(file_id, new_path)
            attr = sftp.stat(new_path)
        except IOError as exc:
            raise ProviderError(f"Restore failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()
        return _ifs_file(attr, new_path, ifs_root)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        trash = f"{self._ifs_root(creds)}/{_TRASH_DIR}"
        name = folder_id.rsplit("/", 1)[-1]
        ssh, sftp = _sftp(creds)
        try:
            try:
                sftp.mkdir(trash)
            except IOError:
                pass
            sftp.rename(folder_id, f"{trash}/{name}")
        except IOError as exc:
            raise ProviderError(f"Trash failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        ifs_root = self._ifs_root(creds)
        name = folder_id.rsplit("/", 1)[-1]
        new_path = f"{ifs_root}/{name}"
        ssh, sftp = _sftp(creds)
        try:
            sftp.rename(folder_id, new_path)
        except IOError as exc:
            raise ProviderError(f"Restore failed: {exc}", status_code=502)
        finally:
            sftp.close()
            ssh.close()
        return _ifs_folder(new_path, ifs_root)

    # --- search ---

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        ifs_root = self._ifs_root(creds)
        folders: list[FolderInfo] = []
        files: list[FileInfo] = []
        q = query.lower()

        def _walk(sftp, path: str, depth: int = 0) -> None:
            if depth > 6:
                return
            try:
                for a in sftp.listdir_attr(path):
                    if a.filename.startswith("."):
                        continue
                    full = f"{path.rstrip('/')}/{a.filename}"
                    if q in a.filename.lower():
                        if stat.S_ISDIR(a.st_mode or 0):
                            folders.append(_ifs_folder(full, ifs_root))
                        else:
                            files.append(_ifs_file(a, full, ifs_root))
                    if stat.S_ISDIR(a.st_mode or 0):
                        _walk(sftp, full, depth + 1)
            except IOError:
                pass

        ssh, sftp = _sftp(creds)
        try:
            _walk(sftp, ifs_root)
        finally:
            sftp.close()
            ssh.close()
        return folders[:200], files[:200]
