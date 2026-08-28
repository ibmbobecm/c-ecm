"""The provider abstraction that makes FileDrive's UI backend-agnostic.

Every storage system (FileNet, local disk, Alfresco, Google Drive, Microsoft
365, Box, ...) implements `StorageProvider`. Routers never talk to a specific
backend directly — they resolve the active provider for the current session
and call these methods. All ids (`folder_id`, `file_id`, `version_id`) are
opaque strings; each provider decides what's actually inside them (a FileNet
GUID, a local integer, a Google Drive file id, ...) and nothing outside the
provider should assume a shape.

Two authentication shapes exist, because they're genuinely different:

- `AuthMode.CREDENTIALS` — username/password checked directly against the
  backend (FileNet, Alfresco, local disk). `authenticate()` validates them
  and the same pair is cached server-side and passed back into every other
  call as `creds` (a small opaque object the provider itself defines the
  shape of — for these it's `{"username": ..., "password": ...}`).
- `AuthMode.OAUTH` — Google Drive / Microsoft 365 / Box. There's no
  authenticate(); instead `get_authorize_url()` sends the browser to the
  provider's consent screen, and `complete_oauth()` exchanges the callback
  code for tokens. Those tokens (access + refresh) become `creds` for every
  subsequent call, refreshed transparently by the provider as needed.

`creds` is passed as a plain dict on every call rather than typed per
provider, since routers are provider-agnostic and shouldn't need to import
every provider's credential type. Each provider validates the shape it
expects internally.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AuthMode(str, Enum):
    CREDENTIALS = "credentials"
    OAUTH = "oauth"


@dataclass
class FolderInfo:
    id: str
    name: str
    parent_id: str | None
    created_at: datetime | None = None


@dataclass
class FileInfo:
    id: str
    name: str
    folder_id: str | None
    version_number: int
    size_bytes: int | None
    content_type: str | None
    updated_at: datetime | None = None


@dataclass
class VersionInfo:
    id: str
    version_number: int
    size_bytes: int | None
    content_type: str | None
    is_current: bool
    updated_at: datetime | None = None


@dataclass
class BreadcrumbEntry:
    id: str | None
    name: str


@dataclass
class FolderContents:
    folder: FolderInfo | None
    breadcrumb: list[BreadcrumbEntry]
    folders: list[FolderInfo]
    files: list[FileInfo]


@dataclass
class PermissionEntry:
    principal_type: str  # "user" | "group" | "anyone_with_link" | "domain"
    principal_id: str
    principal_display: str
    role: str  # provider-native role string (e.g. "viewer", "editor", "owner")
    inherited: bool
    source: str | None = None  # id it's inherited from, if known


@dataclass
class ShareLink:
    id: str
    url: str
    role: str  # "view" | "comment" | "edit"
    expires_at: datetime | None
    password_protected: bool


class ProviderError(Exception):
    """A backend-reported failure (not found, access denied, transient
    error, ...). `status_code` lets routers map it to the right HTTP status
    without every provider re-implementing FastAPI error handling."""

    def __init__(self, message: str, status_code: int = 502, detail: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail or message


@dataclass
class ConfigField:
    """One extra field a provider needs at connection-creation time, beyond
    username/password — a server URL, an object store name, whatever that
    backend's own deployment varies by. Rendered generically by the
    frontend from `StorageProvider.config_fields`, so adding a field here
    doesn't require any frontend change."""

    key: str
    label: str
    placeholder: str = ""
    required: bool = True


