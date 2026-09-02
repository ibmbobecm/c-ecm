"""Authenticated AI Agents API.

POST   /ai-agents                 — create an agent scoped to a folder or file
GET    /ai-agents                 — list the current user's own agents (with usage stats)
GET    /ai-agents/{id}            — get one (owner or admin)
PATCH  /ai-agents/{id}            — rename / toggle active (owner or admin)
DELETE /ai-agents/{id}            — delete (owner or admin)
POST   /ai-agents/{id}/chat       — chat with an agent while logged into C-ECM
GET    /ai-agents/{id}/leads      — "contact us" submissions captured by the public widget (owner or admin)
GET    /admin/ai-agents/report    — admin-only: every agent, every user, with usage stats

The public, unauthenticated equivalent of the chat endpoint (for the
embeddable widget) lives in routers/public_ai_agents.py.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import access_control, activity_service, ai_agents_service, ai_agents_store, groups_store
from ..access_helpers import to_http
from ..auth import CurrentSession, CurrentUser, get_current_session, get_current_user, require_feature
from ..config import API_BASE_URL
from ..schemas import (
    AiAgentChatOut,
    AiAgentChatRequest,
    AiAgentCreateRequest,
    AiAgentEditTokenOut,
    AiAgentLeadOut,
    AiAgentOut,
    AiAgentPageCreateRequest,
    AiAgentPageOut,
    AiAgentPageUpdateRequest,
    AiAgentPostCreateRequest,
    AiAgentPostOut,
    AiAgentPostUpdateRequest,
    AiAgentSiteDraftOut,
    AiAgentSiteOut,
    AiAgentSiteUpdateRequest,
    AiAgentStatsOut,
    AiAgentUpdateRequest,
)
from ..storage_providers.base import ProviderError

router = APIRouter(prefix="/ai-agents", tags=["ai-agents"])
admin_router = APIRouter(prefix="/admin/ai-agents", tags=["ai-agents-admin"])

_admin = require_feature("manage_ai_agents_admin")


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or session.creds.get("username") or "unknown"


def _urls(agent: dict) -> dict:
    return {
        "chat_url": f"{API_BASE_URL}/public/chat/{agent['public_token']}",
        "embed_url": f"{API_BASE_URL}/public/chat/{agent['public_token']}",
        "demo_url": f"{API_BASE_URL}/public/demo/{agent['public_token']}",
        "demo_download_url": f"{API_BASE_URL}/public/demo/{agent['public_token']}/download",
    }


def _out(agent: dict) -> AiAgentOut:
    return AiAgentOut(**agent, **_urls(agent))


def _stats_out(agent: dict) -> AiAgentStatsOut:
    stats = ai_agents_store.get_stats(agent["id"])
    return AiAgentStatsOut(
        **agent,
        **_urls(agent),
        chat_count=stats["chat_count"],
        tokens_total=stats["tokens_total"],
        last_chat_at=stats["last_chat_at"],
        lead_count=stats["lead_count"],
    )


def _resource_name(session: CurrentSession, scope_type: str, resource_id: str, fallback: str) -> str:
    try:
        if scope_type == "file":
            return session.provider.get_file(session.creds, resource_id).name
        contents = session.provider.get_children(session.creds, resource_id)
        return contents.folder.name if contents.folder else fallback
    except Exception:
        return fallback


def _get_owned_or_admin(agent_id: str, user: dict) -> dict:
    agent = ai_agents_store.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    is_admin = user.get("is_superadmin") or groups_store.user_has_feature(user["id"], "manage_ai_agents_admin")
    if agent["owner"] != user.get("username") and not is_admin:
        raise HTTPException(status_code=403, detail="Only the agent's owner or an admin can manage it")
    return agent


@router.post("", response_model=AiAgentOut, status_code=201)
def create_agent(req: AiAgentCreateRequest, session: CurrentSession = Depends(get_current_session)):
    access_control.require_resource_level(session, req.resource_id, req.scope_type, "edit")
    name = _resource_name(session, req.scope_type, req.resource_id, req.resource_name or "Untitled")
    agent = ai_agents_store.create(
        name=req.name,
        description=req.description,
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        scope_type=req.scope_type,
        resource_id=req.resource_id,
        resource_name=name,
        owner=_actor(session),
    )
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type=req.scope_type,
        resource_id=req.resource_id,
        resource_name=name,
        event_type="ai_agent_created",
        actor=_actor(session),
        payload={"agent_id": agent["id"], "agent_name": req.name},
    )
    return _out(agent)


@router.get("", response_model=list[AiAgentStatsOut])
def list_my_agents(
    resource_id: str | None = Query(default=None),
    session: CurrentSession = Depends(get_current_session),
):
    """With resource_id: every agent (any owner) scoped to that exact
    folder/file within the current connection — matches the "existing
    links for this item" list pattern share links already use. Without
    it: just the current user's own agents, for a "my agents" view."""
    if resource_id is not None:
        agents = ai_agents_store.list_for_resource(session.connection_id, resource_id)
    else:
        agents = ai_agents_store.list_for_owner(session.user["username"])
    return [_stats_out(a) for a in agents]


