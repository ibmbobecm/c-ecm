"""Public, unauthenticated AI Agent access — the piece that makes an
agent's chat URL something "anyone can use to post on their site or
application", per the feature's whole point. Two ways in, both scoped by
the same unguessable public_token (same trust model as share_links_store):

  GET  /public/chat/{token}            — a small standalone HTML+JS chat
                                          page, meant to be <iframe>-embedded
                                          on any external site. Same-origin
                                          to the API below, so it needs no
                                          CORS at all.
  GET  /public/ai-agents/{token}       — JSON: the agent's public-safe info
  POST /public/ai-agents/{token}/chat  — JSON: ask a question, get an answer
  POST /public/ai-agents/{token}/leads — JSON: submit a "contact us" lead from the widget's intake flow
  POST /public/ai-agents/{token}/site/edit — JSON: apply one instruction-driven, surgical site edit
  POST /public/ai-agents/{token}/site/images — multipart: upload an image for the pencil editor
  GET  /public/ai-agents/{token}/site/images/{id} — serves an uploaded image, no edit_token needed
  POST /public/ai-agents/{token}/site/draft-item — JSON: draft ONE new page/post from a topic, unsaved

The JSON API is mounted as its own small sub-application (see main.py)
with a permissive CORS policy, so a site that wants to build its own
custom widget can call it directly with fetch() from JS running on their
own origin — deliberately more open than the rest of this app's CORS,
which stays LAN-only; this endpoint's public token, not an origin
allowlist, is what scopes access here, exactly like a public share link.
"""

import html
import io
import threading
import time
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from PIL import Image

from .. import activity_service, ai_agents_service, ai_agents_store, connections_store
from ..ai_agent_site_render import (
    Links,
    render_admin_bar,
    render_blog_index,
    render_contact,
    render_home,
    render_page,
    render_post,
    render_static_site,
    slugify,
)
from ..config import API_BASE_URL
from ..schemas import (
    AiAgentChatOut,
    AiAgentChatRequest,
    AiAgentImageOut,
    AiAgentItemDraftOut,
    AiAgentLeadCreateRequest,
    AiAgentLeadOut,
    AiAgentPageOut,
    AiAgentPostOut,
    AiAgentSiteOut,
    AiAgentSitePublishedOut,
    AiAgentTargetedEditResultOut,
    PublicAiAgentItemDraftRequest,
    PublicAiAgentOut,
    PublicAiAgentPageCreateRequest,
    PublicAiAgentPageUpdateRequest,
    PublicAiAgentPostCreateRequest,
    PublicAiAgentPostUpdateRequest,
    PublicAiAgentSiteUpdateRequest,
    PublicAiAgentTargetedEditRequest,
)
from ..storage_providers.base import ProviderError
from ..storage_providers.registry import get_provider


# Public API (mounted separately with permissive CORS — see main.py)
router = APIRouter(tags=["ai-agents-public"])

# Public widget page (mounted on the main app — a plain page load, no CORS needed)
page_router = APIRouter(tags=["ai-agents-public-page"])

# This endpoint calls a real, potentially paid, LLM API on every request and
# has no login in front of it — an unbounded chat loop against someone's
# watsonx/OpenAI account is a real cost/abuse vector, not just a theoretical
# one. Same in-memory sliding-window shape as sharing.py's link rate limit.
_RATE_LOCK = threading.Lock()
_recent_requests: dict[str, list[float]] = {}
_MAX_REQUESTS = 20
_WINDOW_SECONDS = 60


def _check_rate_limit(token: str) -> None:
    now = time.monotonic()
    with _RATE_LOCK:
        recent = [t for t in _recent_requests.get(token, []) if now - t < _WINDOW_SECONDS]
        if len(recent) >= _MAX_REQUESTS:
            _recent_requests[token] = recent
            raise HTTPException(status_code=429, detail="This agent is receiving too many questions right now — try again in a minute")
        recent.append(now)
        _recent_requests[token] = recent


# A separate, stricter limiter for lead submissions -- these write a
# "real" business record (a sales lead), not an ephemeral chat exchange,
# so they're worth protecting from spam more tightly than a chat burst.
_LEAD_RATE_LOCK = threading.Lock()
_recent_lead_requests: dict[str, list[float]] = {}
_MAX_LEAD_REQUESTS = 5
_LEAD_WINDOW_SECONDS = 300


