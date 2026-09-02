"""Atlassian Confluence Cloud storage-provider adapter.

UNVERIFIED — written from Atlassian's publicly documented Confluence Cloud
REST API v2 (https://developer.atlassian.com/cloud/confluence/rest/v2/) and
the standard Atlassian Cloud OAuth 2.0 (3LO) flow
(https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/), but has
never been exercised against a real Confluence Cloud site. Set
`confluence_client_id` / `confluence_client_secret` via Admin Settings (an
OAuth app registered at https://developer.atlassian.com/console/myapps/, with
a callback URL matching this app's OAuth redirect) and run the actual consent
flow before trusting this the way FileNet's or local disk's providers are
trusted.

FUNDAMENTAL SHOEHORNING — READ BEFORE USING: Confluence is a wiki, not a file
drive. It has no folder tree at all, so this adapter manufactures one out of
concepts that only loosely resemble folders/files:

  - A Confluence **space** becomes a top-level "folder" (one level, directly
    under the root — there is no nesting of spaces).
  - A space's **top-level pages** become subfolders-of-a-sort one level below
    their space, purely so their attachments are reachable through the
    folder browser. Pages are NOT modeled as files, sub-pages are NOT
    modeled as nested folders, and page *content* (the actual wiki text) is
    never exposed by this provider at all — only navigation down to a
    page's attachments.
  - A page's **attachments** are the only thing this provider exposes as a
    "file", because attachments are the closest thing Confluence has to
    real, independently-addressable file storage.

Because of this, `create_folder`/`rename_folder`/`move_folder`/
`delete_folder`/`trash_folder`/`restore_folder` all deliberately raise
`ProviderError` rather than fabricate space/page management this app doesn't
actually offer — spaces and pages are things you manage in Confluence itself,
not through a generic file-drive folder CRUD. Likewise, moving an attachment
to a different page isn't a documented operation, so `move_file` also raises.

Opaque ids this provider hands out are prefixed by kind so `get_children` can
tell them apart: "space:{spaceId}", "page:{pageId}", "att:{attachmentId}".

BIGGEST UNCERTAINTIES (flagged explicitly, not silently assumed correct):

  1. Updating an EXISTING attachment's content (a new version) uses
     `POST .../pages/{pageId}/child/attachment/{attachmentId}/data`
     (multipart). This is Confluence Cloud's known legacy attachment-data-
     update shape carried into a v2-style path — it is a best-effort
     construction, not something confirmed against a live tenant, and it
     requires knowing the attachment's parent page id, which this code reads
     from the attachment metadata's `pageId` field — a field this adapter
     assumes is present but which Atlassian's v2 docs don't unambiguously
     guarantee on every attachment payload. If either the path or that field
     turns out to be wrong, `create_version` and `restore_version` are the
     methods that will fail.
  2. Trash handling: Confluence Cloud does have a trash concept, but a
     confident, documented "list trashed attachments" / "restore a trashed
     attachment" API could not be identified. Rather than guess, `list_trash`
     always reports empty, `restore_file` raises a clear "not supported"
     error, and `trash_file` just issues the real attachment `DELETE` (which
     typically soft-deletes into the space's own trash in the Confluence UI,
     even though this adapter can't list or restore from there).
  3. Whether every entry returned by `GET .../attachments/{id}/versions`
     carries its own `_links.download` is likewise not confidently
     documented; `get_version_content` falls back to the attachment's
     current-version download link when a version entry doesn't have one of
     its own and it looks like the current version.
  4. `search` uses the older, still-supported CQL search endpoint
     (`GET .../wiki/rest/api/search?cql=...`) rather than v2, because v2 has
     no full-text content search of its own — the exact shape of a CQL
     search hit for an attachment (whether it's nested under a `content` key
     or flat, where the parent page id lives) is parsed defensively and may
     not match every tenant/version of Confluence Cloud.
  5. Only the FIRST site returned by `accessible-resources` is used. An
     Atlassian account connected to more than one Confluence Cloud site will
     only ever see the first one through this provider.
  6. List endpoints (`/spaces`, `/spaces/{id}/pages`, `/pages/{id}/
     attachments`, CQL search) are fetched a single page at a time
     (`limit=250`, no cursor-following via `_links.next`) — a space/page
     with more items than that will not show all of them.

Auth: standard Atlassian Cloud OAuth 2.0 (3LO) — `get_authorize_url` sends
the browser to `auth.atlassian.com`, `complete_oauth` exchanges the code for
tokens at the same host (JSON body, not form-encoded — this differs from
Box/Google/Microsoft's OAuth token endpoints elsewhere in this package),
then resolves the tenant's `cloud_id` via
`GET https://api.atlassian.com/oauth/token/accessible-resources` since every
subsequent Confluence REST call is proxied through
`https://api.atlassian.com/ex/confluence/{cloud_id}/...` rather than a
tenant's own `*.atlassian.net` domain. `creds` holds {"access_token",
"refresh_token", "expires_at", "cloud_id", "identity"}, refreshed
transparently via `refresh_if_needed()`.
"""