@router.get("/{agent_id}", response_model=AiAgentStatsOut)
def get_agent(agent_id: str, user: CurrentUser = Depends(get_current_user)):
    agent = _get_owned_or_admin(agent_id, user)
    return _stats_out(agent)


@router.patch("/{agent_id}", response_model=AiAgentStatsOut)
def update_agent(agent_id: str, req: AiAgentUpdateRequest, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    updated = ai_agents_store.update(agent_id, name=req.name, description=req.description, is_active=req.is_active)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _stats_out(updated)


@router.delete("/{agent_id}", status_code=204)
def delete_agent(agent_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    ai_agents_store.delete(agent_id)


@router.get("/{agent_id}/leads", response_model=list[AiAgentLeadOut])
def list_agent_leads(agent_id: str, user: CurrentUser = Depends(get_current_user)):
    """The "contact us" submissions captured by the public chat widget's
    scripted intake flow (see routers/public_ai_agents.py's POST .../leads)
    — visible to the agent's owner or an admin, same gate as every other
    per-agent detail endpoint above."""
    _get_owned_or_admin(agent_id, user)
    return [AiAgentLeadOut(**lead) for lead in ai_agents_store.list_leads(agent_id)]


@router.get("/{agent_id}/site", response_model=AiAgentSiteOut)
def get_agent_site(agent_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    site = ai_agents_store.get_site(agent_id)
    return AiAgentSiteOut(**site) if site else AiAgentSiteOut()


@router.patch("/{agent_id}/site", response_model=AiAgentSiteOut)
def update_agent_site(agent_id: str, req: AiAgentSiteUpdateRequest, user: CurrentUser = Depends(get_current_user)):
    """Customizes the public demo site (GET /public/demo/{token}) — the
    landing page wrapped around the agent's chat widget that's "deployable
    on any server": headline/subheadline/body/accent color only. The
    agent's own name/description (used as defaults here) are managed via
    PATCH /ai-agents/{id} above.

    A field left out of the request body entirely keeps its previously
    saved value — only a field actually present in the JSON body (even as
    null, to intentionally reset it) is written, so a client updating just
    one field can't silently wipe the others."""
    _get_owned_or_admin(agent_id, user)
    site = ai_agents_store.merge_site_update(agent_id, req.model_dump(exclude_unset=True))
    return AiAgentSiteOut(**site)


@router.post("/{agent_id}/edit-token", response_model=AiAgentEditTokenOut)
def create_agent_edit_token(agent_id: str, user: CurrentUser = Depends(get_current_user)):
    """Mints a short-lived (20 min), single-agent-scoped token the frontend
    appends to the demo-site URL when opening it in a new tab ("Open test
    site") — this is what lets the public demo page show a WordPress-style
    admin bar (Customize / Download) to the owner/admin who just navigated
    there from the authenticated app, without ever putting the owner's real
    session JWT in a URL (which would sit in browser history and referrer
    headers for much longer than 20 minutes)."""
    _get_owned_or_admin(agent_id, user)
    token, expires_at = ai_agents_store.create_edit_token(agent_id)
    return AiAgentEditTokenOut(edit_token=token, expires_at=expires_at)


@router.get("/{agent_id}/site/pages", response_model=list[AiAgentPageOut])
def list_agent_pages(agent_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    return [AiAgentPageOut(**p) for p in ai_agents_store.list_pages(agent_id)]


@router.post("/{agent_id}/site/pages", response_model=AiAgentPageOut, status_code=201)
def create_agent_page(agent_id: str, req: AiAgentPageCreateRequest, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    return AiAgentPageOut(**ai_agents_store.create_page(agent_id, req.title, req.content, req.nav_order))


@router.patch("/{agent_id}/site/pages/{page_id}", response_model=AiAgentPageOut)
def update_agent_page(agent_id: str, page_id: str, req: AiAgentPageUpdateRequest, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    updated = ai_agents_store.update_page(agent_id, page_id, title=req.title, content=req.content, nav_order=req.nav_order)
    if updated is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return AiAgentPageOut(**updated)


@router.delete("/{agent_id}/site/pages/{page_id}", status_code=204)
def delete_agent_page(agent_id: str, page_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    ai_agents_store.delete_page(agent_id, page_id)


@router.get("/{agent_id}/site/posts", response_model=list[AiAgentPostOut])
def list_agent_posts(agent_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    return [AiAgentPostOut(**p) for p in ai_agents_store.list_posts(agent_id)]


@router.post("/{agent_id}/site/posts", response_model=AiAgentPostOut, status_code=201)
def create_agent_post(agent_id: str, req: AiAgentPostCreateRequest, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    return AiAgentPostOut(**ai_agents_store.create_post(agent_id, req.title, req.content, req.excerpt))


@router.patch("/{agent_id}/site/posts/{post_id}", response_model=AiAgentPostOut)
def update_agent_post(agent_id: str, post_id: str, req: AiAgentPostUpdateRequest, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    updated = ai_agents_store.update_post(agent_id, post_id, title=req.title, content=req.content, excerpt=req.excerpt)
    if updated is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return AiAgentPostOut(**updated)


@router.delete("/{agent_id}/site/posts/{post_id}", status_code=204)
def delete_agent_post(agent_id: str, post_id: str, user: CurrentUser = Depends(get_current_user)):
    _get_owned_or_admin(agent_id, user)
    ai_agents_store.delete_post(agent_id, post_id)


@router.post("/{agent_id}/site/generate", response_model=AiAgentSiteDraftOut)
def generate_agent_site(agent_id: str, session: CurrentSession = Depends(get_current_session)):
    """Drafts a full site's worth of copy (headline, about text, a few
    topic pages, a few blog posts) from the agent's own knowledge base —
    reviewed and selectively applied by the admin via the page/post CRUD
    endpoints above, never auto-published. Owner/admin-only (like the rest
    of site management) and costs real LLM tokens, unlike plain chat."""
    agent = _get_owned_or_admin(agent_id, session.user)
    if agent["connection_id"] != session.connection_id:
        raise HTTPException(status_code=400, detail="Select this agent's connection first")
    try:
        draft, sources, tokens, estimated, error = ai_agents_service.generate_site_draft(
            session.provider, session.creds, session.connection_id, agent["scope_type"], agent["resource_id"],
        )
    except ProviderError as exc:
        raise to_http(exc)
    if error:
        raise HTTPException(status_code=422, detail=error)
    return AiAgentSiteDraftOut(**draft, sources=sources, tokens_used=tokens, tokens_estimated=estimated)


@router.post("/{agent_id}/chat", response_model=AiAgentChatOut)
def chat_with_agent(agent_id: str, req: AiAgentChatRequest, session: CurrentSession = Depends(get_current_session)):
    """Any authenticated user with this connection selected can chat with an
    active agent — matching the app's general model where a connection's
    content is usable by anyone who can see it, not just the resource's
    owner. Managing the agent itself (rename/deactivate/delete) is
    owner/admin-only — see the endpoints above."""
    agent = ai_agents_store.get(agent_id)
    if agent is None or not agent["is_active"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent["connection_id"] != session.connection_id:
        raise HTTPException(status_code=400, detail="Select this agent's connection first")
    try:
        answer, sources, tokens, estimated = ai_agents_service.answer(
            session.provider, session.creds, session.connection_id, agent["scope_type"], agent["resource_id"], req.question,
        )
    except ProviderError as exc:
        raise to_http(exc)
    ai_agents_store.record_chat(agent_id, actor=_actor(session), question=req.question, tokens_used=tokens, tokens_estimated=estimated)
    activity_service.record_event(
        connection_id=session.connection_id, provider_key=session.provider_key,
        resource_type=agent["scope_type"], resource_id=agent["resource_id"], resource_name=agent["resource_name"],
        event_type="ai_agent_chat", actor=_actor(session),
        payload={"agent_id": agent_id, "agent_name": agent["name"], "tokens_used": tokens},
    )
    return AiAgentChatOut(answer=answer, sources=sources, tokens_used=tokens, tokens_estimated=estimated)


@admin_router.get("/report", response_model=list[AiAgentStatsOut])
def admin_report(_user: CurrentUser = Depends(_admin)):
    return [_stats_out(a) for a in ai_agents_store.list_all()]