def _check_lead_rate_limit(token: str) -> None:
    now = time.monotonic()
    with _LEAD_RATE_LOCK:
        recent = [t for t in _recent_lead_requests.get(token, []) if now - t < _LEAD_WINDOW_SECONDS]
        if len(recent) >= _MAX_LEAD_REQUESTS:
            _recent_lead_requests[token] = recent
            raise HTTPException(status_code=429, detail="Too many submissions right now — please try again in a few minutes")
        recent.append(now)
        _recent_lead_requests[token] = recent


# A separate limiter for image uploads -- these write a file to disk
# (not just a DB row), so a bit stricter than the lead limiter to bound
# a disk-fill abuse vector.
_IMAGE_RATE_LOCK = threading.Lock()
_recent_image_requests: dict[str, list[float]] = {}
_MAX_IMAGE_REQUESTS = 20
_IMAGE_WINDOW_SECONDS = 600
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

_IMAGE_CONTENT_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}


def _check_image_rate_limit(token: str) -> None:
    now = time.monotonic()
    with _IMAGE_RATE_LOCK:
        recent = [t for t in _recent_image_requests.get(token, []) if now - t < _IMAGE_WINDOW_SECONDS]
        if len(recent) >= _MAX_IMAGE_REQUESTS:
            _recent_image_requests[token] = recent
            raise HTTPException(status_code=429, detail="Too many uploads right now — please try again in a few minutes")
        recent.append(now)
        _recent_image_requests[token] = recent


