import datetime

from .schemas import BreadcrumbEntry, FileOut, FileVersionOut, FolderContentsOut, FolderOut
from .storage_providers.base import FileInfo, FolderContents, FolderInfo, VersionInfo


def _parse_dt(value) -> datetime.datetime | None:
    if value is None or isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def folder_out(f: FolderInfo) -> FolderOut:
    return FolderOut(id=f.id, name=f.name, parent_id=f.parent_id, created_at=f.created_at)


def file_out(f: FileInfo) -> FileOut:
    return FileOut(
        id=f.id,
        name=f.name,
        folder_id=f.folder_id,
        version_number=f.version_number,
        size_bytes=f.size_bytes,
        content_type=f.content_type,
        updated_at=f.updated_at,
    )


def file_version_out(v: VersionInfo) -> FileVersionOut:
    return FileVersionOut(
        id=v.id,
        version_number=v.version_number,
        size_bytes=v.size_bytes,
        content_type=v.content_type,
        is_current=v.is_current,
        updated_at=v.updated_at,
    )


def folder_contents_out(c: FolderContents) -> FolderContentsOut:
    return FolderContentsOut(
        folder=folder_out(c.folder) if c.folder else None,
        breadcrumb=[BreadcrumbEntry(id=b.id, name=b.name) for b in c.breadcrumb],
        folders=[folder_out(f) for f in c.folders],
        files=[file_out(f) for f in c.files],
    )
