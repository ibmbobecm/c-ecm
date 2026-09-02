"""AWS S3 and IBM Cloud Object Storage — IBM COS exposes an S3-compatible
API (same request shapes, HMAC access-key/secret-key auth), so both are the
same provider underneath with a different `endpoint_url`: AWS resolves its
own regional endpoint from `region`, IBM COS needs an explicit endpoint URL
(e.g. `https://s3.us-south.cloud-object-storage.appdomain.cloud`) since
there's no single well-known IBM domain to derive it from.

UNVERIFIED — built against boto3's stable, extensively-documented S3 API,
but there's no real AWS/IBM account or bucket in this environment to run it
against. Run it against a real bucket before trusting it the way FileNet's
and local disk's providers are trusted.

S3 has no real folders — only object keys, with "/" as a UI convention. So:
- ids ARE the key: a folder's id is its key ending in "/", a file's id is
  its key. No separate id<->key mapping needed, and it's stable across
  everything (rename/move naturally change the id, same as they'd change a
  FileNet path).
- `get_children` uses `Delimiter="/"` so S3 itself does the "one level"
  grouping (CommonPrefixes = subfolders, Contents = files) — no need to
  fetch and filter the whole bucket.
- A folder "exists" as a zero-byte marker object at its own key (`prefix/
  name/`), the common convention every S3 console/tool uses.
- No native trash or rename: delete moves to a `$Trash/` prefix (permanent
  delete actually removes it); rename/move is copy-then-delete, recursive
  for folders (copy every object under the old prefix, then batch-delete
  the old ones) — S3 has no atomic rename.
- Versions only exist if the bucket has versioning enabled; if not, this
  reports just the single current object rather than erroring.
"""

import datetime

import boto3
import botocore.config
import botocore.exceptions

