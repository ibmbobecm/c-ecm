"""Evernote Teams (formerly Evernote Business).

NOT FUNCTIONAL for real data access, and this is disclosed deliberately
rather than papered over with plausible-looking-but-fake endpoints, for
two compounding reasons specific to Evernote:

1. Evernote's only public API (EDAM — the Evernote Data API) is a
   **Thrift binary-protocol** API, not JSON/REST like every other
   provider in this codebase. There is no documented plain-HTTP/JSON
   surface for notebooks/notes. Implementing a real Thrift client would
   need the `thrift` package as a new project dependency, which this
   adapter deliberately does not add on its own initiative.

2. Evernote's auth is **three-legged OAuth 1.0a** (request token ->
   user authorizes -> exchange for an access token, HMAC-SHA1-signed,
   with the provider's callback returning `oauth_token` +
   `oauth_verifier` query params) — fundamentally different from the
   OAuth 2.0 `code` + `state` shape this app's own OAuth callback route
   (`GET /connections/oauth/{provider_key}/callback`, which requires
   exactly `code` and `state`) is built for. An Evernote OAuth1 redirect
   back to that route would 422 at the routing layer before ever
   reaching this provider's `complete_oauth`, independent of anything
   this file does.

`get_authorize_url` below still performs a REAL, correctly HMAC-SHA1-
signed OAuth1 request-token call against Evernote's actual endpoint (to
demonstrate the protocol is understood, and so `configured` genuinely
reflects whether credentials are registered) — but `complete_oauth` and
every data method raise a clear, honest `ProviderError` explaining why
this provider can't be completed through this app today, rather than
guessing at a fictional REST surface Evernote doesn't publish. Making
this real would require: (a) adding a `thrift` dependency and a genuine
EDAM Thrift client, and (b) extending this app's OAuth callback route to
accept OAuth1's `oauth_token`/`oauth_verifier` shape alongside OAuth2's
`code`/`state` — both are real, scoped follow-up work, not something to
fake here.
"""

import hashlib
import hmac
import time
import urllib.parse
import uuid

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

_UNSUPPORTED = (
    "Evernote Teams isn't usable through this app yet — its API (EDAM) is a "
    "Thrift binary protocol, and its OAuth flow (three-legged OAuth 1.0a) "
    "doesn't fit this app's OAuth2-shaped callback route. See this file's "
    "module docstring for what real support would require."
)


class EvernoteTeamsProvider(StorageProvider):
    key = "evernote_teams"
    display_name = "Evernote Teams"
    auth_mode = AuthMode.OAUTH

    def _client(self) -> tuple[str, str]:
        return (
            settings_store.get_setting("evernote_teams_client_id", ""),
            settings_store.get_setting("evernote_teams_client_secret", ""),
        )

    @property
    def configured(self) -> bool:
        cid, secret = self._client()
        return bool(cid and secret)

    @staticmethod
    def _oauth1_sign(method: str, url: str, params: dict, consumer_secret: str, token_secret: str = "") -> str:
        base_params = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
                                for k, v in sorted(params.items()))
        base_string = "&".join([method.upper(), urllib.parse.quote(url, safe=""), urllib.parse.quote(base_params, safe="")])
        signing_key = f"{urllib.parse.quote(consumer_secret, safe='')}&{urllib.parse.quote(token_secret, safe='')}"
        digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
        import base64
        return base64.b64encode(digest).decode()

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        cid, secret = self._client()
        if not (cid and secret):
            raise ProviderError("Evernote Teams isn't configured yet", status_code=409)
        oauth_params = {
            "oauth_consumer_key": cid, "oauth_nonce": uuid.uuid4().hex, "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())), "oauth_version": "1.0", "oauth_callback": redirect_uri,
        }
        oauth_params["oauth_signature"] = self._oauth1_sign("POST", "https://www.evernote.com/oauth", oauth_params, secret)
        resp = requests.post("https://www.evernote.com/oauth", data=oauth_params, timeout=30)
        if resp.status_code >= 400:
            raise ProviderError(f"Evernote request-token call failed: {resp.text[:300]}", status_code=502)
        parsed = urllib.parse.parse_qs(resp.text)
        oauth_token = parsed.get("oauth_token", [None])[0]
        if not oauth_token:
            raise ProviderError("Evernote didn't return a request token", status_code=502)
        return f"https://www.evernote.com/OAuth.action?oauth_token={urllib.parse.quote(oauth_token)}"

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def whoami(self, creds: dict) -> str:
        return creds.get("identity", "Evernote account")

    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def list_trash(self, creds: dict) -> FolderContents:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def delete_folder(self, creds: dict, folder_id: str) -> None:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def create_document(self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes) -> FileInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def get_file(self, creds: dict, file_id: str) -> FileInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def delete_file(self, creds: dict, file_id: str) -> None:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def get_content(self, creds: dict, file_id: str) -> bytes:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def create_version(self, creds: dict, file_id: str, content_type: str, content: bytes) -> FileInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def trash_folder(self, creds: dict, folder_id: str) -> None:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def trash_file(self, creds: dict, file_id: str) -> None:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def restore_file(self, creds: dict, file_id: str) -> FileInfo:
        raise ProviderError(_UNSUPPORTED, status_code=501)

    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]:
        raise ProviderError(_UNSUPPORTED, status_code=501)
