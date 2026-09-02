"""Salesforce Files (ContentDocument/ContentVersion), via the Salesforce
REST + SOQL API (JSON, OAuth2 authorization-code flow). One of the
best-documented APIs in this codebase's provider set.

UNVERIFIED — no live Salesforce org in this environment to test against,
but built against Salesforce's precisely-documented REST/SOQL object
model, so confidence is higher than most other providers added alongside
this one.

Salesforce Files has NO native nested-folder hierarchy — ContentDocument
records live in flat "Libraries" (ContentWorkspace). This provider models
each library as a top-level folder (one level only) and its documents as
files directly inside it; libraries themselves can't be created/renamed/
moved/deleted here since that's a Salesforce Setup admin action, not a
simple REST call — an honest limitation, not an oversight.

Uses the fixed `login.salesforce.com` host for both authorize and token
exchange; a sandbox org would need `test.salesforce.com` instead (not
supported here). The per-org `instance_url` returned in the token
response is stored in creds and used for every subsequent API call,
since Salesforce's API host is per-org, not fixed.
"""

import base64
import threading
import time

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
from .. import settings_store

_API_VERSION = "v59.0"


class SalesforceFilesProvider(StorageProvider):
    key = "salesforce_files"
    display_name = "Salesforce Files"
    auth_mode = AuthMode.OAUTH

    def __init__(self):
        self._lock = threading.Lock()

    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("salesforce_files_client_id", ""),
            settings_store.get_setting("salesforce_files_client_secret", ""),
        )

    @property
    def configured(self) -> bool:
        cid, secret = self._client()
        return bool(cid and secret)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        cid, _secret = self._client()
        params = {"response_type": "code", "client_id": cid, "redirect_uri": redirect_uri, "state": state}
        return "https://login.salesforce.com/services/oauth2/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        cid, secret = self._client()
        resp = requests.post("https://login.salesforce.com/services/oauth2/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": cid,
            "client_secret": secret, "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Salesforce token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {
            "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "instance_url": tok["instance_url"], "expires_at": time.time() + 7200,
            "identity": tok.get("id", "Salesforce account"),
        }
        return creds

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        if creds.get("expires_at", 0) > time.time() + 30:
            return creds, False
        if not creds.get("refresh_token"):
            raise ProviderError("Salesforce session expired — please reconnect", status_code=401)
        cid, secret = self._client()
        resp = requests.post("https://login.salesforce.com/services/oauth2/token", data={
            "grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
            "client_id": cid, "client_secret": secret,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Salesforce session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["instance_url"] = tok.get("instance_url", creds["instance_url"])
        creds["expires_at"] = time.time() + 7200
        return creds, True

    def _api(self, creds: dict) -> str:
        return f"{creds['instance_url']}/services/data/{_API_VERSION}"

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"Salesforce error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds, _changed = self.refresh_if_needed(creds)
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Salesforce error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "Salesforce account")

    def _query(self, creds: dict, soql: str) -> list[dict]:
        result = self._get(creds, f"{self._api(creds)}/query", params={"q": soql})
        return result.get("records", [])

    @staticmethod
    def _escape(s: str) -> str:
        return s.replace("'", "\\'")

    @staticmethod
    def _parse_dt(s):
        if not s:
            return None
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def _lib_to_folder(self, lib: dict) -> FolderInfo:
        return FolderInfo(id=lib["Id"], name=lib.get("Name", ""), parent_id=None, created_at=None)

    def _doc_to_file(self, doc: dict, folder_id) -> FileInfo:
        version_id = doc.get("LatestPublishedVersionId")
        return FileInfo(
            id=doc["Id"], name=doc.get("Title", ""), folder_id=folder_id, version_number=1,
            size_bytes=doc.get("ContentSize"), content_type=doc.get("FileExtension"),
            updated_at=self._parse_dt(doc.get("LastModifiedDate")),
        )

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        if folder_id is None:
            libs = self._query(creds, "SELECT Id, Name FROM ContentWorkspace")
            return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                                   folders=[self._lib_to_folder(l) for l in libs], files=[])
        links = self._query(creds, f"SELECT ContentDocumentId FROM ContentWorkspaceDoc WHERE ContentWorkspaceId='{folder_id}'")
        files = []
        for link in links:
            doc_id = link["ContentDocumentId"]
            doc = self._get(creds, f"{self._api(creds)}/sobjects/ContentDocument/{doc_id}")
            files.append(self._doc_to_file(doc, folder_id))
        lib = self._get(creds, f"{self._api(creds)}/sobjects/ContentWorkspace/{folder_id}")
        return FolderContents(folder=self._lib_to_folder(lib), breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=[], files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        # Salesforce's REST API has no simple documented "list my Files
        # recycle bin" call, so trashing here is a soft unlink from its
        # library rather than a true delete — nothing to list separately.
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")], folders=[], files=[])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        raise ProviderError("Salesforce Files libraries must be created in Salesforce Setup, not from here", status_code=400)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        raise ProviderError("Salesforce Files libraries must be renamed in Salesforce Setup, not from here", status_code=400)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        raise ProviderError("Salesforce Files libraries can't be moved", status_code=400)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        raise ProviderError("Salesforce Files libraries must be deleted in Salesforce Setup, not from here", status_code=400)

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        b64 = base64.b64encode(content).decode()
        created = self._call(creds, "POST", f"{self._api(creds)}/sobjects/ContentVersion", json={
            "Title": name, "PathOnClient": name, "VersionData": b64,
        }).json()
        version_id = created["id"]
        ver = self._get(creds, f"{self._api(creds)}/sobjects/ContentVersion/{version_id}",
                         params={"fields": "ContentDocumentId"})
        doc_id = ver["ContentDocumentId"]
        if folder_id:
            self._call(creds, "POST", f"{self._api(creds)}/sobjects/ContentWorkspaceDoc", json={
                "ContentWorkspaceId": folder_id, "ContentDocumentId": doc_id,
            })
        doc = self._get(creds, f"{self._api(creds)}/sobjects/ContentDocument/{doc_id}")
        return self._doc_to_file(doc, folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        doc = self._get(creds, f"{self._api(creds)}/sobjects/ContentDocument/{file_id}")
        return self._doc_to_file(doc, None)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        self._call(creds, "PATCH", f"{self._api(creds)}/sobjects/ContentDocument/{file_id}", json={"Title": name})
        return self.get_file(creds, file_id)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        existing = self._query(creds, f"SELECT Id, ContentWorkspaceId FROM ContentWorkspaceDoc WHERE ContentDocumentId='{file_id}'")
        for link in existing:
            self._call(creds, "DELETE", f"{self._api(creds)}/sobjects/ContentWorkspaceDoc/{link['Id']}")
        if new_folder_id:
            self._call(creds, "POST", f"{self._api(creds)}/sobjects/ContentWorkspaceDoc", json={
                "ContentWorkspaceId": new_folder_id, "ContentDocumentId": file_id,
            })
        doc = self._get(creds, f"{self._api(creds)}/sobjects/ContentDocument/{file_id}")
        return self._doc_to_file(doc, new_folder_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._api(creds)}/sobjects/ContentDocument/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        doc = self._get(creds, f"{self._api(creds)}/sobjects/ContentDocument/{file_id}",
                         params={"fields": "LatestPublishedVersionId"})
        version_id = doc["LatestPublishedVersionId"]
        return self._call(creds, "GET", f"{self._api(creds)}/sobjects/ContentVersion/{version_id}/VersionData").content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        versions = self._query(creds,
            f"SELECT Id, VersionNumber, ContentSize, FileType, LastModifiedDate, IsLatest "
            f"FROM ContentVersion WHERE ContentDocumentId='{file_id}' ORDER BY VersionNumber DESC")
        return [
            VersionInfo(id=v["Id"], version_number=int(v.get("VersionNumber") or (i + 1)),
                        size_bytes=v.get("ContentSize"), content_type=v.get("FileType"),
                        is_current=bool(v.get("IsLatest")), updated_at=self._parse_dt(v.get("LastModifiedDate")))
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        b64 = base64.b64encode(content).decode()
        info = self.get_file(creds, file_id)
        self._call(creds, "POST", f"{self._api(creds)}/sobjects/ContentVersion", json={
            "ContentDocumentId": file_id, "PathOnClient": info.name, "VersionData": b64,
        })
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        return self._call(creds, "GET", f"{self._api(creds)}/sobjects/ContentVersion/{version_id}/VersionData").content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        return self.create_version(creds, file_id, "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        raise ProviderError("Salesforce Files libraries can't be trashed from here", status_code=400)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        raise ProviderError("Salesforce Files libraries can't be restored from here", status_code=400)

    def trash_file(self, creds: dict, file_id: str) -> None:
        # Soft: unlink from every library it's in, rather than a true
        # delete — Salesforce's REST API has no confidently-known listable
        # recycle bin for Files to restore from otherwise.
        existing = self._query(creds, f"SELECT Id FROM ContentWorkspaceDoc WHERE ContentDocumentId='{file_id}'")
        for link in existing:
            self._call(creds, "DELETE", f"{self._api(creds)}/sobjects/ContentWorkspaceDoc/{link['Id']}")

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        doc = self._get(creds, f"{self._api(creds)}/sobjects/ContentDocument/{file_id}")
        return self._doc_to_file(doc, None)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        escaped = self._escape(query)
        docs = self._query(creds, f"SELECT Id, Title, ContentSize, FileType, LastModifiedDate, "
                                   f"LatestPublishedVersionId FROM ContentDocument WHERE Title LIKE '%{escaped}%'")
        libs = self._query(creds, f"SELECT Id, Name FROM ContentWorkspace WHERE Name LIKE '%{escaped}%'")
        return [self._lib_to_folder(l) for l in libs], [self._doc_to_file(d, None) for d in docs]
