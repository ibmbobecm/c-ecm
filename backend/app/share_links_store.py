"""C-ECM's own share-link registry — the default (and, for now, only)
implementation behind StorageProvider.create_share_link()/list_share_links()/
revoke_share_link(). None of the nine backends' native sharing is called
here; this works identically for all of them because it's built entirely
on operations every provider already implements (get_file/get_content),
fronted by a token C-ECM itself issues and resolves. A provider can
still override the three methods on StorageProvider if it later wants to
hand back a real backend-hosted link instead — this module doesn't need to
know or care if that happens.

The public, unauthenticated GET /share/{token} route (routers/sharing.py)
is the only thing that reads this table without a C-ECM login — that
route's whole job is to turn a valid, unexpired token back into
(connection_id, resource_id) and then make the ordinary authenticated
provider calls on the visitor's behalf.
"""

import datetime
import hashlib
import hmac
import secrets
import uuid

import sqlalchemy as sa

from . import db
from .storage_providers.base import ShareLink

_metadata = sa.MetaData()

share_links = sa.Table(
    "share_links", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("token", sa.String(64), nullable=False, unique=True),
    sa.Column("connection_id", sa.String(32), nullable=False),
    sa.Column("provider_key", sa.String(64), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("role", sa.String(64), nullable=False),
    sa.Column("expires_at", sa.String(40)),
    sa.Column("password_hash", sa.String(128)),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("revoked_at", sa.String(40)),
    sa.Index("idx_share_links_resource", "connection_id", "resource_id"),
)

_engine = db.get_engine("share_links")


def init_db() -> None:
    db.create_all(_metadata, "share_links")


def _hash_password(password: str) -> str:
    # A lightweight deterrent, not an auth-grade KDF — same plaintext-
    # locally stance this app takes everywhere else, just not literally
    # bare plaintext for something that leaves the machine in a URL. Salted
    # per-link (stored as "salt$digest" in the one existing column, so this
    # doesn't need a schema migration) so two links with the same password
    # don't produce the same hash, and so a precomputed rainbow table for
    # common passwords doesn't work across every link at once.
    salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    salt, sep, digest = stored.partition("$")
    if not sep:
        return False
    candidate = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate, digest)


def _row(row) -> dict:
    return {
        "id": row["id"],
        "token": row["token"],
        "connection_id": row["connection_id"],
        "provider_key": row["provider_key"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "role": row["role"],
        "expires_at": row["expires_at"],
        "password_protected": row["password_hash"] is not None,
        "created_at": row["created_at"],
        "revoked_at": row["revoked_at"],
    }


def _to_share_link(row, base_url: str) -> ShareLink:
    return ShareLink(
        id=row["id"],
        url=f"{base_url}/share/{row['token']}",
        role=row["role"],
        expires_at=datetime.datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        password_protected=row["password_hash"] is not None,
    )


def create(
    connection_id: str,
    provider_key: str,
    resource_id: str,
    resource_type: str,
    role: str,
    expires_at: datetime.datetime | None,
    password: str | None,
) -> ShareLink:
    from .config import API_BASE_URL

    link_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(24)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            share_links.insert().values(
                id=link_id,
                token=token,
                connection_id=connection_id,
                provider_key=provider_key,
                resource_id=resource_id,
                resource_type=resource_type,
                role=role,
                expires_at=expires_at.isoformat() if expires_at else None,
                password_hash=_hash_password(password) if password else None,
                created_at=now,
                revoked_at=None,
            )
        )
        row = conn.execute(sa.select(share_links).where(share_links.c.id == link_id)).mappings().first()
    return _to_share_link(row, API_BASE_URL)


def list_for_resource(connection_id: str, resource_id: str) -> list[ShareLink]:
    from .config import API_BASE_URL

    stmt = (
        sa.select(share_links)
        .where(
            share_links.c.connection_id == connection_id,
            share_links.c.resource_id == resource_id,
            share_links.c.revoked_at.is_(None),
        )
        .order_by(share_links.c.created_at.desc())
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_to_share_link(r, API_BASE_URL) for r in rows]


def revoke(connection_id: str, link_id: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            share_links.update()
            .where(share_links.c.id == link_id, share_links.c.connection_id == connection_id)
            .values(revoked_at=now)
        )


def resolve(token: str) -> dict | None:
    """Looks up a token for the public GET /share/{token} route. Returns
    None for a token that doesn't exist, is revoked, or has expired — the
    router doesn't need to distinguish which, since the visitor-facing
    response is the same "this link isn't available" either way."""
    with _engine.connect() as conn:
        row = conn.execute(sa.select(share_links).where(share_links.c.token == token)).mappings().first()
    if row is None or row["revoked_at"] is not None:
        return None
    if row["expires_at"] and datetime.datetime.fromisoformat(row["expires_at"]) < datetime.datetime.now(datetime.timezone.utc):
        return None
    return _row(row)


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(
            share_links.delete().where(
                share_links.c.connection_id == connection_id, share_links.c.resource_id == resource_id
            )
        )


def delete_for_resources_batch(connection_id: str, resource_ids: list[str]) -> None:
    """Same cleanup as delete_for_resource(), for many resources in one
    connection/commit — used when permanently deleting a folder with
    descendants, which previously called delete_for_resource() once per
    descendant (each opening its own connection)."""
    if not resource_ids:
        return
    with _engine.begin() as conn:
        conn.execute(
            share_links.delete().where(
                share_links.c.connection_id == connection_id, share_links.c.resource_id.in_(resource_ids)
            )
        )


def delete_for_connection(connection_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(share_links.delete().where(share_links.c.connection_id == connection_id))


def check_password(token_row: dict, password: str | None) -> bool:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(share_links.c.password_hash).where(share_links.c.id == token_row["id"])
        ).mappings().first()
    if row["password_hash"] is None:
        return True
    return password is not None and _verify_password(password, row["password_hash"])
