"""Google Drive, Microsoft 365 (OneDrive/SharePoint via Graph), and Box.

UNVERIFIED — written against each platform's stable, documented REST API,
but none of them can be exercised without a real OAuth app (client id +
secret registered in that platform's own developer console) and a live
consent flow through a real account. Set the client id/secret via Admin
Settings (or the FD_*_CLIENT_ID/FD_*_CLIENT_SECRET env vars they fall back
to) and run the actual OAuth flow before trusting these the way FileNet's
and local disk's providers are trusted.

Unlike a FileNet server or an Alfresco URL, the OAuth client id/secret is
NOT per-connection — it's the one app this whole C-ECM deployment
registers with Google/Microsoft/Box, shared by every connection to that
provider (the same way one Slack or Zapier install has a single registered
Google app that all its users consent through). `configured` reflects
whether an admin has set that up; end users never see or enter it — they
just click Connect.

All three follow the same shape: `_root_id()` lazily creates/finds a
"C-ECM" folder so this app never touches the rest of the user's real
drive, ids are the platform's own opaque node/item ids, and `creds` holds
{"access_token", "refresh_token", "expires_at", "identity"} — refreshed
transparently via `refresh_if_needed()`. The root-id cache is keyed by
`identity`, not a single shared value — this provider instance is reused
across every connection to it (e.g. two different Google accounts), so a
single unkeyed cache would leak one account's folder id into another's.
"""

import threading
import time

import requests

from .. import settings_store
from ..config import (
    BOX_CLIENT_ID,
    BOX_CLIENT_SECRET,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    MS_CLIENT_ID,
    MS_CLIENT_SECRET,
    MS_TENANT,
)
from .base import (
    AuthMode,
    BreadcrumbEntry,
    FileInfo,
    FolderContents,
    FolderInfo,
    ProviderError,
    StorageProvider,
    VersionInfo,
)

_APP_ROOT_NAME = "C-ECM"


class _OAuthProviderBase(StorageProvider):
    auth_mode = AuthMode.OAUTH

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "connected account")

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        if creds.get("expires_at", 0) > time.time() + 30:
            return creds, False
        return self._refresh_token(creds), True

    # Kept for internal call sites (_call, create_document, etc.) that need
    # the refreshed creds in hand immediately rather than via the public
    # (creds, changed) contract every other provider method uses.
    def _refresh_if_needed(self, creds: dict) -> dict:
        refreshed, _changed = self.refresh_if_needed(creds)
        return refreshed

    def _refresh_token(self, creds: dict) -> dict:
        raise NotImplementedError


# ---------------------------------------------------------------- Google --

