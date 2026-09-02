"""Hyland OnBase provider, via OnBase's REST API / "Core API"
(commonly mounted under something like `http://<host>/AppNet/Core`).

======================================================================
CONFIDENCE WARNING — READ BEFORE USE
======================================================================
This adapter is UNVERIFIED (no live OnBase server was available in this
environment to test against) AND it is written against a system whose
programmatic surface is genuinely less standardized and less publicly
documented than the other providers in this codebase. For most of
C-ECM's backends (Box, Alfresco, Nuxeo, ...) there's a stable, published
REST/OpenAPI contract to check field names and status codes against.
OnBase does not have an equivalent public reference available here:
historically its primary integration surface was a .NET-only "Unity
API" with no REST equivalent, and while Hyland has since published a
REST/"Core API", this file's endpoint paths, request/response shapes,
and auth parameters are a best-effort reconstruction from general
knowledge of OnBase's architecture (Document Types, keyword values,
document revisions, no native subfolder hierarchy) — NOT copied from a
verified API reference, Swagger/OpenAPI document, or working install.

Treat every path and payload shape below as a labeled guess, not a
citation. Specific things that are lower-confidence than the rest of
this file (and lower-confidence than anything in the other providers):

  * The token endpoint path (`/connect/token`), and especially the
    `client_id`/`scope` values used in the password-grant request —
    these are almost certainly registered per-deployment in a real
    OnBase install and the placeholders here will likely need to be
    swapped for whatever that deployment's OnBase admin configured.
  * Whether the data source name is actually accepted as a `data_source`
    form field on the token request (vs. a header, vs. something
    negotiated a different way entirely) — OnBase's own client model
    ties a login to one configured data source, so *something* like
    this is required, but the literal wire shape is a guess.
  * The document listing/search endpoint path (guessed as
    `GET {base_url}/od/documents`) and its response envelope shape
    (guessed as either a bare list or a dict with an `items` key).
  * Whether the listing endpoint actually returns each document's
    keyword values inline (assumed here, to avoid an N+1 keyword fetch
    per document) — a real Core API may require a separate per-document
    `/keywords` call instead.
  * The verb/path/payload for updating a single keyword value (guessed
    as `PUT {base_url}/od/documents/{id}/keywords` with a
    `{"keywordTypeName": ..., "value": ...}` body) — used here to model
    both "rename" (a title keyword) and "trash" (a status keyword),
    since neither has a more specific endpoint this file is confident
    about.
  * The revisions endpoints (list/create/download by revision number)
    are a reasonable guess given OnBase's documented revision concept,
    but the exact path segments and JSON field names are not verified.
  * There is no confidently-known "revert to revision N" endpoint, so
    `restore_version` is emulated the safe way: download the old
    revision's bytes, then upload them as a brand new current revision.
  * This provider does NOT implement OAuth-style token refresh (per
    `StorageProvider.refresh_if_needed`'s contract, CREDENTIALS-mode
    providers aren't expected to). If a real OnBase deployment's
    password-grant tokens expire quickly, a connection may need to be
    re-authenticated more often than other CREDENTIALS providers here —
    that's a known, undealt-with limitation, not an oversight.

Before ANY production use, this file should be checked line-by-line
against a real OnBase Core API Swagger/OpenAPI definition (or a live
sandbox), more so than any other provider in this codebase — the other
providers are "unverified against a live server but written against a
solid published spec"; this one is "unverified against a live server
*and* written against an uncertain spec."

======================================================================
HONEST DESIGN LIMITATION — OnBase HAS NO FOLDER HIERARCHY
======================================================================
Unlike every other provider here, OnBase does not organize content into
a nested folder tree at all. Content is filed as "Documents" belonging
to a configured "Document Type", found via keyword values and searches
— there is nothing folder-shaped underneath. Rather than fake a folder
hierarchy OnBase doesn't have, this adapter is deliberately honest about
the gap:

  * `get_children(creds, folder_id=None)` lists every visible document
    in one flat pseudo-folder.
  * `get_children(creds, folder_id=<anything else>)` raises
    `ProviderError` — there is no "descending into" anything.
  * `create_folder` / `rename_folder` / `move_folder` / `delete_folder`
    / `trash_folder` / `restore_folder` all raise `ProviderError`
    explaining OnBase has no folder concept, rather than silently
    pretending to support something that isn't there.

Because OnBase documents don't carry a simple filename either (their
identity is normally a Document Type + an internal id, with any
human-readable "name" being just another keyword value), a "Document
Title" keyword is used consistently as the stand-in for `FileInfo.name`
across create/rename/get. Likewise, since this file isn't confident
about a dedicated "list documents pending real deletion" endpoint,
trash is emulated the same way: a "C-ECM Status" keyword is set to
"Trashed"/"Active" rather than calling OnBase's own delete, so trash
and restore stay reversible from C-ECM's side regardless of what a real
deployment's delete endpoint actually does under the hood. The
`delete_file` abstract method (permanent delete, distinct from trash)
does call OnBase's own `DELETE` endpoint directly, since that is the
genuinely destructive operation C-ECM expects it to be.
"""

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

