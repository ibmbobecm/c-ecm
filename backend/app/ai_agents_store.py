"""AI Agents — a named, persistent Q&A endpoint scoped to a folder (every
file under it, recursively) or a single file. Deliberately stores no
snapshot of document content: the knowledge base is gathered fresh from the
live folder/file on every chat call (see ai_agents_service.py), so an
edited or newly-added file is reflected on the agent's very next answer
with no separate re-indexing step to remember to run.

Mirrors share_links_store.py's public-token pattern: a random opaque
token is the only thing the public, unauthenticated chat route needs to
resolve an agent back to its scope.
"""

import datetime
import re
import secrets
import uuid

import sqlalchemy as sa

from . import db
from .config import DATA_DIR

_IMAGES_DIR = DATA_DIR / "ai_agent_images"

_metadata = sa.MetaData()

ai_agents = sa.Table(
    "ai_agents", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("connection_id", sa.String(32), nullable=False),
    sa.Column("provider_key", sa.String(64), nullable=False),
    sa.Column("scope_type", sa.String(64), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_name", sa.String(255), nullable=False),
    sa.Column("owner", sa.String(255), nullable=False),
    sa.Column("public_token", sa.String(64), nullable=False, unique=True),
    sa.Column("is_active", sa.Boolean, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("updated_at", sa.String(40), nullable=False),
    sa.Index("idx_ai_agents_owner", "owner"),
    sa.Index("idx_ai_agents_resource", "connection_id", "resource_id"),
)

ai_agent_chats = sa.Table(
    "ai_agent_chats", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("agent_id", sa.String(32), nullable=False),
    sa.Column("actor", sa.String(255)),
    sa.Column("question", sa.Text, nullable=False),
    sa.Column("tokens_used", sa.Integer),
    sa.Column("tokens_estimated", sa.Boolean, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Index("idx_ai_agent_chats_agent", "agent_id"),
)

# Single row per agent_id (agent_id is its own primary key, not a
# separate id) -- the demo-site's editable copy: headline/body/contact
# info/SEO metadata. Every column here besides agent_id/updated_at is
# optional -- an agent with no site customization yet has no row at all
# (see get_site()/upsert_site()).
ai_agent_sites = sa.Table(
    "ai_agent_sites", _metadata,
    sa.Column("agent_id", sa.String(32), primary_key=True),
    sa.Column("headline", sa.String(255)),
    sa.Column("subheadline", sa.String(255)),
    sa.Column("body", sa.Text),
    sa.Column("accent_color", sa.String(32)),
    sa.Column("contact_email", sa.String(255)),
    sa.Column("contact_phone", sa.String(64)),
    sa.Column("contact_address", sa.Text),
    sa.Column("contact_note", sa.Text),
    sa.Column("seo_description", sa.String(500)),
    sa.Column("footer_tagline", sa.String(255)),
    sa.Column("updated_at", sa.String(40), nullable=False),
)

ai_agent_edit_tokens = sa.Table(
    "ai_agent_edit_tokens", _metadata,
    sa.Column("token", sa.String(64), primary_key=True),
    sa.Column("agent_id", sa.String(32), nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("expires_at", sa.String(40), nullable=False),
    sa.Index("idx_ai_agent_edit_tokens_agent", "agent_id"),
)

ai_agent_pages = sa.Table(
    "ai_agent_pages", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("agent_id", sa.String(32), nullable=False),
    sa.Column("slug", sa.String(255), nullable=False),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("nav_order", sa.Integer, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("updated_at", sa.String(40), nullable=False),
    sa.Index("idx_ai_agent_pages_slug", "agent_id", "slug", unique=True),
)

ai_agent_posts = sa.Table(
    "ai_agent_posts", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("agent_id", sa.String(32), nullable=False),
    sa.Column("slug", sa.String(255), nullable=False),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("excerpt", sa.Text, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("published_at", sa.String(40), nullable=False),
    sa.Column("updated_at", sa.String(40), nullable=False),
    sa.Index("idx_ai_agent_posts_slug", "agent_id", "slug", unique=True),
)

ai_agent_leads = sa.Table(
    "ai_agent_leads", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("agent_id", sa.String(32), nullable=False),
    sa.Column("email", sa.String(255)),
    sa.Column("phone", sa.String(64)),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Index("idx_ai_agent_leads_agent", "agent_id"),
)

# Metadata only -- the raw image bytes themselves are never stored in a
# DB column (see create_image()'s docstring below): they live as a plain
# file on disk under _IMAGES_DIR, named by this row's own id.
ai_agent_images = sa.Table(
    "ai_agent_images", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("agent_id", sa.String(32), nullable=False),
    sa.Column("content_type", sa.String(128), nullable=False),
    sa.Column("size_bytes", sa.Integer, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Index("idx_ai_agent_images_agent", "agent_id"),
)

# table name -> Table object, for _unique_slug()'s two callers (create_page/
# create_post) which each only know their own table by name.
_SLUG_TABLES = {"ai_agent_pages": ai_agent_pages, "ai_agent_posts": ai_agent_posts}

_engine = db.get_engine("ai_agents")


def init_db() -> None:
    db.create_all(_metadata, "ai_agents")


def _row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "connection_id": row["connection_id"],
        "provider_key": row["provider_key"],
        "scope_type": row["scope_type"],
        "resource_id": row["resource_id"],
        "resource_name": row["resource_name"],
        "owner": row["owner"],
        "public_token": row["public_token"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create(
    *,
    name: str,
    description: str,
    connection_id: str,
    provider_key: str,
    scope_type: str,
    resource_id: str,
    resource_name: str,
    owner: str,
) -> dict:
    agent_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(24)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            ai_agents.insert().values(
                id=agent_id, name=name, description=description, connection_id=connection_id,
                provider_key=provider_key, scope_type=scope_type, resource_id=resource_id,
                resource_name=resource_name, owner=owner, public_token=token, is_active=True,
                created_at=now, updated_at=now,
            )
        )
        row = conn.execute(sa.select(ai_agents).where(ai_agents.c.id == agent_id)).mappings().first()
    return _row(row)


def get(agent_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(sa.select(ai_agents).where(ai_agents.c.id == agent_id)).mappings().first()
    return _row(row) if row else None


def resolve_by_token(token: str) -> dict | None:
    """Looks up a public_token for the public chat route. Returns None for
    a token that doesn't exist or belongs to a deactivated agent — the
    visitor-facing response is the same "not available" either way."""
    with _engine.connect() as conn:
        row = conn.execute(sa.select(ai_agents).where(ai_agents.c.public_token == token)).mappings().first()
    if row is None or not row["is_active"]:
        return None
    return _row(row)


def list_for_resource(connection_id: str, resource_id: str) -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(
            sa.select(ai_agents)
            .where(ai_agents.c.connection_id == connection_id, ai_agents.c.resource_id == resource_id)
            .order_by(ai_agents.c.created_at.desc())
        ).mappings().all()
    return [_row(r) for r in rows]


def list_for_owner(owner: str) -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(
            sa.select(ai_agents).where(ai_agents.c.owner == owner).order_by(ai_agents.c.created_at.desc())
        ).mappings().all()
    return [_row(r) for r in rows]


def list_all() -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(sa.select(ai_agents).order_by(ai_agents.c.created_at.desc())).mappings().all()
    return [_row(r) for r in rows]


def update(agent_id: str, *, name: str | None = None, description: str | None = None,
           is_active: bool | None = None) -> dict | None:
    with _engine.begin() as conn:
        row = conn.execute(sa.select(ai_agents).where(ai_agents.c.id == agent_id)).mappings().first()
        if row is None:
            return None
        updates: dict = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if is_active is not None:
            updates["is_active"] = is_active
        if updates:
            updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(ai_agents.update().where(ai_agents.c.id == agent_id).values(**updates))
        row = conn.execute(sa.select(ai_agents).where(ai_agents.c.id == agent_id)).mappings().first()
        return _row(row)


def delete(agent_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(ai_agent_chats.delete().where(ai_agent_chats.c.agent_id == agent_id))
        conn.execute(ai_agent_leads.delete().where(ai_agent_leads.c.agent_id == agent_id))
        _delete_images_for_agent(conn, agent_id)
        conn.execute(ai_agent_sites.delete().where(ai_agent_sites.c.agent_id == agent_id))
        conn.execute(ai_agent_edit_tokens.delete().where(ai_agent_edit_tokens.c.agent_id == agent_id))
        conn.execute(ai_agent_pages.delete().where(ai_agent_pages.c.agent_id == agent_id))
        conn.execute(ai_agent_posts.delete().where(ai_agent_posts.c.agent_id == agent_id))
        conn.execute(ai_agents.delete().where(ai_agents.c.id == agent_id))


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    with _engine.begin() as conn:
        ids = [r["id"] for r in conn.execute(
            sa.select(ai_agents.c.id).where(
                ai_agents.c.connection_id == connection_id, ai_agents.c.resource_id == resource_id
            )
        ).mappings().all()]
        for aid in ids:
            conn.execute(ai_agent_chats.delete().where(ai_agent_chats.c.agent_id == aid))
            conn.execute(ai_agent_leads.delete().where(ai_agent_leads.c.agent_id == aid))
            _delete_images_for_agent(conn, aid)
            conn.execute(ai_agent_sites.delete().where(ai_agent_sites.c.agent_id == aid))
            conn.execute(ai_agent_edit_tokens.delete().where(ai_agent_edit_tokens.c.agent_id == aid))
            conn.execute(ai_agent_pages.delete().where(ai_agent_pages.c.agent_id == aid))
            conn.execute(ai_agent_posts.delete().where(ai_agent_posts.c.agent_id == aid))
        conn.execute(
            ai_agents.delete().where(
                ai_agents.c.connection_id == connection_id, ai_agents.c.resource_id == resource_id
            )
        )


def delete_for_resources_batch(connection_id: str, resource_ids: list[str]) -> None:
    """Same cleanup as delete_for_resource(), for many resources in one
    connection — used when permanently deleting a folder with descendants,
    which previously called delete_for_resource() once per descendant
    (each opening its own connection)."""
    if not resource_ids:
        return
    with _engine.begin() as conn:
        ids = [r["id"] for r in conn.execute(
            sa.select(ai_agents.c.id).where(
                ai_agents.c.connection_id == connection_id, ai_agents.c.resource_id.in_(resource_ids)
            )
        ).mappings().all()]
        for aid in ids:
            conn.execute(ai_agent_chats.delete().where(ai_agent_chats.c.agent_id == aid))
            conn.execute(ai_agent_leads.delete().where(ai_agent_leads.c.agent_id == aid))
            _delete_images_for_agent(conn, aid)
            conn.execute(ai_agent_sites.delete().where(ai_agent_sites.c.agent_id == aid))
            conn.execute(ai_agent_edit_tokens.delete().where(ai_agent_edit_tokens.c.agent_id == aid))
            conn.execute(ai_agent_pages.delete().where(ai_agent_pages.c.agent_id == aid))
            conn.execute(ai_agent_posts.delete().where(ai_agent_posts.c.agent_id == aid))
        conn.execute(
            ai_agents.delete().where(
                ai_agents.c.connection_id == connection_id, ai_agents.c.resource_id.in_(resource_ids)
            )
        )


def delete_for_connection(connection_id: str) -> None:
    with _engine.begin() as conn:
        ids = [r["id"] for r in conn.execute(
            sa.select(ai_agents.c.id).where(ai_agents.c.connection_id == connection_id)
        ).mappings().all()]
        for aid in ids:
            conn.execute(ai_agent_chats.delete().where(ai_agent_chats.c.agent_id == aid))
            conn.execute(ai_agent_leads.delete().where(ai_agent_leads.c.agent_id == aid))
            _delete_images_for_agent(conn, aid)
            conn.execute(ai_agent_sites.delete().where(ai_agent_sites.c.agent_id == aid))
            conn.execute(ai_agent_edit_tokens.delete().where(ai_agent_edit_tokens.c.agent_id == aid))
            conn.execute(ai_agent_pages.delete().where(ai_agent_pages.c.agent_id == aid))
            conn.execute(ai_agent_posts.delete().where(ai_agent_posts.c.agent_id == aid))
        conn.execute(ai_agents.delete().where(ai_agents.c.connection_id == connection_id))


def record_chat(agent_id: str, *, actor: str | None, question: str,
                tokens_used: int | None, tokens_estimated: bool) -> None:
    with _engine.begin() as conn:
        conn.execute(
            ai_agent_chats.insert().values(
                id=uuid.uuid4().hex, agent_id=agent_id, actor=actor, question=question[:2000],
                tokens_used=tokens_used, tokens_estimated=bool(tokens_estimated),
                created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )
        )


def get_stats(agent_id: str) -> dict:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(
                sa.func.count().label("chat_count"),
                sa.func.coalesce(sa.func.sum(ai_agent_chats.c.tokens_used), 0).label("tokens_total"),
                sa.func.max(ai_agent_chats.c.created_at).label("last_chat_at"),
            ).where(ai_agent_chats.c.agent_id == agent_id)
        ).mappings().first()
        lead_count = conn.execute(
            sa.select(sa.func.count()).select_from(ai_agent_leads).where(ai_agent_leads.c.agent_id == agent_id)
        ).scalar_one()
    return {
        "chat_count": row["chat_count"],
        "tokens_total": row["tokens_total"],
        "last_chat_at": row["last_chat_at"],
        "lead_count": lead_count,
    }


def list_recent_chats(agent_id: str, limit: int = 50) -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(
            sa.select(ai_agent_chats)
            .where(ai_agent_chats.c.agent_id == agent_id)
            .order_by(ai_agent_chats.c.created_at.desc())
            .limit(limit)
        ).mappings().all()
    return [
        {
            "id": r["id"],
            "actor": r["actor"],
            "question": r["question"],
            "tokens_used": r["tokens_used"],
            "tokens_estimated": bool(r["tokens_estimated"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def create_lead(agent_id: str, *, email: str | None, phone: str | None, message: str) -> dict:
    """A visitor-submitted "contact us" lead from the public chat widget's
    scripted intake flow -- deliberately reachable with no auth/edit_token
    at all (same as a real website's contact form), gated only by the
    public rate limiter in routers/public_ai_agents.py."""
    lead_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            ai_agent_leads.insert().values(
                id=lead_id, agent_id=agent_id, email=(email or None), phone=(phone or None),
                message=message[:2000], created_at=now,
            )
        )
    return {"id": lead_id, "agent_id": agent_id, "email": email or None, "phone": phone or None,
            "message": message[:2000], "created_at": now}


def list_leads(agent_id: str, limit: int = 200) -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(
            sa.select(ai_agent_leads)
            .where(ai_agent_leads.c.agent_id == agent_id)
            .order_by(ai_agent_leads.c.created_at.desc())
            .limit(limit)
        ).mappings().all()
    return [
        {
            "id": r["id"], "agent_id": r["agent_id"], "email": r["email"], "phone": r["phone"],
            "message": r["message"], "created_at": r["created_at"],
        }
        for r in rows
    ]


def create_image(agent_id: str, content_type: str, data: bytes) -> dict:
    """Stores one image uploaded through the pencil editor's Quill image
    button -- metadata in this DB, raw bytes as a plain file on disk
    (matching how large blobs are handled elsewhere in this app rather
    than stuffing them into a SQLite column). The caller (routers/
    public_ai_agents.py) has already verified `data` is a genuine image
    of `content_type` before this is called -- this function trusts it."""
    image_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    (_IMAGES_DIR / image_id).write_bytes(data)
    with _engine.begin() as conn:
        conn.execute(
            ai_agent_images.insert().values(
                id=image_id, agent_id=agent_id, content_type=content_type, size_bytes=len(data), created_at=now
            )
        )
    return {"id": image_id, "agent_id": agent_id, "content_type": content_type, "size_bytes": len(data), "created_at": now}


def get_image(agent_id: str, image_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(ai_agent_images).where(ai_agent_images.c.id == image_id, ai_agent_images.c.agent_id == agent_id)
        ).mappings().first()
    return dict(row) if row else None


def get_image_bytes(image_id: str) -> bytes | None:
    path = _IMAGES_DIR / image_id
    try:
        return path.read_bytes()
    except OSError:
        return None


def _delete_images_for_agent(conn: sa.engine.Connection, agent_id: str) -> None:
    """Removes both the DB rows and their on-disk files for one agent's
    images -- unlike every other child table here, images have a file on
    disk too, so a plain SQL DELETE alone would leak that file."""
    ids = [r["id"] for r in conn.execute(
        sa.select(ai_agent_images.c.id).where(ai_agent_images.c.agent_id == agent_id)
    ).mappings().all()]
    conn.execute(ai_agent_images.delete().where(ai_agent_images.c.agent_id == agent_id))
    for image_id in ids:
        (_IMAGES_DIR / image_id).unlink(missing_ok=True)


_SITE_FIELDS = (
    "headline", "subheadline", "body", "accent_color", "contact_email", "contact_phone", "contact_address",
    "contact_note", "seo_description", "footer_tagline",
)


def get_site(agent_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(sa.select(ai_agent_sites).where(ai_agent_sites.c.agent_id == agent_id)).mappings().first()
    if row is None:
        return None
    return {**{f: row[f] for f in _SITE_FIELDS}, "updated_at": row["updated_at"]}


def upsert_site(agent_id: str, *, headline: str | None = None, subheadline: str | None = None,
                 body: str | None = None, accent_color: str | None = None,
                 contact_email: str | None = None, contact_phone: str | None = None,
                 contact_address: str | None = None, contact_note: str | None = None,
                 seo_description: str | None = None, footer_tagline: str | None = None) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    values = {
        "headline": headline, "subheadline": subheadline, "body": body, "accent_color": accent_color,
        "contact_email": contact_email, "contact_phone": contact_phone,
        "contact_address": contact_address, "contact_note": contact_note,
        "seo_description": seo_description, "footer_tagline": footer_tagline,
    }
    # Portable upsert: SQLite/Postgres "INSERT ... ON CONFLICT DO UPDATE"
    # has no equivalent construct in SQLAlchemy Core for Oracle (which
    # would need a MERGE statement instead), so this checks for an
    # existing row for this agent_id (its own primary key) and inserts or
    # updates accordingly, both inside the same transaction.
    with _engine.begin() as conn:
        existing = conn.execute(
            sa.select(ai_agent_sites.c.agent_id).where(ai_agent_sites.c.agent_id == agent_id)
        ).first()
        if existing is None:
            conn.execute(ai_agent_sites.insert().values(agent_id=agent_id, updated_at=now, **values))
        else:
            conn.execute(
                ai_agent_sites.update().where(ai_agent_sites.c.agent_id == agent_id).values(updated_at=now, **values)
            )
    return get_site(agent_id)


def merge_site_update(agent_id: str, provided: dict) -> dict:
    """Applies a partial update (as produced by
    SomeUpdateRequest.model_dump(exclude_unset=True)) on top of whatever's
    already saved, so a caller that only sends one field never wipes the
    others — the exact bug this one shared helper exists to make
    impossible to reintroduce by having two call sites (the authenticated
    and public site-update routes) each re-implement the same merge."""
    existing = get_site(agent_id) or {}
    return upsert_site(agent_id, **{f: provided.get(f, existing.get(f)) for f in _SITE_FIELDS})


def delete_site(agent_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(ai_agent_sites.delete().where(ai_agent_sites.c.agent_id == agent_id))


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "page"


def _unique_slug(conn: sa.engine.Connection, table: str, agent_id: str, title: str) -> str:
    base = _slugify(title)
    slug = base
    n = 2
    tbl = _SLUG_TABLES[table]
    while conn.execute(
        sa.select(tbl.c.slug).where(tbl.c.agent_id == agent_id, tbl.c.slug == slug)
    ).first() is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _page_row(row) -> dict:
    return {
        "id": row["id"], "agent_id": row["agent_id"], "slug": row["slug"], "title": row["title"],
        "content": row["content"], "nav_order": row["nav_order"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def create_page(agent_id: str, title: str, content: str, nav_order: int = 0) -> dict:
    page_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        slug = _unique_slug(conn, "ai_agent_pages", agent_id, title)
        conn.execute(
            ai_agent_pages.insert().values(
                id=page_id, agent_id=agent_id, slug=slug, title=title, content=content,
                nav_order=nav_order, created_at=now, updated_at=now,
            )
        )
        row = conn.execute(sa.select(ai_agent_pages).where(ai_agent_pages.c.id == page_id)).mappings().first()
    return _page_row(row)


def list_pages(agent_id: str) -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(
            sa.select(ai_agent_pages)
            .where(ai_agent_pages.c.agent_id == agent_id)
            .order_by(ai_agent_pages.c.nav_order, ai_agent_pages.c.created_at)
        ).mappings().all()
    return [_page_row(r) for r in rows]


def get_page(agent_id: str, page_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(ai_agent_pages).where(ai_agent_pages.c.id == page_id, ai_agent_pages.c.agent_id == agent_id)
        ).mappings().first()
    return _page_row(row) if row else None


def get_page_by_slug(agent_id: str, slug: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(ai_agent_pages).where(ai_agent_pages.c.agent_id == agent_id, ai_agent_pages.c.slug == slug)
        ).mappings().first()
    return _page_row(row) if row else None


def update_page(agent_id: str, page_id: str, *, title: str | None = None, content: str | None = None,
                nav_order: int | None = None) -> dict | None:
    with _engine.begin() as conn:
        row = conn.execute(
            sa.select(ai_agent_pages).where(ai_agent_pages.c.id == page_id, ai_agent_pages.c.agent_id == agent_id)
        ).mappings().first()
        if row is None:
            return None
        updates: dict = {}
        if title is not None:
            updates["title"] = title
        if content is not None:
            updates["content"] = content
        if nav_order is not None:
            updates["nav_order"] = nav_order
        if updates:
            updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(ai_agent_pages.update().where(ai_agent_pages.c.id == page_id).values(**updates))
        row = conn.execute(sa.select(ai_agent_pages).where(ai_agent_pages.c.id == page_id)).mappings().first()
        return _page_row(row)


def delete_page(agent_id: str, page_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(ai_agent_pages.delete().where(ai_agent_pages.c.id == page_id, ai_agent_pages.c.agent_id == agent_id))


def _post_row(row) -> dict:
    return {
        "id": row["id"], "agent_id": row["agent_id"], "slug": row["slug"], "title": row["title"],
        "excerpt": row["excerpt"], "content": row["content"],
        "published_at": row["published_at"], "updated_at": row["updated_at"],
    }


def create_post(agent_id: str, title: str, content: str, excerpt: str = "") -> dict:
    post_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        slug = _unique_slug(conn, "ai_agent_posts", agent_id, title)
        conn.execute(
            ai_agent_posts.insert().values(
                id=post_id, agent_id=agent_id, slug=slug, title=title, excerpt=excerpt, content=content,
                published_at=now, updated_at=now,
            )
        )
        row = conn.execute(sa.select(ai_agent_posts).where(ai_agent_posts.c.id == post_id)).mappings().first()
    return _post_row(row)


def list_posts(agent_id: str) -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(
            sa.select(ai_agent_posts)
            .where(ai_agent_posts.c.agent_id == agent_id)
            .order_by(ai_agent_posts.c.published_at.desc())
        ).mappings().all()
    return [_post_row(r) for r in rows]


def get_post(agent_id: str, post_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(ai_agent_posts).where(ai_agent_posts.c.id == post_id, ai_agent_posts.c.agent_id == agent_id)
        ).mappings().first()
    return _post_row(row) if row else None


def get_post_by_slug(agent_id: str, slug: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(ai_agent_posts).where(ai_agent_posts.c.agent_id == agent_id, ai_agent_posts.c.slug == slug)
        ).mappings().first()
    return _post_row(row) if row else None


def update_post(agent_id: str, post_id: str, *, title: str | None = None, content: str | None = None,
                excerpt: str | None = None) -> dict | None:
    with _engine.begin() as conn:
        row = conn.execute(
            sa.select(ai_agent_posts).where(ai_agent_posts.c.id == post_id, ai_agent_posts.c.agent_id == agent_id)
        ).mappings().first()
        if row is None:
            return None
        updates: dict = {}
        if title is not None:
            updates["title"] = title
        if content is not None:
            updates["content"] = content
        if excerpt is not None:
            updates["excerpt"] = excerpt
        if updates:
            updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(ai_agent_posts.update().where(ai_agent_posts.c.id == post_id).values(**updates))
        row = conn.execute(sa.select(ai_agent_posts).where(ai_agent_posts.c.id == post_id)).mappings().first()
        return _post_row(row)


def delete_post(agent_id: str, post_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(ai_agent_posts.delete().where(ai_agent_posts.c.id == post_id, ai_agent_posts.c.agent_id == agent_id))


def create_edit_token(agent_id: str, ttl_minutes: int = 20) -> tuple[str, str]:
    """A short-lived, single-agent-scoped token — deliberately NOT the
    admin's real session JWT, which would otherwise end up sitting in a
    URL (browser history, referrer headers) for a much longer-lived,
    all-purpose credential. Minted fresh each time "Open test site" is
    clicked from the authenticated app; only lets the holder view/edit
    THIS one agent's public demo-site copy, nothing else."""
    token = secrets.token_urlsafe(24)
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = (now + datetime.timedelta(minutes=ttl_minutes)).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            ai_agent_edit_tokens.insert().values(
                token=token, agent_id=agent_id, created_at=now.isoformat(), expires_at=expires_at
            )
        )
    return token, expires_at


def resolve_edit_token(token: str, agent_id: str) -> bool:
    """True if `token` is valid, unexpired, and scoped to this exact agent."""
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(ai_agent_edit_tokens.c.expires_at).where(
                ai_agent_edit_tokens.c.token == token, ai_agent_edit_tokens.c.agent_id == agent_id
            )
        ).mappings().first()
    if row is None:
        return False
    return datetime.datetime.fromisoformat(row["expires_at"]) > datetime.datetime.now(datetime.timezone.utc)
