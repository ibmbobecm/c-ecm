"""Configurable database engine for C-ECM's own control-plane data.

Every store module (users_store.py, workflows_store.py, ...) still owns
its schema (as SQLAlchemy Table objects) and its own queries — this module
only decides WHERE those tables live, based on FD_DB_ENGINE:

  - "sqlite" (default): each store gets its own engine, pointed at its own
    file under DATA_DIR — exactly today's one-file-per-store layout, zero
    config needed. Safe default for a single-box / dev deployment.
  - "postgres" / "oracle": every store shares ONE engine/database — a real
    RDBMS doesn't need file-per-concern separation, and each store's table
    names are already distinct from every other store's.

A store's init_db() calls this module's create_all(metadata, store_name)
once at startup (see main.py's lifespan) — idempotent (CREATE ... IF NOT
EXISTS-equivalent via SQLAlchemy's checkfirst), so it's what actually
creates the database's tables/indexes the first time it runs against an
empty database, and is a no-op on every startup after that. That's the
whole "first-time setup" story: there's no separate migration/bootstrap
script to run by hand.

Query pattern every store follows (see notifications_store.py for the
fullest worked example): open a connection with `with engine.begin() as
conn:` (commits on success, rolls back and re-raises on any exception,
always closes — the same try/finally-with-commit shape every store used
with raw sqlite3 before), and read rows back via `.mappings()` so they
support dict-style `row["column"]` access exactly like sqlite3.Row did —
this is what let every store's existing `_row(row) -> dict` helper stay
completely unchanged by this conversion.
"""
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from . import config as _config

_sqlite_engines: dict[str, Engine] = {}
_shared_engine: Engine | None = None


def _sqlite_url(store_name: str) -> str:
    _config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_config.DATA_DIR / f'{store_name}.db'}"


def _shared_url() -> str:
    if _config.FD_DB_URL:
        return _config.FD_DB_URL
    if _config.FD_DB_ENGINE == "postgres":
        driver, default_port = "postgresql+psycopg", "5432"
    elif _config.FD_DB_ENGINE == "oracle":
        driver, default_port = "oracle+oracledb", "1521"
    else:
        raise ValueError(
            f"Unknown FD_DB_ENGINE={_config.FD_DB_ENGINE!r} — expected 'sqlite', 'postgres', or 'oracle'."
        )
    host = _config.FD_DB_HOST or "localhost"
    port = _config.FD_DB_PORT or default_port
    auth = f"{_config.FD_DB_USER}:{_config.FD_DB_PASSWORD}@" if _config.FD_DB_USER else ""
    return f"{driver}://{auth}{host}:{port}/{_config.FD_DB_NAME}"


def get_engine(store_name: str) -> Engine:
    """The engine a given store should run its queries against. In sqlite
    mode every store gets its own (one file each, same as always); in
    postgres/oracle mode every store shares the one process-wide engine."""
    global _shared_engine
    if _config.FD_DB_ENGINE == "sqlite":
        engine = _sqlite_engines.get(store_name)
        if engine is None:
            engine = sa.create_engine(_sqlite_url(store_name), connect_args={"timeout": 5})

            @sa.event.listens_for(engine, "connect")
            def _set_sqlite_pragmas(dbapi_conn, _record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

            _sqlite_engines[store_name] = engine
        return engine

    if _shared_engine is None:
        # pool_pre_ping: a real network DB connection can go stale (idle
        # timeout, restart) between requests in a way a local SQLite file
        # never does — validate before handing a pooled connection out
        # rather than surfacing that as a request failure.
        _shared_engine = sa.create_engine(_shared_url(), pool_pre_ping=True)
    return _shared_engine


def create_all(metadata: sa.MetaData, store_name: str) -> None:
    """Create this store's tables/indexes if they don't already exist —
    called from each store's own init_db(), itself called once at app
    startup for every store (see main.py's lifespan). Idempotent."""
    metadata.create_all(get_engine(store_name), checkfirst=True)
