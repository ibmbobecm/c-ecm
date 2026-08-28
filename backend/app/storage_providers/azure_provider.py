"""Azure Blob Storage.

UNVERIFIED — built against the `azure-storage-blob` SDK's stable, documented
API, but there's no real Azure account/storage account in this environment
to run it against. Run it against a real container before trusting it the
way FileNet's and local disk's providers are trusted.

Same virtual-folder story as S3 (see s3_provider.py's docstring for the
full rationale) — blob names ARE ids, "/" is a UI convention, folders are
zero-byte marker blobs, rename/move is copy-then-delete (via a short-lived
SAS token on the source, the standard documented way to do a same-account
server-side copy without ambiguity about which credentials authorize the
read side), trash is a "$Trash/" prefix, and versions only show real
history if the storage account has blob versioning turned on.
"""

import datetime

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, ContentSettings, generate_blob_sas
from azure.storage.blob import BlobServiceClient

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

_TRASH_PREFIX = "$Trash/"


def _wrap(exc: Exception, not_found_detail: str = "Not found") -> ProviderError:
    if isinstance(exc, ResourceNotFoundError):
        return ProviderError(not_found_detail, status_code=404, detail=str(exc))
    return ProviderError(f"Azure Blob Storage error: {exc}", status_code=502, detail=str(exc))