# A wrong access key/bucket/endpoint means the request just never comes back
# under boto3's default retry policy — short timeouts (and no retries) turn
# that into a fast, clear failure instead of a hang.
_BOTO_CONFIG = botocore.config.Config(connect_timeout=10, read_timeout=20, retries={"max_attempts": 1})

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
    if isinstance(exc, botocore.exceptions.ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            return ProviderError(not_found_detail, status_code=404, detail=str(exc))
        if code in ("AccessDenied", "403"):
            return ProviderError("Access denied", status_code=403, detail=str(exc))
        return ProviderError(f"S3 error ({code}): {exc}", status_code=502, detail=str(exc))
    return ProviderError(f"Couldn't reach the storage service: {exc}", status_code=502, detail=str(exc))


class _S3CompatibleProvider(StorageProvider):
    auth_mode = AuthMode.CREDENTIALS
    credential_labels = ("Access Key ID", "Secret Access Key")

    def _endpoint_url(self, creds: dict) -> str | None:
        return None

    def _client(self, creds: dict):
        return boto3.client(
            "s3",
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"],
            region_name=creds.get("region") or None,
            endpoint_url=self._endpoint_url(creds),
            config=_BOTO_CONFIG,
        )

    def _bucket(self, creds: dict) -> str:
        return creds["bucket"]

    def _root_prefix(self, creds: dict) -> str:
        prefix = (creds.get("prefix") or "").strip("/")
        return f"{prefix}/" if prefix else ""

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        bucket = (config.get("bucket") or "").strip()
        if not bucket:
            raise ProviderError("Bucket is required", status_code=400)
        creds = {
            "access_key": username,
            "secret_key": password,
            "bucket": bucket,
            "region": (config.get("region") or "").strip(),
            "prefix": (config.get("prefix") or "").strip(),
            "endpoint_url": (config.get("endpoint_url") or "").strip(),
        }
        try:
            self._client(creds).head_bucket(Bucket=bucket)
        except Exception:
            return None
        return creds

    def whoami(self, creds: dict) -> str:
        return f"s3://{creds['bucket']}/{self._root_prefix(creds)}"

    # --- id/key helpers ---
    def _folder_key(self, creds: dict, folder_id: str | None) -> str:
        return folder_id if folder_id is not None else self._root_prefix(creds)

    def _name_from_key(self, key: str) -> str:
        return key.rstrip("/").rsplit("/", 1)[-1]

    def _folder_info(self, key: str, root_prefix: str) -> FolderInfo:
        parent = key[:-1].rsplit("/", 1)
        parent_key = parent[0] + "/" if len(parent) > 1 else ""
        return FolderInfo(
            id=key, name=self._name_from_key(key),
            parent_id=None if parent_key == root_prefix else parent_key,
        )

    def _file_info(self, obj: dict, root_prefix: str) -> FileInfo:
        key = obj["Key"]
        parent = key.rsplit("/", 1)
        parent_key = parent[0] + "/" if len(parent) > 1 else ""
        return FileInfo(
            id=key, name=self._name_from_key(key),
            folder_id=None if parent_key == root_prefix else parent_key,
            version_number=1, size_bytes=obj.get("Size"),
            content_type=obj.get("ContentType"),
            updated_at=obj.get("LastModified"),
        )

    def _list(self, creds: dict, prefix: str) -> tuple[list[dict], list[dict]]:
        client = self._client(creds)
        folders, files = [], []
        paginator = client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self._bucket(creds), Prefix=prefix, Delimiter="/"):
                for cp in page.get("CommonPrefixes", []):
                    p = cp["Prefix"]
                    if not p.startswith(_TRASH_PREFIX):
                        folders.append(p)
                for obj in page.get("Contents", []):
                    if obj["Key"] != prefix and not obj["Key"].endswith("/"):
                        files.append(obj)
        except Exception as exc:
            raise _wrap(exc)
        return folders, files

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_prefix = self._root_prefix(creds)
        prefix = self._folder_key(creds, folder_id)
        folder_keys, file_objs = self._list(creds, prefix)
        current_folder = None
        if folder_id is not None:
            current_folder = self._folder_info(folder_id, root_prefix)
        return FolderContents(
            folder=current_folder,
            breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
            folders=[self._folder_info(k, root_prefix) for k in folder_keys],
            files=[self._file_info(o, root_prefix) for o in file_objs],
        )

    def list_trash(self, creds: dict) -> FolderContents:
        root_prefix = self._root_prefix(creds)
        trash_prefix = root_prefix + _TRASH_PREFIX
        folder_keys, file_objs = self._list(creds, trash_prefix)
        return FolderContents(
            folder=None,
            breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._folder_info(k, trash_prefix) for k in folder_keys],
            files=[self._file_info(o, trash_prefix) for o in file_objs],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        parent = self._folder_key(creds, parent_id)
        key = f"{parent}{name}/"
        try:
            self._client(creds).put_object(Bucket=self._bucket(creds), Key=key, Body=b"")
        except Exception as exc:
            raise _wrap(exc)
        return self._folder_info(key, self._root_prefix(creds))

    def _copy_tree(self, creds: dict, old_prefix: str, new_prefix: str) -> None:
        client = self._client(creds)
        bucket = self._bucket(creds)
        paginator = client.get_paginator("list_objects_v2")
        keys_to_delete = []
        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=old_prefix):
                for obj in page.get("Contents", []):
                    old_key = obj["Key"]
                    new_key = new_prefix + old_key[len(old_prefix):]
                    client.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": old_key}, Key=new_key)
                    keys_to_delete.append(old_key)
            for i in range(0, len(keys_to_delete), 1000):
                batch = keys_to_delete[i:i + 1000]
                client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in batch]})
        except Exception as exc:
            raise _wrap(exc)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        parent = folder_id[:-1].rsplit("/", 1)
        parent_prefix = parent[0] + "/" if len(parent) > 1 else ""
        new_key = f"{parent_prefix}{name}/"
        self._copy_tree(creds, folder_id, new_key)
        return self._folder_info(new_key, self._root_prefix(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        name = self._name_from_key(folder_id)
        new_parent = self._folder_key(creds, new_parent_id)
        new_key = f"{new_parent}{name}/"
        self._copy_tree(creds, folder_id, new_key)
        return self._folder_info(new_key, self._root_prefix(creds))

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        client = self._client(creds)
        bucket = self._bucket(creds)
        try:
            paginator = client.get_paginator("list_objects_v2")
            keys = []
            for page in paginator.paginate(Bucket=bucket, Prefix=folder_id):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
            for i in range(0, len(keys), 1000):
                batch = keys[i:i + 1000]
                client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in batch]})
        except Exception as exc:
            raise _wrap(exc)

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        parent = self._folder_key(creds, folder_id)
        key = f"{parent}{name}"
        try:
            self._client(creds).put_object(Bucket=self._bucket(creds), Key=key, Body=content, ContentType=content_type)
        except Exception as exc:
            raise _wrap(exc)
        return self.get_file(creds, key)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        try:
            head = self._client(creds).head_object(Bucket=self._bucket(creds), Key=file_id)
        except Exception as exc:
            raise _wrap(exc, "File not found")
        obj = {"Key": file_id, "Size": head.get("ContentLength"), "ContentType": head.get("ContentType"),
               "LastModified": head.get("LastModified")}
        return self._file_info(obj, self._root_prefix(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        parent = file_id.rsplit("/", 1)
        parent_prefix = parent[0] + "/" if len(parent) > 1 else ""
        new_key = f"{parent_prefix}{name}"
        try:
            self._client(creds).copy_object(
                Bucket=self._bucket(creds), CopySource={"Bucket": self._bucket(creds), "Key": file_id}, Key=new_key
            )
            self._client(creds).delete_object(Bucket=self._bucket(creds), Key=file_id)
        except Exception as exc:
            raise _wrap(exc)
        return self.get_file(creds, new_key)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        name = self._name_from_key(file_id)
        new_parent = self._folder_key(creds, new_folder_id)
        new_key = f"{new_parent}{name}"
        try:
            self._client(creds).copy_object(
                Bucket=self._bucket(creds), CopySource={"Bucket": self._bucket(creds), "Key": file_id}, Key=new_key
            )
            self._client(creds).delete_object(Bucket=self._bucket(creds), Key=file_id)
        except Exception as exc:
            raise _wrap(exc)
        return self.get_file(creds, new_key)

    def delete_file(self, creds: dict, file_id: str) -> None:
        try:
            self._client(creds).delete_object(Bucket=self._bucket(creds), Key=file_id)
        except Exception as exc:
            raise _wrap(exc)

    def get_content(self, creds: dict, file_id: str) -> bytes:
        try:
            resp = self._client(creds).get_object(Bucket=self._bucket(creds), Key=file_id)
            return resp["Body"].read()
        except Exception as exc:
            raise _wrap(exc, "File not found")

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        try:
            resp = self._client(creds).list_object_versions(Bucket=self._bucket(creds), Prefix=file_id)
        except Exception as exc:
            raise _wrap(exc, "File not found")
        versions = [v for v in resp.get("Versions", []) if v["Key"] == file_id]
        if not versions:
            info = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=1, size_bytes=info.size_bytes,
                                 content_type=info.content_type, is_current=True, updated_at=info.updated_at)]
        versions.sort(key=lambda v: v["LastModified"], reverse=True)
        return [
            VersionInfo(id=v["VersionId"], version_number=i + 1, size_bytes=v.get("Size"),
                         content_type=None, is_current=v.get("IsLatest", False), updated_at=v.get("LastModified"))
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        try:
            self._client(creds).put_object(Bucket=self._bucket(creds), Key=file_id, Body=content, ContentType=content_type)
        except Exception as exc:
            raise _wrap(exc)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        try:
            kwargs = {} if version_id == "current" else {"VersionId": version_id}
            resp = self._client(creds).get_object(Bucket=self._bucket(creds), Key=file_id, **kwargs)
            return resp["Body"].read()
        except Exception as exc:
            raise _wrap(exc, "Version not found")

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        info = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, info.content_type or "application/octet-stream", old_bytes)

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
        new_key = f"{root_prefix}{_TRASH_PREFIX}{name}"
        try:
            self._client(creds).copy_object(
                Bucket=self._bucket(creds), CopySource={"Bucket": self._bucket(creds), "Key": file_id}, Key=new_key
            )
            self._client(creds).delete_object(Bucket=self._bucket(creds), Key=file_id)
        except Exception as exc:
            raise _wrap(exc)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root_prefix = self._root_prefix(creds)
        name = self._name_from_key(file_id)
        new_key = f"{root_prefix}{name}"
        try:
            self._client(creds).copy_object(
                Bucket=self._bucket(creds), CopySource={"Bucket": self._bucket(creds), "Key": file_id}, Key=new_key
            )
            self._client(creds).delete_object(Bucket=self._bucket(creds), Key=file_id)
        except Exception as exc:
            raise _wrap(exc)
        return self.get_file(creds, new_key)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root_prefix = self._root_prefix(creds)
        client = self._client(creds)
        folders, files = [], []
        query_lower = query.lower()
        try:
            paginator = client.get_paginator("list_objects_v2")
            count = 0
            for page in paginator.paginate(Bucket=self._bucket(creds), Prefix=root_prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.startswith(root_prefix + _TRASH_PREFIX):
                        continue
                    name = self._name_from_key(key)
                    if query_lower not in name.lower():
                        continue
                    if key.endswith("/"):
                        folders.append(self._folder_info(key, root_prefix))
                    else:
                        files.append(self._file_info(obj, root_prefix))
                    count += 1
                    if count >= 500:
                        break
                if count >= 500:
                    break
        except Exception as exc:
            raise _wrap(exc)
        return folders, files


class AWSS3Provider(_S3CompatibleProvider):
    key = "aws_s3"
    display_name = "AWS S3"

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("bucket", "Bucket name"),
            ConfigField("region", "Region", "us-east-1"),
            ConfigField("prefix", "Folder prefix (optional root within the bucket)", "", required=False),
        ]

    def _endpoint_url(self, creds: dict) -> str | None:
        return None  # boto3 resolves the standard AWS regional endpoint