class GoogleDriveProvider(_OAuthProviderBase):
    key = "google_drive"
    display_name = "Google Drive"

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()

    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("google_client_id", GOOGLE_CLIENT_ID),
            settings_store.get_setting("google_client_secret", GOOGLE_CLIENT_SECRET),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret = self._client()
        return bool(client_id and client_secret)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret = self._client()
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            # drive.file, not the full "drive" scope — C-ECM only ever
            # touches the one folder it creates for itself, and drive.file
            # (files/folders this app created, or the user explicitly picked)
            # covers that. It matters beyond scope-minimalism: Google
            # classifies plain "drive" as a *restricted* scope requiring a
            # paid third-party security assessment before the app can leave
            # testing mode — drive.file doesn't carry that requirement.
            "scope": "https://www.googleapis.com/auth/drive.file",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret = self._client()
        resp = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Google token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + tok.get("expires_in", 3600),
        }
        about = self._get(creds, "https://www.googleapis.com/drive/v3/about", params={"fields": "user"})
        creds["identity"] = about.get("user", {}).get("emailAddress", "Google account")
        return creds

    def _refresh_token(self, creds: dict) -> dict:
        if not creds.get("refresh_token"):
            raise ProviderError("Google session expired — please reconnect", status_code=401)
        client_id, client_secret = self._client()
        resp = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": creds["refresh_token"], "grant_type": "refresh_token",
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Google session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        return creds

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"Google Drive error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds = self._refresh_if_needed(creds)
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Google Drive error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def _root_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "")
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        # This provider instance is a process-wide singleton (registry.py)
        # and FastAPI runs sync handlers in a real thread pool, so without
        # a lock, several concurrent first-requests for the same
        # newly-connected account (a page load fires parallel calls for
        # listing/tags/activity/notifications right after connecting) would
        # each see an empty cache, each find no existing folder, and each
        # create their own duplicate "C-ECM" root in the real account.
        # Double-checked locking: re-test the cache after acquiring the
        # lock, since another thread may have populated it while we waited.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            q = "name='C-ECM' and mimeType='application/vnd.google-apps.folder' and 'root' in parents and trashed=false"
            found = self._call(creds, "GET", "https://www.googleapis.com/drive/v3/files", params={"q": q}).json()
            files = found.get("files", [])
            if files:
                self._root_id_cache[cache_key] = files[0]["id"]
                return files[0]["id"]
            created = self._call(creds, "POST", "https://www.googleapis.com/drive/v3/files", json={
                "name": "C-ECM", "mimeType": "application/vnd.google-apps.folder", "parents": ["root"],
            }).json()
            self._root_id_cache[cache_key] = created["id"]
            return created["id"]

    def _node_to_folder(self, e: dict, root_id: str) -> FolderInfo:
        parents = e.get("parents") or []
        parent_id = parents[0] if parents else None
        if parent_id == root_id:
            parent_id = None
        return FolderInfo(id=e["id"], name=e["name"], parent_id=parent_id, created_at=None)

    def _node_to_file(self, e: dict, root_id: str) -> FileInfo:
        parents = e.get("parents") or []
        parent_id = parents[0] if parents else None
        if parent_id == root_id:
            parent_id = None
        size = e.get("size")
        return FileInfo(
            id=e["id"], name=e["name"], folder_id=parent_id, version_number=1,
            size_bytes=int(size) if size else None, content_type=e.get("mimeType"), updated_at=None,
        )

    _FIELDS = "id,name,mimeType,size,parents,modifiedTime"

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id or root_id
        q = f"'{node_id}' in parents and trashed=false"
        result = self._call(creds, "GET", "https://www.googleapis.com/drive/v3/files",
                             params={"q": q, "fields": f"files({self._FIELDS})", "pageSize": 1000}).json()
        entries = result.get("files", [])
        folders = [self._node_to_folder(e, root_id) for e in entries if e["mimeType"] == "application/vnd.google-apps.folder"]
        files = [self._node_to_file(e, root_id) for e in entries if e["mimeType"] != "application/vnd.google-apps.folder"]
        current_folder = None
        if folder_id is not None:
            node = self._call(creds, "GET", f"https://www.googleapis.com/drive/v3/files/{node_id}",
                               params={"fields": self._FIELDS}).json()
            current_folder = self._node_to_folder(node, root_id)
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        root_id = self._root_id(creds)
        result = self._call(creds, "GET", "https://www.googleapis.com/drive/v3/files", params={
            "q": "trashed=true", "fields": f"files({self._FIELDS})", "pageSize": 1000,
        }).json()
        entries = result.get("files", [])
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=[self._node_to_folder(e, root_id) for e in entries if e["mimeType"] == "application/vnd.google-apps.folder"],
                               files=[self._node_to_file(e, root_id) for e in entries if e["mimeType"] != "application/vnd.google-apps.folder"])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        root_id = self._root_id(creds)
        parent = parent_id or root_id
        created = self._call(creds, "POST", "https://www.googleapis.com/drive/v3/files", json={
            "name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent],
        }).json()
        return self._node_to_folder(created, root_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "PATCH", f"https://www.googleapis.com/drive/v3/files/{folder_id}", json={"name": name}).json()
        return self._node_to_folder(updated, self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        node = self._call(creds, "GET", f"https://www.googleapis.com/drive/v3/files/{folder_id}", params={"fields": "parents"}).json()
        old_parents = ",".join(node.get("parents", []))
        root_id = self._root_id(creds)
        new_parent = new_parent_id or root_id
        updated = self._call(creds, "PATCH", f"https://www.googleapis.com/drive/v3/files/{folder_id}",
                              params={"addParents": new_parent, "removeParents": old_parents}).json()
        return self._node_to_folder(updated, root_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"https://www.googleapis.com/drive/v3/files/{folder_id}")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        creds = self._refresh_if_needed(creds)
        root_id = self._root_id(creds)
        parent = folder_id or root_id
        import json as _json
        boundary = "filedrive-boundary"
        metadata = _json.dumps({"name": name, "parents": [parent]})
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n"
            f"--{boundary}\r\nContent-Type: {content_type}\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--".encode()
        resp = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            headers={"Authorization": f"Bearer {creds['access_token']}", "Content-Type": f"multipart/related; boundary={boundary}"},
            data=body, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Google Drive upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        created = self._call(creds, "GET", f"https://www.googleapis.com/drive/v3/files/{resp.json()['id']}",
                              params={"fields": self._FIELDS}).json()
        return self._node_to_file(created, root_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        node = self._call(creds, "GET", f"https://www.googleapis.com/drive/v3/files/{file_id}", params={"fields": self._FIELDS}).json()
        return self._node_to_file(node, self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "PATCH", f"https://www.googleapis.com/drive/v3/files/{file_id}", json={"name": name}).json()
        return self._node_to_file(updated, self._root_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        node = self._call(creds, "GET", f"https://www.googleapis.com/drive/v3/files/{file_id}", params={"fields": "parents"}).json()
        old_parents = ",".join(node.get("parents", []))
        root_id = self._root_id(creds)
        new_parent = new_folder_id or root_id
        updated = self._call(creds, "PATCH", f"https://www.googleapis.com/drive/v3/files/{file_id}",
                              params={"addParents": new_parent, "removeParents": old_parents}).json()
        return self._node_to_file(updated, root_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"https://www.googleapis.com/drive/v3/files/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        resp = self._call(creds, "GET", f"https://www.googleapis.com/drive/v3/files/{file_id}", params={"alt": "media"})
        return resp.content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._call(creds, "GET", f"https://www.googleapis.com/drive/v3/files/{file_id}/revisions",
                             params={"fields": "revisions(id,mimeType,size,modifiedTime)"}).json()
        revisions = result.get("revisions", [])
        return [
            VersionInfo(id=r["id"], version_number=i + 1, size_bytes=int(r["size"]) if r.get("size") else None,
                         content_type=r.get("mimeType"), is_current=(i == len(revisions) - 1), updated_at=None)
            for i, r in enumerate(revisions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        creds = self._refresh_if_needed(creds)
        resp = requests.patch(
            f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media",
            headers={"Authorization": f"Bearer {creds['access_token']}", "Content-Type": content_type},
            data=content, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Google Drive version upload failed ({resp.status_code})", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        resp = self._call(creds, "GET", f"https://www.googleapis.com/drive/v3/files/{file_id}/revisions/{version_id}",
                           params={"alt": "media"})
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        node = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, node.content_type or "application/octet-stream", old_bytes)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "PATCH", f"https://www.googleapis.com/drive/v3/files/{folder_id}", json={"trashed": True})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        updated = self._call(creds, "PATCH", f"https://www.googleapis.com/drive/v3/files/{folder_id}", json={"trashed": False}).json()
        return self._node_to_folder(updated, self._root_id(creds))

    def trash_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "PATCH", f"https://www.googleapis.com/drive/v3/files/{file_id}", json={"trashed": True})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        updated = self._call(creds, "PATCH", f"https://www.googleapis.com/drive/v3/files/{file_id}", json={"trashed": False}).json()
        return self._node_to_file(updated, self._root_id(creds))

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root_id = self._root_id(creds)
        q = f"name contains '{query}' and trashed=false"
        result = self._call(creds, "GET", "https://www.googleapis.com/drive/v3/files",
                             params={"q": q, "fields": f"files({self._FIELDS})", "pageSize": 100}).json()
        entries = result.get("files", [])
        return (
            [self._node_to_folder(e, root_id) for e in entries if e["mimeType"] == "application/vnd.google-apps.folder"],
            [self._node_to_file(e, root_id) for e in entries if e["mimeType"] != "application/vnd.google-apps.folder"],
        )


# ------------------------------------------------------------- Microsoft --

class MicrosoftGraphProvider(_OAuthProviderBase):
    key = "onedrive_sharepoint"
    display_name = "Microsoft 365 (OneDrive / SharePoint)"

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()

    def _client(self) -> tuple[str, str, str]:
        return (
            settings_store.get_setting("ms_client_id", MS_CLIENT_ID),
            settings_store.get_setting("ms_client_secret", MS_CLIENT_SECRET),
            settings_store.get_setting("ms_tenant", MS_TENANT),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret, _tenant = self._client()
        return bool(client_id and client_secret)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret, tenant = self._client()
        params = {
            "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
            "response_mode": "query", "scope": "offline_access Files.ReadWrite User.Read", "state": state,
        }
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret, tenant = self._client()
        resp = requests.post(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", data={
            "client_id": client_id, "client_secret": client_secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Microsoft token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                 "expires_at": time.time() + tok.get("expires_in", 3600)}
        me = self._get(creds, "https://graph.microsoft.com/v1.0/me")
        creds["identity"] = me.get("mail") or me.get("userPrincipalName", "Microsoft account")
        return creds

    def _refresh_token(self, creds: dict) -> dict:
        if not creds.get("refresh_token"):
            raise ProviderError("Microsoft session expired — please reconnect", status_code=401)
        client_id, client_secret, tenant = self._client()
        resp = requests.post(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": creds["refresh_token"], "grant_type": "refresh_token",
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Microsoft session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        if tok.get("refresh_token"):
            creds["refresh_token"] = tok["refresh_token"]
        return creds

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"Microsoft Graph error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds = self._refresh_if_needed(creds)
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Microsoft Graph error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    _API = "https://graph.microsoft.com/v1.0"

    def _root_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "")
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        # See GoogleDriveProvider._root_id for why this needs a lock: a
        # process-wide singleton instance plus FastAPI's real thread pool
        # means concurrent first-requests for the same account would
        # otherwise each create their own duplicate root folder.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            try:
                node = self._call(creds, "GET", f"{self._API}/me/drive/root:/{_APP_ROOT_NAME}").json()
                root_id = node["id"]
            except ProviderError:
                created = self._call(creds, "POST", f"{self._API}/me/drive/root/children", json={
                    "name": _APP_ROOT_NAME, "folder": {}, "@microsoft.graph.conflictBehavior": "rename",
                }).json()
                root_id = created["id"]
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _node_to_folder(self, e: dict, root_id: str) -> FolderInfo:
        parent_id = (e.get("parentReference") or {}).get("id")
        if parent_id == root_id:
            parent_id = None
        return FolderInfo(id=e["id"], name=e["name"], parent_id=parent_id, created_at=None)

    def _node_to_file(self, e: dict, root_id: str) -> FileInfo:
        parent_id = (e.get("parentReference") or {}).get("id")
        if parent_id == root_id:
            parent_id = None
        return FileInfo(
            id=e["id"], name=e["name"], folder_id=parent_id, version_number=1,
            size_bytes=e.get("size"), content_type=(e.get("file") or {}).get("mimeType"), updated_at=None,
        )

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id or root_id
        result = self._call(creds, "GET", f"{self._API}/me/drive/items/{node_id}/children").json()
        entries = result.get("value", [])
        folders = [self._node_to_folder(e, root_id) for e in entries if "folder" in e]
        files = [self._node_to_file(e, root_id) for e in entries if "file" in e]
        current_folder = None
        if folder_id is not None:
            node = self._call(creds, "GET", f"{self._API}/me/drive/items/{node_id}").json()
            current_folder = self._node_to_folder(node, root_id)
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        # Graph's recycle-bin listing isn't consistently available for
        # personal OneDrive — deletes below go to the native recycle bin,
        # but this app can't reliably enumerate or restore from it via a
        # single documented endpoint. Reporting empty rather than guessing.
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")], folders=[], files=[])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        root_id = self._root_id(creds)
        parent = parent_id or root_id
        created = self._call(creds, "POST", f"{self._API}/me/drive/items/{parent}/children", json={
            "name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "rename",
        }).json()
        return self._node_to_folder(created, root_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "PATCH", f"{self._API}/me/drive/items/{folder_id}", json={"name": name}).json()
        return self._node_to_folder(updated, self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        root_id = self._root_id(creds)
        target = new_parent_id or root_id
        updated = self._call(creds, "PATCH", f"{self._API}/me/drive/items/{folder_id}",
                              json={"parentReference": {"id": target}}).json()
        return self._node_to_folder(updated, root_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{self._API}/me/drive/items/{folder_id}")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        root_id = self._root_id(creds)
        parent = folder_id or root_id
        resp = self._call(creds, "PUT", f"{self._API}/me/drive/items/{parent}:/{name}:/content",
                           data=content, headers={"Content-Type": content_type})
        return self._node_to_file(resp.json(), root_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        node = self._call(creds, "GET", f"{self._API}/me/drive/items/{file_id}").json()
        return self._node_to_file(node, self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "PATCH", f"{self._API}/me/drive/items/{file_id}", json={"name": name}).json()
        return self._node_to_file(updated, self._root_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        root_id = self._root_id(creds)
        target = new_folder_id or root_id
        updated = self._call(creds, "PATCH", f"{self._API}/me/drive/items/{file_id}",
                              json={"parentReference": {"id": target}}).json()
        return self._node_to_file(updated, root_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._API}/me/drive/items/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        resp = self._call(creds, "GET", f"{self._API}/me/drive/items/{file_id}/content")
        return resp.content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._call(creds, "GET", f"{self._API}/me/drive/items/{file_id}/versions").json()
        versions = result.get("value", [])
        return [
            VersionInfo(id=v["id"], version_number=i + 1, size_bytes=v.get("size"),
                         content_type=None, is_current=(i == 0), updated_at=None)
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        resp = self._call(creds, "PUT", f"{self._API}/me/drive/items/{file_id}/content",
                           data=content, headers={"Content-Type": content_type})
        return self._node_to_file(resp.json(), self._root_id(creds))

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        resp = self._call(creds, "GET", f"{self._API}/me/drive/items/{file_id}/versions/{version_id}/content")
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        self._call(creds, "POST", f"{self._API}/me/drive/items/{file_id}/versions/{version_id}/restoreVersion")
        return self.get_file(creds, file_id)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self.delete_folder(creds, folder_id)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        raise ProviderError("Restoring from OneDrive's recycle bin isn't supported yet", status_code=501)

    def trash_file(self, creds: dict, file_id: str) -> None:
        self.delete_file(creds, file_id)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        raise ProviderError("Restoring from OneDrive's recycle bin isn't supported yet", status_code=501)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root_id = self._root_id(creds)
        result = self._call(creds, "GET", f"{self._API}/me/drive/items/{root_id}/search(q='{query}')").json()
        entries = result.get("value", [])
        return (
            [self._node_to_folder(e, root_id) for e in entries if "folder" in e],
            [self._node_to_file(e, root_id) for e in entries if "file" in e],
        )


# ------------------------------------------------------------------ Box --

class BoxProvider(_OAuthProviderBase):
    key = "box"
    display_name = "Box"

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()

    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("box_client_id", BOX_CLIENT_ID),
            settings_store.get_setting("box_client_secret", BOX_CLIENT_SECRET),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret = self._client()
        return bool(client_id and client_secret)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret = self._client()
        params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "state": state}
        return "https://account.box.com/api/oauth2/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret = self._client()
        resp = requests.post("https://api.box.com/oauth2/token", data={
            "client_id": client_id, "client_secret": client_secret, "code": code,
            "grant_type": "authorization_code",
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Box token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                 "expires_at": time.time() + tok.get("expires_in", 3600)}
        me = self._get(creds, "https://api.box.com/2.0/users/me")
        creds["identity"] = me.get("login", "Box account")
        return creds

    def _refresh_token(self, creds: dict) -> dict:
        if not creds.get("refresh_token"):
            raise ProviderError("Box session expired — please reconnect", status_code=401)
        client_id, client_secret = self._client()
        resp = requests.post("https://api.box.com/oauth2/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": creds["refresh_token"], "grant_type": "refresh_token",
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Box session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        return creds

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"Box error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds = self._refresh_if_needed(creds)
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Box error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    _API = "https://api.box.com/2.0"

    def _root_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "")
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        # See GoogleDriveProvider._root_id for why this needs a lock.
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            items = self._call(creds, "GET", f"{self._API}/folders/0/items", params={"fields": "id,name,type"}).json()
            for e in items.get("entries", []):
                if e["type"] == "folder" and e["name"] == _APP_ROOT_NAME:
                    self._root_id_cache[cache_key] = e["id"]
                    return e["id"]
            created = self._call(creds, "POST", f"{self._API}/folders", json={"name": _APP_ROOT_NAME, "parent": {"id": "0"}}).json()
            self._root_id_cache[cache_key] = created["id"]
            return created["id"]

    def _node_to_folder(self, e: dict, root_id: str) -> FolderInfo:
        parent = e.get("parent")
        parent_id = parent["id"] if parent else None
        if parent_id == root_id:
            parent_id = None
        return FolderInfo(id=e["id"], name=e["name"], parent_id=parent_id, created_at=None)

    def _node_to_file(self, e: dict, root_id: str) -> FileInfo:
        parent = e.get("parent")
        parent_id = parent["id"] if parent else None
        if parent_id == root_id:
            parent_id = None
        return FileInfo(id=e["id"], name=e["name"], folder_id=parent_id, version_number=1,
                         size_bytes=e.get("size"), content_type=None, updated_at=None)

    _FIELDS = "id,name,parent,size,type"

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id or root_id
        result = self._call(creds, "GET", f"{self._API}/folders/{node_id}/items",
                             params={"fields": self._FIELDS, "limit": 1000}).json()
        entries = result.get("entries", [])
        folders = [self._node_to_folder(e, root_id) for e in entries if e["type"] == "folder"]
        files = [self._node_to_file(e, root_id) for e in entries if e["type"] == "file"]
        current_folder = None
        if folder_id is not None:
            node = self._call(creds, "GET", f"{self._API}/folders/{node_id}", params={"fields": self._FIELDS}).json()
            current_folder = self._node_to_folder(node, root_id)
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        root_id = self._root_id(creds)
        result = self._call(creds, "GET", f"{self._API}/folders/trash/items", params={"fields": self._FIELDS}).json()
        entries = result.get("entries", [])
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
                               folders=[self._node_to_folder(e, root_id) for e in entries if e["type"] == "folder"],
                               files=[self._node_to_file(e, root_id) for e in entries if e["type"] == "file"])

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        root_id = self._root_id(creds)
        parent = parent_id or root_id
        created = self._call(creds, "POST", f"{self._API}/folders", json={"name": name, "parent": {"id": parent}}).json()
        return self._node_to_folder(created, root_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "PUT", f"{self._API}/folders/{folder_id}", json={"name": name}).json()
        return self._node_to_folder(updated, self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        root_id = self._root_id(creds)
        target = new_parent_id or root_id
        updated = self._call(creds, "PUT", f"{self._API}/folders/{folder_id}", json={"parent": {"id": target}}).json()
        return self._node_to_folder(updated, root_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{self._API}/folders/{folder_id}", params={"recursive": "true"})
        self._call(creds, "DELETE", f"{self._API}/folders/{folder_id}/trash")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        creds = self._refresh_if_needed(creds)
        root_id = self._root_id(creds)
        parent = folder_id or root_id
        import json as _json
        resp = requests.post(
            "https://upload.box.com/api/2.0/files/content",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            data={"attributes": _json.dumps({"name": name, "parent": {"id": parent}})},
            files={"file": (name, content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Box upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self._node_to_file(resp.json()["entries"][0], root_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        node = self._call(creds, "GET", f"{self._API}/files/{file_id}", params={"fields": self._FIELDS}).json()
        return self._node_to_file(node, self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "PUT", f"{self._API}/files/{file_id}", json={"name": name}).json()
        return self._node_to_file(updated, self._root_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        root_id = self._root_id(creds)
        target = new_folder_id or root_id
        updated = self._call(creds, "PUT", f"{self._API}/files/{file_id}", json={"parent": {"id": target}}).json()
        return self._node_to_file(updated, root_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._API}/files/{file_id}")
        self._call(creds, "DELETE", f"{self._API}/files/{file_id}/trash")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        resp = self._call(creds, "GET", f"{self._API}/files/{file_id}/content")
        return resp.content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._call(creds, "GET", f"{self._API}/files/{file_id}/versions").json()
        versions = result.get("entries", [])
        return [
            VersionInfo(id=v["id"], version_number=i + 1, size_bytes=v.get("size"),
                         content_type=None, is_current=False, updated_at=None)
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        creds = self._refresh_if_needed(creds)
        resp = requests.post(
            f"https://upload.box.com/api/2.0/files/{file_id}/content",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            files={"file": ("content", content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Box version upload failed ({resp.status_code})", status_code=502)
        return self._node_to_file(resp.json()["entries"][0], self._root_id(creds))

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        resp = self._call(creds, "GET", f"{self._API}/files/{file_id}/content", params={"version": version_id})
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        updated = self._call(creds, "POST", f"{self._API}/files/{file_id}/versions/current",
                              json={"type": "file_version", "id": version_id}).json()
        return self._node_to_file(updated, self._root_id(creds))

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{self._API}/folders/{folder_id}", params={"recursive": "true"})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        updated = self._call(creds, "POST", f"{self._API}/folders/{folder_id}").json()
        return self._node_to_folder(updated, self._root_id(creds))

    def trash_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._API}/files/{file_id}")

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        updated = self._call(creds, "POST", f"{self._API}/files/{file_id}").json()
        return self._node_to_file(updated, self._root_id(creds))

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root_id = self._root_id(creds)
        result = self._call(creds, "GET", f"{self._API}/search",
                             params={"query": query, "ancestor_folder_ids": root_id, "fields": self._FIELDS}).json()
        entries = result.get("entries", [])
        return (
            [self._node_to_folder(e, root_id) for e in entries if e["type"] == "folder"],
            [self._node_to_file(e, root_id) for e in entries if e["type"] == "file"],
        )