class AzureBlobProvider(StorageProvider):
    key = "azure_blob"
    display_name = "Azure Blob Storage"
    auth_mode = AuthMode.CREDENTIALS
    credential_labels = ("Storage account name", "Account key")

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("container", "Container name"),
            ConfigField("prefix", "Folder prefix (optional root within the container)", "", required=False),
        ]

    def _service(self, creds: dict) -> BlobServiceClient:
        account_url = f"https://{creds['account_name']}.blob.core.windows.net"
        # A wrong account name/key means DNS or the request just never comes
        # back under the SDK's default retry policy — short timeouts (and no
        # retries) turn that into a fast, clear failure instead of a hang.
        return BlobServiceClient(
            account_url=account_url, credential=creds["account_key"],
            connection_timeout=10, read_timeout=20, retry_total=1,
        )

    def _container(self, creds: dict):
        return self._service(creds).get_container_client(creds["container"])

    def _root_prefix(self, creds: dict) -> str:
        prefix = (creds.get("prefix") or "").strip("/")
        return f"{prefix}/" if prefix else ""

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        container = (config.get("container") or "").strip()
        if not container:
            raise ProviderError("Container name is required", status_code=400)
        creds = {
            "account_name": username, "account_key": password,
            "container": container, "prefix": (config.get("prefix") or "").strip(),
        }
        try:
            self._container(creds).get_container_properties()
        except Exception:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return f"{creds['account_name']}/{creds['container']}/{self._root_prefix(creds)}"

    # --- helpers ---
    def _folder_key(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_prefix(creds)

    def _name_from_key(self, key: str) -> str:
        return key.rstrip("/").rsplit("/", 1)[-1]

    def _folder_info(self, key: str, root_prefix: str) -> FolderInfo:
        parent = key[:-1].rsplit("/", 1)
        parent_key = parent[0] + "/" if len(parent) > 1 else ""
        return FolderInfo(id=key, name=self._name_from_key(key), parent_id=None if parent_key == root_prefix else parent_key)

    def _file_info(self, blob, root_prefix: str) -> FileInfo:
        key = blob.name
        parent = key.rsplit("/", 1)
        parent_key = parent[0] + "/" if len(parent) > 1 else ""
        return FileInfo(
            id=key, name=self._name_from_key(key),
            folder_id=None if parent_key == root_prefix else parent_key,
            version_number=1, size_bytes=blob.size,
            content_type=(blob.content_settings.content_type if blob.content_settings else None),
            updated_at=blob.last_modified,
        )

    def _list(self, creds: dict, prefix: str) -> tuple[list[str], list]:
        container = self._container(creds)
        folders, files = [], []
        try:
            for item in container.walk_blobs(name_starts_with=prefix, delimiter="/"):
                if hasattr(item, "size"):  # BlobProperties (a file), not BlobPrefix
                    if item.name != prefix and not item.name.endswith("/"):
                        files.append(item)
                else:
                    if not item.name.startswith(prefix + _TRASH_PREFIX.rstrip("/")) or prefix == "":
                        folders.append(item.name)
        except Exception as exc:
            raise _wrap(exc)
        return folders, files

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_prefix = self._root_prefix(creds)
        prefix = self._folder_key(creds, folder_id)
        folder_keys, file_blobs = self._list(creds, prefix)
        folder_keys = [k for k in folder_keys if not k.startswith(root_prefix + _TRASH_PREFIX)]
        current_folder = self._folder_info(folder_id, root_prefix) if folder_id is not None else None
        return FolderContents(
            folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
            folders=[self._folder_info(k, root_prefix) for k in folder_keys],
            files=[self._file_info(b, root_prefix) for b in file_blobs],
        )

    def list_trash(self, creds: dict) -> FolderContents:
        root_prefix = self._root_prefix(creds)
        trash_prefix = root_prefix + _TRASH_PREFIX
        folder_keys, file_blobs = self._list(creds, trash_prefix)
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._folder_info(k, trash_prefix) for k in folder_keys],
            files=[self._file_info(b, trash_prefix) for b in file_blobs],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._folder_key(creds, parent_id)
        key = f"{parent}{name}/"
        try:
            self._container(creds).upload_blob(name=key, data=b"", overwrite=True)
        except Exception as exc:
            raise _wrap(exc)
        return self._folder_info(key, self._root_prefix(creds))

    def _source_url_with_sas(self, creds: dict, key: str) -> str:
        blob_client = self._container(creds).get_blob_client(key)
        sas = generate_blob_sas(
            account_name=creds["account_name"], container_name=creds["container"], blob_name=key,
            account_key=creds["account_key"], permission=BlobSasPermissions(read=True),
            expiry=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15),
        )
        return f"{blob_client.url}?{sas}"

    def _copy_blob(self, creds: dict, old_key: str, new_key: str) -> None:
        source_url = self._source_url_with_sas(creds, old_key)
        dest_client = self._container(creds).get_blob_client(new_key)
        try:
            dest_client.start_copy_from_url(source_url)
            self._container(creds).delete_blob(old_key)
        except Exception as exc:
            raise _wrap(exc)

    def _copy_tree(self, creds: dict, old_prefix: str, new_prefix: str) -> None:
        container = self._container(creds)
        try:
            keys = [b.name for b in container.list_blobs(name_starts_with=old_prefix)]
        except Exception as exc:
            raise _wrap(exc)
        for old_key in keys:
            new_key = new_prefix + old_key[len(old_prefix):]
            self._copy_blob(creds, old_key, new_key)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        parent = folder_id[:-1].rsplit("/", 1)
        parent_prefix = parent[0] + "/" if len(parent) > 1 else ""
        new_key = f"{parent_prefix}{name}/"
        self._copy_tree(creds, folder_id, new_key)
        return self._folder_info(new_key, self._root_prefix(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        name = self._name_from_key(folder_id)
        new_key = f"{self._folder_key(creds, new_parent_id)}{name}/"
        self._copy_tree(creds, folder_id, new_key)
        return self._folder_info(new_key, self._root_prefix(creds))

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        container = self._container(creds)
        try:
            for blob in list(container.list_blobs(name_starts_with=folder_id)):
                container.delete_blob(blob.name)
        except Exception as exc:
            raise _wrap(exc)

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        key = f"{self._folder_key(creds, folder_id)}{name}"
        try:
            self._container(creds).upload_blob(
                name=key, data=content, overwrite=True, content_settings=ContentSettings(content_type=content_type)
            )
        except Exception as exc:
            raise _wrap(exc)
        return self.get_file(creds, key)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        try:
            props = self._container(creds).get_blob_client(file_id).get_blob_properties()
        except Exception as exc:
            raise _wrap(exc, "File not found")
        return self._file_info(props, self._root_prefix(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        parent = file_id.rsplit("/", 1)
        parent_prefix = parent[0] + "/" if len(parent) > 1 else ""
        new_key = f"{parent_prefix}{name}"
        self._copy_blob(creds, file_id, new_key)
        return self.get_file(creds, new_key)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        name = self._name_from_key(file_id)
        new_key = f"{self._folder_key(creds, new_folder_id)}{name}"
        self._copy_blob(creds, file_id, new_key)
        return self.get_file(creds, new_key)

    def delete_file(self, creds: dict, file_id: str) -> None:
        try:
            self._container(creds).delete_blob(file_id)
        except Exception as exc:
            raise _wrap(exc)

    def get_content(self, creds: dict, file_id: str) -> bytes:
        try:
            return self._container(creds).download_blob(file_id).readall()
        except Exception as exc:
            raise _wrap(exc, "File not found")

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        container = self._container(creds)
        try:
            blobs = list(container.list_blobs(name_starts_with=file_id, include=["versions"]))
        except Exception as exc:
            raise _wrap(exc, "File not found")
        versions = [b for b in blobs if b.name == file_id and getattr(b, "version_id", None)]
        if not versions:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True, updated_at=info.updated_at)]
        versions.sort(key=lambda b: b.last_modified, reverse=True)
        return [
            VersionInfo(id=b.version_id, version_number=i + 1, size_bytes=b.size,
                         content_type=(b.content_settings.content_type if b.content_settings else None),
                         is_current=getattr(b, "is_current_version", i == 0), updated_at=b.last_modified)
            for i, b in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        return self._overwrite(creds, file_id, content_type, content)

    def _overwrite(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        try:
            self._container(creds).upload_blob(
                name=file_id, data=content, overwrite=True, content_settings=ContentSettings(content_type=content_type)
            )
        except Exception as exc:
            raise _wrap(exc)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        try:
            kwargs = {} if version_id == "current" else {"version_id": version_id}
            return self._container(creds).download_blob(file_id, **kwargs).readall()
        except Exception as exc:
            raise _wrap(exc, "Version not found")

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        info = self.get_file(creds, file_id)
        return self._overwrite(creds, file_id, info.content_type or "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        root_prefix = self._root_prefix(creds)
        name = self._name_from_key(folder_id)
        self._copy_tree(creds, folder_id, f"{root_prefix}{_TRASH_PREFIX}{name}/")

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root_prefix = self._root_prefix(creds)
        name = self._name_from_key(folder_id)
        new_key = f"{root_prefix}{name}/"
        self._copy_tree(creds, folder_id, new_key)
        return self._folder_info(new_key, root_prefix)

    def trash_file(self, creds: dict, file_id: str) -> None:
        root_prefix = self._root_prefix(creds)
        name = self._name_from_key(file_id)
        self._copy_blob(creds, file_id, f"{root_prefix}{_TRASH_PREFIX}{name}")

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root_prefix = self._root_prefix(creds)
        name = self._name_from_key(file_id)
        new_key = f"{root_prefix}{name}"
        self._copy_blob(creds, file_id, new_key)
        return self.get_file(creds, new_key)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root_prefix = self._root_prefix(creds)
        container = self._container(creds)
        query_lower = query.lower()
        folders, files = [], []
        try:
            count = 0
            for blob in container.list_blobs(name_starts_with=root_prefix):
                if blob.name.startswith(root_prefix + _TRASH_PREFIX):
                    continue
                name = self._name_from_key(blob.name)
                if query_lower not in name.lower():
                    continue
                if blob.name.endswith("/"):
                    folders.append(self._folder_info(blob.name, root_prefix))
                else:
                    files.append(self._file_info(blob, root_prefix))
                count += 1
                if count >= 500:
                    break
        except Exception as exc:
            raise _wrap(exc)
        return folders, files