class IBMCOSProvider(_S3CompatibleProvider):
    key = "ibm_cos"
    display_name = "IBM Cloud Object Storage"

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("endpoint_url", "Endpoint URL", "https://s3.us-south.cloud-object-storage.appdomain.cloud"),
            ConfigField("bucket", "Bucket name"),
            ConfigField("region", "Region", "us-south", required=False),
            ConfigField("prefix", "Folder prefix (optional root within the bucket)", "", required=False),
        ]

    def _endpoint_url(self, creds: dict) -> str | None:
        return creds.get("endpoint_url") or None


class WasabiProvider(_S3CompatibleProvider):
    """Wasabi's "hot cloud storage" is a documented, byte-for-byte
    S3-API-compatible service — same request signing, same operations,
    just a different regional endpoint host (`s3.<region>.wasabisys.com`)
    and its own separate access-key/secret-key pair issued from the Wasabi
    console. Confidence here is as high as AWS S3's own provider above,
    since this is the same boto3 S3 client pointed at a different host."""

    key = "wasabi"
    display_name = "Wasabi"

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("bucket", "Bucket name"),
            ConfigField("region", "Region", "us-east-1"),
            ConfigField("prefix", "Folder prefix (optional root within the bucket)", "", required=False),
        ]

    def _endpoint_url(self, creds: dict) -> str | None:
        region = (creds.get("region") or "us-east-1").strip()
        return f"https://s3.{region}.wasabisys.com"