import time

import requests

from .. import settings_store
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


class ConfluenceProvider(StorageProvider):
    key = "confluence"
    display_name = "Confluence"
    auth_mode = AuthMode.OAUTH

    _AUTH_BASE = "https://auth.atlassian.com"
    _API_BASE = "https://api.atlassian.com"

    _FOLDER_UNSUPPORTED = (
        "Confluence spaces and pages can't be managed as folders from here — "
        "only browsing existing spaces/pages and their attachments is supported"
    )

    # --- app-level OAuth client -------------------------------------------

    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("confluence_client_id", ""),
            settings_store.get_setting("confluence_client_secret", ""),
        )

    @property
    def configured(self) -> bool:
        client_id, client_secret = self._client()
        return bool(client_id and client_secret)

    # --- oauth --------------------------------------------------------------

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id, _secret = self._client()
        params = {
            "audience": "api.atlassian.com",
            "client_id": client_id,
            # Broad-ish but documented scopes: read/write content (which
            # covers attachments) plus offline_access for a refresh token.
            # Confluence Cloud's granular scope catalogue is large and not
            # exhaustively verified here — if a specific call starts
            # rejecting with an insufficient-scope error, this is the first
            # place to widen.
            "scope": "read:confluence-content.all write:confluence-content offline_access",
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
        return f"{self._AUTH_BASE}/authorize?" + requests.compat.urlencode(params)

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        client_id, client_secret = self._client()
        resp = requests.post(
            f"{self._AUTH_BASE}/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Confluence token exchange failed: {resp.text[:300]}", status_code=502)
        tok = resp.json()
        creds = {
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + tok.get("expires_in", 3600),
        }
        resources = self._get(creds, f"{self._API_BASE}/oauth/token/accessible-resources")
        if not resources:
            raise ProviderError("This Atlassian account has no accessible Confluence sites", status_code=400)
        # Only the first accessible site is used — see module docstring
        # uncertainty #5.
        site = resources[0]
        creds["cloud_id"] = site["id"]
        creds["identity"] = site.get("name") or site.get("url") or "Confluence site"
        return creds

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        if creds.get("expires_at", 0) > time.time() + 30:
            return creds, False
        if not creds.get("refresh_token"):
            raise ProviderError("Session expired — please reconnect", status_code=401)
        client_id, client_secret = self._client()
        resp = requests.post(
            f"{self._AUTH_BASE}/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": creds["refresh_token"],
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            raise ProviderError("Session expired — please reconnect", status_code=401)
        tok = resp.json()
        creds["access_token"] = tok["access_token"]
        creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
        creds["expires_at"] = time.time() + tok.get("expires_in", 3600)
        return creds, True

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "Confluence account")

    # --- low-level HTTP helpers ---------------------------------------------

    def _get(self, creds: dict, url: str, **kw):
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {creds['access_token']}", "Accept": "application/json"},
            timeout=30,
            **kw,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Confluence error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp.json() if resp.content else {}

    def _call(self, creds: dict, method: str, url: str, **kw) -> requests.Response:
        creds, _ = self.refresh_if_needed(creds)
        resp = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {creds['access_token']}", "Accept": "application/json"},
            timeout=30,
            **kw,
        )
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"Confluence error {resp.status_code}: {resp.text[:300]}", status_code=502)
        return resp

    def _api_v2(self, creds: dict) -> str:
        return f"{self._API_BASE}/ex/confluence/{creds['cloud_id']}/wiki/api/v2"

    def _api_v1(self, creds: dict) -> str:
        return f"{self._API_BASE}/ex/confluence/{creds['cloud_id']}/wiki/rest/api"

    # --- id scheme -----------------------------------------------------------

    @staticmethod
    def _split_id(opaque_id: str) -> tuple[str, str]:
        if ":" not in opaque_id:
            raise ProviderError(f"'{opaque_id}' is not a recognized Confluence id", status_code=400)
        kind, _, raw = opaque_id.partition(":")
        return kind, raw

    @staticmethod
    def _parse_dt(s: str | None):
        if not s:
            return None
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    # --- converters ------------------------------------------------------------

    def _space_to_folder(self, s: dict) -> FolderInfo:
        return FolderInfo(id=f"space:{s.get('id')}", name=s.get("name") or s.get("key", ""), parent_id=None, created_at=None)

    def _att_to_file(self, meta: dict, folder_id: str | None = None) -> FileInfo:
        if folder_id is None:
            page_id = meta.get("pageId")
            folder_id = f"page:{page_id}" if page_id else None
        version = meta.get("version") or {}
        return FileInfo(
            id=f"att:{meta.get('id')}",
            name=meta.get("title", ""),
            folder_id=folder_id,
            version_number=version.get("number", 1),
            size_bytes=meta.get("fileSize"),
            content_type=meta.get("mediaType"),
            updated_at=self._parse_dt(version.get("createdAt")),
        )

    def _get_space(self, creds: dict, space_id: str) -> dict:
        return self._call(creds, "GET", f"{self._api_v2(creds)}/spaces/{space_id}").json()

    def _get_page(self, creds: dict, page_id: str) -> dict:
        return self._call(creds, "GET", f"{self._api_v2(creds)}/pages/{page_id}").json()

    def _get_attachment(self, creds: dict, att_id: str) -> dict:
        return self._call(creds, "GET", f"{self._api_v2(creds)}/attachments/{att_id}").json()

    # --- folder browsing (spaces -> top-level pages -> attachments) --------

    def _list_spaces(self, creds: dict) -> FolderContents:
        data = self._call(creds, "GET", f"{self._api_v2(creds)}/spaces", params={"limit": 250}).json()
        spaces = data.get("results", [])
        return FolderContents(
            folder=None,
            breadcrumb=[BreadcrumbEntry(id=None, name="Confluence")],
            folders=[self._space_to_folder(s) for s in spaces],
            files=[],
        )

    def _list_space_pages(self, creds: dict, space_id: str) -> FolderContents:
        space = self._get_space(creds, space_id)
        space_name = space.get("name") or space.get("key", "")
        space_folder_id = f"space:{space_id}"
        data = self._call(
            creds, "GET", f"{self._api_v2(creds)}/spaces/{space_id}/pages", params={"limit": 250}
        ).json()
        pages = data.get("results", [])
        top_pages = [p for p in pages if not p.get("parentId")]
        folders = [
            FolderInfo(id=f"page:{p.get('id')}", name=p.get("title", ""), parent_id=space_folder_id, created_at=None)
            for p in top_pages
        ]
        current_folder = FolderInfo(id=space_folder_id, name=space_name, parent_id=None, created_at=None)
        breadcrumb = [BreadcrumbEntry(id=None, name="Confluence"), BreadcrumbEntry(id=space_folder_id, name=space_name)]
        return FolderContents(folder=current_folder, breadcrumb=breadcrumb, folders=folders, files=[])

    def _list_page_attachments(self, creds: dict, page_id: str) -> FolderContents:
        page = self._get_page(creds, page_id)
        page_title = page.get("title", "")
        page_folder_id = f"page:{page_id}"
        space_id = page.get("spaceId")
        space_folder_id = f"space:{space_id}" if space_id else None
        space_name = None
        if space_id:
            try:
                space_name = self._get_space(creds, space_id).get("name")
            except ProviderError:
                space_name = None
        data = self._call(
            creds, "GET", f"{self._api_v2(creds)}/pages/{page_id}/attachments", params={"limit": 250}
        ).json()
        attachments = data.get("results", [])
        files = [self._att_to_file(a, folder_id=page_folder_id) for a in attachments]
        current_folder = FolderInfo(id=page_folder_id, name=page_title, parent_id=space_folder_id, created_at=None)
        breadcrumb = [BreadcrumbEntry(id=None, name="Confluence")]
        if space_folder_id:
            breadcrumb.append(BreadcrumbEntry(id=space_folder_id, name=space_name or space_id))
        breadcrumb.append(BreadcrumbEntry(id=page_folder_id, name=page_title))
        return FolderContents(folder=current_folder, breadcrumb=breadcrumb, folders=[], files=files)

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        if folder_id is None:
            return self._list_spaces(creds)
        kind, raw_id = self._split_id(folder_id)
        if kind == "space":
            return self._list_space_pages(creds, raw_id)
        if kind == "page":
            return self._list_page_attachments(creds, raw_id)
        raise ProviderError(f"'{folder_id}' is not a recognized Confluence folder id", status_code=400)

    def list_trash(self, creds: dict) -> FolderContents:
        # See module docstring uncertainty #2: no confident "list trashed
        # attachments" API exists, so this always reports empty rather than
        # guessing at one.
        return FolderContents(folder=None, breadcrumb=[BreadcrumbEntry(id=None, name="Trash")], folders=[], files=[])

    # --- folder mutation: honestly unsupported ------------------------------

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        raise ProviderError(self._FOLDER_UNSUPPORTED, status_code=400)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        raise ProviderError(self._FOLDER_UNSUPPORTED, status_code=400)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        raise ProviderError(self._FOLDER_UNSUPPORTED, status_code=400)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        raise ProviderError(self._FOLDER_UNSUPPORTED, status_code=400)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        raise ProviderError(self._FOLDER_UNSUPPORTED, status_code=400)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        raise ProviderError(self._FOLDER_UNSUPPORTED, status_code=400)

    # --- files (attachments) -------------------------------------------------

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        if folder_id is None:
            raise ProviderError(
                "Attachments can only be uploaded to a Confluence page — open a space and a page first",
                status_code=400,
            )
        kind, page_id = self._split_id(folder_id)
        if kind != "page":
            raise ProviderError(
                "Attachments can only be uploaded to a page, not a space directly — open a page inside this space first",
                status_code=400,
            )
        creds, _ = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{self._api_v2(creds)}/pages/{page_id}/attachments",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            files={"file": (name, content, content_type)},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Confluence attachment upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        data = resp.json() if resp.content else {}
        results = data.get("results") if isinstance(data, dict) else None
        created = results[0] if results else data
        return self._att_to_file(created, folder_id=folder_id)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        kind, att_id = self._split_id(file_id)
        if kind != "att":
            raise ProviderError(f"'{file_id}' is not an attachment id", status_code=400)
        return self._att_to_file(self._get_attachment(creds, att_id))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        kind, att_id = self._split_id(file_id)
        if kind != "att":
            raise ProviderError(f"'{file_id}' is not an attachment id", status_code=400)
        meta = self._get_attachment(creds, att_id)
        page_id = meta.get("pageId")
        next_version = (meta.get("version") or {}).get("number", 1) + 1
        updated = self._call(
            creds,
            "PUT",
            f"{self._api_v2(creds)}/attachments/{att_id}",
            json={
                "id": att_id,
                "status": meta.get("status", "current"),
                "title": name,
                "version": {"number": next_version, "message": "Renamed via C-ECM"},
            },
        ).json()
        return self._att_to_file(updated, folder_id=(f"page:{page_id}" if page_id else None))

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        raise ProviderError(
            "Confluence attachments belong to the page they were uploaded to and can't be moved to a "
            "different page via the documented API",
            status_code=400,
        )

    def delete_file(self, creds: dict, file_id: str) -> None:
        kind, att_id = self._split_id(file_id)
        if kind != "att":
            raise ProviderError(f"'{file_id}' is not an attachment id", status_code=400)
        self._call(creds, "DELETE", f"{self._api_v2(creds)}/attachments/{att_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        kind, att_id = self._split_id(file_id)
        if kind != "att":
            raise ProviderError(f"'{file_id}' is not an attachment id", status_code=400)
        meta = self._get_attachment(creds, att_id)
        download_path = (meta.get("_links") or {}).get("download")
        if not download_path:
            raise ProviderError("This attachment doesn't expose a download link", status_code=502)
        creds, _ = self.refresh_if_needed(creds)
        resp = requests.get(
            f"{self._API_BASE}{download_path}",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Confluence download failed ({resp.status_code})", status_code=502)
        return resp.content

    # --- versions --------------------------------------------------------------

    def _find_version_entry(self, creds: dict, att_id: str, version_id: str) -> dict:
        data = self._call(
            creds, "GET", f"{self._api_v2(creds)}/attachments/{att_id}/versions", params={"limit": 250}
        ).json()
        for e in data.get("results", []):
            if str(e.get("number")) == str(version_id):
                return e
        raise ProviderError(f"Version '{version_id}' not found", status_code=404)

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        kind, att_id = self._split_id(file_id)
        if kind != "att":
            raise ProviderError(f"'{file_id}' is not an attachment id", status_code=400)
        meta = self._get_attachment(creds, att_id)
        data = self._call(
            creds, "GET", f"{self._api_v2(creds)}/attachments/{att_id}/versions", params={"limit": 250}
        ).json()
        entries = data.get("results", [])
        if not entries:
            # Defensive fallback: an attachment always has at least its
            # current version, even if this listing endpoint returns
            # nothing for it in some tenant/edge case.
            v = meta.get("version") or {}
            entries = [{"number": v.get("number", 1), "createdAt": v.get("createdAt")}]
        max_number = max((e.get("number", 1) for e in entries), default=1)
        return [
            VersionInfo(
                id=str(e.get("number", 1)),
                version_number=e.get("number", 1),
                size_bytes=meta.get("fileSize"),
                content_type=meta.get("mediaType"),
                is_current=(e.get("number", 1) == max_number),
                updated_at=self._parse_dt(e.get("createdAt")),
            )
            for e in entries
        ]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        kind, att_id = self._split_id(file_id)
        if kind != "att":
            raise ProviderError(f"'{file_id}' is not an attachment id", status_code=400)
        meta = self._get_attachment(creds, att_id)
        page_id = meta.get("pageId")
        if not page_id:
            # See module docstring uncertainty #1.
            raise ProviderError(
                "Couldn't determine the parent page for this attachment, so a new version can't be "
                "uploaded (Confluence's attachment metadata isn't confidently documented to always carry "
                "pageId)",
                status_code=501,
            )
        creds, _ = self.refresh_if_needed(creds)
        resp = requests.post(
            f"{self._api_v2(creds)}/pages/{page_id}/child/attachment/{att_id}/data",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            files={"file": (meta.get("title", "file"), content, content_type)},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Confluence attachment version upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502
            )
        data = resp.json() if resp.content else {}
        results = data.get("results") if isinstance(data, dict) else None
        updated = results[0] if results else data
        return self._att_to_file(updated, folder_id=f"page:{page_id}")

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        kind, att_id = self._split_id(file_id)
        if kind != "att":
            raise ProviderError(f"'{file_id}' is not an attachment id", status_code=400)
        entry = self._find_version_entry(creds, att_id, version_id)
        download_path = (entry.get("_links") or {}).get("download")
        if not download_path:
            # See module docstring uncertainty #3.
            meta = self._get_attachment(creds, att_id)
            if str((meta.get("version") or {}).get("number")) == str(version_id):
                download_path = (meta.get("_links") or {}).get("download")
        if not download_path:
            raise ProviderError("This attachment version doesn't expose a download link", status_code=502)
        creds, _ = self.refresh_if_needed(creds)
        resp = requests.get(
            f"{self._API_BASE}{download_path}",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Confluence version download failed ({resp.status_code})", status_code=502)
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        old_bytes = self.get_version_content(creds, file_id, version_id)
        current = self.get_file(creds, file_id)
        return self.create_version(creds, file_id, current.content_type or "application/octet-stream", old_bytes)

    # --- trash (attachments only — see list_trash / module docstring #2) ---

    def trash_file(self, creds: dict, file_id: str) -> None:
        self.delete_file(creds, file_id)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        raise ProviderError(
            "Restoring a deleted Confluence attachment isn't supported — Confluence Cloud's trash-restore "
            "API for attachments isn't confidently documented",
            status_code=501,
        )

    # --- search ------------------------------------------------------------------

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        # Legacy-but-supported CQL search (v2 has no full-text search of its
        # own) — see module docstring uncertainty #4 for the response-shape
        # caveats this parses defensively around.
        escaped = query.replace("\\", "\\\\").replace('"', '\\"')
        cql = f'text ~ "{escaped}" and type = attachment'
        data = self._call(creds, "GET", f"{self._api_v1(creds)}/search", params={"cql": cql, "limit": 100}).json()
        results = data.get("results", [])
        files: list[FileInfo] = []
        for r in results:
            content = r.get("content") or r
            if content.get("type") != "attachment":
                continue
            container = content.get("container") or {}
            page_id = container.get("id")
            version = content.get("version") or {}
            extensions = content.get("extensions") or {}
            history = content.get("history") or {}
            last_updated = history.get("lastUpdated") or {}
            files.append(
                FileInfo(
                    id=f"att:{content.get('id')}",
                    name=content.get("title", r.get("title", "")),
                    folder_id=f"page:{page_id}" if page_id else None,
                    version_number=version.get("number", 1),
                    size_bytes=extensions.get("fileSize"),
                    content_type=extensions.get("mediaType"),
                    updated_at=self._parse_dt(last_updated.get("when")),
                )
            )
        # CQL search here is restricted to type=attachment, so there are
        # never any folder (space) hits to report.
        return [], files