class StorageProvider(ABC):
    key: str
    display_name: str
    auth_mode: AuthMode

    @property
    def configured(self) -> bool:
        """False when required *admin-level* setup (an OAuth app's client
        id/secret, registered once for this whole FileDrive deployment)
        hasn't been supplied yet. Providers whose configuration is entirely
        per-connection (FileNet's server URL, Alfresco's base URL) don't
        need this — every connection carries its own, so there's nothing
        to block on ahead of time. Only OAuth providers override this."""
        return True

    @property
    def config_fields(self) -> list[ConfigField]:
        """Extra fields the "add connection" form should collect beyond
        username/password, for CREDENTIALS-mode providers whose server
        varies by connection (not needed for OAuth providers — there's
        nothing to ask the end user, the app-level OAuth client covers
        every connection)."""
        return []

    requires_credentials: bool = True
    """False for a provider with no real remote identity to check (local
    disk — it's just a folder on this machine; you already logged into
    FileDrive itself). The frontend hides the username/password fields
    entirely rather than asking for a meaningless login."""

    credential_labels: tuple[str, str] = ("Username", "Password")
    """Override when the credential pair isn't literally a username/
    password — S3-style providers use an access key id + secret key, for
    instance. Purely a display label; `authenticate(username, password, ...)`
    still receives them positionally the same way."""

    # --- credentials-mode auth ---
    def authenticate(self, username: str, password: str, config: dict | None = None) -> dict | None:
        """Returns a `creds` dict on success, None on failure. `config`
        carries whatever `config_fields` declared (e.g. a server URL) —
        merged into the returned creds so every later call has it too.
        Only implemented by CREDENTIALS-mode providers."""
        raise NotImplementedError

    # --- oauth-mode auth ---
    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        raise NotImplementedError

    def complete_oauth(self, code: str, redirect_uri: str) -> dict:
        """Exchanges an authorization code for a `creds` dict (tokens)."""
        raise NotImplementedError

    def whoami(self, creds: dict) -> str:
        """A display identity for the connected account (email, username,
        ...), used to show who's logged in and to validate creds are live."""
        raise NotImplementedError

    def refresh_if_needed(self, creds: dict) -> tuple[dict, bool]:
        """Returns (creds, changed). Credential-mode providers never need
        this (a username/password doesn't expire); OAuth providers override
        it to refresh an expiring access token. When `changed` is True, the
        caller persists the returned creds back to the connections store —
        this method never has side effects of its own."""
        return creds, False

    # --- folders ---
    @abstractmethod
    def get_children(self, creds: dict, folder_id: str | None) -> FolderContents: ...

    @abstractmethod
    def list_trash(self, creds: dict) -> FolderContents: ...

    @abstractmethod
    def create_folder(self, creds: dict, parent_id: str | None, name: str) -> FolderInfo: ...

    @abstractmethod
    def rename_folder(self, creds: dict, folder_id: str, name: str) -> FolderInfo: ...

    @abstractmethod
    def move_folder(self, creds: dict, folder_id: str, new_parent_id: str | None) -> FolderInfo: ...

    @abstractmethod
    def delete_folder(self, creds: dict, folder_id: str) -> None: ...

    # --- files ---
    @abstractmethod
    def create_document(
        self, creds: dict, folder_id: str | None, name: str, content_type: str, content: bytes
    ) -> FileInfo: ...

    @abstractmethod
    def get_file(self, creds: dict, file_id: str) -> FileInfo: ...

    @abstractmethod
    def rename_file(self, creds: dict, file_id: str, name: str) -> FileInfo: ...

    @abstractmethod
    def move_file(self, creds: dict, file_id: str, new_folder_id: str | None) -> FileInfo: ...

    @abstractmethod
    def delete_file(self, creds: dict, file_id: str) -> None: ...

    @abstractmethod
    def get_content(self, creds: dict, file_id: str) -> bytes: ...

    # --- versions ---
    @abstractmethod
    def list_versions(self, creds: dict, file_id: str) -> list[VersionInfo]: ...

    @abstractmethod
    def create_version(
        self, creds: dict, file_id: str, content_type: str, content: bytes
    ) -> FileInfo: ...

    @abstractmethod
    def get_version_content(self, creds: dict, file_id: str, version_id: str) -> bytes: ...

    @abstractmethod
    def restore_version(self, creds: dict, file_id: str, version_id: str) -> FileInfo: ...

    # --- trash (best-effort; providers without native trash emulate it) ---
    @abstractmethod
    def trash_folder(self, creds: dict, folder_id: str) -> None: ...

    @abstractmethod
    def restore_folder(self, creds: dict, folder_id: str) -> FolderInfo: ...

    @abstractmethod
    def trash_file(self, creds: dict, file_id: str) -> None: ...

    @abstractmethod
    def restore_file(self, creds: dict, file_id: str) -> FileInfo: ...

    # --- search ---
    @abstractmethod
    def search(self, creds: dict, query: str) -> tuple[list[FolderInfo], list[FileInfo]]: ...

    # --- permissions (optional — most backends' real ACLs can't be
    # honestly emulated, so unlike sharing below there's no generic default
    # that works everywhere; a provider opts in by overriding both the flag
    # and the method once it can read real permission data from that
    # backend) ---
    supports_permissions: bool = False

    def get_permissions(self, creds: dict, resource_id: str, resource_type: str) -> list[PermissionEntry]:
        raise ProviderError("Permissions listing isn't supported by this connection yet", status_code=501)

    # --- sharing (default works for every provider out of the box — see
    # share_links_store.py — because it's FileDrive's own token registry
    # plus the read methods every provider already implements, not
    # anything backend-specific. A provider only overrides these when it
    # has genuinely better native sharing to offer (a real, backend-hosted
    # URL instead of one FileDrive has to broker) — none do yet, so the
    # base implementation is what actually runs today for all nine.) ---
    supports_share_links: bool = True

    def create_share_link(
        self,
        creds: dict,
        connection_id: str,
        resource_id: str,
        resource_type: str,
        role: str = "view",
        expires_at: datetime | None = None,
        password: str | None = None,
    ) -> ShareLink:
        from .. import share_links_store

        return share_links_store.create(connection_id, self.key, resource_id, resource_type, role, expires_at, password)

    def list_share_links(self, creds: dict, connection_id: str, resource_id: str, resource_type: str) -> list[ShareLink]:
        from .. import share_links_store

        return share_links_store.list_for_resource(connection_id, resource_id)

    def revoke_share_link(self, creds: dict, connection_id: str, resource_id: str, resource_type: str, link_id: str) -> None:
        from .. import share_links_store

        share_links_store.revoke(connection_id, link_id)