class BackblazeB2Provider(_S3CompatibleProvider):
    """Backblaze B2's native API is its own (bucket/file-id based, not S3),
    but B2 also publishes a documented S3-Compatible API at
    `s3.<region>.backblazeb2.com` accepting the same access-key-id/
    secret-key credentials as B2's "Application Keys" — reusing that here
    rather than writing a second bespoke B2-native client, since it's the
    same boto3 S3 client (like Wasabi above) pointed at a different host,
    which is the same confidence level as this file's other S3-compatible
    providers rather than a fresh, unverified REST surface."""

    key = "backblaze_b2"
    display_name = "Backblaze B2"

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("bucket", "Bucket name"),
            ConfigField("region", "Region", "us-west-004"),
            ConfigField("prefix", "Folder prefix (optional root within the bucket)", "", required=False),
        ]

    def _endpoint_url(self, creds: dict) -> str | None:
        region = (creds.get("region") or "us-west-004").strip()
        return f"https://s3.{region}.backblazeb2.com"


class GCSProvider(_S3CompatibleProvider):
    """Google Cloud Storage's native API is JSON/OAuth2-based, but GCS also
    publishes a documented XML "interoperability" API at
    `storage.googleapis.com` that accepts S3-style HMAC access-key/secret
    pairs (created under Cloud Storage's "Interoperability" settings) —
    reusing the same S3-compatible client as this file's other providers
    rather than a separate, unverified OAuth2/service-account JSON flow.
    This does mean the credential pair here is a GCS HMAC key, not a full
    Google account login — flagged via `credential_labels`."""

    key = "gcs"
    display_name = "Google Cloud Storage"
    credential_labels = ("HMAC Access Key", "HMAC Secret")

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("bucket", "Bucket name"),
            ConfigField("prefix", "Folder prefix (optional root within the bucket)", "", required=False),
        ]

    def _endpoint_url(self, creds: dict) -> str | None:
        return "https://storage.googleapis.com"
