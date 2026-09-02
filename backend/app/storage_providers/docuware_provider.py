"""DocuWare provider, via the DocuWare Platform REST API
(`{base_url}/DocuWare/Platform/...`, JSON, cookie/session-based login).

UNVERIFIED — there is no live DocuWare server in this environment to test
against. This was written from DocuWare Platform REST API's documented
conventions (the Logon flow, FileCabinets/Documents resource shapes, the
FileDownload endpoint) plus a fair amount of educated, disclosed guessing
where the exact contract isn't confidently known. Treat this file as a
starting point to verify against a real DocuWare Cloud or on-prem instance,
not as something already proven correct — genuinely more uncertain than
alfresco_provider.py/box's OAuth flow in this codebase.

Specific points of real uncertainty, flagged honestly rather than papered
over:

1. FOLDERS DON'T EXIST IN DOCUWARE THE WAY THIS APP MODELS THEM. DocuWare's
   organizing unit is the File Cabinet — a flat, searchable document store
   where documents are found via indexed metadata fields, not a nested
   folder tree. (DocuWare does have "Baskets"/"Trays", but those are a
   personal/temporary holding area, not a shared hierarchical folder
   structure suitable for this app.) So this provider deliberately exposes
   only a single flat "virtual folder" — the C-ECM app's whole notion of
   folder_id is always None here. get_children/create_folder/rename_folder/
   move_folder/delete_folder/trash_folder/restore_folder all reject any
   real folder_id with a clear ProviderError instead of pretending DocuWare
   has subfolders it doesn't. This is a deliberate, honest simplification of
   DocuWare's actual data model, not an oversight.

2. THE DEDICATED "C-ECM" FILE CABINET MUST ALREADY EXIST. DocuWare's REST
   API does not offer a confident, simple way to create a new File Cabinet
   (cabinet creation normally happens through DocuWare Administration and
   involves choosing a database, field schema, etc. — not something this
   provider should guess at). So on first use per connection this provider
   looks for an existing cabinet literally named "C-ECM" and raises a clear
   ProviderError telling the admin to create one first if it's missing,
   rather than silently failing or fabricating cabinet-creation semantics.

3. THE SESSION IS A COOKIE STRING, NOT A LIVE OBJECT. Logon
   (`POST /Account/Logon`, form-encoded Username/Password/Organization)
   establishes a session via Set-Cookie, but `creds` must stay a plain
   JSON-serializable dict (it's persisted as JSON elsewhere in this
   codebase) — so rather than keeping a stateful `requests.Session`, the
   cookies from the login response are flattened into a single
   `creds["cookie"]` string ("Name=Value; Name2=Value2") and sent back as a
   literal `Cookie:` header on every later call. Built from `requests`'
   parsed cookie jar (which correctly merges however many Set-Cookie
   headers came back, across any redirect chain) rather than manual header
   splitting — same intent as "join the Set-Cookie values", just via a
   more reliable mechanism.

4. THE "INFORMATIONAL NAME FIELD" IS A GUESS. DocuWare documents are
   identified by whatever indexed metadata fields the cabinet's admin
   configured — there's no guaranteed "file name" field. This provider
   looks for one of a handful of plausibly-named candidate fields
   (DOCUMENT_NAME, DOCUMENTNAME, DWDOCNAME, DOC_NAME, NAME, TITLE) to use
   as a display name, a rename target, and a trash tag; if a given
   document has none of those fields, its name falls back to
   "Document {Id}" and rename/trash raise a clear error rather than
   silently doing nothing. New documents uploaded by this provider are
   tagged under "DOCUMENT_NAME" by convention, since there's no way to
   know the admin's real schema in advance.

5. TRASH IS FULLY EMULATED, NOT DOCUWARE'S OWN RECYCLE BIN. DocuWare does
   have a real recycle-bin concept in many configurations, but the exact
   REST surface for listing/restoring it isn't confidently known here. So
   — same pattern this codebase uses elsewhere for backends with uncertain
   native trash APIs — trash_file prefixes the name field's value with
   "[TRASHED] " and list_trash/restore_file filter/strip that prefix,
   entirely inside this provider, never touching a real DocuWare trash.

6. VERSIONING IS SIMPLIFIED TO "ONE CURRENT VERSION". DocuWare's own
   multi-version/section semantics for a document aren't confidently known
   (POST .../Documents/{id}/Sections looks like the mechanism for
   replacing/appending page content, but whether repeated calls produce
   enumerable distinct historical versions via a documented GET endpoint is
   not something this provider is confident about). So, honestly rather
   than fabricating a fuller version history: list_versions always reports
   a single "current" version, create_version pushes new content via
   Sections and returns the (still single) current version, and
   get_version_content/restore_version are no-ops that just return the
   current content/metadata. This is the same simplified fallback already
   used elsewhere in this codebase (see ibmi_provider.py/ibmz_provider.py)
   for backends without confidently-known native version history.

7. SEARCH IS CLIENT-SIDE SUBSTRING FILTERING. DocuWare's real strength is
   structured metadata search (a dialog-expression query language), but its
   exact DSL isn't confidently known here, so search fetches the full
   document list (same as get_children) and filters in Python for
   documents whose display-name field contains the query substring — an
   always-correct, if unsophisticated, fallback rather than a guessed
   server-side query that might silently return wrong results.

8. FIELD NAMES FOR SIZE/CONTENT-TYPE/TIMESTAMPS ON THE RAW DOCUMENT OBJECT
   (FileSize, ContentType, and the DWSTOREDATETIME/DWMODDATETIME system
   field names checked for a last-modified timestamp) are best-effort
   based on typical DocuWare schema conventions, not independently
   confirmed field-by-field — code defensively falls back to None rather
   than raising when they're absent or shaped differently than expected.

9. THE MULTIPART "STORE DOCUMENT" REQUEST SHAPE (a "document" JSON part
   describing field values, alongside a "file" part with the raw bytes) is
   the documented general convention, not verified against a live server;
   the response envelope is assumed to (by analogy with FileCabinets'
   `{"FileCabinet": [...]}` shape) either be the created Document object
   directly or wrapped as `{"Document": ...}`, handled defensively either
   way.

Connection config fields:
  - base_url     : DocuWare server URL — either a DocuWare Cloud root
                    (e.g. "https://yourcompany.docuware.cloud") or an
                    on-prem URL that may already include the
                    "/DocuWare/Platform" suffix. Normalized in
                    `_platform_url` so either form works.
  - organization  : the DocuWare organization name (required at login;
                    DocuWare Cloud can host multiple organizations under
                    one server).
"""

