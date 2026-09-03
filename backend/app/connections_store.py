"""Persistent storage for backend connections — a user connects FileNet,
Alfresco, Google Drive, etc. once (via Settings), and it's remembered across
logins, not just cached for one session's lifetime like the old per-login
model was. creds_json is encrypted at rest (crypto_util.py) — the SQLite
file itself never holds a plaintext username/password/OAuth token.
"""

import datetime
import json
import uuid

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from . import crypto_util
from . import db

_metadata = sa.MetaData()

connections = sa.Table(
    "connections", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("provider_key", sa.String(64), nullable=False),
    sa.Column("display_name", sa.String(255), nullable=False),
    sa.Column("creds_json", sa.Text, nullable=False),
    sa.Column("identity", sa.String(255)),
    sa.Column("created_at", sa.String(40), nullable=False),
)

# COLLATE NOCASE (SQLite-only syntax) has no portable equivalent across
# sqlite/postgres/oracle, so case-insensitive uniqueness is expressed instead
# as a functional unique index on lower(display_name) — a standard construct
# all three dialects support. Deliberately NOT created as part of a single
# db.create_all() pass — see init_db() below for why.
idx_connections_name_nocase = sa.Index(
    "idx_connections_name_nocase", sa.func.lower(connections.c.display_name), unique=True
)

_engine = db.get_engine("connections")


class DuplicateConnectionNameError(Exception):
    """Raised when a connection's display_name collides (case-insensitively)
    with an existing one — names double as how a user tells connections
    apart in the switcher, so two "IBM FileNet"s isn't just untidy, it's
    genuinely ambiguous."""

    def __init__(self, display_name: str):
        self.display_name = display_name
        super().__init__(f"A connection named \"{display_name}\" already exists")


def _dedupe_existing_names(conn: Connection) -> None:
    """One-time migration: rows created before this uniqueness rule existed
    may already share a display_name. Keep the older row's name as-is and
    disambiguate any later same-named row, so the UNIQUE index below can
    actually be created on the data that's already there."""
    rows = conn.execute(sa.select(connections).order_by(connections.c.created_at)).mappings().all()
    seen_lower: set[str] = set()
    for row in rows:
        name = row["display_name"]
        lower = name.lower()
        if lower not in seen_lower:
            seen_lower.add(lower)
            continue
        n = 2
        while f"{name} ({n})".lower() in seen_lower:
            n += 1
        new_name = f"{name} ({n})"
        conn.execute(connections.update().where(connections.c.id == row["id"]).values(display_name=new_name))
        seen_lower.add(new_name.lower())


def init_db() -> None:
    with _engine.begin() as conn:
        # Table first (idempotent — SQLAlchemy's reflection-based checkfirst,
        # not a raw "IF NOT EXISTS" which Oracle doesn't support anyway).
        if not sa.inspect(conn).has_table("connections"):
            conn.execute(sa.schema.CreateTable(connections))
        # Then dedupe whatever's already there...
        _dedupe_existing_names(conn)
        # ...and only then add the uniqueness index — safe now that any
        # pre-existing case-collisions are gone. This has to stay a separate,
        # ordered step rather than a single db.create_all(_metadata, ...)
        # call: create_all() would create the table and the index in the
        # same pass, so on a database that predates this uniqueness rule
        # (and may already hold two rows differing only in case) the index
        # creation would fail outright before _dedupe_existing_names ever ran.
        #
        # Whether the index already exists (e.g. a prior startup already
        # created it) can't be reliably checked via reflection on every
        # dialect — SQLite in particular can't reflect an expression-based
        # index back into get_indexes() (it silently skips it with a
        # SAWarning) — so just attempt the create and treat "already exists"
        # as success. A SAVEPOINT (begin_nested) keeps a failed attempt here
        # from poisoning the rest of this transaction, since the dedupe and
        # legacy-creds-encryption migrations below still need to commit.
        try:
            with conn.begin_nested():
                conn.execute(sa.schema.CreateIndex(idx_connections_name_nocase))
        except sa.exc.DBAPIError:
            pass
        _encrypt_legacy_creds(conn)


def _encrypt_legacy_creds(conn: Connection) -> None:
    """One-time migration: encrypts any creds_json row still holding plain
    JSON from before encryption-at-rest existed. crypto_util.ensure_encrypted
    is idempotent, so this is safe to run on every startup — an
    already-encrypted row is read back unchanged and skipped."""
    rows = conn.execute(sa.select(connections.c.id, connections.c.creds_json)).mappings().all()
    for row in rows:
        upgraded = crypto_util.ensure_encrypted(row["creds_json"])
        if upgraded != row["creds_json"]:
            conn.execute(connections.update().where(connections.c.id == row["id"]).values(creds_json=upgraded))


def name_exists(display_name: str) -> bool:
    stmt = sa.select(sa.literal(1)).where(sa.func.lower(connections.c.display_name) == display_name.lower())
    with _engine.connect() as conn:
        row = conn.execute(stmt).first()
    return row is not None


def unique_display_name(base: str) -> str:
    """Returns `base` if it's free, otherwise the first "`base` (N)" that is —
    used where there's no form to send a rejection back to (the OAuth
    callback lands after the user already granted access on Google's/etc.
    own page, so failing outright there would be a dead end)."""
    if not name_exists(base):
        return base
    n = 2
    while name_exists(f"{base} ({n})"):
        n += 1
    return f"{base} ({n})"


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "provider_key": row["provider_key"],
        "display_name": row["display_name"],
        "identity": row["identity"],
        "created_at": row["created_at"],
    }


def list_connections() -> list[dict]:
    stmt = sa.select(connections).order_by(connections.c.created_at)
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_row_to_dict(r) for r in rows]


def get_connection(connection_id: str) -> dict | None:
    stmt = sa.select(connections).where(connections.c.id == connection_id)
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _row_to_dict(row) if row else None


def get_creds(connection_id: str) -> tuple[str, dict] | None:
    """Returns (provider_key, creds) or None."""
    stmt = sa.select(connections).where(connections.c.id == connection_id)
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    if row is None:
        return None
    return row["provider_key"], json.loads(crypto_util.decrypt(row["creds_json"]))


def create_connection(provider_key: str, display_name: str, creds: dict, identity: str) -> dict:
    conn_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        with _engine.begin() as conn:
            conn.execute(
                connections.insert().values(
                    id=conn_id,
                    provider_key=provider_key,
                    display_name=display_name,
                    creds_json=crypto_util.encrypt(json.dumps(creds)),
                    identity=identity,
                    created_at=now,
                )
            )
    except sa.exc.IntegrityError:
        raise DuplicateConnectionNameError(display_name)
    return get_connection(conn_id)


def update_creds(connection_id: str, creds: dict) -> None:
    with _engine.begin() as conn:
        conn.execute(
            connections.update()
            .where(connections.c.id == connection_id)
            .values(creds_json=crypto_util.encrypt(json.dumps(creds)))
        )


def delete_connection(connection_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(connections.delete().where(connections.c.id == connection_id))
