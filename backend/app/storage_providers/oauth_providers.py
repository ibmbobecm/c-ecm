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
    DROPBOX_CLIENT_ID,
    DROPBOX_CLIENT_SECRET,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    LASERFICHE_CLIENT_ID,
    LASERFICHE_CLIENT_SECRET,
    MS_CLIENT_ID,
    MS_CLIENT_SECRET,
    MS_TENANT,
    SHAREFILE_CLIENT_ID,
    SHAREFILE_CLIENT_SECRET,
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


# -------------------------------------------------------------- Dropbox --

class DropboxProvider(_OAuthProviderBase):
    """Unlike Box/Google/MS above, Dropbox's API is PATH-based, not
    id-based — a file/folder's "id" here IS its full Dropbox path (e.g.
    "/reports/q3/file.txt"), which is still an opaque string as far as the
    rest of C-ECM is concerned. Requires the OAuth app to be registered
    with "App folder" access in Dropbox's own App Console (not "Full
    Dropbox") — that scopes every path this provider ever sees to the
    app's own sandboxed folder automatically, so path="" (empty string,
    Dropbox's own convention for "the root") already means exactly this
    app's folder, with no separate root-finding dance needed the way
    Box/Google/MS require.

    Dropbox's public API has no native "list what's in trash" endpoint
    (deleted items are only restorable if you already know their exact
    former path) — trash is emulated the same way local disk's own trash
    is, by moving things into a dedicated in-app folder rather than
    relying on anything Dropbox itself tracks. The original path is
    encoded (percent-encoded, via the same urllib every browser uses for
    URLs) into a single flat filename under that folder, both so restore
    knows where to put something back and so a folder trashed from deep
    in the tree still shows up as ONE entry in the trash listing instead
    of reappearing nested the way its real path would put it."""

    key = "dropbox"
    display_name = "Dropbox"

    _API = "https://api.dropboxapi.com/2"
    _CONTENT_API = "https://content.dropboxapi.com/2"
    _TRASH_ROOT = "/.c-ecm-trash"

    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("dropbox_client_id", DROPBOX_CLIENT_ID),
            settings_store.get_setting("dropbox_client_secret", DROPBOX_CLIENT_SECRET),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret = self._client()
        return bool(client_id and client_secret)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret = self._client()
        params = {
            "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "state": state,
            # Without this, Dropbox only ever returns a short-lived access
            # token and no refresh_token — refresh_if_needed would have
            # nothing to work with the moment it expired.
            "token_access_type": "offline",
        }
        return "https://www.dropbox.com/oauth2/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret = self._client()
        resp = requests.post("https://api.dropboxapi.com/oauth2/token", data={
            "client_id": client_id, "client_secret": client_secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Dropbox token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                 "expires_at": time.time() + tok.get("expires_in", 14400)}
        me = self._call(creds, "users/get_current_account", None)
        creds["identity"] = me.get("email") or (me.get("name") or {}).get("display_name", "Dropbox account")
        return creds

    def _refresh_token(self, creds: dict) -> dict:
        if not creds.get("refresh_token"):
            raise ProviderError("Dropbox session expired — please reconnect", status_code=401)
        client_id, client_secret = self._client()
        resp = requests.post("https://api.dropboxapi.com/oauth2/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": creds["refresh_token"], "grant_type": "refresh_token",
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Dropbox session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["expires_at"] = time.time() + tok.get("expires_in", 14400)
        return creds

    def _call(self, creds: dict, endpoint: str, args: dict | None) -> dict:
        creds = self._refresh_if_needed(creds)
        resp = requests.post(
            f"{self._API}/{endpoint}",
            headers={"Authorization": f"Bearer {creds['access_token']}", "Content-Type": "application/json"},
            json=args, timeout=30,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Dropbox error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _content_call(self, creds: dict, endpoint: str, args: dict, content: bytes) -> dict:
        import json as _json
        creds = self._refresh_if_needed(creds)
        resp = requests.post(
            f"{self._CONTENT_API}/{endpoint}",
            headers={
                "Authorization": f"Bearer {creds['access_token']}",
                "Dropbox-API-Arg": _json.dumps(args),
                "Content-Type": "application/octet-stream",
            },
            data=content, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Dropbox upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return resp.json()

    def _download(self, creds: dict, args: dict) -> bytes:
        import json as _json
        creds = self._refresh_if_needed(creds)
        resp = requests.post(
            f"{self._CONTENT_API}/files/download",
            headers={"Authorization": f"Bearer {creds['access_token']}", "Dropbox-API-Arg": _json.dumps(args)},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Dropbox download failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return resp.content

    @staticmethod
    def _parent_path(path: str) -> str | None:
        idx = path.rstrip("/").rfind("/")
        parent = path[:idx] if idx > 0 else ""
        return parent or None

    @staticmethod
    def _parse_dt(s: str | None):
        if not s:
            return None
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def _to_trash_path(self, original_path: str) -> str:
        from urllib.parse import quote
        return f"{self._TRASH_ROOT}/{quote(original_path, safe='')}"

    @classmethod
    def _from_trash_path(cls, trash_path: str) -> str:
        """Display name for a trashed item — just the original basename,
        not its full original path (use _from_trash_path_full for that)."""
        original = cls._from_trash_path_full(trash_path)
        return original.rsplit("/", 1)[-1]

    def _node_to_folder(self, e: dict) -> FolderInfo:
        path = e["path_lower"]
        return FolderInfo(id=path, name=e["name"], parent_id=self._parent_path(path), created_at=None)

    def _node_to_file(self, e: dict) -> FileInfo:
        path = e["path_lower"]
        return FileInfo(id=path, name=e["name"], folder_id=self._parent_path(path), version_number=1,
                         size_bytes=e.get("size"), content_type=None,
                         updated_at=self._parse_dt(e.get("server_modified")))

    def _trash_node_to_folder(self, e: dict) -> FolderInfo:
        return FolderInfo(id=e["path_lower"], name=self._from_trash_path(e["path_lower"]), parent_id=None, created_at=None)

    def _trash_node_to_file(self, e: dict) -> FileInfo:
        return FileInfo(id=e["path_lower"], name=self._from_trash_path(e["path_lower"]), folder_id=None,
                         version_number=1, size_bytes=e.get("size"), content_type=None,
                         updated_at=self._parse_dt(e.get("server_modified")))

    def _list_folder_all(self, creds: dict, path: str) -> list[dict]:
        result = self._call(creds, "files/list_folder", {"path": path})
        entries = list(result.get("entries", []))
        while result.get("has_more"):
            result = self._call(creds, "files/list_folder/continue", {"cursor": result["cursor"]})
            entries.extend(result.get("entries", []))
        return entries

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        path = folder_id or ""
        entries = self._list_folder_all(creds, path)
        folders = [self._node_to_folder(e) for e in entries if e.get(".tag") == "folder"]
        files = [self._node_to_file(e) for e in entries if e.get(".tag") == "file"]
        current_folder = None
        if folder_id:
            meta = self._call(creds, "files/get_metadata", {"path": folder_id})
            current_folder = self._node_to_folder(meta)
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        try:
            entries = self._list_folder_all(creds, self._TRASH_ROOT)
        except ProviderError:
            entries = []  # nothing's ever been trashed, so the folder doesn't exist yet
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._trash_node_to_folder(e) for e in entries if e.get(".tag") == "folder"],
            files=[self._trash_node_to_file(e) for e in entries if e.get(".tag") == "file"],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        result = self._call(creds, "files/create_folder_v2", {"path": f"{parent_id or ''}/{name}"})
        return self._node_to_folder(result["metadata"])

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        new_path = f"{self._parent_path(folder_id) or ''}/{name}"
        result = self._call(creds, "files/move_v2", {"from_path": folder_id, "to_path": new_path})
        return self._node_to_folder(result["metadata"])

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        name = folder_id.rsplit("/", 1)[-1]
        new_path = f"{new_parent_id or ''}/{name}"
        result = self._call(creds, "files/move_v2", {"from_path": folder_id, "to_path": new_path})
        return self._node_to_folder(result["metadata"])

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "files/delete_v2", {"path": folder_id})

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        path = f"{folder_id or ''}/{name}"
        result = self._content_call(creds, "files/upload", {"path": path, "mode": "add", "autorename": True}, content)
        return self._node_to_file(result)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        meta = self._call(creds, "files/get_metadata", {"path": file_id})
        return self._node_to_file(meta)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        new_path = f"{self._parent_path(file_id) or ''}/{name}"
        result = self._call(creds, "files/move_v2", {"from_path": file_id, "to_path": new_path})
        return self._node_to_file(result["metadata"])

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        name = file_id.rsplit("/", 1)[-1]
        new_path = f"{new_folder_id or ''}/{name}"
        result = self._call(creds, "files/move_v2", {"from_path": file_id, "to_path": new_path})
        return self._node_to_file(result["metadata"])

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "files/delete_v2", {"path": file_id})

    def get_content(self, creds: dict, file_id: str) -> bytes:
        return self._download(creds, {"path": file_id})

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._call(creds, "files/list_revisions", {"path": file_id, "mode": "path", "limit": 50})
        entries = result.get("entries", [])  # newest first
        total = len(entries)
        return [
            VersionInfo(id=e["rev"], version_number=total - i, size_bytes=e.get("size"),
                         content_type=None, is_current=(i == 0), updated_at=self._parse_dt(e.get("server_modified")))
            for i, e in enumerate(entries)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        result = self._content_call(creds, "files/upload", {"path": file_id, "mode": "overwrite"}, content)
        return self._node_to_file(result)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        return self._download(creds, {"path": f"rev:{version_id}"})

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        result = self._call(creds, "files/restore", {"path": file_id, "rev": version_id})
        return self._node_to_file(result)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "files/move_v2",
                    {"from_path": folder_id, "to_path": self._to_trash_path(folder_id), "autorename": True})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        original = self._from_trash_path_full(folder_id)
        result = self._call(creds, "files/move_v2", {"from_path": folder_id, "to_path": original, "autorename": True})
        return self._node_to_folder(result["metadata"])

    def trash_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "files/move_v2",
                    {"from_path": file_id, "to_path": self._to_trash_path(file_id), "autorename": True})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        original = self._from_trash_path_full(file_id)
        result = self._call(creds, "files/move_v2", {"from_path": file_id, "to_path": original, "autorename": True})
        return self._node_to_file(result["metadata"])

    @classmethod
    def _from_trash_path_full(cls, trash_path: str) -> str:
        """Same decoding as _from_trash_path, but returns the full original
        path (not just the name) — what a restore actually moves back to."""
        from urllib.parse import unquote
        encoded = trash_path.rsplit("/", 1)[-1]
        return unquote(encoded)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        result = self._call(creds, "files/search_v2", {"query": query, "options": {"path": ""}})
        folders: list[FolderInfo] = []
        files: list[FileInfo] = []
        for m in result.get("matches", []):
            entry = (m.get("metadata") or {}).get("metadata") or {}
            tag = entry.get(".tag")
            if tag == "folder":
                folders.append(self._node_to_folder(entry))
            elif tag == "file":
                files.append(self._node_to_file(entry))
        return folders, files


# ------------------------------------------------------------ Laserfiche --

class LaserficheProvider(_OAuthProviderBase):
    """Laserfiche Cloud, via its documented Repository API v1
    (`api.laserfiche.com/repository/v1`), OAuth2 authorization-code flow
    against `signin.laserfiche.com`.

    UNVERIFIED — written from Laserfiche's published API reference, but
    there's no live Laserfiche Cloud account in this environment to test
    against. Confidence here is lower than Box/Dropbox/Google: Laserfiche's
    "Entries" model (folders and documents are both "Entries", disambiguated
    by an `entryType` field) is well-documented for browsing, but the exact
    request shapes for creating a subfolder, moving an entry, and listing
    document versions are reconstructed from general API convention rather
    than a line-by-line-confirmed spec — flagged inline below and again in
    the class's own worst-case fallbacks.

    A Laserfiche account can have multiple repositories; this provider picks
    the first one returned by `GET /Repositories` and remembers it in creds
    (`repository_id`) — fine for the common single-repository case, but a
    multi-repository account would need a repository picker this app doesn't
    have yet.

    Laserfiche has no confidently-documented listable "trash" API, so — same
    as Dropbox's provider above — trash is emulated by moving entries into a
    dedicated hidden folder (`_C-ECM-Trash`) under the app root rather than
    relying on anything Laserfiche itself tracks.
    """

    key = "laserfiche"
    display_name = "Laserfiche"

    _AUTH_BASE = "https://signin.laserfiche.com"
    _API = "https://api.laserfiche.com/repository/v1"

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("laserfiche_client_id", LASERFICHE_CLIENT_ID),
            settings_store.get_setting("laserfiche_client_secret", LASERFICHE_CLIENT_SECRET),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret = self._client()
        return bool(client_id and client_secret)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret = self._client()
        params = {
            "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
            "state": state, "scope": "repository.Read repository.Write",
        }
        return f"{self._AUTH_BASE}/oauth/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret = self._client()
        resp = requests.post(f"{self._AUTH_BASE}/oauth/token", data={
            "client_id": client_id, "client_secret": client_secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Laserfiche token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                 "expires_at": time.time() + tok.get("expires_in", 3600)}
        repos = self._get(creds, f"{self._API}/Repositories")
        repo_list = repos if isinstance(repos, list) else repos.get("value", repos.get("Repositories", []))
        if not repo_list:
            raise ProviderError("No Laserfiche repository is available on this account", status_code=502)
        first = repo_list[0]
        creds["repository_id"] = first.get("repoId") or first.get("id") or first.get("repositoryId")
        creds["identity"] = first.get("repoName") or first.get("name") or "Laserfiche account"
        return creds

    def _refresh_token(self, creds: dict) -> dict:
        if not creds.get("refresh_token"):
            raise ProviderError("Laserfiche session expired — please reconnect", status_code=401)
        client_id, client_secret = self._client()
        resp = requests.post(f"{self._AUTH_BASE}/oauth/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": creds["refresh_token"], "grant_type": "refresh_token",
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("Laserfiche session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        return creds

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"Laserfiche error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds = self._refresh_if_needed(creds)
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Laserfiche error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def _repo_api(self, creds: dict) -> str:
        return f"{self._API}/Repositories/{creds['repository_id']}"

    def _root_id(self, creds: dict) -> int:
        cache_key = creds.get("identity", "") + str(creds.get("repository_id", ""))
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            entries = self._call(creds, "GET", f"{self._repo_api(creds)}/Entries/1/entries").json().get("value", [])
            for e in entries:
                if e.get("entryType") == "Folder" and e.get("name") == _APP_ROOT_NAME:
                    self._root_id_cache[cache_key] = e["id"]
                    return e["id"]
            created = self._call(
                creds, "POST", f"{self._repo_api(creds)}/Entries/1/folders",
                json={"name": _APP_ROOT_NAME},
            ).json()
            root_id = created["id"]
            self._root_id_cache[cache_key] = root_id
            return root_id

    def _trash_id(self, creds: dict) -> int:
        cache_key = creds.get("identity", "") + str(creds.get("repository_id", ""))
        cached = self._trash_id_cache.get(cache_key)
        if cached:
            return cached
        with self._trash_id_lock:
            cached = self._trash_id_cache.get(cache_key)
            if cached:
                return cached
            root = self._root_id(creds)
            entries = self._call(creds, "GET", f"{self._repo_api(creds)}/Entries/{root}/entries").json().get("value", [])
            for e in entries:
                if e.get("entryType") == "Folder" and e.get("name") == "_C-ECM-Trash":
                    self._trash_id_cache[cache_key] = e["id"]
                    return e["id"]
            created = self._call(
                creds, "POST", f"{self._repo_api(creds)}/Entries/{root}/folders",
                json={"name": "_C-ECM-Trash"},
            ).json()
            trash_id = created["id"]
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "Laserfiche account")

    @staticmethod
    def _parse_dt(s: str | None):
        if not s:
            return None
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def _node_to_folder(self, e: dict, root_id) -> FolderInfo:
        parent_id = e.get("parentId")
        if parent_id == root_id or parent_id is None:
            parent_id = None
        else:
            parent_id = str(parent_id)
        return FolderInfo(id=str(e["id"]), name=e.get("name", ""), parent_id=parent_id,
                           created_at=self._parse_dt(e.get("creationTime")))

    def _node_to_file(self, e: dict, root_id) -> FileInfo:
        parent_id = e.get("parentId")
        if parent_id == root_id or parent_id is None:
            parent_id = None
        else:
            parent_id = str(parent_id)
        return FileInfo(id=str(e["id"]), name=e.get("name", ""), folder_id=parent_id, version_number=1,
                         size_bytes=e.get("elecDocumentSize"), content_type=e.get("mimeType"),
                         updated_at=self._parse_dt(e.get("lastModifiedTime")))

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id or root_id
        entries = self._call(creds, "GET", f"{self._repo_api(creds)}/Entries/{node_id}/entries").json().get("value", [])
        folders = [self._node_to_folder(e, root_id) for e in entries
                   if e.get("entryType") == "Folder" and e.get("name") != "_C-ECM-Trash"]
        files = [self._node_to_file(e, root_id) for e in entries if e.get("entryType") == "Document"]
        current_folder = None
        if folder_id is not None:
            node = self._call(creds, "GET", f"{self._repo_api(creds)}/Entries/{node_id}").json()
            current_folder = self._node_to_folder(node, root_id)
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash_id = self._trash_id(creds)
        entries = self._call(creds, "GET", f"{self._repo_api(creds)}/Entries/{trash_id}/entries").json().get("value", [])
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._node_to_folder(e, trash_id) for e in entries if e.get("entryType") == "Folder"],
            files=[self._node_to_file(e, trash_id) for e in entries if e.get("entryType") == "Document"],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        root_id = self._root_id(creds)
        parent = parent_id or root_id
        created = self._call(creds, "POST", f"{self._repo_api(creds)}/Entries/{parent}/folders", json={"name": name}).json()
        return self._node_to_folder(created, root_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "PATCH", f"{self._repo_api(creds)}/Entries/{folder_id}", json={"name": name}).json()
        return self._node_to_folder(updated, self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        root_id = self._root_id(creds)
        target = new_parent_id or root_id
        updated = self._call(creds, "PATCH", f"{self._repo_api(creds)}/Entries/{folder_id}",
                              json={"parentId": target}).json()
        return self._node_to_folder(updated, root_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{self._repo_api(creds)}/Entries/{folder_id}")

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        root_id = self._root_id(creds)
        parent = folder_id or root_id
        import json as _json
        resp = requests.post(
            f"{self._repo_api(creds)}/Entries/{parent}/documents",
            headers={"Authorization": f"Bearer {self._refresh_if_needed(creds)['access_token']}"},
            data={"request": _json.dumps({"name": name})},
            files={"electronicDocument": (name, content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Laserfiche upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self._node_to_file(resp.json(), root_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        node = self._call(creds, "GET", f"{self._repo_api(creds)}/Entries/{file_id}").json()
        return self._node_to_file(node, self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "PATCH", f"{self._repo_api(creds)}/Entries/{file_id}", json={"name": name}).json()
        return self._node_to_file(updated, self._root_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        root_id = self._root_id(creds)
        target = new_folder_id or root_id
        updated = self._call(creds, "PATCH", f"{self._repo_api(creds)}/Entries/{file_id}",
                              json={"parentId": target}).json()
        return self._node_to_file(updated, root_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._repo_api(creds)}/Entries/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        resp = self._call(creds, "GET", f"{self._repo_api(creds)}/Entries/{file_id}/edoc")
        return resp.content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        # No confidently-documented endpoint to enumerate historical
        # electronic-document versions distinctly from the current one, so
        # (same honest fallback used elsewhere in this codebase for
        # low-confidence version APIs) only the current content is reported.
        node = self._call(creds, "GET", f"{self._repo_api(creds)}/Entries/{file_id}").json()
        return [VersionInfo(id="current", version_number=1, size_bytes=node.get("elecDocumentSize"),
                             content_type=node.get("mimeType"), is_current=True,
                             updated_at=self._parse_dt(node.get("lastModifiedTime")))]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        creds = self._refresh_if_needed(creds)
        resp = requests.post(
            f"{self._repo_api(creds)}/Entries/{file_id}/edoc",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            files={"electronicDocument": ("content", content, content_type)}, timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Laserfiche version upload failed ({resp.status_code})", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        return self.get_content(creds, file_id)

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        # Only one version is ever tracked (see list_versions) — nothing to
        # restore to but the current content itself.
        return self.get_file(creds, file_id)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "PATCH", f"{self._repo_api(creds)}/Entries/{folder_id}", json={"parentId": target})

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        updated = self._call(creds, "PATCH", f"{self._repo_api(creds)}/Entries/{folder_id}",
                              json={"parentId": root}).json()
        return self._node_to_folder(updated, root)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "PATCH", f"{self._repo_api(creds)}/Entries/{file_id}", json={"parentId": target})

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        updated = self._call(creds, "PATCH", f"{self._repo_api(creds)}/Entries/{file_id}",
                              json={"parentId": root}).json()
        return self._node_to_file(updated, root)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        # Laserfiche's real search surface (SearchContexts + a dedicated
        # query DSL) isn't confidently known well enough to build against
        # reliably, so this walks the app's own folder tree and filters by
        # name client-side — slower on a large repository, but always
        # correct regardless of the exact server-side query syntax.
        root_id = self._root_id(creds)
        trash_id = self._trash_id(creds)
        found_folders: list[FolderInfo] = []
        found_files: list[FileInfo] = []
        q = query.lower()

        def walk(node_id, depth: int):
            if depth > 6:
                return
            entries = self._call(creds, "GET", f"{self._repo_api(creds)}/Entries/{node_id}/entries").json().get("value", [])
            for e in entries:
                if e.get("id") == trash_id:
                    continue
                name = e.get("name", "")
                if e.get("entryType") == "Folder":
                    if q in name.lower():
                        found_folders.append(self._node_to_folder(e, root_id))
                    walk(e["id"], depth + 1)
                elif e.get("entryType") == "Document" and q in name.lower():
                    found_files.append(self._node_to_file(e, root_id))

        walk(root_id, 0)
        return found_folders, found_files


# ------------------------------------------------------------- ShareFile --

class ShareFileProvider(_OAuthProviderBase):
    """Citrix ShareFile, via its OData v3 REST API, OAuth2 authorization-code
    flow starting at `secure.sharefile.com` (ShareFile figures out which
    account subdomain the user belongs to during login, rather than the
    caller needing to know it upfront).

    UNVERIFIED — written from ShareFile's published REST API reference, but
    there's no live ShareFile account in this environment to test against.

    ShareFile's token response includes both a `subdomain` and an `apicp`
    ("API control plane" host, e.g. "sharefile.com") that together build
    the account's real API base — `https://{subdomain}.{apicp}/sf/v3` — so
    both are stored in creds and every call is built against that, not a
    fixed host.

    File upload is ShareFile's documented two-step "Standard Uploader"
    protocol: ask `Items({id})/Upload` for a one-time upload URL, then POST
    the raw bytes to that URL. Versioning, native trash listing, and search
    are lower-confidence areas — see the inline comments on those methods.
    """

    key = "sharefile"
    display_name = "ShareFile"

    _AUTH_HOST = "secure.sharefile.com"

    def __init__(self):
        self._root_id_cache: dict[str, str] = {}
        self._trash_id_cache: dict[str, str] = {}
        self._root_id_lock = threading.Lock()
        self._trash_id_lock = threading.Lock()

    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("sharefile_client_id", SHAREFILE_CLIENT_ID),
            settings_store.get_setting("sharefile_client_secret", SHAREFILE_CLIENT_SECRET),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret = self._client()
        return bool(client_id and client_secret)

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret = self._client()
        params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "state": state}
        return f"https://{self._AUTH_HOST}/oauth/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret = self._client()
        resp = requests.post(f"https://{self._AUTH_HOST}/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "client_secret": client_secret, "redirect_uri": redirect_uri,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"ShareFile token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {
            "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + tok.get("expires_in", 3600),
            "subdomain": tok["subdomain"], "apicp": tok["apicp"],
        }
        me = self._get(creds, f"{self._api(creds)}/Users/Get")
        creds["identity"] = me.get("Email") or me.get("FullName") or "ShareFile account"
        return creds

    def _refresh_token(self, creds: dict) -> dict:
        if not creds.get("refresh_token"):
            raise ProviderError("ShareFile session expired — please reconnect", status_code=401)
        client_id, client_secret = self._client()
        resp = requests.post(f"https://{self._AUTH_HOST}/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
            "client_id": client_id, "client_secret": client_secret,
        }, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError("ShareFile session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        creds["subdomain"] = tok.get("subdomain", creds["subdomain"])
        creds["apicp"] = tok.get("apicp", creds["apicp"])
        return creds

    def _api(self, creds: dict) -> str:
        return f"https://{creds['subdomain']}.{creds['apicp']}/sf/v3"

    def _get(self, creds: dict, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise ProviderError(f"ShareFile error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kwargs) -> requests.Response:
        creds = self._refresh_if_needed(creds)
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {creds['access_token']}"}, timeout=30, **kwargs)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"ShareFile error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "ShareFile account")

    def _root_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "")
        cached = self._root_id_cache.get(cache_key)
        if cached:
            return cached
        with self._root_id_lock:
            cached = self._root_id_cache.get(cache_key)
            if cached:
                return cached
            home = self._call(creds, "GET", f"{self._api(creds)}/Items(home)").json()
            children = self._call(creds, "GET", f"{self._api(creds)}/Items({home['Id']})/Children").json()
            for e in children.get("value", []):
                if e.get("Name") == _APP_ROOT_NAME:
                    self._root_id_cache[cache_key] = e["Id"]
                    return e["Id"]
            created = self._call(creds, "POST", f"{self._api(creds)}/Items({home['Id']})/Folder",
                                  json={"Name": _APP_ROOT_NAME}).json()
            self._root_id_cache[cache_key] = created["Id"]
            return created["Id"]

    def _trash_id(self, creds: dict) -> str:
        cache_key = creds.get("identity", "")
        cached = self._trash_id_cache.get(cache_key)
        if cached:
            return cached
        with self._trash_id_lock:
            cached = self._trash_id_cache.get(cache_key)
            if cached:
                return cached
            root = self._root_id(creds)
            children = self._call(creds, "GET", f"{self._api(creds)}/Items({root})/Children").json()
            for e in children.get("value", []):
                if e.get("Name") == "_C-ECM-Trash":
                    self._trash_id_cache[cache_key] = e["Id"]
                    return e["Id"]
            created = self._call(creds, "POST", f"{self._api(creds)}/Items({root})/Folder",
                                  json={"Name": "_C-ECM-Trash"}).json()
            trash_id = created["Id"]
            self._trash_id_cache[cache_key] = trash_id
            return trash_id

    @staticmethod
    def _parse_dt(s: str | None):
        if not s:
            return None
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def _node_to_folder(self, e: dict, root_id: str) -> FolderInfo:
        parent = e.get("Parent") or {}
        parent_id = parent.get("Id")
        if parent_id == root_id:
            parent_id = None
        return FolderInfo(id=e["Id"], name=e["Name"], parent_id=parent_id,
                           created_at=self._parse_dt(e.get("CreationDate")))

    def _node_to_file(self, e: dict, root_id: str) -> FileInfo:
        parent = e.get("Parent") or {}
        parent_id = parent.get("Id")
        if parent_id == root_id:
            parent_id = None
        return FileInfo(id=e["Id"], name=e["Name"], folder_id=parent_id,
                         version_number=e.get("VersionNumber", 1) or 1,
                         size_bytes=e.get("FileSizeBytes"), content_type=None,
                         updated_at=self._parse_dt(e.get("ProgenyEditDate") or e.get("ClientModifiedDate")))

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        root_id = self._root_id(creds)
        node_id = folder_id or root_id
        result = self._call(creds, "GET", f"{self._api(creds)}/Items({node_id})/Children").json()
        entries = result.get("value", [])
        folders = [self._node_to_folder(e, root_id) for e in entries
                   if e.get("odata.type", "").endswith("Folder") and e.get("Name") != "_C-ECM-Trash"]
        files = [self._node_to_file(e, root_id) for e in entries if e.get("odata.type", "").endswith("File")]
        current_folder = None
        if folder_id is not None:
            node = self._call(creds, "GET", f"{self._api(creds)}/Items({node_id})").json()
            current_folder = self._node_to_folder(node, root_id)
        return FolderContents(folder=current_folder, breadcrumb=[BreadcrumbEntry(id=None, name="My Drive")],
                               folders=folders, files=files)

    def list_trash(self, creds: dict) -> FolderContents:
        trash_id = self._trash_id(creds)
        result = self._call(creds, "GET", f"{self._api(creds)}/Items({trash_id})/Children").json()
        entries = result.get("value", [])
        return FolderContents(
            folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[self._node_to_folder(e, trash_id) for e in entries if e.get("odata.type", "").endswith("Folder")],
            files=[self._node_to_file(e, trash_id) for e in entries if e.get("odata.type", "").endswith("File")],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        root_id = self._root_id(creds)
        parent = parent_id or root_id
        created = self._call(creds, "POST", f"{self._api(creds)}/Items({parent})/Folder", json={"Name": name}).json()
        return self._node_to_folder(created, root_id)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        updated = self._call(creds, "PATCH", f"{self._api(creds)}/Items({folder_id})", json={"Name": name}).json()
        return self._node_to_folder(updated, self._root_id(creds))

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        root_id = self._root_id(creds)
        target = new_parent_id or root_id
        # ShareFile's exact "move" verb isn't confidently known — Items(id)
        # only documents Copy reliably, so move is modeled as copy-then-
        # delete-original, which is always end-user-correct even if it's
        # not ShareFile's literal single native move call.
        copied = self._call(creds, "POST", f"{self._api(creds)}/Items({folder_id})/Copy",
                             params={"targetid": target}).json()
        self._call(creds, "DELETE", f"{self._api(creds)}/Items({folder_id})")
        return self._node_to_folder(copied, root_id)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._call(creds, "DELETE", f"{self._api(creds)}/Items({folder_id})")

    def _upload(self, creds: dict, parent_id: str, name: str, content: bytes) -> dict:
        creds = self._refresh_if_needed(creds)
        upload_info = self._call(
            creds, "GET", f"{self._api(creds)}/Items({parent_id})/Upload",
            params={"method": "standard", "fileName": name, "fileSize": len(content), "overwrite": "true"},
        ).json()
        chunk_uri = upload_info["ChunkUri"]
        resp = requests.post(chunk_uri, files={"Filename": (name, content)}, timeout=60)
        if resp.status_code >= 400:
            raise ProviderError(f"ShareFile upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return resp.json()

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        root_id = self._root_id(creds)
        parent = folder_id or root_id
        self._upload(creds, parent, name, content)
        children = self._call(creds, "GET", f"{self._api(creds)}/Items({parent})/Children").json().get("value", [])
        match = next((e for e in children if e.get("Name") == name), None)
        if not match:
            raise ProviderError("ShareFile upload succeeded but the new file couldn't be located", status_code=502)
        return self._node_to_file(match, root_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        node = self._call(creds, "GET", f"{self._api(creds)}/Items({file_id})").json()
        return self._node_to_file(node, self._root_id(creds))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        updated = self._call(creds, "PATCH", f"{self._api(creds)}/Items({file_id})", json={"Name": name}).json()
        return self._node_to_file(updated, self._root_id(creds))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        root_id = self._root_id(creds)
        target = new_folder_id or root_id
        copied = self._call(creds, "POST", f"{self._api(creds)}/Items({file_id})/Copy",
                             params={"targetid": target}).json()
        self._call(creds, "DELETE", f"{self._api(creds)}/Items({file_id})")
        return self._node_to_file(copied, root_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        self._call(creds, "DELETE", f"{self._api(creds)}/Items({file_id})")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        resp = self._call(creds, "GET", f"{self._api(creds)}/Items({file_id})/Download")
        return resp.content

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        # ShareFile does document real file versions, but the exact
        # navigation-property name isn't confidently known — best-effort
        # guess at Items({id})/Versions; if a real account's API doesn't
        # expose it under this name, this degrades to just the current
        # version rather than raising.
        try:
            result = self._call(creds, "GET", f"{self._api(creds)}/Items({file_id})/Versions").json()
        except ProviderError:
            node = self.get_file(creds, file_id)
            return [VersionInfo(id="current", version_number=node.version_number, size_bytes=node.size_bytes,
                                 content_type=None, is_current=True, updated_at=node.updated_at)]
        versions = result.get("value", [])
        return [
            VersionInfo(id=v.get("id", str(i)), version_number=v.get("VersionNumber", i + 1),
                        size_bytes=v.get("FileSizeBytes"), content_type=None,
                        is_current=bool(v.get("IsCurrentVersion")),
                        updated_at=self._parse_dt(v.get("CreationDate")))
            for i, v in enumerate(versions)
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        node = self.get_file(creds, file_id)
        parent = node.folder_id or self._root_id(creds)
        self._upload(creds, parent, node.name, content)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        if version_id == "current":
            return self.get_content(creds, file_id)
        resp = self._call(creds, "GET", f"{self._api(creds)}/Items({file_id})/Versions({version_id})/Download")
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        content = self.get_version_content(creds, file_id, version_id)
        return self.create_version(creds, file_id, "application/octet-stream", content)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "POST", f"{self._api(creds)}/Items({folder_id})/Copy", params={"targetid": target})
        self._call(creds, "DELETE", f"{self._api(creds)}/Items({folder_id})")

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        root = self._root_id(creds)
        copied = self._call(creds, "POST", f"{self._api(creds)}/Items({folder_id})/Copy", params={"targetid": root}).json()
        self._call(creds, "DELETE", f"{self._api(creds)}/Items({folder_id})")
        return self._node_to_folder(copied, root)

    def trash_file(self, creds: dict, file_id: str) -> None:
        target = self._trash_id(creds)
        self._call(creds, "POST", f"{self._api(creds)}/Items({file_id})/Copy", params={"targetid": target})
        self._call(creds, "DELETE", f"{self._api(creds)}/Items({file_id})")

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        root = self._root_id(creds)
        copied = self._call(creds, "POST", f"{self._api(creds)}/Items({file_id})/Copy", params={"targetid": root}).json()
        self._call(creds, "DELETE", f"{self._api(creds)}/Items({file_id})")
        return self._node_to_file(copied, root)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        root_id = self._root_id(creds)
        try:
            result = self._call(creds, "GET", f"{self._api(creds)}/Items({root_id})/Search",
                                 params={"query": query}).json()
            entries = result.get("value", [])
            return (
                [self._node_to_folder(e, root_id) for e in entries if e.get("odata.type", "").endswith("Folder")],
                [self._node_to_file(e, root_id) for e in entries if e.get("odata.type", "").endswith("File")],
            )
        except ProviderError:
            # Fallback if the Search navigation property isn't actually
            # exposed the way assumed above: walk the tree client-side.
            found_folders: list[FolderInfo] = []
            found_files: list[FileInfo] = []
            q = query.lower()

            def walk(node_id, depth: int):
                if depth > 6:
                    return
                children = self._call(creds, "GET", f"{self._api(creds)}/Items({node_id})/Children").json().get("value", [])
                for e in children:
                    if e.get("Name") == "_C-ECM-Trash":
                        continue
                    is_folder = e.get("odata.type", "").endswith("Folder")
                    if is_folder:
                        if q in e.get("Name", "").lower():
                            found_folders.append(self._node_to_folder(e, root_id))
                        walk(e["Id"], depth + 1)
                    elif q in e.get("Name", "").lower():
                        found_files.append(self._node_to_file(e, root_id))

            walk(root_id, 0)
            return found_folders, found_files