import json
import threading
from datetime import datetime, timezone

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

_APP_CABINET_NAME = "C-ECM"
_TRASH_PREFIX = "[TRASHED] "
_PRIMARY_NAME_FIELD = "DOCUMENT_NAME"
_NAME_FIELD_CANDIDATES = ("DOCUMENT_NAME", "DOCUMENTNAME", "DWDOCNAME", "DOC_NAME", "NAME", "TITLE")
_DATE_FIELD_CANDIDATES = ("DWMODDATETIME", "DWSTOREDATETIME")

_NO_FOLDERS_MSG = (
    "DocuWare organizes documents by indexed metadata fields within a File "
    "Cabinet, not by folders — only a single flat document list is available."
)


def _parse_dw_datetime(value) -> datetime | None:
    """Best-effort parse of a DocuWare field's date/time value. DocuWare's
    JSON API has historically used the ASP.NET "/Date(1690000000000)/"
    epoch-millisecond form for some date fields; newer surfaces are more
    likely plain ISO-8601. Neither is independently confirmed here, so both
    are tried and anything unrecognized quietly returns None rather than
    raising — an updated_at we can't confidently parse just stays absent."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("/Date(") and text.endswith(")/"):
            digits = text[len("/Date("):-len(")/")].split("+")[0].split("-")[0]
            try:
                return datetime.fromtimestamp(int(digits) / 1000.0, tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class DocuWareProvider(StorageProvider):
    """DocuWare Platform REST API provider. See the module docstring above
    for the (substantial) list of genuinely-uncertain design points — this
    is written from documented conventions, not verified against a live
    DocuWare server."""

    key = "docuware"
    display_name = "DocuWare"
    auth_mode = AuthMode.CREDENTIALS

    def __init__(self):
        self._cabinet_id_cache: dict[tuple[str, str], str] = {}
        self._cabinet_id_lock = threading.Lock()

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField("base_url", "Server URL", "https://yourcompany.docuware.cloud"),
            ConfigField("organization", "Organization"),
        ]

    # --- low-level plumbing ---

    def _platform_url(self, creds: dict) -> str:
        base = creds["base_url"].rstrip("/")
        if base.lower().endswith("/docuware/platform"):
            return base
        return base + "/DocuWare/Platform"

    def _headers(self, creds: dict) -> dict:
        return {"Cookie": creds.get("cookie", ""), "Accept": "application/json"}

    def _request(self, creds: dict, method: str, path: str, **kwargs) -> dict:
        url = self._platform_url(creds) + path
        headers = {**self._headers(creds), **(kwargs.pop("headers", None) or {})}
        try:
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach DocuWare: {exc}", status_code=502)
        if resp.status_code == 401:
            raise ProviderError("DocuWare session expired or invalid — please reconnect", status_code=401)
        if resp.status_code == 404:
            raise ProviderError("Not found", status_code=404)
        if resp.status_code >= 400:
            raise ProviderError(f"DocuWare error {resp.status_code}: {resp.text[:300]}", status_code=502)
        if resp.content and resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {}

    # --- auth ---

    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        config = config or {}
        base_url = (config.get("base_url") or "").strip()
        organization = (config.get("organization") or "").strip()
        if not base_url:
            raise ProviderError("Server URL is required", status_code=400)
        if not organization:
            raise ProviderError("Organization is required", status_code=400)
        creds = {
            "username": username,
            "password": password,
            "base_url": base_url,
            "organization": organization,
            "cookie": "",
        }
        url = self._platform_url(creds) + "/Account/Logon"
        try:
            resp = requests.post(
                url,
                data={"Username": username, "Password": password, "Organization": organization},
                headers={"Accept": "application/json"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach DocuWare: {exc}", status_code=502)
        if resp.status_code in (401, 403):
            return None
        if resp.status_code >= 400:
            raise ProviderError(f"DocuWare login failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        cookie_jar: dict[str, str] = {}
        for hist_resp in (*resp.history, resp):
            for c in hist_resp.cookies:
                cookie_jar[c.name] = c.value
        if not cookie_jar:
            raise ProviderError("DocuWare login succeeded but returned no session cookie", status_code=502)
        creds["cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_jar.items())
        return creds

    def whoami(self, creds: dict) -> str:
        return f"{creds['username']}@{creds.get('organization', '')}"

    # --- cabinet resolution (cached per (platform url, organization),
    # thread-safe double-checked locking -- same pattern as
    # AlfrescoProvider._root_id, since this provider instance is a
    # process-wide singleton shared by every connection) ---

    def _cabinet_id(self, creds: dict) -> str:
        cache_key = (self._platform_url(creds), creds.get("organization", ""))
        cached = self._cabinet_id_cache.get(cache_key)
        if cached:
            return cached
        with self._cabinet_id_lock:
            cached = self._cabinet_id_cache.get(cache_key)
            if cached:
                return cached
            data = self._request(creds, "GET", "/FileCabinets")
            cabinets = data.get("FileCabinet", [])
            match = next(
                (c for c in cabinets if (c.get("Name") or "").strip().lower() == _APP_CABINET_NAME.lower()),
                None,
            )
            if not match:
                raise ProviderError(
                    f'No DocuWare File Cabinet named "{_APP_CABINET_NAME}" was found in organization '
                    f'"{creds.get("organization")}". DocuWare\'s REST API can\'t reliably create a new '
                    f'File Cabinet, so please create one named "{_APP_CABINET_NAME}" in DocuWare first '
                    "(DocuWare Administration -> File Cabinets -> New), then reconnect.",
                    status_code=400,
                )
            cabinet_id = str(match["Id"])
            self._cabinet_id_cache[cache_key] = cabinet_id
            return cabinet_id

    # --- field helpers (see module docstring point 4 on the uncertainty here) ---

    def _find_name_field(self, fields: list[dict]) -> str | None:
        by_upper = {(f.get("FieldName") or "").upper(): f.get("FieldName") for f in fields}
        for candidate in _NAME_FIELD_CANDIDATES:
            if candidate in by_upper:
                return by_upper[candidate]
        return None

    def _field_value(self, fields: list[dict], field_name: str):
        for f in fields:
            if (f.get("FieldName") or "").upper() == field_name.upper():
                return f.get("Item")
        return None

    def _doc_display_name(self, doc: dict) -> tuple[str, str | None]:
        """Returns (display_name, name_field_used). name_field_used is None
        when the document has none of the candidate informational fields —
        callers use that to know rename/trash-tagging can't be done."""
        fields = doc.get("Fields") or []
        field_name = self._find_name_field(fields)
        if field_name:
            value = self._field_value(fields, field_name)
            if isinstance(value, str) and value.strip():
                return value, field_name
        return f"Document {doc.get('Id')}", None

    def _doc_updated_at(self, fields: list[dict]) -> datetime | None:
        for candidate in _DATE_FIELD_CANDIDATES:
            value = self._field_value(fields, candidate)
            if value:
                parsed = _parse_dw_datetime(value)
                if parsed:
                    return parsed
        return None

    def _doc_to_file(self, doc: dict) -> FileInfo:
        name, _ = self._doc_display_name(doc)
        display = name[len(_TRASH_PREFIX):] if name.startswith(_TRASH_PREFIX) else name
        fields = doc.get("Fields") or []
        return FileInfo(
            id=str(doc.get("Id")),
            name=display,
            folder_id=None,
            version_number=1,
            size_bytes=doc.get("FileSize"),
            content_type=doc.get("ContentType"),
            updated_at=self._doc_updated_at(fields),
        )

    def _set_name_field(self, creds: dict, cabinet_id: str, doc: dict, new_value: str) -> None:
        fields = doc.get("Fields") or []
        field_name = self._find_name_field(fields) or _PRIMARY_NAME_FIELD
        body = {"Field": [{"FieldName": field_name, "ItemElementName": "String", "Item": new_value}]}
        self._request(creds, "PUT", f"/FileCabinets/{cabinet_id}/Documents/{doc.get('Id')}/Fields", json=body)

    def _list_documents(self, creds: dict) -> list[dict]:
        cabinet_id = self._cabinet_id(creds)
        data = self._request(creds, "GET", f"/FileCabinets/{cabinet_id}/Documents", params={"count": 5000})
        return data.get("Document", [])

    def _get_document(self, creds: dict, file_id: str) -> dict:
        cabinet_id = self._cabinet_id(creds)
        return self._request(creds, "GET", f"/FileCabinets/{cabinet_id}/Documents/{file_id}")

    # --- folders (see module docstring point 1: deliberately, honestly flat) ---

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        if folder_id is not None:
            raise ProviderError(_NO_FOLDERS_MSG, status_code=400)
        files = []
        for doc in self._list_documents(creds):
            name, _ = self._doc_display_name(doc)
            if name.startswith(_TRASH_PREFIX):
                continue
            files.append(self._doc_to_file(doc))
        return FolderContents(
            folder=None,
            breadcrumb=[BreadcrumbEntry(id=None, name=_APP_CABINET_NAME)],
            folders=[],
            files=files,
        )

    def list_trash(self, creds: dict) -> FolderContents:
        files = []
        for doc in self._list_documents(creds):
            name, _ = self._doc_display_name(doc)
            if name.startswith(_TRASH_PREFIX):
                files.append(self._doc_to_file(doc))
        return FolderContents(
            folder=None,
            breadcrumb=[BreadcrumbEntry(id=None, name="Trash")],
            folders=[],
            files=files,
        )

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        raise ProviderError(_NO_FOLDERS_MSG, status_code=400)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        raise ProviderError(_NO_FOLDERS_MSG, status_code=400)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        raise ProviderError(_NO_FOLDERS_MSG, status_code=400)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        raise ProviderError(_NO_FOLDERS_MSG, status_code=400)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        raise ProviderError(_NO_FOLDERS_MSG, status_code=400)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        raise ProviderError(_NO_FOLDERS_MSG, status_code=400)

    # --- files ---

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        if folder_id is not None:
            raise ProviderError(_NO_FOLDERS_MSG, status_code=400)
        cabinet_id = self._cabinet_id(creds)
        url = self._platform_url(creds) + f"/FileCabinets/{cabinet_id}/Documents"
        doc_meta = {"Field": [{"FieldName": _PRIMARY_NAME_FIELD, "ItemElementName": "String", "Item": name}]}
        files = {
            "document": (None, json.dumps(doc_meta), "application/json"),
            "file": (name, content, content_type or "application/octet-stream"),
        }
        try:
            resp = requests.post(url, headers=self._headers(creds), files=files, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach DocuWare: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"DocuWare upload failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        data = resp.json() if resp.content else {}
        doc = data
        if isinstance(data, dict) and "Document" in data:
            doc = data["Document"]
        if isinstance(doc, list):
            doc = doc[0] if doc else {}
        if not isinstance(doc, dict) or not doc.get("Id"):
            raise ProviderError("DocuWare accepted the upload but didn't return a document id", status_code=502)
        return self._doc_to_file(doc)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        return self._doc_to_file(self._get_document(creds, file_id))

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        cabinet_id = self._cabinet_id(creds)
        doc = self._get_document(creds, file_id)
        self._set_name_field(creds, cabinet_id, doc, name)
        return self.get_file(creds, file_id)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        if new_folder_id is not None:
            raise ProviderError(
                "DocuWare organizes documents by indexed metadata fields within a File "
                "Cabinet, not by folders — there's nowhere else to move a document to.",
                status_code=400,
            )
        return self.get_file(creds, file_id)

    def delete_file(self, creds: dict, file_id: str) -> None:
        cabinet_id = self._cabinet_id(creds)
        self._request(creds, "DELETE", f"/FileCabinets/{cabinet_id}/Documents/{file_id}")

    def get_content(self, creds: dict, file_id: str) -> bytes:
        cabinet_id = self._cabinet_id(creds)
        url = self._platform_url(creds) + f"/FileCabinets/{cabinet_id}/Documents/{file_id}/FileDownload"
        try:
            resp = requests.get(
                url,
                headers={"Cookie": creds.get("cookie", "")},
                params={"targetFileType": "Auto"},
                timeout=60,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach DocuWare: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError("Couldn't fetch document content", status_code=404)
        return resp.content

    # --- versions (see module docstring point 6: simplified single-current-
    # version model, same fallback pattern used by ibmi_provider.py /
    # ibmz_provider.py for backends without confidently-known native
    # version history) ---

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        info = self.get_file(creds, file_id)
        return [VersionInfo(
            id="current", version_number=1, size_bytes=info.size_bytes,
            content_type=info.content_type, is_current=True, updated_at=info.updated_at,
        )]

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        cabinet_id = self._cabinet_id(creds)
        url = self._platform_url(creds) + f"/FileCabinets/{cabinet_id}/Documents/{file_id}/Sections"
        files = {"file": (f"version_{file_id}", content, content_type or "application/octet-stream")}
        try:
            resp = requests.post(url, headers=self._headers(creds), files=files, timeout=60)
        except requests.RequestException as exc:
            raise ProviderError(f"Couldn't reach DocuWare: {exc}", status_code=502)
        if resp.status_code >= 400:
            raise ProviderError(f"DocuWare content update failed ({resp.status_code}): {resp.text[:300]}", status_code=502)
        return self.get_file(creds, file_id)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        return self.get_content(creds, file_id)

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        return self.get_file(creds, file_id)

    # --- trash (emulated via a name-field tag — see module docstring point 5) ---

    def trash_file(self, creds: dict, file_id: str) -> None:
        cabinet_id = self._cabinet_id(creds)
        doc = self._get_document(creds, file_id)
        fields = doc.get("Fields") or []
        field_name = self._find_name_field(fields)
        if not field_name:
            raise ProviderError(
                "This document has no recognizable name field (checked for "
                f"{', '.join(_NAME_FIELD_CANDIDATES)}) to tag as trashed — trash isn't "
                "available for documents in a cabinet without one of these indexed fields.",
                status_code=501,
            )
        current = self._field_value(fields, field_name) or ""
        if not current.startswith(_TRASH_PREFIX):
            body = {"Field": [{"FieldName": field_name, "ItemElementName": "String", "Item": _TRASH_PREFIX + current}]}
            self._request(creds, "PUT", f"/FileCabinets/{cabinet_id}/Documents/{file_id}/Fields", json=body)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        cabinet_id = self._cabinet_id(creds)
        doc = self._get_document(creds, file_id)
        fields = doc.get("Fields") or []
        field_name = self._find_name_field(fields)
        if field_name:
            current = self._field_value(fields, field_name) or ""
            if current.startswith(_TRASH_PREFIX):
                body = {"Field": [{"FieldName": field_name, "ItemElementName": "String", "Item": current[len(_TRASH_PREFIX):]}]}
                self._request(creds, "PUT", f"/FileCabinets/{cabinet_id}/Documents/{file_id}/Fields", json=body)
        return self.get_file(creds, file_id)

    # --- search (client-side substring filter — see module docstring point 7) ---

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        q = (query or "").lower()
        files = []
        for doc in self._list_documents(creds):
            name, _ = self._doc_display_name(doc)
            if name.startswith(_TRASH_PREFIX):
                continue
            if q in name.lower():
                files.append(self._doc_to_file(doc))
        return [], files[:200]