# --- auth placeholders (LOW CONFIDENCE — see module docstring) ---
_CLIENT_ID = "onbase-rest-api"
_SCOPE = "obrestapi"

# --- keyword-based stand-ins for concepts OnBase doesn't model natively ---
_DEFAULT_DOCUMENT_TYPE = "C-ECM Document"
_TITLE_KEYWORD = "Document Title"
_STATUS_KEYWORD = "C-ECM Status"
_TRASHED_VALUE = "Trashed"
_ACTIVE_VALUE = "Active"


class OnBaseProvider(StorageProvider):
    key = "onbase"
    display_name = "Hyland OnBase"
    auth_mode = AuthMode.CREDENTIALS

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("base_url", "Server URL", "http://localhost/AppNet/Core"),
            ConfigField("data_source", "Data Source"),
            ConfigField("document_type", "Document Type", _DEFAULT_DOCUMENT_TYPE, required=False),
        ]

    # --- plumbing ---

    def _base_url(self, creds: dict) -> str:
        return creds["base_url"].rstrip("/")

    def _headers(self, creds: dict) -> dict:
        return {"Authorization": f"Bearer {creds['access_token']}"}

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> dict:
        url = self._base_url(creds) + path
        try:
            resp = requests.request(method, url, headers=self._headers(creds), timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the OnBase server: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("OnBase session expired or invalid", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"OnBase error {resp.status_code}: {resp.text[:300]}", status_code=502)
        if resp.status_code == 204 or not resp.content:
            return {}
        if resp.headers.get("Content-Type", "").startswith("application/json"):
            try:
                return resp.json()
            except ValueError:
                return {}
        return {}

    @staticmethod
    def _extract_items(result) -> list:
        """The document-listing endpoint's exact response envelope isn't
        confidently known — accept either a bare JSON array or a dict
        wrapping it under a plausible key."""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("items", "documents", "results", "value"):
                if key in result and isinstance(result[key], list):
                    return result[key]
        return []

    @staticmethod
    def _get_keyword(doc: dict, keyword_type: str, default=None):
        for kw in doc.get("keywords") or []:
            if kw.get("keywordTypeName") == keyword_type:
                return kw.get("value")
        return default

    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_dt(value: str | None):
        if not value:
            return None
        import datetime as _dt
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _doc_to_file(self, doc: dict) -> FileInfo:
        doc_id = str(doc.get("id"))
        name = self._get_keyword(doc, _TITLE_KEYWORD) or doc.get("documentTypeName") or f"Document {doc_id}"
        version_number = self._safe_int(
            doc.get("latestRevisionNumber") or doc.get("revisionNumber"), 1
        )
        return FileInfo(
            id=doc_id,
            name=name,
            folder_id=None,
            version_number=version_number,
            size_bytes=doc.get("fileSize"),
            content_type=doc.get("mimeType"),
            updated_at=self._parse_dt(doc.get("dateStored") or doc.get("modifiedDate")),
        )

    def _list_documents(self, creds: dict, trashed: bool, keyword_search: str | None = None) -> list[dict]:
        params = {"documentTypeName": creds.get("document_type") or _DEFAULT_DOCUMENT_TYPE}
        if keyword_search:
            params["keywordSearch"] = keyword_search
        result = self._request(creds, "GET", "/od/documents", params=params)
        out = []
        for doc in self._extract_items(result):
            status = self._get_keyword(doc, _STATUS_KEYWORD)
            if (status == _TRASHED_VALUE) == trashed:
                out.append(doc)
        return out

    def _set_keyword(self, creds: dict, file_id: str, keyword_type: str, value: str) -> None:
        # LOW CONFIDENCE (see module docstring): exact verb/path/payload
        # for updating one keyword value is a best-effort guess.
        self._request(
            creds, "PUT", f"/od/documents/{file_id}/keywords",
            json={"keywordTypeName": keyword_type, "value": value},
        )

    def _ensure_root(self, folder_id: str | None) -> None:
        if folder_id is not None:
            raise ProviderError(
                "OnBase doesn't support nested folders — only a single flat document list is available",
                status_code=400,
            )

    def _no_folders(self) -> None:
        raise ProviderError(
            "OnBase doesn't support folders — documents are filed by Document Type and keyword "
            "values, not a folder hierarchy",
            status_code=400,
        )

    # --- auth ---

    def _fetch_token(self, base_url: str, username: str, password: str, data_source: str) -> str | None:
        url = base_url.rstrip("/") + "/connect/token"
        data = {
            "grant_type": "password",
            "username": username,
            "password": password,
            "client_id": _CLIENT_ID,
            "scope": _SCOPE,
            # LOW CONFIDENCE: whether the data source belongs on this
            # request at all, and if so whether as a form field named
            # exactly this — see module docstring.
            "data_source": data_source,
        }
        try:
            resp = requests.post(url, data=data, timeout=30)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the OnBase server: {exc}", status_code=502)
        if resp.status_code in (400, 401):
            return None
        if resp.status_code >= 400:
            raise ProviderError(f"OnBase error {resp.status_code}: {resp.text[:300]}", status_code=502)
        try:
            body = resp.json()
        except ValueError:
            raise ProviderError("Unexpected response from the OnBase token endpoint", status_code=502)
        return body.get("access_token") or None

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        data_source = (config.get("data_source") or "").strip()
        document_type = (config.get("document_type") or "").strip() or _DEFAULT_DOCUMENT_TYPE
        if not base_url:
            raise ProviderError("Server URL is required", status_code=400)
        if not data_source:
            raise ProviderError("Data source is required", status_code=400)
        token = self._fetch_token(base_url, username, password, data_source)
        if not token:
            return None
        return {
            "base_url": base_url,
            "data_source": data_source,
            "document_type": document_type,
            "username": username,
            "access_token": token,
        }

    def whoami(self, creds: dict) -> str:
        return creds["username"]

    # --- folders (all raise except get_children(None)/list_trash — see module docstring) ---

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        self._ensure_root(folder_id)
        docs = self._list_documents(creds, trashed=False)
        return FolderContents(
            folder=None,
            breadcrumb=[BreadcrumbEntry(id=None, name="OnBase Documents")],
            folders=[],
            files=[self._doc_to_file(d) for d in docs],
        )

    def list_trash(self, creds: dict) -> FolderContents:
        docs = self._list_documents(creds, trashed=True)
        return FolderContents(
            folder=None,
            breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[],
            files=[self._doc_to_file(d) for d in docs],
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        self._no_folders()

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        self._no_folders()

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        self._no_folders()

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        self._no_folders()

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        self._no_folders()

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        self._no_folders()

    # --- files ---

    def create_document(
        self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes
    ) -> FileInfo:
        self._ensure_root(folder_id)
        url = self._base_url(creds) + "/od/documents"
        document_type = creds.get("document_type") or _DEFAULT_DOCUMENT_TYPE
        files = {"file": (name, content, content_type)}
        data = {
            "documentType": document_type,
            "docTypeName": document_type,
            f"keyword.{_TITLE_KEYWORD}": name,
        }
        try:
            resp = requests.post(url, headers=self._headers(creds), files=files, data=data, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the OnBase server: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("OnBase session expired or invalid", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError(f"OnBase upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        try:
            doc = resp.json()
        except ValueError:
            raise ProviderError("Unexpected response from the OnBase upload endpoint", status_code=502)
        # In case the create response doesn't echo keywords back (uncertain
        # either way), make sure the title we just set is still reflected.
        doc.setdefault("keywords", [{"keywordTypeName": _TITLE_KEYWORD, "value": name}])
        return self._doc_to_file(doc)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        doc = self._request(creds, "GET", f"/od/documents/{file_id}")
        return self._doc_to_file(doc)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        self._set_keyword(creds, file_id, _TITLE_KEYWORD, name)
        return self.get_file(creds, file_id)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        self._ensure_root(new_folder_id)
        # Only one flat location exists, so "moving" within it is a no-op.
        return self.get_file(creds, file_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        # Genuine permanent delete (distinct from trash_file below, which
        # only flips a keyword) — calls OnBase's own delete endpoint.
        self._request(creds, "DELETE", f"/od/documents/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        url = self._base_url(creds) + f"/od/documents/{file_id}/content"
        try:
            resp = requests.get(url, headers=self._headers(creds), timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the OnBase server: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("OnBase session expired or invalid", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError("Couldn't fetch content", status_code=404)
        return resp.content

    # --- versions (OnBase's native "revisions") ---

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        result = self._request(creds, "GET", f"/od/documents/{file_id}/revisions")
        items = self._extract_items(result)
        out = []
        for i, rev in enumerate(items):
            version_number = self._safe_int(rev.get("revisionNumber"), i + 1)
            out.append(VersionInfo(
                id=str(rev.get("revisionNumber", version_number)),
                version_number=version_number,
                size_bytes=rev.get("fileSize"),
                content_type=rev.get("mimeType"),
                is_current=bool(rev.get("isLatest", i == 0)),
                updated_at=self._parse_dt(rev.get("dateStored")),
            ))
        return out

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        url = self._base_url(creds) + f"/od/documents/{file_id}/revisions"
        files = {"file": ("revision", content, content_type)}
        try:
            resp = requests.post(url, headers=self._headers(creds), files=files, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the OnBase server: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("OnBase session expired or invalid", status_code=401)
        if resp.status_code >= 400:
            raise ProviderError(f"OnBase revision upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        url = self._base_url(creds) + f"/od/documents/{file_id}/revisions/{version_id}/content"
        try:
            resp = requests.get(url, headers=self._headers(creds), timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach the OnBase server: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError("Version content not found", status_code=404)
        return resp.content

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        # No confidently-known "revert to revision N" endpoint (see module
        # docstring) — the safe fallback is to pull the old revision's
        # bytes down and lay them back as a brand-new current revision.
        old_content = self.get_version_content(creds, file_id, version_id)
        current = self.get_file(creds, file_id)
        content_type = current.content_type or "application/octet-stream"
        return self.create_version(creds, file_id, content_type, old_content)

    # --- trash (emulated via a keyword flag — see module docstring) ---

    def trash_file(self, creds: dict, file_id: str) -> None:
        self._set_keyword(creds, file_id, _STATUS_KEYWORD, _TRASHED_VALUE)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        self._set_keyword(creds, file_id, _STATUS_KEYWORD, _ACTIVE_VALUE)
        return self.get_file(creds, file_id)

    # --- search ---

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        docs = self._list_documents(creds, trashed=False, keyword_search=query)
        # The server-side keywordSearch param's exact matching behavior
        # isn't confidently known (which keyword types it covers, whether
        # it's substring or exact) — apply an honest client-side substring
        # filter against the title keyword on top of it, per the task's
        # guidance to match against the display-title keyword specifically.
        needle = query.lower()
        matched = [d for d in docs if needle in (self._get_keyword(d, _TITLE_KEYWORD) or "").lower()]
        return [], [self._doc_to_file(d) for d in matched]
