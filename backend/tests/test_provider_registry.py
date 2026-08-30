"""Tests for the storage-provider registry's lazy, thread-safe build."""
import concurrent.futures

from app.storage_providers import registry


def test_concurrent_first_access_registers_every_provider(monkeypatch):
    """Regression test: registry.py used to register providers one at a
    time directly into the module-level _PROVIDERS dict, using
    `if _PROVIDERS: return` as its "already built" guard. That guard flips
    true the instant the FIRST provider registers (FileNet, unconditionally,
    before any of the try/except blocks) — so a second thread arriving while
    the build was still in progress would see a truthy-but-incomplete dict
    and return immediately, raising "Unknown provider" for everything not
    yet added. This is exactly what happens on a fresh process's first
    Global Search: routers/search.py fans the request out across every
    connection on its own thread, all racing into get_provider() at once.
    Reproduced deterministically pre-fix (errors appeared within the first
    few of a handful of trials); the fix builds into a local dict and only
    publishes it to the module-level name once, fully built."""
    registry._PROVIDERS.clear()
    keys = [
        "filenet", "local", "local", "ibm_cos", "local",
        "ibm_i", "local", "ibm_z", "local", "alfresco",
    ]

    def worker(key: str) -> tuple[str, Exception | None]:
        try:
            provider = registry.get_provider(key)
            assert provider.key == key
            return key, None
        except Exception as exc:  # pragma: no cover - failure path under test
            return key, exc

    for _ in range(20):
        registry._PROVIDERS.clear()
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(worker, keys))
        errors = [(k, e) for k, e in results if e is not None]
        assert errors == [], f"provider registry race reproduced: {errors}"

    # Leave the registry fully (and correctly) built for any other test
    # in the same process.
    registry._build_registry()
