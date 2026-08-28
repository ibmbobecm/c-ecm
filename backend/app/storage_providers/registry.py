"""All providers FileDrive knows about, keyed by their `key`. Providers that
need config which hasn't been supplied yet (an Alfresco URL, an OAuth app's
client id/secret) still register — `/providers` reports them as
`configured: false` so the UI can show "Connect" as disabled with an
explanation, rather than hiding the backend entirely.
"""

from .base import StorageProvider
from .filenet_provider import FileNetProvider
from .local_provider import LocalDiskProvider

_PROVIDERS: dict[str, StorageProvider] = {}


def _register(provider: StorageProvider) -> None:
    _PROVIDERS[provider.key] = provider


def _build_registry() -> None:
    if _PROVIDERS:
        return
    _register(FileNetProvider())
    _register(LocalDiskProvider())

    try:
        from .alfresco_provider import AlfrescoProvider
        _register(AlfrescoProvider())
    except Exception:
        pass

    try:
        from .oauth_providers import BoxProvider, GoogleDriveProvider, MicrosoftGraphProvider
        _register(GoogleDriveProvider())
        _register(MicrosoftGraphProvider())
        _register(BoxProvider())
    except Exception:
        pass

    try:
        from .s3_provider import AWSS3Provider, IBMCOSProvider
        _register(AWSS3Provider())
        _register(IBMCOSProvider())
    except Exception:
        pass

    try:
        from .azure_provider import AzureBlobProvider
        _register(AzureBlobProvider())
    except Exception:
        pass


def get_provider(key: str) -> StorageProvider:
    _build_registry()
    provider = _PROVIDERS.get(key)
    if provider is None:
        raise KeyError(f"Unknown provider '{key}'")
    return provider


def list_providers() -> list[StorageProvider]:
    _build_registry()
    return list(_PROVIDERS.values())
