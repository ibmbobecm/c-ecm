"""All providers C-ECM knows about, keyed by their `key`. Providers that
need config which hasn't been supplied yet (an Alfresco URL, an OAuth app's
client id/secret) still register — `/providers` reports them as
`configured: false` so the UI can show "Connect" as disabled with an
explanation, rather than hiding the backend entirely.
"""

import threading

from .base import StorageProvider
from .filenet_provider import FileNetProvider
from .local_provider import LocalDiskProvider

_PROVIDERS: dict[str, StorageProvider] = {}
_build_lock = threading.Lock()


def _build_registry() -> None:
    """Lazily populates _PROVIDERS exactly once.

    Global search fans a request out across every connection on its own
    thread (routers/search.py), so this can genuinely be entered by several
    threads at once — most commonly on a fresh process's very first search.
    The old version registered providers one at a time directly into the
    module-level dict and used `if _PROVIDERS: return` as a "done" guard;
    that guard flips true the instant the FIRST provider registers, so any
    thread still queued behind the lock (or that simply reads the guard a
    moment later) would treat a still-partially-built registry as finished
    and raise "Unknown provider" for everything not yet added — a real,
    reproducible race, not a hypothetical one. Building into a local dict
    and only publishing it to the module-level name once, at the very end,
    means no thread can ever observe a partially-built registry: the name
    is either the empty initial dict or the fully-built one, never
    in-between (a single dict-reference assignment is atomic under the
    GIL). The lock just prevents redundant concurrent rebuilds, not
    torn reads.
    """
    global _PROVIDERS
    if _PROVIDERS:
        return
    with _build_lock:
        if _PROVIDERS:
            return
        registry: dict[str, StorageProvider] = {}

        def _register(provider: StorageProvider) -> None:
            registry[provider.key] = provider

        # IBM family + local disk first (this app's primary, most-supported
        # backends), then everything else in no particular priority order.
        _register(FileNetProvider())

        try:
            from .s3_provider import IBMCOSProvider
            _register(IBMCOSProvider())
        except Exception:
            pass

        try:
            from .ibmi_provider import IBMiProvider
            _register(IBMiProvider())
        except Exception:
            pass

        try:
            from .ibmz_provider import IBMZProvider
            _register(IBMZProvider())
        except Exception:
            pass

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
            from .s3_provider import AWSS3Provider
            _register(AWSS3Provider())
        except Exception:
            pass

        try:
            from .azure_provider import AzureBlobProvider
            _register(AzureBlobProvider())
        except Exception:
            pass

        _PROVIDERS = registry


def get_provider(key: str) -> StorageProvider:
    _build_registry()
    provider = _PROVIDERS.get(key)
    if provider is None:
        raise KeyError(f"Unknown provider '{key}'")
    return provider


def list_providers() -> list[StorageProvider]:
    _build_registry()
    return list(_PROVIDERS.values())
