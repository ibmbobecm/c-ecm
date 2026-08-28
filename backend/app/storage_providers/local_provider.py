"""Local-disk storage provider: a real filesystem backend behind the same
StorageProvider interface as every other provider.

No real remote identity to check — it's just a folder on this machine, and
you already logged into FileDrive itself — so `requires_credentials =
False` and the only thing each connection configures is *where* (a folder
path, defaulting to this app's own data directory if left blank). Different
connections can point at entirely different folders, each getting its own
small SQLite database + blob directory *inside* that folder.

Folders/files/versions live in that per-connection SQLite database; file
content lives as flat blobs on disk, named by a random key so renames don't
touch content. Trash is a real `deleted_at` column (unlike FileNet's
provider, which has to emulate it with a hidden folder) — local disk
restores to the exact original parent, which FileNet's provider currently
can't.
"""

import datetime
import sqlite3
import uuid
from pathlib import Path

from ..config import LOCAL_STORAGE_DIR
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES folders(id),
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    folder_id INTEGER REFERENCES folders(id),
    current_version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS file_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id),
    version_number INTEGER NOT NULL,
    blob_key TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    content_type TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    return datetime.datetime.fromisoformat(value)


class LocalDiskProvider(StorageProvider):
    key = "local"
    display_name = "Local Disk"
    auth_mode = AuthMode.CREDENTIALS
    requires_credentials = False

    @property
    def config_fields(self) -> list[ConfigField]:
        return [ConfigField("storage_path", "Folder to store files in", str(LOCAL_STORAGE_DIR), required=False)]

    def _storage_dir(self, creds: dict) -> Path:
        raw = (creds.get("storage_path") or "").strip()
        return Path(raw) if raw else LOCAL_STORAGE_DIR

    def _blobs_dir(self, creds: dict) -> Path:
        return self._storage_dir(creds) / "blobs"

    def _conn(self, creds: dict) -> sqlite3.Connection:
        storage_dir = self._storage_dir(creds)
        try:
            storage_dir.mkdir(parents=True, exist_ok=True)
            self._blobs_dir(creds).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProviderError(f"Can't use folder '{storage_dir}': {exc}", status_code=400)
        conn = sqlite3.connect(str(storage_dir / "filedrive.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        return conn

    # --- auth ---
    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        storage_path = (config.get("storage_path") or "").strip() or str(LOCAL_STORAGE_DIR)
        creds = {"storage_path": storage_path}
        try:
            self._conn(creds).close()
        except ProviderError:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return str(self._storage_dir(creds))

    # --- folders ---
    def _row_folder(self, row: sqlite3.Row) -> FolderInfo:
        return FolderInfo(
            id=str(row["id"]),
            name=row["name"],
            parent_id=str(row["parent_id"]) if row["parent_id"] is not None else None,
            created_at=_parse_dt(row["created_at"]),
        )

    def _row_file(self, conn: sqlite3.Connection, row: sqlite3.Row) -> FileInfo:
        v = conn.execute(
            "SELECT * FROM file_versions WHERE file_id = ? AND version_number = ?",
            (row["id"], row["current_version"]),
        ).fetchone()
        return FileInfo(
            id=str(row["id"]),
            name=row["name"],
            folder_id=str(row["folder_id"]) if row["folder_id"] is not None else None,
            version_number=row["current_version"],
            size_bytes=v["size_bytes"] if v else None,
            content_type=v["content_type"] if v else None,
            updated_at=_parse_dt(row["updated_at"]),
        )

    def _breadcrumb(self, conn: sqlite3.Connection, folder_id: str | None) -> list[BreadcrumbEntry]:
        crumbs = []
        current = int(folder_id) if folder_id is not None else None
        seen = set()
        while current is not None and current not in seen:
            row = conn.execute("SELECT * FROM folders WHERE id = ?", (current,)).fetchone()
            if row is None:
                break
            crumbs.append(BreadcrumbEntry(id=str(row["id"]), name=row["name"]))
            seen.add(current)
            current = row["parent_id"]
        crumbs.append(BreadcrumbEntry(id=None, name="My Drive"))
        crumbs.reverse()
        return crumbs

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        conn = self._conn(creds)
        try:
            fid = int(folder_id) if folder_id is not None else None
            current_folder = None
            if fid is not None:
                row = conn.execute("SELECT * FROM folders WHERE id = ? AND deleted_at IS NULL", (fid,)).fetchone()
                if row is None:
                    raise ProviderError("Folder not found", status_code=404)
                current_folder = self._row_folder(row)

            folders = conn.execute(
                "SELECT * FROM folders WHERE parent_id IS ? AND deleted_at IS NULL ORDER BY name", (fid,)
            ).fetchall()
            files = conn.execute(
                "SELECT * FROM files WHERE folder_id IS ? AND deleted_at IS NULL ORDER BY name", (fid,)
            ).fetchall()
            return FolderContents(
                folder=current_folder,
                breadcrumb=self._breadcrumb(conn, folder_id),
                folders=[self._row_folder(f) for f in folders],
                files=[self._row_file(conn, f) for f in files],
            )
        finally:
            conn.close()

    def list_trash(self, creds: dict) -> FolderContents:
        conn = self._conn(creds)
        try:
            trashed_folders = conn.execute("SELECT * FROM folders WHERE deleted_at IS NOT NULL").fetchall()
            trashed_files = conn.execute("SELECT * FROM files WHERE deleted_at IS NOT NULL").fetchall()
            trashed_folder_ids = {f["id"] for f in trashed_folders}
            # Only the top of each deleted subtree — a child whose immediate
            # parent is also trashed reappears on its own once the parent is
            # restored, so it shouldn't clutter the trash list independently.
            top_folders = [f for f in trashed_folders if f["parent_id"] not in trashed_folder_ids]
            top_files = [f for f in trashed_files if f["folder_id"] not in trashed_folder_ids]
            return FolderContents(
                folder=None,
                breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                folders=sorted((self._row_folder(f) for f in top_folders), key=lambda f: f.name),
                files=sorted((self._row_file(conn, f) for f in top_files), key=lambda f: f.name),
            )
        finally:
            conn.close()

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        conn = self._conn(creds)
        try:
            fid = int(parent_id) if parent_id is not None else None
            cur = conn.execute(
                "INSERT INTO folders (name, parent_id, created_at) VALUES (?, ?, ?)", (name, fid, _now())
            )
            conn.commit()
            row = conn.execute("SELECT * FROM folders WHERE id = ?", (cur.lastrowid,)).fetchone()
            return self._row_folder(row)
        finally:
            conn.close()

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        conn = self._conn(creds)
        try:
            conn.execute("UPDATE folders SET name = ? WHERE id = ?", (name, int(folder_id)))
            conn.commit()
            row = conn.execute("SELECT * FROM folders WHERE id = ?", (int(folder_id),)).fetchone()
            if row is None:
                raise ProviderError("Folder not found", status_code=404)
            return self._row_folder(row)
        finally:
            conn.close()

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        conn = self._conn(creds)
        try:
            new_pid = int(new_parent_id) if new_parent_id is not None else None
            conn.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (new_pid, int(folder_id)))
            conn.commit()
            row = conn.execute("SELECT * FROM folders WHERE id = ?", (int(folder_id),)).fetchone()
            if row is None:
                raise ProviderError("Folder not found", status_code=404)
            return self._row_folder(row)
        finally:
            conn.close()

    def _delete_folder_recursive(self, conn: sqlite3.Connection, folder_id: int) -> list[str]:
        """Depth-first delete of a folder's entire subtree within an
        already-open transaction — folders/files have FOREIGN KEY
        constraints on their parent, so deleting a non-empty folder
        without first deleting its descendants fails with an
        IntegrityError. Returns every blob key freed along the way, to
        unlink from disk once the whole transaction commits."""
        blob_keys: list[str] = []
        for sub in conn.execute("SELECT id FROM folders WHERE parent_id = ?", (folder_id,)).fetchall():
            blob_keys.extend(self._delete_folder_recursive(conn, sub["id"]))
        for f in conn.execute("SELECT id FROM files WHERE folder_id = ?", (folder_id,)).fetchall():
            blob_keys.extend(self._delete_file_rows(conn, f["id"]))
        conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        return blob_keys

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        conn = self._conn(creds)
        try:
            blob_keys = self._delete_folder_recursive(conn, int(folder_id))
            conn.commit()
        finally:
            conn.close()
        for key in blob_keys:
            (self._blobs_dir(creds) / key).unlink(missing_ok=True)

    # --- files ---
    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        conn = self._conn(creds)
        try:
            fid = int(folder_id) if folder_id is not None else None
            now = _now()
            cur = conn.execute(
                "INSERT INTO files (name, folder_id, current_version, updated_at) VALUES (?, ?, 1, ?)",
                (name, fid, now),
            )
            file_id = cur.lastrowid
            blob_key = uuid.uuid4().hex
            (self._blobs_dir(creds) / blob_key).write_bytes(content)
            conn.execute(
                "INSERT INTO file_versions (file_id, version_number, blob_key, size_bytes, content_type, created_at) "
                "VALUES (?, 1, ?, ?, ?, ?)",
                (file_id, blob_key, len(content), content_type, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            return self._row_file(conn, row)
        finally:
            conn.close()

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        conn = self._conn(creds)
        try:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
            if row is None:
                raise ProviderError("File not found", status_code=404)
            return self._row_file(conn, row)
        finally:
            conn.close()

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        conn = self._conn(creds)
        try:
            conn.execute("UPDATE files SET name = ?, updated_at = ? WHERE id = ?", (name, _now(), int(file_id)))
            conn.commit()
            row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
            return self._row_file(conn, row)
        finally:
            conn.close()

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        conn = self._conn(creds)
        try:
            fid = int(new_folder_id) if new_folder_id is not None else None
            conn.execute("UPDATE files SET folder_id = ?, updated_at = ? WHERE id = ?", (fid, _now(), int(file_id)))
            conn.commit()
            row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
            return self._row_file(conn, row)
        finally:
            conn.close()

    def _delete_file_rows(self, conn: sqlite3.Connection, file_id: int) -> list[str]:
        """Deletes a file's own rows (versions then the file) within an
        already-open transaction; returns the blob keys to unlink from
        disk once the whole transaction commits — never before, so a
        failed commit can't leave the DB and the blobs disagreeing."""
        versions = conn.execute("SELECT blob_key FROM file_versions WHERE file_id = ?", (file_id,)).fetchall()
        conn.execute("DELETE FROM file_versions WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        return [v["blob_key"] for v in versions]

    def delete_file(self, creds: dict, file_id: str) -> None:
        conn = self._conn(creds)
        try:
            blob_keys = self._delete_file_rows(conn, int(file_id))
            conn.commit()
        finally:
            conn.close()
        for key in blob_keys:
            (self._blobs_dir(creds) / key).unlink(missing_ok=True)

    def get_content(self, creds: dict, file_id: str) -> bytes:
        conn = self._conn(creds)
        try:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
            if row is None:
                raise ProviderError("File not found", status_code=404)
            v = conn.execute(
                "SELECT * FROM file_versions WHERE file_id = ? AND version_number = ?",
                (row["id"], row["current_version"]),
            ).fetchone()
            if v is None:
                raise ProviderError("No content for this file", status_code=404)
            return (self._blobs_dir(creds) / v["blob_key"]).read_bytes()
        finally:
            conn.close()

    # --- versions ---
    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        conn = self._conn(creds)
        try:
            file_row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
            if file_row is None:
                raise ProviderError("File not found", status_code=404)
            rows = conn.execute(
                "SELECT * FROM file_versions WHERE file_id = ? ORDER BY version_number DESC", (int(file_id),)
            ).fetchall()
            return [
                VersionInfo(
                    id=str(r["id"]),
                    version_number=r["version_number"],
                    size_bytes=r["size_bytes"],
                    content_type=r["content_type"],
                    is_current=r["version_number"] == file_row["current_version"],
                    updated_at=_parse_dt(r["created_at"]),
                )
                for r in rows
            ]
        finally:
            conn.close()

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        conn = self._conn(creds)
        try:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
            if row is None:
                raise ProviderError("File not found", status_code=404)
            next_version = row["current_version"] + 1
            blob_key = uuid.uuid4().hex
            (self._blobs_dir(creds) / blob_key).write_bytes(content)
            now = _now()
            conn.execute(
                "INSERT INTO file_versions (file_id, version_number, blob_key, size_bytes, content_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (int(file_id), next_version, blob_key, len(content), content_type, now),
            )
            conn.execute(
                "UPDATE files SET current_version = ?, updated_at = ? WHERE id = ?",
                (next_version, now, int(file_id)),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
            return self._row_file(conn, row)
        finally:
            conn.close()

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        conn = self._conn(creds)
        try:
            v = conn.execute(
                "SELECT * FROM file_versions WHERE id = ? AND file_id = ?", (int(version_id), int(file_id))
            ).fetchone()
            if v is None:
                raise ProviderError("Version not found", status_code=404)
            return (self._blobs_dir(creds) / v["blob_key"]).read_bytes()
        finally:
            conn.close()

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        conn = self._conn(creds)
        try:
            target = conn.execute(
                "SELECT * FROM file_versions WHERE id = ? AND file_id = ?", (int(version_id), int(file_id))
            ).fetchone()
            if target is None:
                raise ProviderError("Version not found", status_code=404)
            row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
            next_version = row["current_version"] + 1
            now = _now()
            conn.execute(
                "INSERT INTO file_versions (file_id, version_number, blob_key, size_bytes, content_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (int(file_id), next_version, target["blob_key"], target["size_bytes"], target["content_type"], now),
            )
            conn.execute(
                "UPDATE files SET current_version = ?, updated_at = ? WHERE id = ?",
                (next_version, now, int(file_id)),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
            return self._row_file(conn, row)
        finally:
            conn.close()

    # --- trash ---
    def trash_folder(self, creds: dict, folder_id: str) -> None:
        conn = self._conn(creds)
        try:
            conn.execute("UPDATE folders SET deleted_at = ? WHERE id = ?", (_now(), int(folder_id)))
            conn.commit()
        finally:
            conn.close()

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        conn = self._conn(creds)
        try:
            conn.execute("UPDATE folders SET deleted_at = NULL WHERE id = ?", (int(folder_id),))
            conn.commit()
            row = conn.execute("SELECT * FROM folders WHERE id = ?", (int(folder_id),)).fetchone()
            if row is None:
                raise ProviderError("Folder not found", status_code=404)
            return self._row_folder(row)
        finally:
            conn.close()

    def trash_file(self, creds: dict, file_id: str) -> None:
        conn = self._conn(creds)
        try:
            conn.execute("UPDATE files SET deleted_at = ? WHERE id = ?", (_now(), int(file_id)))
            conn.commit()
        finally:
            conn.close()

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        conn = self._conn(creds)
        try:
            conn.execute("UPDATE files SET deleted_at = NULL WHERE id = ?", (int(file_id),))
            conn.commit()
            row = conn.execute("SELECT * FROM files WHERE id = ?", (int(file_id),)).fetchone()
            return self._row_file(conn, row)
        finally:
            conn.close()

    # --- search ---
    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        conn = self._conn(creds)
        try:
            pattern = f"%{query}%"
            folders = conn.execute(
                "SELECT * FROM folders WHERE deleted_at IS NULL AND name LIKE ? ORDER BY name", (pattern,)
            ).fetchall()
            files = conn.execute(
                "SELECT * FROM files WHERE deleted_at IS NULL AND name LIKE ? ORDER BY name", (pattern,)
            ).fetchall()
            return [self._row_folder(f) for f in folders], [self._row_file(conn, f) for f in files]
        finally:
            conn.close()
