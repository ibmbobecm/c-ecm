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
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "ai_agents.db"
_IMAGES_DIR = DATA_DIR / "ai_agent_images"


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")  # wait for a concurrent writer instead of failing instantly
    return conn


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                connection_id TEXT NOT NULL,
                provider_key TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                resource_name TEXT NOT NULL DEFAULT '',
                owner TEXT NOT NULL,
                public_token TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_agents_owner ON ai_agents (owner)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_agents_resource ON ai_agents (connection_id, resource_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_agent_chats (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                actor TEXT,
                question TEXT NOT NULL,
                tokens_used INTEGER,
                tokens_estimated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_agent_chats_agent ON ai_agent_chats (agent_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_agent_sites (
                agent_id TEXT PRIMARY KEY,
                headline TEXT,
                subheadline TEXT,
                body TEXT,
                accent_color TEXT,
                contact_email TEXT,
                contact_phone TEXT,
                contact_address TEXT,
                contact_note TEXT,
                seo_description TEXT,
                footer_tagline TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Older databases created before contact_*/seo_description/
        # footer_tagline existed — SQLite has no "ADD COLUMN IF NOT
        # EXISTS", so check the schema and add whatever's missing instead
        # of forcing everyone to delete their data.
        existing_site_cols = {r["name"] for r in conn.execute("PRAGMA table_info(ai_agent_sites)").fetchall()}
        for col in ("contact_email", "contact_phone", "contact_address", "contact_note", "seo_description", "footer_tagline"):
            if col not in existing_site_cols:
                conn.execute(f"ALTER TABLE ai_agent_sites ADD COLUMN {col} TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_agent_edit_tokens (
                token TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_agent_edit_tokens_agent ON ai_agent_edit_tokens (agent_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_agent_pages (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                nav_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_agent_pages_slug ON ai_agent_pages (agent_id, slug)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_agent_posts (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                excerpt TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_agent_posts_slug ON ai_agent_posts (agent_id, slug)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_agent_leads (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_agent_leads_agent ON ai_agent_leads (agent_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_agent_images (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_agent_images_agent ON ai_agent_images (agent_id)")
        conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> dict:
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
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO ai_agents (id, name, description, connection_id, provider_key, scope_type, resource_id, "
            "resource_name, owner, public_token, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (agent_id, name, description, connection_id, provider_key, scope_type, resource_id,
             resource_name, owner, token, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ai_agents WHERE id = ?", (agent_id,)).fetchone()
    finally:
        conn.close()
    return _row(row)


def get(agent_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM ai_agents WHERE id = ?", (agent_id,)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def resolve_by_token(token: str) -> dict | None:
    """Looks up a public_token for the public chat route. Returns None for
    a token that doesn't exist or belongs to a deactivated agent — the
    visitor-facing response is the same "not available" either way."""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM ai_agents WHERE public_token = ?", (token,)).fetchone()
        if row is None or not row["is_active"]:
            return None
        return _row(row)
    finally:
        conn.close()


def list_for_resource(connection_id: str, resource_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ai_agents WHERE connection_id = ? AND resource_id = ? ORDER BY created_at DESC",
            (connection_id, resource_id),
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def list_for_owner(owner: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ai_agents WHERE owner = ? ORDER BY created_at DESC", (owner,)
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def list_all() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM ai_agents ORDER BY created_at DESC").fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def update(agent_id: str, *, name: str | None = None, description: str | None = None,
           is_active: bool | None = None) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM ai_agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            return None
        updates: list[tuple[str, object]] = []
        if name is not None:
            updates.append(("name", name))
        if description is not None:
            updates.append(("description", description))
        if is_active is not None:
            updates.append(("is_active", int(is_active)))
        if updates:
            updates.append(("updated_at", datetime.datetime.now(datetime.timezone.utc).isoformat()))
            set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
            conn.execute(f"UPDATE ai_agents SET {set_clause} WHERE id = ?", (*[v for _, v in updates], agent_id))
            conn.commit()
        row = conn.execute("SELECT * FROM ai_agents WHERE id = ?", (agent_id,)).fetchone()
        return _row(row)
    finally:
        conn.close()


def delete(agent_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM ai_agent_chats WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM ai_agent_leads WHERE agent_id = ?", (agent_id,))
        _delete_images_for_agent(conn, agent_id)
        conn.execute("DELETE FROM ai_agent_sites WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM ai_agent_edit_tokens WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM ai_agent_pages WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM ai_agent_posts WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM ai_agents WHERE id = ?", (agent_id,))
        conn.commit()
    finally:
        conn.close()


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    conn = _conn()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM ai_agents WHERE connection_id = ? AND resource_id = ?", (connection_id, resource_id)
        ).fetchall()]
        for aid in ids:
            conn.execute("DELETE FROM ai_agent_chats WHERE agent_id = ?", (aid,))
            conn.execute("DELETE FROM ai_agent_leads WHERE agent_id = ?", (aid,))
            _delete_images_for_agent(conn, aid)
            conn.execute("DELETE FROM ai_agent_sites WHERE agent_id = ?", (aid,))
            conn.execute("DELETE FROM ai_agent_edit_tokens WHERE agent_id = ?", (aid,))
            conn.execute("DELETE FROM ai_agent_pages WHERE agent_id = ?", (aid,))
            conn.execute("DELETE FROM ai_agent_posts WHERE agent_id = ?", (aid,))
        conn.execute("DELETE FROM ai_agents WHERE connection_id = ? AND resource_id = ?", (connection_id, resource_id))
        conn.commit()
    finally:
        conn.close()


def delete_for_connection(connection_id: str) -> None:
    conn = _conn()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM ai_agents WHERE connection_id = ?", (connection_id,)
        ).fetchall()]
        for aid in ids:
            conn.execute("DELETE FROM ai_agent_chats WHERE agent_id = ?", (aid,))
            conn.execute("DELETE FROM ai_agent_leads WHERE agent_id = ?", (aid,))
            _delete_images_for_agent(conn, aid)
            conn.execute("DELETE FROM ai_agent_sites WHERE agent_id = ?", (aid,))
            conn.execute("DELETE FROM ai_agent_edit_tokens WHERE agent_id = ?", (aid,))
            conn.execute("DELETE FROM ai_agent_pages WHERE agent_id = ?", (aid,))
            conn.execute("DELETE FROM ai_agent_posts WHERE agent_id = ?", (aid,))
        conn.execute("DELETE FROM ai_agents WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()


def record_chat(agent_id: str, *, actor: str | None, question: str,
                tokens_used: int | None, tokens_estimated: bool) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO ai_agent_chats (id, agent_id, actor, question, tokens_used, tokens_estimated, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, agent_id, actor, question[:2000], tokens_used, int(tokens_estimated),
             datetime.datetime.now(datetime.timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_stats(agent_id: str) -> dict:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS chat_count, COALESCE(SUM(tokens_used), 0) AS tokens_total, "
            "MAX(created_at) AS last_chat_at FROM ai_agent_chats WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        lead_count = conn.execute(
            "SELECT COUNT(*) AS lead_count FROM ai_agent_leads WHERE agent_id = ?", (agent_id,)
        ).fetchone()["lead_count"]
        return {
            "chat_count": row["chat_count"],
            "tokens_total": row["tokens_total"],
            "last_chat_at": row["last_chat_at"],
            "lead_count": lead_count,
        }
    finally:
        conn.close()


def list_recent_chats(agent_id: str, limit: int = 50) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ai_agent_chats WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
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
    finally:
        conn.close()


def create_lead(agent_id: str, *, email: str | None, phone: str | None, message: str) -> dict:
    """A visitor-submitted "contact us" lead from the public chat widget's
    scripted intake flow -- deliberately reachable with no auth/edit_token
    at all (same as a real website's contact form), gated only by the
    public rate limiter in routers/public_ai_agents.py."""
    conn = _conn()
    try:
        lead_id = uuid.uuid4().hex
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO ai_agent_leads (id, agent_id, email, phone, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (lead_id, agent_id, (email or None), (phone or None), message[:2000], now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": lead_id, "agent_id": agent_id, "email": email or None, "phone": phone or None,
            "message": message[:2000], "created_at": now}


def list_leads(agent_id: str, limit: int = 200) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ai_agent_leads WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"], "agent_id": r["agent_id"], "email": r["email"], "phone": r["phone"],
                "message": r["message"], "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


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
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO ai_agent_images (id, agent_id, content_type, size_bytes, created_at) VALUES (?, ?, ?, ?, ?)",
            (image_id, agent_id, content_type, len(data), now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": image_id, "agent_id": agent_id, "content_type": content_type, "size_bytes": len(data), "created_at": now}


def get_image(agent_id: str, image_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM ai_agent_images WHERE id = ? AND agent_id = ?", (image_id, agent_id)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_image_bytes(image_id: str) -> bytes | None:
    path = _IMAGES_DIR / image_id
    try:
        return path.read_bytes()
    except OSError:
        return None


def _delete_images_for_agent(conn: sqlite3.Connection, agent_id: str) -> None:
    """Removes both the DB rows and their on-disk files for one agent's
    images -- unlike every other child table here, images have a file on
    disk too, so a plain SQL DELETE alone would leak that file."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM ai_agent_images WHERE agent_id = ?", (agent_id,)).fetchall()]
    conn.execute("DELETE FROM ai_agent_images WHERE agent_id = ?", (agent_id,))
    for image_id in ids:
        (_IMAGES_DIR / image_id).unlink(missing_ok=True)


_SITE_FIELDS = (
    "headline", "subheadline", "body", "accent_color", "contact_email", "contact_phone", "contact_address",
    "contact_note", "seo_description", "footer_tagline",
)


def get_site(agent_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM ai_agent_sites WHERE agent_id = ?", (agent_id,)).fetchone()
        if row is None:
            return None
        return {**{f: row[f] for f in _SITE_FIELDS}, "updated_at": row["updated_at"]}
    finally:
        conn.close()


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
    conn = _conn()
    try:
        cols = ", ".join(_SITE_FIELDS)
        placeholders = ", ".join("?" for _ in _SITE_FIELDS)
        set_clause = ", ".join(f"{f}=excluded.{f}" for f in _SITE_FIELDS)
        conn.execute(
            f"INSERT INTO ai_agent_sites (agent_id, {cols}, updated_at) VALUES (?, {placeholders}, ?) "
            f"ON CONFLICT(agent_id) DO UPDATE SET {set_clause}, updated_at=excluded.updated_at",
            (agent_id, *[values[f] for f in _SITE_FIELDS], now),
        )
        conn.commit()
    finally:
        conn.close()
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
    conn = _conn()
    try:
        conn.execute("DELETE FROM ai_agent_sites WHERE agent_id = ?", (agent_id,))
        conn.commit()
    finally:
        conn.close()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "page"


def _unique_slug(conn: sqlite3.Connection, table: str, agent_id: str, title: str) -> str:
    base = _slugify(title)
    slug = base
    n = 2
    while conn.execute(f"SELECT 1 FROM {table} WHERE agent_id = ? AND slug = ?", (agent_id, slug)).fetchone():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _page_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "agent_id": row["agent_id"], "slug": row["slug"], "title": row["title"],
        "content": row["content"], "nav_order": row["nav_order"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def create_page(agent_id: str, title: str, content: str, nav_order: int = 0) -> dict:
    page_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        slug = _unique_slug(conn, "ai_agent_pages", agent_id, title)
        conn.execute(
            "INSERT INTO ai_agent_pages (id, agent_id, slug, title, content, nav_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (page_id, agent_id, slug, title, content, nav_order, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ai_agent_pages WHERE id = ?", (page_id,)).fetchone()
    finally:
        conn.close()
    return _page_row(row)


def list_pages(agent_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ai_agent_pages WHERE agent_id = ? ORDER BY nav_order, created_at", (agent_id,)
        ).fetchall()
        return [_page_row(r) for r in rows]
    finally:
        conn.close()


def get_page(agent_id: str, page_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM ai_agent_pages WHERE id = ? AND agent_id = ?", (page_id, agent_id)).fetchone()
        return _page_row(row) if row else None
    finally:
        conn.close()


def get_page_by_slug(agent_id: str, slug: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM ai_agent_pages WHERE agent_id = ? AND slug = ?", (agent_id, slug)).fetchone()
        return _page_row(row) if row else None
    finally:
        conn.close()


def update_page(agent_id: str, page_id: str, *, title: str | None = None, content: str | None = None,
                nav_order: int | None = None) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM ai_agent_pages WHERE id = ? AND agent_id = ?", (page_id, agent_id)).fetchone()
        if row is None:
            return None
        updates: list[tuple[str, object]] = []
        if title is not None:
            updates.append(("title", title))
        if content is not None:
            updates.append(("content", content))
        if nav_order is not None:
            updates.append(("nav_order", nav_order))
        if updates:
            updates.append(("updated_at", datetime.datetime.now(datetime.timezone.utc).isoformat()))
            set_clause = ", ".join(f"{c} = ?" for c, _ in updates)
            conn.execute(f"UPDATE ai_agent_pages SET {set_clause} WHERE id = ?", (*[v for _, v in updates], page_id))
            conn.commit()
        row = conn.execute("SELECT * FROM ai_agent_pages WHERE id = ?", (page_id,)).fetchone()
        return _page_row(row)
    finally:
        conn.close()


def delete_page(agent_id: str, page_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM ai_agent_pages WHERE id = ? AND agent_id = ?", (page_id, agent_id))
        conn.commit()
    finally:
        conn.close()


def _post_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "agent_id": row["agent_id"], "slug": row["slug"], "title": row["title"],
        "excerpt": row["excerpt"], "content": row["content"],
        "published_at": row["published_at"], "updated_at": row["updated_at"],
    }


def create_post(agent_id: str, title: str, content: str, excerpt: str = "") -> dict:
    post_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        slug = _unique_slug(conn, "ai_agent_posts", agent_id, title)
        conn.execute(
            "INSERT INTO ai_agent_posts (id, agent_id, slug, title, excerpt, content, published_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (post_id, agent_id, slug, title, excerpt, content, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ai_agent_posts WHERE id = ?", (post_id,)).fetchone()
    finally:
        conn.close()
    return _post_row(row)


def list_posts(agent_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ai_agent_posts WHERE agent_id = ? ORDER BY published_at DESC", (agent_id,)
        ).fetchall()
        return [_post_row(r) for r in rows]
    finally:
        conn.close()


def get_post(agent_id: str, post_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM ai_agent_posts WHERE id = ? AND agent_id = ?", (post_id, agent_id)).fetchone()
        return _post_row(row) if row else None
    finally:
        conn.close()


def get_post_by_slug(agent_id: str, slug: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM ai_agent_posts WHERE agent_id = ? AND slug = ?", (agent_id, slug)).fetchone()
        return _post_row(row) if row else None
    finally:
        conn.close()


def update_post(agent_id: str, post_id: str, *, title: str | None = None, content: str | None = None,
                excerpt: str | None = None) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM ai_agent_posts WHERE id = ? AND agent_id = ?", (post_id, agent_id)).fetchone()
        if row is None:
            return None
        updates: list[tuple[str, object]] = []
        if title is not None:
            updates.append(("title", title))
        if content is not None:
            updates.append(("content", content))
        if excerpt is not None:
            updates.append(("excerpt", excerpt))
        if updates:
            updates.append(("updated_at", datetime.datetime.now(datetime.timezone.utc).isoformat()))
            set_clause = ", ".join(f"{c} = ?" for c, _ in updates)
            conn.execute(f"UPDATE ai_agent_posts SET {set_clause} WHERE id = ?", (*[v for _, v in updates], post_id))
            conn.commit()
        row = conn.execute("SELECT * FROM ai_agent_posts WHERE id = ?", (post_id,)).fetchone()
        return _post_row(row)
    finally:
        conn.close()


def delete_post(agent_id: str, post_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM ai_agent_posts WHERE id = ? AND agent_id = ?", (post_id, agent_id))
        conn.commit()
    finally:
        conn.close()


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
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO ai_agent_edit_tokens (token, agent_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, agent_id, now.isoformat(), expires_at),
        )
        conn.commit()
    finally:
        conn.close()
    return token, expires_at


def resolve_edit_token(token: str, agent_id: str) -> bool:
    """True if `token` is valid, unexpired, and scoped to this exact agent."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT expires_at FROM ai_agent_edit_tokens WHERE token = ? AND agent_id = ?", (token, agent_id)
        ).fetchone()
        if row is None:
            return False
        return datetime.datetime.fromisoformat(row["expires_at"]) > datetime.datetime.now(datetime.timezone.utc)
    finally:
        conn.close()