def _validate_image(data: bytes) -> str | None:
    """Returns a safe, canonical content-type if `data` is a genuine,
    supported image; None otherwise. Never trusts the browser-supplied
    Content-Type or filename -- both are trivial to spoof -- verifies
    the actual file signature instead."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
            fmt = (img.format or "").upper()
    except Exception:
        return None
    return _IMAGE_CONTENT_TYPES.get(fmt)


def _resolve(token: str) -> dict:
    agent = ai_agents_store.resolve_by_token(token)
    if agent is None:
        raise HTTPException(status_code=404, detail="This AI agent is unavailable or has been deactivated")
    return agent


@router.get("/{token}", response_model=PublicAiAgentOut)
def get_public_agent(token: str):
    agent = _resolve(token)
    return PublicAiAgentOut(name=agent["name"], description=agent["description"])


@router.post("/{token}/chat", response_model=AiAgentChatOut)
def public_chat(token: str, req: AiAgentChatRequest):
    agent = _resolve(token)
    _check_rate_limit(token)

    creds_entry = connections_store.get_creds(agent["connection_id"])
    if creds_entry is None:
        raise HTTPException(status_code=404, detail="The connection behind this agent no longer exists")
    provider_key, creds = creds_entry
    try:
        provider = get_provider(provider_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="The backend behind this agent is no longer available")

    try:
        answer, sources, tokens, estimated = ai_agents_service.answer(
            provider, creds, agent["connection_id"], agent["scope_type"], agent["resource_id"], req.question,
        )
    except ProviderError:
        raise HTTPException(status_code=502, detail="Couldn't reach this agent's document source right now")

    ai_agents_store.record_chat(agent["id"], actor=None, question=req.question, tokens_used=tokens, tokens_estimated=estimated)
    activity_service.record_event(
        connection_id=agent["connection_id"], provider_key=agent["provider_key"],
        resource_type=agent["scope_type"], resource_id=agent["resource_id"], resource_name=agent["resource_name"],
        event_type="ai_agent_chat", actor="public",
        payload={"agent_id": agent["id"], "agent_name": agent["name"], "tokens_used": tokens, "public": True},
    )
    return AiAgentChatOut(answer=answer, sources=sources, tokens_used=tokens, tokens_estimated=estimated)


@router.post("/{token}/leads", response_model=AiAgentLeadOut, status_code=201)
def public_create_lead(token: str, req: AiAgentLeadCreateRequest):
    """Powers the floating chat widget's "Contact us" scripted intake flow
    (see ai_agent_site_render.py's _chat_widget) -- a real "contact us"
    submission, deliberately reachable with no auth/edit_token at all
    (same trust model as a normal website's contact form), gated only by
    the stricter lead-specific rate limit above. Visible to the agent's
    owner/admin via GET /ai-agents/{id}/leads."""
    agent = _resolve(token)
    _check_lead_rate_limit(token)
    lead = ai_agents_store.create_lead(agent["id"], email=req.email, phone=req.phone, message=req.message)
    activity_service.record_event(
        connection_id=agent["connection_id"], provider_key=agent["provider_key"],
        resource_type=agent["scope_type"], resource_id=agent["resource_id"], resource_name=agent["resource_name"],
        event_type="ai_agent_lead_captured", actor="public",
        payload={"agent_id": agent["id"], "agent_name": agent["name"], "has_email": bool(req.email), "has_phone": bool(req.phone)},
    )
    return AiAgentLeadOut(**lead)


@router.patch("/{token}/site", response_model=AiAgentSiteOut)
def public_update_site(token: str, req: PublicAiAgentSiteUpdateRequest):
    """Powers the admin bar's "Save changes" button on the public demo page
    (see _admin_bar's fetch() call) — reachable with no C-ECM login at
    all, gated entirely by req.edit_token matching this exact agent (see
    create_edit_token in routers/ai_agents.py). Same merge-safe partial-
    update behavior as the authenticated PATCH /ai-agents/{id}/site."""
    agent = _resolve(token)
    if not ai_agents_store.resolve_edit_token(req.edit_token, agent["id"]):
        raise HTTPException(status_code=403, detail="This edit link has expired — reopen the test site from C-ECM")
    provided = req.model_dump(exclude_unset=True, exclude={"edit_token"})
    site = ai_agents_store.merge_site_update(agent["id"], provided)
    return AiAgentSiteOut(**site)


def _require_edit_token(agent_id: str, edit_token: str) -> None:
    if not ai_agents_store.resolve_edit_token(edit_token, agent_id):
        raise HTTPException(status_code=403, detail="This edit link has expired — reopen the test site from C-ECM")


@router.post("/{token}/site/pages", response_model=AiAgentPageOut, status_code=201)
def public_create_page(token: str, req: PublicAiAgentPageCreateRequest):
    agent = _resolve(token)
    _require_edit_token(agent["id"], req.edit_token)
    return AiAgentPageOut(**ai_agents_store.create_page(agent["id"], req.title, req.content, req.nav_order))


@router.patch("/{token}/site/pages/{page_id}", response_model=AiAgentPageOut)
def public_update_page(token: str, page_id: str, req: PublicAiAgentPageUpdateRequest):
    agent = _resolve(token)
    _require_edit_token(agent["id"], req.edit_token)
    updated = ai_agents_store.update_page(agent["id"], page_id, title=req.title, content=req.content, nav_order=req.nav_order)
    if updated is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return AiAgentPageOut(**updated)


@router.delete("/{token}/site/pages/{page_id}", status_code=204)
def public_delete_page(token: str, page_id: str, edit_token: str):
    agent = _resolve(token)
    _require_edit_token(agent["id"], edit_token)
    ai_agents_store.delete_page(agent["id"], page_id)


@router.post("/{token}/site/posts", response_model=AiAgentPostOut, status_code=201)
def public_create_post(token: str, req: PublicAiAgentPostCreateRequest):
    agent = _resolve(token)
    _require_edit_token(agent["id"], req.edit_token)
    return AiAgentPostOut(**ai_agents_store.create_post(agent["id"], req.title, req.content, req.excerpt))


@router.patch("/{token}/site/posts/{post_id}", response_model=AiAgentPostOut)
def public_update_post(token: str, post_id: str, req: PublicAiAgentPostUpdateRequest):
    agent = _resolve(token)
    _require_edit_token(agent["id"], req.edit_token)
    updated = ai_agents_store.update_post(agent["id"], post_id, title=req.title, content=req.content, excerpt=req.excerpt)
    if updated is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return AiAgentPostOut(**updated)


@router.delete("/{token}/site/posts/{post_id}", status_code=204)
def public_delete_post(token: str, post_id: str, edit_token: str):
    agent = _resolve(token)
    _require_edit_token(agent["id"], edit_token)
    ai_agents_store.delete_post(agent["id"], post_id)


@router.post("/{token}/site/images", response_model=AiAgentImageOut, status_code=201)
async def public_upload_image(token: str, edit_token: str = Form(...), file: UploadFile = File(...)):
    """Powers the pencil editor's image-upload modal (see
    ai_agent_site_render.py's render_admin_bar) -- a real local-file
    upload, not just "paste a URL". The returned url is what's handed
    straight to Quill's insertEmbed()."""
    agent = _resolve(token)
    _require_edit_token(agent["id"], edit_token)
    _check_image_rate_limit(token)

    data = await file.read()
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large — please use a file under 5 MB")
    content_type = _validate_image(data)
    if content_type is None:
        raise HTTPException(status_code=422, detail="That doesn't look like a supported image (PNG, JPEG, GIF, or WEBP)")

    image = ai_agents_store.create_image(agent["id"], content_type, data)
    return AiAgentImageOut(
        id=image["id"], content_type=image["content_type"], size_bytes=image["size_bytes"],
        url=f"{API_BASE_URL}/public/ai-agents/{token}/site/images/{image['id']}",
    )


@router.get("/{token}/site/images/{image_id}")
def public_get_image(token: str, image_id: str):
    """Serves one uploaded image -- deliberately public with no
    edit_token needed (unlike every write above): a site's ordinary
    visitors need to load these images too, not just its admin, exactly
    like every other piece of published site content."""
    agent = _resolve(token)
    image = ai_agents_store.get_image(agent["id"], image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    data = ai_agents_store.get_image_bytes(image_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return Response(content=data, media_type=image["content_type"], headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.post("/{token}/site/generate", response_model=AiAgentSitePublishedOut)
def public_generate_and_publish(token: str, edit_token: str):
    """The live admin bar's one-click "Generate & publish" action: unlike
    the authenticated /ai-agents/{id}/site/generate endpoint (which hands
    a draft back to the app's React UI for the admin to review and apply
    piece by piece), there's no practical place to build that same
    per-item review UI in a server-rendered admin bar — so this drafts
    AND immediately creates every page/post and updates the site fields
    in one action, on the reasoning that anything unwanted is one click
    away to edit or delete via Manage pages / Manage blog afterward."""
    agent = _resolve(token)
    _require_edit_token(agent["id"], edit_token)

    creds_entry = connections_store.get_creds(agent["connection_id"])
    if creds_entry is None:
        raise HTTPException(status_code=404, detail="The connection behind this agent no longer exists")
    provider_key, creds = creds_entry
    try:
        provider = get_provider(provider_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="The backend behind this agent is no longer available")

    try:
        draft, _sources, tokens, estimated, error = ai_agents_service.generate_site_draft(
            provider, creds, agent["connection_id"], agent["scope_type"], agent["resource_id"],
        )
    except ProviderError:
        raise HTTPException(status_code=502, detail="Couldn't reach this agent's document source right now")
    if error:
        raise HTTPException(status_code=422, detail=error)

    counts = ai_agents_service.apply_site_draft(agent["id"], draft)
    return AiAgentSitePublishedOut(
        pages_created=counts["pages_created"], posts_created=counts["posts_created"],
        tokens_used=tokens, tokens_estimated=estimated,
    )


@router.post("/{token}/site/edit", response_model=AiAgentTargetedEditResultOut)
def public_targeted_edit(token: str, req: PublicAiAgentTargetedEditRequest):
    """The live admin bar's free-form "describe a specific change" box —
    a surgical alternative to "Regenerate & publish full site" above: the
    instruction is read alongside the site's current content and the
    knowledge base, and only the site fields / pages / posts it actually
    names get touched. Everything else is left exactly as it was."""
    agent = _resolve(token)
    _require_edit_token(agent["id"], req.edit_token)

    creds_entry = connections_store.get_creds(agent["connection_id"])
    if creds_entry is None:
        raise HTTPException(status_code=404, detail="The connection behind this agent no longer exists")
    provider_key, creds = creds_entry
    try:
        provider = get_provider(provider_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="The backend behind this agent is no longer available")

    try:
        patch, _sources, tokens, estimated, error = ai_agents_service.generate_targeted_edit(
            provider, creds, agent["connection_id"], agent["scope_type"], agent["resource_id"],
            agent["id"], req.instruction,
        )
    except ProviderError:
        raise HTTPException(status_code=502, detail="Couldn't reach this agent's document source right now")
    if error:
        raise HTTPException(status_code=422, detail=error)

    result = ai_agents_service.apply_targeted_edit_patch(agent["id"], patch)
    return AiAgentTargetedEditResultOut(
        summary=result["summary"], site_updated=result["site_updated"],
        pages_updated=result["pages_updated"], posts_updated=result["posts_updated"],
        tokens_used=tokens, tokens_estimated=estimated,
    )


@router.post("/{token}/site/draft-item", response_model=AiAgentItemDraftOut)
def public_draft_item(token: str, req: PublicAiAgentItemDraftRequest):
    """Powers the "Add a page"/"Add a post" forms' own "Generate with AI"
    button -- drafts ONE new item from the knowledge base (steered by
    whatever's typed in the Title field as an optional topic hint) for
    the admin to review before clicking Add. Nothing is saved here."""
    agent = _resolve(token)
    _require_edit_token(agent["id"], req.edit_token)

    creds_entry = connections_store.get_creds(agent["connection_id"])
    if creds_entry is None:
        raise HTTPException(status_code=404, detail="The connection behind this agent no longer exists")
    provider_key, creds = creds_entry
    try:
        provider = get_provider(provider_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="The backend behind this agent is no longer available")

    try:
        draft, _sources, _tokens, _estimated, error = ai_agents_service.generate_item_draft(
            provider, creds, agent["connection_id"], agent["scope_type"], agent["resource_id"], req.kind, req.topic,
        )
    except ProviderError:
        raise HTTPException(status_code=502, detail="Couldn't reach this agent's document source right now")
    if error:
        raise HTTPException(status_code=422, detail=error)
    return AiAgentItemDraftOut(**draft)


def _maybe_auto_generate(agent: dict, edit_token: str | None) -> None:
    """The first time the site's admin opens their own test site, build the
    whole thing automatically from the knowledge base — no separate manual
    "Generate" click required. Only ever fires once: any saved headline/
    subheadline/body, or any existing page or post, makes the site no
    longer "empty", so this can never overwrite real admin edits or a
    previous generation. Silently does nothing on any failure (missing AI
    config, unreachable connection, empty knowledge base, ...) so a first
    visit never breaks — the admin can still fall back to "Regenerate with
    AI" in the admin bar once the underlying issue is fixed."""
    if not edit_token:
        return
    site = ai_agents_store.get_site(agent["id"])
    if site and (site.get("headline") or site.get("subheadline") or site.get("body")):
        return
    if ai_agents_store.list_pages(agent["id"]) or ai_agents_store.list_posts(agent["id"]):
        return

    creds_entry = connections_store.get_creds(agent["connection_id"])
    if creds_entry is None:
        return
    provider_key, creds = creds_entry
    try:
        provider = get_provider(provider_key)
    except KeyError:
        return

    try:
        draft, _sources, _tokens, _estimated, error = ai_agents_service.generate_site_draft(
            provider, creds, agent["connection_id"], agent["scope_type"], agent["resource_id"],
        )
    except ProviderError:
        return
    if error or not draft:
        return
    ai_agents_service.apply_site_draft(agent["id"], draft)


@page_router.get("/chat/{token}", response_class=HTMLResponse)
def public_chat_widget(token: str):
    agent = ai_agents_store.resolve_by_token(token)
    if agent is None:
        return HTMLResponse(
            "<!doctype html><html><body style=\"font-family:sans-serif;padding:40px;text-align:center;color:#57606a\">"
            "This AI agent is unavailable or has been deactivated.</body></html>",
            status_code=404,
        )
    name = html.escape(agent["name"])
    description = html.escape(agent["description"] or f"Ask a question about {html.escape(agent['resource_name'])}.")
    safe_token = html.escape(token, quote=True)
    return HTMLResponse(f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{name}</title>
<style>
  html, body {{ margin: 0; padding: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f8fa; }}
  .wrap {{ display: flex; flex-direction: column; height: 100vh; box-sizing: border-box; }}
  .header {{ padding: 14px 16px; border-bottom: 1px solid #d0d7de; background: #fff; }}
  .header h1 {{ margin: 0; font-size: 15px; font-weight: 700; color: #24292f; }}
  .header p {{ margin: 2px 0 0; font-size: 12px; color: #57606a; }}
  .messages {{ flex: 1; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }}
  .msg {{ max-width: 88%; padding: 8px 12px; border-radius: 10px; font-size: 13px; line-height: 1.5; white-space: pre-wrap; }}
  .msg.user {{ align-self: flex-end; background: #0969da; color: #fff; }}
  .msg.bot {{ align-self: flex-start; background: #fff; border: 1px solid #d0d7de; color: #24292f; }}
  .msg.sources {{ font-size: 11px; color: #57606a; margin-top: -4px; }}
  .composer {{ display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #d0d7de; background: #fff; }}
  .composer input {{ flex: 1; min-width: 0; padding: 9px 12px; border: 1px solid #d0d7de; border-radius: 8px; font-size: 13px; }}
  .composer button {{ padding: 9px 16px; border: none; border-radius: 8px; background: #0969da; color: #fff; font-weight: 600; font-size: 13px; cursor: pointer; }}
  .composer button:disabled {{ opacity: 0.5; cursor: default; }}
  .empty {{ color: #57606a; font-size: 13px; text-align: center; margin-top: 24px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>{name}</h1>
    <p>{description}</p>
  </div>
  <div class="messages" id="messages">
    <div class="empty">Ask a question to get started.</div>
  </div>
  <div class="composer">
    <input id="input" type="text" placeholder="Ask a question…" autocomplete="off" />
    <button id="send">Send</button>
  </div>
</div>
<script>
(function() {{
  var token = "{safe_token}";
  var messagesEl = document.getElementById("messages");
  var inputEl = document.getElementById("input");
  var sendBtn = document.getElementById("send");
  var emptied = false;

  function addMessage(text, cls) {{
    if (!emptied) {{ messagesEl.innerHTML = ""; emptied = true; }}
    var el = document.createElement("div");
    el.className = "msg " + cls;
    el.textContent = text;
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
  }}

  function addSources(sources) {{
    if (!sources || !sources.length) return;
    var el = document.createElement("div");
    el.className = "msg sources";
    el.textContent = "Sources: " + sources.join(", ");
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }}

  function send() {{
    var q = inputEl.value.trim();
    if (!q) return;
    addMessage(q, "user");
    inputEl.value = "";
    inputEl.disabled = true;
    sendBtn.disabled = true;
    var thinking = addMessage("Thinking…", "bot");
    fetch("/public/ai-agents/" + encodeURIComponent(token) + "/chat", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ question: q }}),
    }})
      .then(function(r) {{
        if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Something went wrong."); }});
        return r.json();
      }})
      .then(function(data) {{
        thinking.textContent = data.answer;
        addSources(data.sources);
      }})
      .catch(function(err) {{
        thinking.textContent = err.message || "Something went wrong. Please try again.";
      }})
      .finally(function() {{
        inputEl.disabled = false;
        sendBtn.disabled = false;
        inputEl.focus();
      }});
  }}

  sendBtn.addEventListener("click", send);
  inputEl.addEventListener("keydown", function(e) {{ if (e.key === "Enter") send(); }});
}})();
</script>
</body>
</html>""")


def _not_found_page() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><body style=\"font-family:sans-serif;padding:40px;text-align:center;color:#57606a\">"
        "This AI agent is unavailable or has been deactivated.</body></html>",
        status_code=404,
    )


_VALID_PANELS = {"customize", "pages", "blog", "generate"}


def _demo_context(token: str, edit_token: str | None, panel: str | None = None):
    """Resolves everything every demo-site page needs: the agent, its site
    customization, its pages/posts, and (only if edit_token actually
    validates for this exact agent) an admin bar to inject. Returns None
    for the agent when the token doesn't resolve at all, so callers can
    render one consistent "unavailable" response."""
    agent = ai_agents_store.resolve_by_token(token)
    if agent is None:
        return None
    site = ai_agents_store.get_site(agent["id"]) or {}
    pages = ai_agents_store.list_pages(agent["id"])
    posts = ai_agents_store.list_posts(agent["id"])
    has_posts = bool(posts)
    has_contact = bool(site.get("contact_email") or site.get("contact_phone") or site.get("contact_address") or site.get("contact_note"))
    valid_edit_token = edit_token if (edit_token and ai_agents_store.resolve_edit_token(edit_token, agent["id"])) else None
    active_panel = panel if panel in _VALID_PANELS else None
    admin_bar = (
        render_admin_bar(agent, site, pages, posts, valid_edit_token, f"{API_BASE_URL}/public/demo/{token}/download", active_panel)
        if valid_edit_token else ""
    )
    links = Links(mode="live", token=token, edit_token=valid_edit_token)
    return agent, site, pages, posts, has_posts, has_contact, admin_bar, links


@page_router.get("/demo/{token}", response_class=HTMLResponse)
def public_demo_home(token: str, edit_token: str | None = None, panel: str | None = None):
    agent = ai_agents_store.resolve_by_token(token)
    if agent is not None and edit_token and ai_agents_store.resolve_edit_token(edit_token, agent["id"]):
        _maybe_auto_generate(agent, edit_token)

    ctx = _demo_context(token, edit_token, panel)
    if ctx is None:
        return _not_found_page()
    agent, site, pages, posts, has_posts, has_contact, admin_bar, links = ctx
    return HTMLResponse(render_home(agent, site, links, pages, has_posts, has_contact, admin_bar, posts=posts))


@page_router.get("/demo/{token}/page/{slug}", response_class=HTMLResponse)
def public_demo_page(token: str, slug: str, edit_token: str | None = None):
    ctx = _demo_context(token, edit_token)
    if ctx is None:
        return _not_found_page()
    agent, site, pages, posts, has_posts, has_contact, admin_bar, links = ctx
    page = ai_agents_store.get_page_by_slug(agent["id"], slug)
    if page is None:
        return HTMLResponse("Page not found.", status_code=404)
    return HTMLResponse(render_page(agent, site, page, links, pages, has_posts, has_contact, admin_bar))


@page_router.get("/demo/{token}/blog", response_class=HTMLResponse)
def public_demo_blog(token: str, edit_token: str | None = None):
    ctx = _demo_context(token, edit_token)
    if ctx is None:
        return _not_found_page()
    agent, site, pages, posts, has_posts, has_contact, admin_bar, links = ctx
    return HTMLResponse(render_blog_index(agent, site, posts, links, pages, has_contact, admin_bar))


@page_router.get("/demo/{token}/blog/{slug}", response_class=HTMLResponse)
def public_demo_post(token: str, slug: str, edit_token: str | None = None):
    ctx = _demo_context(token, edit_token)
    if ctx is None:
        return _not_found_page()
    agent, site, pages, posts, has_posts, has_contact, admin_bar, links = ctx
    post = ai_agents_store.get_post_by_slug(agent["id"], slug)
    if post is None:
        return HTMLResponse("Post not found.", status_code=404)
    return HTMLResponse(render_post(agent, site, post, links, pages, has_contact, admin_bar))


@page_router.get("/demo/{token}/contact", response_class=HTMLResponse)
def public_demo_contact(token: str, edit_token: str | None = None):
    ctx = _demo_context(token, edit_token)
    if ctx is None:
        return _not_found_page()
    agent, site, pages, posts, has_posts, has_contact, admin_bar, links = ctx
    return HTMLResponse(render_contact(agent, site, links, pages, has_posts, admin_bar))


@page_router.get("/demo/{token}/download")
def public_demo_download(token: str):
    agent = ai_agents_store.resolve_by_token(token)
    if agent is None:
        return _not_found_page()
    site = ai_agents_store.get_site(agent["id"])
    pages = ai_agents_store.list_pages(agent["id"])
    posts = ai_agents_store.list_posts(agent["id"])
    files = render_static_site(agent, site, pages, posts)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in files.items():
            zf.writestr(filename, content)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slugify(agent["name"])}-site.zip"'},
    )
