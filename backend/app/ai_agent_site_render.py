"""HTML rendering for an AI Agent's public site — a small multi-page site
(home, admin-defined pages, blog, contact) with a real header, hero,
content sections, and footer, wrapped around the agent's chat widget, in
two modes:

  "live"   — server-rendered per request; internal links point back to
             this server's own dynamic routes (/public/demo/{token}/...),
             carrying the edit_token through when present so navigating
             between pages doesn't drop the admin bar.
  "static" — used only for the full-site ZIP export: the identical page
             bodies, but internal links point to flat local filenames
             (index.html, page-slug.html, ...) so the exported files work
             unmodified when hosted anywhere — the one thing that still
             needs this server is the chat iframe itself, unavoidably,
             since that's where the actual AI logic lives.
"""

import datetime
import html
import json
import re
from dataclasses import dataclass

from .config import API_BASE_URL

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")


def safe_color(color: str | None, default: str = "#0969da") -> str:
    return color if color and _HEX_COLOR.fullmatch(color) else default


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "page"


def _paragraphs(escaped_text: str) -> str:
    """Splits already-html-escaped body text on blank lines into real <p>
    elements with proper margin between them, instead of one giant blob
    of pre-wrapped text where multi-paragraph AI-drafted copy only had a
    blank *line* between "paragraphs", never a real paragraph gap."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", escaped_text) if p.strip()]
    return "\n        ".join(f"<p>{p}</p>" for p in parts) or "<p></p>"


# A rich-text field (site body, page/post content) holds one of two
# shapes: legacy plain text (written before the pencil editor's Quill
# integration existed, or via the API directly) or real HTML (written
# through Quill and sanitized server-side at write time -- see
# html_sanitize.py). This tells the two apart at render time so each
# gets the right treatment: plain text must still be escaped and
# paragraph-split; HTML is already safe and is trusted as-is.
_LOOKS_LIKE_HTML = re.compile(r"<(p|ul|ol|li|h2|h3|blockquote|strong|em|b|i|u|s|a|img)[ >/]", re.IGNORECASE)


def _render_rich(raw: str | None) -> str:
    raw = raw or ""
    if _LOOKS_LIKE_HTML.search(raw):
        return raw
    return _paragraphs(html.escape(raw))


def _editable_block(edit_token: str | None, endpoint: str, label: str, fields: list[tuple[str, str, str, bool]]) -> str:
    """Renders one or more (name, tag, raw_value, multiline) fields --
    raw_value is the UNESCAPED stored value; a multiline field is
    rendered via _render_rich() (escaped+paragraph-split for legacy
    plain text, trusted as-is for real HTML), a single-line field is
    always escaped, exactly as before -- and, ONLY for an admin viewing
    with a valid edit_token, wraps them with a small pencil button that
    turns them into inline editors (a rich Quill editor for multiline
    fields, plain inputs otherwise) and PATCHes `endpoint` directly with
    every field's new value, or an "AI generate" button that asks the
    same endpoint to redraft everything in this block from the
    knowledge base. `label` is a short human-readable name for the
    block ("the homepage headline and subheadline", "the About
    section", ...) shown in that AI-generate flow.

    A field's raw text never reaches the page without edit_token: a
    plain visitor gets ordinary tags with no data-cecm-* markers and no
    hidden raw-text textarea, so there is nothing for a normal page load
    to carry that an editor UI would need later."""
    def _view(raw_value: str, multiline: bool) -> str:
        return _render_rich(raw_value) if multiline else html.escape(raw_value or "")

    if not edit_token:
        parts = [f"<{tag}>{_view(value, multiline)}</{tag}>" for _name, tag, value, multiline in fields]
        return "\n      ".join(parts)

    def _field_html(name: str, tag: str, raw_value: str, multiline: bool) -> str:
        attrs = f' data-cecm-field="{name}"' + (" data-cecm-multiline" if multiline else "")
        view = _view(raw_value, multiline)
        if multiline:
            raw_source = f'<textarea class="cecm-raw-source" hidden>{html.escape(raw_value or "")}</textarea>'
            return f"<{tag}{attrs}>{view}{raw_source}</{tag}>"
        return f"<{tag}{attrs}>{view}</{tag}>"

    inner_html = "\n        ".join(_field_html(*f) for f in fields)
    safe_endpoint = html.escape(endpoint, quote=True)
    safe_label = html.escape(label, quote=True)
    return f"""<div class="cecm-editable" data-cecm-endpoint="{safe_endpoint}" data-cecm-label="{safe_label}">
      <button type="button" class="cecm-edit-pencil" aria-label="Edit this section" title="Edit this section">&#9998;</button>
      <div class="cecm-editable-view">
        {inner_html}
      </div>
    </div>"""


def _excerpt(text: str, limit: int = 140) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;: ") + "…"


@dataclass
class Links:
    mode: str  # "live" | "static"
    token: str
    edit_token: str | None = None

    def _with_edit(self, path: str) -> str:
        if self.mode == "live" and self.edit_token:
            return f"{path}?edit_token={self.edit_token}"
        return path

    def home(self) -> str:
        return self._with_edit(f"{API_BASE_URL}/public/demo/{self.token}") if self.mode == "live" else "index.html"

    def page(self, slug: str) -> str:
        return self._with_edit(f"{API_BASE_URL}/public/demo/{self.token}/page/{slug}") if self.mode == "live" else f"page-{slug}.html"

    def blog(self) -> str:
        return self._with_edit(f"{API_BASE_URL}/public/demo/{self.token}/blog") if self.mode == "live" else "blog.html"

    def post(self, slug: str) -> str:
        return self._with_edit(f"{API_BASE_URL}/public/demo/{self.token}/blog/{slug}") if self.mode == "live" else f"blog-{slug}.html"

    def contact(self) -> str:
        return self._with_edit(f"{API_BASE_URL}/public/demo/{self.token}/contact") if self.mode == "live" else "contact.html"

    def chat_api(self) -> str:
        return f"{API_BASE_URL}/public/ai-agents/{self.token}/chat"

    def leads_api(self) -> str:
        return f"{API_BASE_URL}/public/ai-agents/{self.token}/leads"


def _nav_items(links: Links, pages: list[dict], has_posts: bool, has_contact: bool) -> list[dict]:
    items = [{"label": "Home", "href": links.home(), "key": "home"}]
    for p in pages:
        items.append({"label": p["title"], "href": links.page(p["slug"]), "key": f"page:{p['slug']}"})
    if has_posts:
        items.append({"label": "Blog", "href": links.blog(), "key": "blog"})
    if has_contact:
        items.append({"label": "Contact", "href": links.contact(), "key": "contact"})
    return items


def _nav(items: list[dict], active: str) -> str:
    parts = []
    for it in items:
        cls = ' class="active"' if it["key"] == active else ""
        parts.append(f'<a href="{it["href"]}"{cls}>{html.escape(it["label"])}</a>')
    return "\n      ".join(parts)


def _meta_tags(agent: dict, site: dict) -> str:
    desc = (
        site.get("seo_description")
        or site.get("subheadline")
        or agent.get("description")
        or f"{agent['name']} — ask a question, get an instant, grounded answer."
    )
    desc = html.escape(desc[:300], quote=True)
    title = html.escape(agent["name"], quote=True)
    return (
        f'<meta name="description" content="{desc}" />\n'
        f'<meta property="og:type" content="website" />\n'
        f'<meta property="og:title" content="{title}" />\n'
        f'<meta property="og:description" content="{desc}" />'
    )


def _footer(agent: dict, site: dict, items: list[dict], accent: str, edit_token: str | None) -> str:
    name = html.escape(agent["name"])
    tagline_raw = site.get("footer_tagline") or site.get("subheadline") or agent.get("description") or ""
    tagline_editable = _editable_block(
        edit_token, f"{API_BASE_URL}/public/ai-agents/{agent['public_token']}/site", "the footer tagline",
        [("footer_tagline", "p", tagline_raw, False)],
    )

    def links_html(entries: list[dict]) -> str:
        return "\n        ".join(f'<a href="{it["href"]}">{html.escape(it["label"])}</a>' for it in entries)

    company_items = [it for it in items if it["key"] == "home" or it["key"].startswith("page:")]
    blog_item = next((it for it in items if it["key"] == "blog"), None)
    contact_item = next((it for it in items if it["key"] == "contact"), None)

    company_col = f"""
      <div class="footer-col">
        <div class="footer-heading">Company</div>
        {links_html(company_items)}
      </div>""" if company_items else ""

    resources_col = f"""
      <div class="footer-col">
        <div class="footer-heading">Resources</div>
        {links_html([blog_item])}
      </div>""" if blog_item else ""

    contact_bits = []
    if contact_item:
        contact_bits.append(f'<a href="{contact_item["href"]}">Get in touch</a>')
    if site.get("contact_email"):
        addr = html.escape(site["contact_email"], quote=True)
        contact_bits.append(f'<a href="mailto:{addr}">{html.escape(site["contact_email"])}</a>')
    if site.get("contact_phone"):
        contact_bits.append(f'<span>{html.escape(site["contact_phone"])}</span>')
    support_col = ""
    if contact_bits:
        support_col = f"""
      <div class="footer-col">
        <div class="footer-heading">Support</div>
        {"".join(contact_bits)}
      </div>"""

    year = datetime.datetime.now().year
    return f"""
  <footer class="site-footer">
    <div class="footer-inner">
      <div class="footer-brand">
        <div class="footer-brand-name"><span class="dot" style="background:{accent};"></span>{name}</div>
        {tagline_editable}
      </div>{company_col}{resources_col}{support_col}
    </div>
    <div class="footer-bottom">
      <span>&copy; {year} {name}. All rights reserved.</span>
      <span>Built with <a href="{API_BASE_URL}" target="_blank" rel="noopener">C-ECM AI Agents</a></span>
    </div>
  </footer>"""


def _chat_widget(agent: dict, site: dict, links: Links) -> str:
    """A floating launcher bubble + popup panel present on every page of the
    generated site — the visitor-facing counterpart to the admin bar.
    Talks directly to the existing public chat API (POST .../chat) rather
    than embedding the standalone /public/chat/{token} page in an iframe,
    so it can float above the page instead of being pinned to one spot in
    the layout. The standalone iframe page itself is untouched and still
    used by the separate "embed on your site" snippet."""
    accent = safe_color(site.get("accent_color"))
    name = html.escape(agent["name"])
    safe_name_attr = html.escape(agent["name"], quote=True)
    initial = html.escape((agent["name"][:1] or "?").upper())
    greeting = html.escape(
        agent.get("description")
        or f"Ask me anything about {agent['resource_name']} — I'll answer from this site's own knowledge base."
    )
    chat_api = links.chat_api()
    leads_api = links.leads_api()

    return f"""
  <div id="cecm-chat-widget">
    <button id="cecm-chat-launcher" type="button" aria-expanded="false" aria-label="Chat with {safe_name_attr}" title="Chat with {safe_name_attr}">
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 4h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H9l-5 4v-4H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z" fill="currentColor"/></svg>
    </button>
    <div id="cecm-chat-panel" class="cecm-chat-panel" hidden>
      <div class="cecm-chat-header">
        <span class="cecm-chat-avatar">{initial}</span>
        <div class="cecm-chat-header-text">
          <div class="cecm-chat-title">{name}</div>
          <div class="cecm-chat-sub">Answers from this site's knowledge base</div>
        </div>
        <button id="cecm-chat-close" type="button" aria-label="Close chat">&times;</button>
      </div>
      <div class="cecm-chat-messages" id="cecm-chat-messages">
        <div class="cecm-chat-msg bot">{greeting}</div>
      </div>
      <div class="cecm-chat-suggestions">
        <button type="button" class="cecm-chat-pill" data-q="What can you help me with?">💬 What can you help me with?</button>
        <button type="button" class="cecm-chat-pill" id="cecm-contact-pill">📞 Contact us</button>
      </div>
      <div class="cecm-chat-composer">
        <input id="cecm-chat-input" type="text" placeholder="Ask a question…" autocomplete="off" />
        <button id="cecm-chat-send" type="button" aria-label="Send">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 12h15m0 0-6-6m6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
      </div>
      <div class="cecm-chat-disclaimer">Answers are generated from this site's own knowledge base.</div>
    </div>
  </div>
  <style>
    #cecm-chat-widget {{ position: fixed; right: 24px; bottom: 24px; z-index: 2000; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    #cecm-chat-launcher {{ width: 60px; height: 60px; border-radius: 50%; background: {accent}; border: none; box-shadow: 0 10px 28px rgba(0,0,0,0.22); cursor: pointer; display: flex; align-items: center; justify-content: center; color: #fff; }}
    #cecm-chat-launcher:hover {{ filter: brightness(1.08); }}
    #cecm-chat-launcher svg {{ width: 26px; height: 26px; }}
    .cecm-chat-panel {{ position: absolute; right: 0; bottom: 76px; width: 368px; max-width: calc(100vw - 48px); max-height: 72vh; background: #fff; border-radius: 16px; box-shadow: 0 20px 56px rgba(0,0,0,0.24); display: flex; flex-direction: column; overflow: hidden; }}
    .cecm-chat-panel[hidden] {{ display: none; }}
    .cecm-chat-header {{ display: flex; align-items: center; gap: 10px; padding: 16px; background: #0b1220; color: #fff; flex-shrink: 0; }}
    .cecm-chat-avatar {{ width: 34px; height: 34px; border-radius: 50%; background: {accent}; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 14px; flex-shrink: 0; }}
    .cecm-chat-header-text {{ flex: 1; min-width: 0; }}
    .cecm-chat-title {{ font-weight: 700; font-size: 14px; }}
    .cecm-chat-sub {{ font-size: 11px; opacity: 0.7; }}
    #cecm-chat-close {{ background: none; border: none; color: #fff; opacity: 0.7; cursor: pointer; font-size: 18px; line-height: 1; flex-shrink: 0; }}
    #cecm-chat-close:hover {{ opacity: 1; }}
    .cecm-chat-messages {{ flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; background: #f6f8fa; min-height: 140px; }}
    .cecm-chat-msg {{ max-width: 88%; padding: 9px 13px; border-radius: 12px; font-size: 13px; line-height: 1.5; white-space: pre-wrap; }}
    .cecm-chat-msg.bot {{ align-self: flex-start; background: #fff; border: 1px solid #d0d7de; color: #24292f; }}
    .cecm-chat-msg.user {{ align-self: flex-end; background: {accent}; color: #fff; }}
    .cecm-chat-msg.sources {{ align-self: flex-start; background: none; padding: 0 4px; font-size: 10.5px; color: #8b949e; }}
    .cecm-chat-suggestions {{ padding: 0 16px 12px; display: flex; flex-wrap: wrap; gap: 8px; background: #f6f8fa; flex-shrink: 0; }}
    .cecm-chat-pill {{ border: 1px solid {accent}; color: {accent}; background: #fff; border-radius: 999px; padding: 6px 12px; font-size: 11.5px; font-weight: 600; cursor: pointer; }}
    .cecm-chat-pill:hover {{ background: {accent}; color: #fff; }}
    .cecm-chat-composer {{ display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #e5e9ee; background: #fff; flex-shrink: 0; }}
    .cecm-chat-composer input {{ flex: 1; min-width: 0; padding: 9px 13px; border: 1px solid #d0d7de; border-radius: 999px; font-size: 13px; }}
    .cecm-chat-composer input:focus {{ outline: 2px solid {accent}; outline-offset: 1px; }}
    #cecm-chat-send {{ width: 36px; height: 36px; border-radius: 50%; border: none; background: {accent}; color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }}
    #cecm-chat-send svg {{ width: 18px; height: 18px; }}
    #cecm-chat-send:disabled {{ opacity: 0.5; cursor: default; }}
    .cecm-chat-disclaimer {{ font-size: 10.5px; color: #8b949e; padding: 0 16px 14px; background: #fff; flex-shrink: 0; }}
    @media (max-width: 480px) {{ #cecm-chat-widget {{ right: 16px; bottom: 16px; }} .cecm-chat-panel {{ right: -8px; width: calc(100vw - 32px); }} }}
  </style>
  <script>
  (function() {{
    var launcher = document.getElementById("cecm-chat-launcher");
    var panel = document.getElementById("cecm-chat-panel");
    var closeBtn = document.getElementById("cecm-chat-close");
    var messagesEl = document.getElementById("cecm-chat-messages");
    var input = document.getElementById("cecm-chat-input");
    var sendBtn = document.getElementById("cecm-chat-send");
    var contactPill = document.getElementById("cecm-contact-pill");
    var opened = false;
    // When set, the next thing typed is answered by the scripted "contact
    // us" intake flow below instead of being sent to the knowledge-base
    // chat API -- a simple one-step state machine, not a real conversation
    // engine, which is all a fixed 3-question intake needs.
    var contactStep = null;
    var lead = {{ email: null, phone: null, message: null }};

    function openChat() {{
      panel.hidden = false;
      launcher.setAttribute("aria-expanded", "true");
      opened = true;
      input.focus();
    }}
    function closeChat() {{
      panel.hidden = true;
      launcher.setAttribute("aria-expanded", "false");
      opened = false;
    }}
    window.cecmOpenChat = openChat;
    launcher.addEventListener("click", function() {{ if (opened) {{ closeChat(); }} else {{ openChat(); }} }});
    closeBtn.addEventListener("click", closeChat);
    document.addEventListener("keydown", function(e) {{
      if (e.key === "Escape" && opened) {{ closeChat(); launcher.focus(); }}
    }});
    document.querySelectorAll(".cecm-chat-pill[data-q]").forEach(function(btn) {{
      btn.addEventListener("click", function() {{
        input.value = btn.getAttribute("data-q");
        send();
      }});
    }});
    contactPill.addEventListener("click", function() {{
      contactStep = "email";
      addMessage("Sure — I just need a few details so our team can follow up. What's your email address?", "bot");
      input.focus();
    }});

    function addMessage(text, cls) {{
      var el = document.createElement("div");
      el.className = "cecm-chat-msg " + cls;
      el.textContent = text;
      messagesEl.appendChild(el);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return el;
    }}
    function addSources(sources) {{
      if (!sources || !sources.length) return;
      addMessage("Sources: " + sources.join(", "), "sources");
    }}

    function handleContactAnswer(q) {{
      if (contactStep === "email") {{
        if (!/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(q)) {{
          addMessage("That doesn't look like a valid email — could you try again?", "bot");
          return;
        }}
        lead.email = q;
        contactStep = "phone";
        addMessage("Thanks! And a phone number where our team can reach you?", "bot");
        return;
      }}
      if (contactStep === "phone") {{
        lead.phone = q;
        contactStep = "message";
        addMessage("Got it — how can we help you?", "bot");
        return;
      }}
      if (contactStep === "message") {{
        lead.message = q;
        input.disabled = true;
        sendBtn.disabled = true;
        var thinking = addMessage("Sending…", "bot");
        fetch("{leads_api}", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ email: lead.email, phone: lead.phone, message: lead.message }}),
        }})
          .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Something went wrong."); }}); return r.json(); }})
          .then(function() {{
            thinking.textContent = "Thanks — we've got your details. One of our team will be in touch soon!";
            contactStep = null;
            lead = {{ email: null, phone: null, message: null }};
          }})
          .catch(function(err) {{
            thinking.textContent = err.message || "Something went wrong saving your details — please try again.";
            contactStep = "message";
          }})
          .finally(function() {{ input.disabled = false; sendBtn.disabled = false; input.focus(); }});
      }}
    }}

    function send() {{
      var q = input.value.trim();
      if (!q) return;
      addMessage(q, "user");
      input.value = "";
      if (contactStep) {{ handleContactAnswer(q); return; }}
      input.disabled = true;
      sendBtn.disabled = true;
      var thinking = addMessage("Thinking…", "bot");
      fetch("{chat_api}", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ question: q }}),
      }})
        .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Something went wrong."); }}); return r.json(); }})
        .then(function(data) {{ thinking.textContent = data.answer; addSources(data.sources); }})
        .catch(function(err) {{ thinking.textContent = err.message || "Something went wrong. Please try again."; }})
        .finally(function() {{ input.disabled = false; sendBtn.disabled = false; input.focus(); }});
    }}
    sendBtn.addEventListener("click", send);
    input.addEventListener("keydown", function(e) {{ if (e.key === "Enter") send(); }});
  }})();
  </script>"""


def _shell(agent: dict, site: dict, body_html: str, admin_bar: str, links: Links,
           pages: list[dict], has_posts: bool, has_contact: bool, active: str) -> str:
    accent = safe_color(site.get("accent_color"))
    title = html.escape(agent["name"])
    items = _nav_items(links, pages, has_posts, has_contact)
    nav = _nav(items, active)
    meta = _meta_tags(agent, site)
    footer = _footer(agent, site, items, accent, links.edit_token)
    chat_widget = _chat_widget(agent, site, links)

    page_content_class = "cecm-has-admin" if links.edit_token else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
{meta}
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #24292f; background: #f6f8fa; }}
  header {{ background: #0b1220; }}
  .header-inner {{ max-width: 1100px; margin: 0 auto; padding: 14px 24px; display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }}
  .brand {{ font-weight: 700; font-size: 15px; display: flex; align-items: center; gap: 8px; color: #fff; }}
  .brand .dot {{ width: 8px; height: 8px; border-radius: 50%; background: {accent}; }}
  nav {{ display: flex; gap: 18px; flex-wrap: wrap; }}
  nav a {{ color: #cfd6dc; text-decoration: none; font-size: 13px; font-weight: 600; }}
  nav a:hover, nav a.active {{ color: #fff; }}
  .nav-cta {{ margin-left: auto; background: {accent}; color: #fff; border: none; padding: 9px 18px; border-radius: 999px; font-size: 12.5px; font-weight: 700; cursor: pointer; white-space: nowrap; }}
  .nav-cta:hover {{ filter: brightness(1.08); }}
  .nav-toggle {{ display: none; background: none; border: none; color: #fff; cursor: pointer; padding: 4px; margin-left: auto; }}
  .nav-toggle svg {{ width: 22px; height: 22px; }}
  @media (max-width: 760px) {{
    .header-inner {{ position: relative; }}
    .nav-toggle {{ display: flex; align-items: center; justify-content: center; order: 2; }}
    .nav-cta {{ margin-left: 0; order: 3; }}
    nav {{ display: none; flex-basis: 100%; flex-direction: column; gap: 4px; order: 4; padding-top: 4px; }}
    nav.cecm-nav-open {{ display: flex; }}
  }}
  .page-wrap {{ max-width: 1100px; margin: 0 auto; padding: 40px 24px 72px; }}
  .hero {{ text-align: center; padding: 56px 32px; border-radius: 20px; background: linear-gradient(135deg, #0b1220 0%, {accent} 140%); color: #fff; }}
  .hero .eyebrow {{ display: inline-block; background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.28); color: #fff; font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; padding: 6px 14px; border-radius: 999px; margin-bottom: 20px; }}
  .hero h1 {{ font-size: clamp(26px, 4vw, 40px); margin: 0 0 12px; font-weight: 800; letter-spacing: -0.02em; color: #fff; }}
  .hero p {{ font-size: 16px; color: #cfd6dc; max-width: 620px; margin: 0 auto; line-height: 1.6; }}
  .hero-actions {{ display: flex; gap: 12px; justify-content: center; margin-top: 26px; flex-wrap: wrap; }}
  .btn {{ display: inline-block; background: {accent}; color: #fff; text-decoration: none; font-weight: 700; font-size: 13.5px; padding: 11px 22px; border-radius: 8px; border: none; cursor: pointer; }}
  .btn:hover {{ filter: brightness(1.08); }}
  .btn-secondary {{ background: #fff; color: #24292f; border: 1px solid #d0d7de; }}
  .hero .btn {{ background: #fff; color: #0b1220; }}
  .hero .btn-secondary {{ background: transparent; color: #fff; border: 1px solid rgba(255,255,255,0.4); }}
  /* No max-width here on purpose: .about-card used to cap itself at
     820px and center within .page-wrap, which visually broke ranks
     with .hero and every other .card (page/post content) sitting
     directly below it at the full page-wrap width -- keep just the
     top spacing so it lines up with them instead. */
  .about-card {{ margin-top: 32px; }}
  .card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 12px; padding: 24px; line-height: 1.7; }}
  .card h1, .card h2 {{ margin-top: 0; }}
  /* No max-width here on purpose: it used to cap published paragraphs
     at 68ch for readability, but that fought with the editor -- the
     editor doesn't apply that cap (so you can type across the full
     box), so the same text wrapped differently the moment you saved
     it. Matching the editor exactly (full card width, no extra cap)
     keeps what you see while typing identical to what gets published. */
  .card p {{ white-space: pre-wrap; margin: 0 0 16px; }}
  .card p:last-child {{ margin-bottom: 0; }}
  .card img {{ max-width: 100%; height: auto; border-radius: 8px; margin: 8px 0; }}
  .section {{ margin-top: 56px; }}
  .section-head {{ text-align: center; margin-bottom: 24px; }}
  .section-head h2 {{ margin: 0 0 6px; font-size: 22px; }}
  .section-head p {{ margin: 0; color: #57606a; font-size: 14px; }}
  .grid-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; }}
  .mini-card {{ display: block; background: #fff; border: 1px solid #d0d7de; border-radius: 12px; padding: 20px; text-decoration: none; color: inherit; }}
  .mini-card:hover {{ border-color: {accent}; }}
  .mini-card h3 {{ margin: 0 0 8px; font-size: 16px; color: #24292f; }}
  .mini-card p {{ margin: 0; color: #57606a; font-size: 13px; line-height: 1.6; }}
  .mini-card .read-more {{ display: inline-block; margin-top: 10px; color: {accent}; font-size: 12.5px; font-weight: 700; }}
  .cta-band {{ margin-top: 56px; background: linear-gradient(135deg, #0b1220 0%, {accent} 140%); color: #fff; border-radius: 16px; padding: 44px 32px; text-align: center; }}
  .cta-band h2 {{ margin: 0 0 8px; font-size: 22px; }}
  .cta-band p {{ margin: 0 0 22px; opacity: 0.9; font-size: 14px; }}
  .cta-band .btn {{ background: #fff; color: #0b1220; }}
  .post-list {{ display: flex; flex-direction: column; gap: 16px; margin-top: 24px; }}
  .post-list-item {{ background: #fff; border: 1px solid #d0d7de; border-radius: 12px; padding: 20px; }}
  .post-list-item h2 {{ margin: 0 0 6px; font-size: 18px; }}
  .post-list-item h2 a {{ color: #24292f; text-decoration: none; }}
  .post-list-item p {{ color: #57606a; margin: 0; }}
  .post-list-item a.read-more {{ color: {accent}; font-size: 13px; font-weight: 600; text-decoration: none; }}
  .contact-item {{ margin-bottom: 10px; }}
  .contact-item strong {{ display: inline-block; width: 90px; color: #57606a; font-weight: 600; }}
  .site-footer {{ background: #0b1220; color: #cfd6dc; margin-top: 64px; }}
  .footer-inner {{ max-width: 1100px; margin: 0 auto; padding: 40px 24px; display: flex; flex-wrap: wrap; gap: 32px 40px; }}
  .footer-brand {{ flex: 2 1 240px; }}
  .footer-brand-name {{ display: flex; align-items: center; gap: 8px; font-weight: 700; color: #fff; font-size: 15px; }}
  .footer-brand-name .dot {{ width: 8px; height: 8px; border-radius: 50%; }}
  .footer-brand p {{ margin: 10px 0 0; font-size: 13px; line-height: 1.6; color: #9aa4ad; max-width: 320px; }}
  .footer-heading {{ font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.06em; color: #8b949e; font-weight: 700; margin-bottom: 12px; }}
  .footer-col {{ flex: 1 1 140px; display: flex; flex-direction: column; gap: 8px; }}
  .footer-col a, .footer-col span {{ color: #cfd6dc; text-decoration: none; font-size: 13px; }}
  .footer-col a:hover {{ color: #fff; }}
  .footer-bottom {{ border-top: 1px solid rgba(255,255,255,0.08); padding: 16px 24px; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; max-width: 1100px; margin: 0 auto; font-size: 12px; color: #8b949e; }}
  .footer-bottom a {{ color: {accent}; text-decoration: none; }}
  .cecm-editable {{ position: relative; }}
  .cecm-edit-pencil {{ position: absolute; top: 8px; right: 8px; width: 30px; height: 30px; border-radius: 50%; background: #1d2327; color: #fff; border: 2px solid #fff; cursor: pointer; font-size: 13px; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.25); z-index: 5; }}
  .cecm-edit-pencil:hover {{ background: {accent}; }}
  .cecm-editable-view {{ padding-right: 6px; }}
  /* .cecm-has-admin is set only when an edit_token is present (i.e. an
     admin, not a visitor, is viewing) -- a class on #cecm-page-content
     itself rather than a "follows the admin bar" sibling selector,
     because the admin bar's own markup (image-upload modal, <style>,
     <script>) sits between .cecm-bar and this div in the DOM, so an
     adjacent-sibling selector never actually matched and the page
     content never shifted -- it just sat underneath the fixed sidebar
     and panel, which is why they visually overlapped it. */
  #cecm-page-content {{ transition: margin-left 0.18s ease; }}
  #cecm-page-content.cecm-has-admin {{ margin-left: 260px; }}
  #cecm-page-content.cecm-has-admin.cecm-shifted {{ margin-left: 740px; }}
  @media (max-width: 900px) {{ #cecm-page-content.cecm-has-admin, #cecm-page-content.cecm-has-admin.cecm-shifted {{ margin-left: 0; }} }}
  .cecm-quill-mount {{ background: #fff; border-radius: 8px; margin-bottom: 8px; }}
  .cecm-quill-mount .ql-toolbar {{ border-color: #d0d7de; border-radius: 8px 8px 0 0; background: #fff; }}
  .cecm-quill-mount .ql-container {{ border-color: #d0d7de; border-radius: 0 0 8px 8px; font-size: 15px; font-family: inherit; }}
  /* The panel's dark admin theme sets color:#fff on everything inside
     it for readability against its dark background, but this mount's
     own background is white -- without an explicit override here that
     inherited white text becomes invisible against it (white on white). */
  .cecm-quill-mount .ql-editor {{ min-height: 460px; color: #24292f; }}
  .cecm-quill-mount .ql-editor.ql-blank::before {{ color: #6e7781; font-style: normal; }}
  .cecm-quill-mount .ql-stroke {{ stroke: #444; }}
  .cecm-quill-mount .ql-fill {{ fill: #444; }}
  .cecm-quill-mount .ql-picker {{ color: #444; }}
</style>
</head>
<body>
  {admin_bar}
  <div id="cecm-page-content" class="{page_content_class}">
  <header>
    <div class="header-inner">
      <div class="brand"><span class="dot"></span>{title}</div>
      <button type="button" id="cecm-nav-toggle" class="nav-toggle" aria-label="Menu" aria-expanded="false">
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
      </button>
      <nav id="cecm-nav">
      {nav}
      </nav>
      <button type="button" class="nav-cta" onclick="cecmOpenChat()">Chat now</button>
    </div>
  </header>
  <div class="page-wrap">
    {body_html}
  </div>
  {footer}
  </div>
  {chat_widget}
  <script>
  (function() {{
    var toggle = document.getElementById("cecm-nav-toggle");
    var navEl = document.getElementById("cecm-nav");
    if (!toggle || !navEl) return;
    toggle.addEventListener("click", function() {{
      var open = navEl.classList.toggle("cecm-nav-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }});
  }})();
  </script>
</body>
</html>"""


def _json_for_script(value) -> str:
    """json.dumps, safe to inline inside a <script> tag — escapes '</' so a
    string containing "</script>" can't prematurely close the tag."""
    return json.dumps(value).replace("</", "<\\/")


def render_admin_bar(agent: dict, site: dict, pages: list[dict], posts: list[dict],
                      edit_token: str, download_url: str, active_panel: str | None = None) -> str:
    """A WordPress-style admin bar: only ever rendered server-side after
    resolve_edit_token() has already validated the token for this exact
    agent (see the public_demo_* routes) — a visitor without a valid token
    never receives this markup at all, not just a hidden-by-CSS version.
    Everything the site admin can do — Customize, Pages, Blog, Regenerate
    with AI — lives here, on the live page itself, rather than in the
    C-ECM app. Each add/edit/delete/apply reloads the page on success
    (simplest correct behavior in plain JS with no framework), which is
    also why every field the site already has is baked in here as data,
    not fetched separately."""
    site = site or {}
    headline = html.escape(site.get("headline") or "")
    subheadline = html.escape(site.get("subheadline") or "")
    body_json = _json_for_script(site.get("body") or "")
    seo_description = html.escape(site.get("seo_description") or "")
    footer_tagline = html.escape(site.get("footer_tagline") or "")
    contact_email = html.escape(site.get("contact_email") or "")
    contact_phone = html.escape(site.get("contact_phone") or "")
    contact_address = html.escape(site.get("contact_address") or "")
    contact_note = html.escape(site.get("contact_note") or "")
    accent = safe_color(site.get("accent_color"))
    safe_token = html.escape(agent["public_token"], quote=True)
    safe_edit_token = html.escape(edit_token, quote=True)
    safe_active_panel = html.escape(active_panel or "", quote=True)

    pages_json = _json_for_script(pages)
    posts_json = _json_for_script(posts)

    return f"""
  <link href="https://cdnjs.cloudflare.com/ajax/libs/quill/1.3.7/quill.snow.min.css" rel="stylesheet">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/quill/1.3.7/quill.min.js"></script>
  <div id="cecm-admin-bar" class="cecm-bar">
    <div class="cecm-bar-head">
      <div class="cecm-bar-title">Editing &quot;{html.escape(agent['name'])}&quot;</div>
      <div class="cecm-bar-sub">— only you can see this bar</div>
      <button id="cecm-dismiss-bar" class="cecm-bar-dismiss" aria-label="Hide admin bar" title="Hide admin bar">&times;</button>
    </div>
    <nav class="cecm-bar-nav">
      <button class="cecm-tab-btn" data-panel="customize">Customize</button>
      <button class="cecm-tab-btn" data-panel="pages">Pages</button>
      <button class="cecm-tab-btn" data-panel="blog">Blog</button>
      <button class="cecm-tab-btn" data-panel="generate">Regenerate with AI</button>
      <a class="cecm-bar-link" href="{download_url}">Download .zip</a>
    </nav>

    <div id="cecm-panel-customize" class="cecm-panel">
      <div class="cecm-panel-inner">
        <div class="cecm-panel-head">
          <h2>Customize</h2>
          <p>Headline, About text, SEO description, brand color, and the Contact page — changes apply the moment you save.</p>
        </div>
        <div class="cecm-field-grid">
          <label class="cecm-field cecm-field-wide">Headline
            <input id="cecm-headline" value="{headline}" placeholder="{html.escape(agent['name'])}" />
          </label>
          <label class="cecm-field cecm-field-wide">Subheadline
            <input id="cecm-subheadline" value="{subheadline}" />
          </label>
          <label class="cecm-field cecm-field-full">About text
            <div id="cecm-body-content-mount" class="cecm-quill-mount"></div>
          </label>
          <label class="cecm-field cecm-field-full">SEO description
            <input id="cecm-seo-description" value="{seo_description}" />
            <span class="cecm-hint">Shown in search results and social previews — aim for around 150-160 characters.</span>
          </label>
          <label class="cecm-field">Footer tagline
            <input id="cecm-footer-tagline" value="{footer_tagline}" />
          </label>
          <label class="cecm-field">Accent color
            <span class="cecm-color-row">
              <input id="cecm-accent" type="color" class="cecm-color-input" value="{accent}" />
              <span id="cecm-accent-value" class="cecm-color-value">{accent}</span>
            </span>
          </label>
        </div>
        <div class="cecm-section-title">Contact page</div>
        <div class="cecm-field-grid">
          <label class="cecm-field cecm-field-full">Contact note
            <input id="cecm-contact-note" value="{contact_note}" />
          </label>
          <label class="cecm-field">Email
            <input id="cecm-contact-email" value="{contact_email}" />
          </label>
          <label class="cecm-field">Phone
            <input id="cecm-contact-phone" value="{contact_phone}" />
          </label>
          <label class="cecm-field cecm-field-wide">Address
            <input id="cecm-contact-address" value="{contact_address}" />
          </label>
        </div>
        <div id="cecm-customize-error" class="cecm-error"></div>
        <div class="cecm-actions">
          <button id="cecm-customize-save" class="cecm-btn cecm-btn-primary">Save changes</button>
          <button type="button" class="cecm-btn cecm-btn-secondary cecm-panel-cancel">Cancel</button>
        </div>
      </div>
    </div>

    <div id="cecm-panel-pages" class="cecm-panel">
      <div class="cecm-panel-inner">
        <div class="cecm-panel-head"><h2>Pages</h2><p>Topic pages that appear in the site's navigation and footer.</p></div>
        <div id="cecm-pages-list" class="cecm-item-list"></div>
        <div class="cecm-section-title">Add a page</div>
        <div class="cecm-field-grid">
          <label class="cecm-field cecm-field-wide">Title <span class="cecm-hint">(optional — a topic works too)</span>
            <input id="cecm-new-page-title" placeholder="e.g. Services" />
          </label>
        </div>
        <div class="cecm-actions">
          <button type="button" id="cecm-generate-page-btn" class="cecm-btn cecm-btn-secondary">✨ Generate with AI</button>
        </div>
        <div id="cecm-generate-page-error" class="cecm-error"></div>
        <label class="cecm-field cecm-field-full">Content
          <div id="cecm-new-page-content-mount" class="cecm-quill-mount"></div>
        </label>
        <div id="cecm-pages-error" class="cecm-error"></div>
        <div class="cecm-actions">
          <button id="cecm-add-page" class="cecm-btn cecm-btn-primary">Add page</button>
          <button type="button" class="cecm-btn cecm-btn-secondary cecm-panel-cancel">Cancel</button>
        </div>
      </div>
    </div>

    <div id="cecm-panel-blog" class="cecm-panel">
      <div class="cecm-panel-inner">
        <div class="cecm-panel-head"><h2>Blog</h2><p>Posts shown on the homepage preview and the full blog index.</p></div>
        <div id="cecm-posts-list" class="cecm-item-list"></div>
        <div class="cecm-section-title">Add a post</div>
        <div class="cecm-field-grid">
          <label class="cecm-field cecm-field-wide">Title <span class="cecm-hint">(optional — a topic works too)</span>
            <input id="cecm-new-post-title" placeholder="e.g. How it works" />
          </label>
        </div>
        <div class="cecm-actions">
          <button type="button" id="cecm-generate-post-btn" class="cecm-btn cecm-btn-secondary">✨ Generate with AI</button>
        </div>
        <div id="cecm-generate-post-error" class="cecm-error"></div>
        <label class="cecm-field cecm-field-full">Excerpt
          <input id="cecm-new-post-excerpt" />
        </label>
        <label class="cecm-field cecm-field-full">Content
          <div id="cecm-new-post-content-mount" class="cecm-quill-mount"></div>
        </label>
        <div id="cecm-posts-error" class="cecm-error"></div>
        <div class="cecm-actions">
          <button id="cecm-add-post" class="cecm-btn cecm-btn-primary">Add post</button>
          <button type="button" class="cecm-btn cecm-btn-secondary cecm-panel-cancel">Cancel</button>
        </div>
      </div>
    </div>

    <div id="cecm-panel-generate" class="cecm-panel">
      <div class="cecm-panel-inner">
        <div class="cecm-panel-head">
          <h2>Regenerate with AI</h2>
          <p>Two ways to have the AI update this site from its knowledge base — rewrite everything, or make one specific change.</p>
        </div>

        <div class="cecm-section-title" style="border-top:none;padding-top:0;">Regenerate the entire site</div>
        <p style="margin:0;opacity:0.65;font-size:12.5px;line-height:1.6;max-width:680px;">
          This site was already drafted automatically from the knowledge base the first time it was opened.
          Run it again any time — it re-analyzes every document, then rewrites the headline, About text, SEO
          description, footer tagline, topic pages, and blog posts. Anything you don't want is one click away
          to edit or delete in Pages / Blog afterward.
        </p>
        <div id="cecm-generate-error" class="cecm-error"></div>
        <div class="cecm-actions">
          <button id="cecm-generate-btn" class="cecm-btn cecm-btn-primary">Regenerate &amp; publish full site</button>
          <button type="button" class="cecm-btn cecm-btn-secondary cecm-panel-cancel">Cancel</button>
        </div>

        <div class="cecm-section-title">Or describe a specific change</div>
        <p style="margin:0;opacity:0.65;font-size:12.5px;line-height:1.6;max-width:680px;">
          Tell it exactly what to change — a page, the footer, the menu order, the accent color, anything —
          and it edits only that, leaving the rest of the site untouched.
        </p>
        <label class="cecm-field cecm-field-full">Instruction
          <textarea id="cecm-edit-instruction" rows="3" placeholder="e.g. Make the footer tagline mention 24/7 support, or move the Security page first in the menu"></textarea>
        </label>
        <div id="cecm-edit-result" class="cecm-hint" style="display:none;color:#8fd19e;"></div>
        <div id="cecm-edit-error" class="cecm-error"></div>
        <div class="cecm-actions">
          <button id="cecm-edit-btn" class="cecm-btn cecm-btn-primary">Apply change</button>
          <button type="button" class="cecm-btn cecm-btn-secondary cecm-panel-cancel">Cancel</button>
        </div>
      </div>
    </div>
  </div>

  <div id="cecm-image-modal" class="cecm-image-modal" hidden>
    <div class="cecm-image-modal-card">
      <div class="cecm-image-modal-head">
        <h3>Insert image</h3>
        <button type="button" id="cecm-image-modal-close" aria-label="Close">&times;</button>
      </div>
      <button type="button" id="cecm-image-upload-btn" class="cecm-btn cecm-btn-primary">Upload from your computer</button>
      <input type="file" id="cecm-image-file-input" accept="image/png,image/jpeg,image/gif,image/webp" hidden />
      <div id="cecm-image-file-name" class="cecm-hint"></div>
      <div class="cecm-image-modal-divider">or paste an image URL</div>
      <input type="text" id="cecm-image-url-input" class="cecm-inline-input" placeholder="https://example.com/photo.jpg" />
      <div id="cecm-image-modal-error" class="cecm-error"></div>
      <div class="cecm-actions">
        <button type="button" id="cecm-image-insert-btn" class="cecm-btn cecm-btn-primary">Insert</button>
        <button type="button" id="cecm-image-cancel-btn" class="cecm-btn cecm-btn-secondary">Cancel</button>
      </div>
    </div>
  </div>
  <style>
    /* A persistent left-side nav rail (WordPress-admin style), not a
       horizontal top bar -- always visible while editing, full viewport
       height. #cecm-page-content is permanently offset by its width
       (see the base rule near the top of this stylesheet) so the live
       site never sits underneath it. */
    /* Colors deliberately reuse the live site's OWN palette rather than
       a generic dark-admin gray, so the editing chrome reads as part of
       this particular site rather than a bolted-on tool: the nav rail
       is the same navy as <header>/.hero (#0b1220), the panel canvas is
       the same light gray as .page-wrap's own background (#f6f8fa), and
       its fields/cards are white surfaces on that canvas -- exactly the
       card-on-canvas convention the site itself already uses. */
    .cecm-bar {{ position: fixed; top: 0; left: 0; bottom: 0; width: 260px; z-index: 1000; background: #0b1220; color: #fff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 13px; box-shadow: 2px 0 12px rgba(0,0,0,0.25); display: flex; flex-direction: column; overflow-y: auto; }}
    .cecm-bar-head {{ position: relative; padding: 18px 40px 16px 16px; border-bottom: 1px solid rgba(255,255,255,0.1); }}
    .cecm-bar-title {{ font-weight: 700; line-height: 1.4; }}
    .cecm-bar-sub {{ opacity: 0.55; font-size: 11.5px; margin-top: 2px; }}
    .cecm-bar-nav {{ display: flex; flex-direction: column; gap: 4px; padding: 10px; }}
    .cecm-tab-btn {{ display: block; width: 100%; text-align: left; background: transparent; border: none; color: #fff; padding: 10px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; }}
    .cecm-tab-btn:hover {{ background: rgba(255,255,255,0.1); }}
    .cecm-tab-btn.active {{ background: {accent}; }}
    .cecm-bar-link {{ display: block; padding: 10px 12px; border-radius: 6px; color: #fff; opacity: 0.8; text-decoration: none; font-size: 12.5px; font-weight: 600; }}
    .cecm-bar-link:hover {{ opacity: 1; background: rgba(255,255,255,0.1); }}
    .cecm-bar-dismiss {{ position: absolute; top: 14px; right: 12px; background: none; border: none; color: #fff; opacity: 0.6; cursor: pointer; font-size: 18px; line-height: 1; padding: 0 2px; }}
    .cecm-bar-dismiss:hover {{ opacity: 1; }}
    /* The active panel's form appears as a second fixed column right
       next to the nav rail (not overlaying the whole viewport), so the
       live site -- shifted further right again, see .cecm-shifted in
       _shell()'s own stylesheet -- is always visible alongside whatever
       is being edited. Wide enough that the content editor below isn't
       cramped. */
    .cecm-panel {{ display: none; position: fixed; top: 0; left: 260px; bottom: 0; width: 480px; max-width: calc(94vw - 260px); overflow-y: auto; background: #f6f8fa; color: #24292f; box-shadow: 4px 0 24px rgba(0,0,0,0.2); z-index: 900; border-left: 1px solid #d0d7de; }}
    .cecm-panel-inner {{ padding: 22px 22px 30px; display: flex; flex-direction: column; gap: 16px; }}
    .cecm-panel-head h2 {{ margin: 0 0 4px; font-size: 16px; color: #24292f; }}
    .cecm-panel-head p {{ margin: 0; color: #57606a; font-size: 12.5px; line-height: 1.6; }}
    .cecm-section-title {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: #57606a; font-weight: 700; border-top: 1px solid #d0d7de; padding-top: 16px; }}
    .cecm-field-grid {{ display: grid; grid-template-columns: 1fr; gap: 14px; }}
    .cecm-field {{ display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; font-weight: 600; color: #24292f; }}
    .cecm-field-wide, .cecm-field-full {{ grid-column: 1; }}
    .cecm-field input, .cecm-field textarea, .cecm-inline-input, .cecm-inline-textarea {{ padding: 9px 11px; border-radius: 6px; border: 1px solid #d0d7de; background: #fff; color: #24292f; font-size: 13px; font-family: inherit; width: 100%; box-sizing: border-box; }}
    .cecm-field input:focus, .cecm-field textarea:focus, .cecm-inline-input:focus, .cecm-inline-textarea:focus {{ outline: 2px solid {accent}; outline-offset: 1px; border-color: {accent}; }}
    .cecm-field textarea, .cecm-inline-textarea {{ resize: vertical; line-height: 1.5; }}
    .cecm-hint {{ font-size: 11px; font-weight: 400; color: #6e7781; }}
    .cecm-color-row {{ display: flex; align-items: center; gap: 10px; }}
    .cecm-color-input {{ -webkit-appearance: none; -moz-appearance: none; appearance: none; width: 40px; height: 34px; padding: 0; border: 2px solid #d0d7de; border-radius: 6px; background: none; cursor: pointer; }}
    .cecm-color-input::-webkit-color-swatch-wrapper {{ padding: 0; }}
    .cecm-color-input::-webkit-color-swatch {{ border: none; border-radius: 4px; }}
    .cecm-color-input::-moz-color-swatch {{ border: none; border-radius: 4px; }}
    .cecm-color-value {{ font-family: ui-monospace, monospace; font-size: 12.5px; color: #57606a; }}
    .cecm-actions {{ display: flex; gap: 10px; }}
    .cecm-btn {{ border: none; padding: 9px 18px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 700; }}
    .cecm-btn-primary {{ background: {accent}; color: #fff; }}
    .cecm-btn-primary:hover {{ filter: brightness(1.08); }}
    .cecm-btn-primary:disabled {{ opacity: 0.6; cursor: default; }}
    .cecm-btn-secondary, .cecm-inline-btn-secondary {{ background: #fff; border: 1px solid #d0d7de; color: #24292f; }}
    .cecm-btn-secondary:hover, .cecm-inline-btn-secondary:hover {{ background: #eaeef2; }}
    .cecm-item-list {{ display: flex; flex-direction: column; gap: 10px; }}
    .cecm-item {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; background: #fff; }}
    .cecm-item-row {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }}
    .cecm-item-title {{ font-weight: 700; color: #24292f; }}
    .cecm-item-preview {{ color: #57606a; font-size: 12px; max-height: 36px; overflow: hidden; line-height: 1.5; }}
    .cecm-item-actions {{ display: flex; gap: 8px; flex-shrink: 0; }}
    .cecm-item-actions button {{ background: #fff; border: 1px solid #d0d7de; color: #24292f; padding: 4px 10px; border-radius: 5px; cursor: pointer; font-size: 11.5px; }}
    .cecm-item-actions button:hover {{ background: #eaeef2; }}
    .cecm-inline-btn {{ background: {accent}; color: #fff; border: none; padding: 6px 14px; border-radius: 5px; cursor: pointer; font-size: 12px; font-weight: 700; }}
    .cecm-inline-actions {{ display: flex; gap: 8px; margin-top: 4px; }}
    .cecm-error {{ display: none; color: #cf222e; font-size: 12.5px; }}
    .cecm-image-modal {{ position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 3000; display: flex; align-items: center; justify-content: center; padding: 20px; }}
    .cecm-image-modal[hidden] {{ display: none; }}
    .cecm-image-modal-card {{ background: #fff; color: #24292f; border-radius: 12px; padding: 24px; width: 380px; max-width: 100%; box-shadow: 0 20px 56px rgba(0,0,0,0.35); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
    .cecm-image-modal-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }}
    .cecm-image-modal-head h3 {{ margin: 0; font-size: 15px; color: #24292f; }}
    #cecm-image-modal-close {{ background: none; border: none; color: #24292f; opacity: 0.6; cursor: pointer; font-size: 20px; line-height: 1; }}
    #cecm-image-modal-close:hover {{ opacity: 1; }}
    .cecm-image-modal-card .cecm-btn {{ width: 100%; }}
    #cecm-image-file-name {{ margin-top: 8px; word-break: break-all; color: #57606a; font-size: 11px; }}
    .cecm-image-modal-divider {{ text-align: center; color: #6e7781; font-size: 11px; margin: 18px 0 10px; text-transform: uppercase; letter-spacing: 0.05em; }}
    .cecm-image-modal-card .cecm-actions {{ margin-top: 16px; }}
    .cecm-image-modal-card .cecm-actions .cecm-btn {{ width: auto; flex: 1; }}
  </style>
  <script>
  (function() {{
    var TOKEN = "{safe_token}";
    var EDIT_TOKEN = "{safe_edit_token}";
    var PAGES = {pages_json};
    var POSTS = {posts_json};
    var CUSTOMIZE_BODY = {body_json};

    // Shared image-insert modal -- every Quill instance's image button
    // (there can be several editable blocks on one page) opens this same
    // modal rather than each having its own; pendingQuill tracks which
    // editor an Insert click should apply to.
    var imageModal = document.getElementById("cecm-image-modal");
    var imageFileInput = document.getElementById("cecm-image-file-input");
    var imageFileName = document.getElementById("cecm-image-file-name");
    var imageUrlInput = document.getElementById("cecm-image-url-input");
    var imageUploadBtn = document.getElementById("cecm-image-upload-btn");
    var imageInsertBtn = document.getElementById("cecm-image-insert-btn");
    var imageCancelBtn = document.getElementById("cecm-image-cancel-btn");
    var imageCloseBtn = document.getElementById("cecm-image-modal-close");
    var imageModalError = document.getElementById("cecm-image-modal-error");
    var pendingQuill = null;
    var pendingImageFile = null;

    function openImageModal(quillInstance) {{
      pendingQuill = quillInstance;
      pendingImageFile = null;
      imageFileInput.value = "";
      imageFileName.textContent = "";
      imageUrlInput.value = "";
      imageModalError.style.display = "none";
      imageInsertBtn.disabled = false;
      imageInsertBtn.textContent = "Insert";
      imageModal.hidden = false;
    }}
    function closeImageModal() {{
      imageModal.hidden = true;
      pendingQuill = null;
      pendingImageFile = null;
    }}
    window.cecmOpenImageModal = openImageModal;

    imageUploadBtn.addEventListener("click", function() {{ imageFileInput.click(); }});
    imageFileInput.addEventListener("change", function() {{
      pendingImageFile = imageFileInput.files[0] || null;
      imageFileName.textContent = pendingImageFile ? pendingImageFile.name : "";
      if (pendingImageFile) imageUrlInput.value = "";  // an uploaded file takes priority over a typed URL
    }});
    imageCancelBtn.addEventListener("click", closeImageModal);
    imageCloseBtn.addEventListener("click", closeImageModal);
    imageModal.addEventListener("click", function(e) {{ if (e.target === imageModal) closeImageModal(); }});

    imageInsertBtn.addEventListener("click", function() {{
      if (!pendingQuill) return;
      var quillRef = pendingQuill;
      var urlValue = imageUrlInput.value.trim();
      imageModalError.style.display = "none";

      if (pendingImageFile) {{
        imageInsertBtn.disabled = true;
        imageInsertBtn.textContent = "Uploading…";
        var formData = new FormData();
        formData.append("file", pendingImageFile);
        formData.append("edit_token", EDIT_TOKEN);
        fetch("/public/ai-agents/" + TOKEN + "/site/images", {{ method: "POST", body: formData }})
          .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Couldn't upload image."); }}); return r.json(); }})
          .then(function(data) {{
            var range = quillRef.getSelection(true) || {{ index: quillRef.getLength() }};
            quillRef.insertEmbed(range.index, "image", data.url, "user");
            closeImageModal();
          }})
          .catch(function(err) {{
            imageModalError.textContent = err.message || "Couldn't upload image.";
            imageModalError.style.display = "block";
          }})
          .finally(function() {{
            imageInsertBtn.disabled = false;
            imageInsertBtn.textContent = "Insert";
          }});
        return;
      }}
      if (urlValue) {{
        var range2 = quillRef.getSelection(true) || {{ index: quillRef.getLength() }};
        quillRef.insertEmbed(range2.index, "image", urlValue, "user");
        closeImageModal();
        return;
      }}
      imageModalError.textContent = "Choose a file to upload, or paste an image URL.";
      imageModalError.style.display = "block";
    }});

    // Shared by every Quill instance on this page (the "Add a page"/"Add
    // a post" editors below, and the on-page pencil editors) -- AI-
    // generated content and legacy saves are plain text, so it needs to
    // become real HTML (one <p> per blank-line-separated paragraph)
    // before Quill can display it; genuine HTML is trusted as-is.
    function cecmLooksLikeHtml(text) {{
      return /<(p|ul|ol|li|h2|h3|blockquote|strong|em|b|i|u|a|img)[ >]/i.test(text);
    }}
    function cecmPlainTextToHtml(text) {{
      if (cecmLooksLikeHtml(text)) return text;
      return (text || "").split(/\\n\\s*\\n/).map(function(s) {{
        var d = document.createElement("div");
        d.textContent = s.trim();
        return "<p>" + d.innerHTML + "</p>";
      }}).join("");
    }}
    function cecmMakeQuill(mountEl) {{
      var quill = new Quill(mountEl, {{
        theme: "snow",
        modules: {{ toolbar: [
          ["bold", "italic", "underline"],
          [{{ header: [2, 3, false] }}],
          [{{ list: "ordered" }}, {{ list: "bullet" }}],
          ["blockquote", "link", "image"],
          ["clean"],
        ] }},
      }});
      // Quill's built-in image button reads a local file and embeds it
      // as a giant base64 data: URI, which would blow past a content
      // field's length limit almost immediately -- every Quill instance
      // uses the shared upload-or-paste-a-URL modal instead.
      quill.getModule("toolbar").addHandler("image", function() {{ openImageModal(quill); }});
      return quill;
    }}
    var customizeBodyQuill = cecmMakeQuill(document.getElementById("cecm-body-content-mount"));
    // CUSTOMIZE_BODY is either real HTML (saved by this same editor
    // before) or legacy plain text (written before it existed) -- Quill
    // needs HTML either way.
    customizeBodyQuill.clipboard.dangerouslyPasteHTML(cecmPlainTextToHtml(CUSTOMIZE_BODY));
    var newPageQuill = cecmMakeQuill(document.getElementById("cecm-new-page-content-mount"));
    var newPostQuill = cecmMakeQuill(document.getElementById("cecm-new-post-content-mount"));

    var bar = document.getElementById("cecm-admin-bar");
    // NOT cached at parse time: #cecm-page-content belongs to _shell()'s
    // own markup, which is written to the response AFTER this entire
    // admin-bar string (it's everything the {{admin_bar}} placeholder
    // expands to) -- so when this script tag first runs, the HTML parser
    // hasn't reached that div yet and document.getElementById would
    // return null forever, silently no-op'ing every "shift the page
    // content over" call below (the panel would still visibly open, but
    // the live site behind it never actually moved, so it sat hidden
    // underneath the fixed sidebar/panel instead of sliding into view
    // next to them). Looking it up fresh on each use avoids that --
    // by the time a user can actually click anything, the whole page has
    // long finished parsing.
    function getPageContent() {{ return document.getElementById("cecm-page-content"); }}
    document.getElementById("cecm-dismiss-bar").addEventListener("click", function() {{
      bar.style.display = "none";
      // The nav rail is gone, so its permanent left offset (and any
      // panel's extra shift) must go with it or the page content is
      // left stranded with dead margin on the left.
      var pc = getPageContent();
      if (pc) {{ pc.style.marginLeft = "0"; pc.classList.remove("cecm-shifted"); }}
    }});
    // The single source of truth for which panel is open. Deliberately
    // NOT re-derived from panel.style.display on each click: that only
    // reflects an *inline* style, and these panels start out hidden via
    // the .cecm-panel{{display:none}} STYLESHEET rule with no inline
    // style set at all -- so before showPanel() ever runs once,
    // panel.style.display reads as "" (not "none"), which made the very
    // first click on any tab silently read as "already open" and close
    // itself instead of opening. Every tab needed two clicks.
    var currentPanel = null;
    function showPanel(name) {{
      document.querySelectorAll(".cecm-panel").forEach(function(p) {{ p.style.display = "none"; }});
      document.querySelectorAll(".cecm-tab-btn").forEach(function(b) {{ b.classList.remove("active"); }});
      var panel = name ? document.getElementById("cecm-panel-" + name) : null;
      var pc = getPageContent();
      if (panel) {{
        panel.style.display = "block";
        if (pc) pc.classList.add("cecm-shifted");
        var btn = document.querySelector('.cecm-tab-btn[data-panel="' + name + '"]');
        if (btn) btn.classList.add("active");
        currentPanel = name;
      }} else {{
        if (pc) pc.classList.remove("cecm-shifted");
        currentPanel = null;
      }}
    }}
    document.addEventListener("keydown", function(e) {{
      if (e.key === "Escape") showPanel(null);
    }});
    document.querySelectorAll(".cecm-tab-btn").forEach(function(btn) {{
      btn.addEventListener("click", function() {{
        var name = btn.getAttribute("data-panel");
        showPanel(currentPanel === name ? null : name);
      }});
    }});
    document.querySelectorAll(".cecm-panel-cancel").forEach(function(btn) {{
      btn.addEventListener("click", function() {{ showPanel(null); }});
    }});

    var accentInput = document.getElementById("cecm-accent");
    var accentValueEl = document.getElementById("cecm-accent-value");
    accentInput.addEventListener("input", function() {{ accentValueEl.textContent = accentInput.value; }});

    function showError(id, message) {{
      var el = document.getElementById(id);
      el.textContent = message;
      el.style.display = "block";
    }}

    document.getElementById("cecm-customize-save").addEventListener("click", function() {{
      var errorEl = document.getElementById("cecm-customize-error");
      errorEl.style.display = "none";
      fetch("/public/ai-agents/" + TOKEN + "/site", {{
        method: "PATCH",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
          edit_token: EDIT_TOKEN,
          headline: document.getElementById("cecm-headline").value || null,
          subheadline: document.getElementById("cecm-subheadline").value || null,
          is_rich_html: true,
          body: customizeBodyQuill.root.innerHTML,
          seo_description: document.getElementById("cecm-seo-description").value || null,
          footer_tagline: document.getElementById("cecm-footer-tagline").value || null,
          accent_color: document.getElementById("cecm-accent").value,
          contact_note: document.getElementById("cecm-contact-note").value || null,
          contact_email: document.getElementById("cecm-contact-email").value || null,
          contact_phone: document.getElementById("cecm-contact-phone").value || null,
          contact_address: document.getElementById("cecm-contact-address").value || null,
        }}),
      }})
        .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Couldn't save changes."); }}); return r.json(); }})
        .then(function() {{ window.location.reload(); }})
        .catch(function(err) {{ showError("cecm-customize-error", err.message); }});
    }});

    function cecmStripHtml(str) {{
      var tmp = document.createElement("div");
      tmp.innerHTML = str || "";
      return tmp.textContent || "";
    }}

    function renderList(items, containerId, kind) {{
      var container = document.getElementById(containerId);
      container.innerHTML = "";
      items.forEach(function(item) {{
        var box = document.createElement("div");
        box.className = "cecm-item";
        var row = document.createElement("div");
        row.className = "cecm-item-row";
        var title = document.createElement("div");
        title.className = "cecm-item-title";
        title.textContent = item.title;
        var actions = document.createElement("div");
        actions.className = "cecm-item-actions";
        var editBtn = document.createElement("button");
        editBtn.textContent = "Edit";
        var delBtn = document.createElement("button");
        delBtn.textContent = "Delete";
        actions.appendChild(editBtn);
        actions.appendChild(delBtn);
        row.appendChild(title);
        row.appendChild(actions);
        var preview = document.createElement("div");
        preview.className = "cecm-item-preview";
        preview.textContent = item.excerpt || cecmStripHtml(item.content) || "";
        box.appendChild(row);
        box.appendChild(preview);
        container.appendChild(box);

        editBtn.addEventListener("click", function() {{
          box.innerHTML = "";
          var titleInput = document.createElement("input");
          titleInput.className = "cecm-inline-input";
          titleInput.value = item.title;
          titleInput.style.marginBottom = "8px";
          box.appendChild(titleInput);
          var excerptInput = null;
          if (kind === "posts") {{
            excerptInput = document.createElement("input");
            excerptInput.className = "cecm-inline-input";
            excerptInput.value = item.excerpt || "";
            excerptInput.placeholder = "Excerpt";
            excerptInput.style.marginBottom = "8px";
            box.appendChild(excerptInput);
          }}
          var contentMount = document.createElement("div");
          contentMount.className = "cecm-quill-mount";
          contentMount.style.marginBottom = "8px";
          box.appendChild(contentMount);
          var itemQuill = cecmMakeQuill(contentMount);
          // item.content is either real HTML (saved by this same editor
          // before) or legacy plain text (written before it existed) --
          // Quill needs HTML either way.
          itemQuill.clipboard.dangerouslyPasteHTML(cecmPlainTextToHtml(item.content || ""));
          var actionsRow = document.createElement("div");
          actionsRow.className = "cecm-inline-actions";
          var saveBtn = document.createElement("button");
          saveBtn.textContent = "Save";
          saveBtn.className = "cecm-inline-btn";
          var cancelBtn = document.createElement("button");
          cancelBtn.textContent = "Cancel";
          cancelBtn.className = "cecm-inline-btn cecm-inline-btn-secondary";
          actionsRow.appendChild(saveBtn);
          actionsRow.appendChild(cancelBtn);
          box.appendChild(actionsRow);

          cancelBtn.addEventListener("click", function() {{ window.location.reload(); }});
          saveBtn.addEventListener("click", function() {{
            var payload = {{ edit_token: EDIT_TOKEN, title: titleInput.value, is_rich_html: true, content: itemQuill.root.innerHTML }};
            if (excerptInput) payload.excerpt = excerptInput.value;
            fetch("/public/ai-agents/" + TOKEN + "/site/" + kind + "/" + item.id, {{
              method: "PATCH",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify(payload),
            }})
              .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Couldn't save."); }}); return r.json(); }})
              .then(function() {{ window.location.reload(); }})
              .catch(function(err) {{ showError(kind === "pages" ? "cecm-pages-error" : "cecm-posts-error", err.message); }});
          }});
        }});

        delBtn.addEventListener("click", function() {{
          if (!window.confirm("Delete \\"" + item.title + "\\"?")) return;
          fetch("/public/ai-agents/" + TOKEN + "/site/" + kind + "/" + item.id + "?edit_token=" + encodeURIComponent(EDIT_TOKEN), {{
            method: "DELETE",
          }})
            .then(function(r) {{ if (!r.ok && r.status !== 204) throw new Error("Couldn't delete."); }})
            .then(function() {{ window.location.reload(); }})
            .catch(function(err) {{ showError(kind === "pages" ? "cecm-pages-error" : "cecm-posts-error", err.message); }});
        }});
      }});
    }}
    renderList(PAGES, "cecm-pages-list", "pages");
    renderList(POSTS, "cecm-posts-list", "posts");

    document.getElementById("cecm-add-page").addEventListener("click", function() {{
      var title = document.getElementById("cecm-new-page-title").value.trim();
      if (!title) return;
      fetch("/public/ai-agents/" + TOKEN + "/site/pages", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ edit_token: EDIT_TOKEN, title: title, is_rich_html: true, content: newPageQuill.root.innerHTML }}),
      }})
        .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Couldn't add page."); }}); return r.json(); }})
        .then(function() {{ window.location.reload(); }})
        .catch(function(err) {{ showError("cecm-pages-error", err.message); }});
    }});

    document.getElementById("cecm-add-post").addEventListener("click", function() {{
      var title = document.getElementById("cecm-new-post-title").value.trim();
      if (!title) return;
      fetch("/public/ai-agents/" + TOKEN + "/site/posts", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
          edit_token: EDIT_TOKEN, title: title,
          excerpt: document.getElementById("cecm-new-post-excerpt").value,
          is_rich_html: true, content: newPostQuill.root.innerHTML,
        }}),
      }})
        .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Couldn't add post."); }}); return r.json(); }})
        .then(function() {{ window.location.reload(); }})
        .catch(function(err) {{ showError("cecm-posts-error", err.message); }});
    }});

    function cecmGenerateItemDraft(kind, titleInputId, excerptInputId, quill, errorElId, btnId) {{
      var btn = document.getElementById(btnId);
      var topic = document.getElementById(titleInputId).value.trim();
      var errorEl = document.getElementById(errorElId);
      errorEl.style.display = "none";
      btn.disabled = true;
      btn.textContent = "Generating…";
      fetch("/public/ai-agents/" + TOKEN + "/site/draft-item", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ edit_token: EDIT_TOKEN, kind: kind, topic: topic }}),
      }})
        .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Couldn't generate a draft."); }}); return r.json(); }})
        .then(function(data) {{
          document.getElementById(titleInputId).value = data.title;
          if (excerptInputId) document.getElementById(excerptInputId).value = data.excerpt || "";
          quill.setContents([]);
          quill.clipboard.dangerouslyPasteHTML(cecmPlainTextToHtml(data.content));
        }})
        .catch(function(err) {{
          errorEl.textContent = err.message;
          errorEl.style.display = "block";
        }})
        .finally(function() {{
          btn.disabled = false;
          btn.textContent = "✨ Generate with AI";
        }});
    }}
    document.getElementById("cecm-generate-page-btn").addEventListener("click", function() {{
      cecmGenerateItemDraft("page", "cecm-new-page-title", null, newPageQuill, "cecm-generate-page-error", "cecm-generate-page-btn");
    }});
    document.getElementById("cecm-generate-post-btn").addEventListener("click", function() {{
      cecmGenerateItemDraft("post", "cecm-new-post-title", "cecm-new-post-excerpt", newPostQuill, "cecm-generate-post-error", "cecm-generate-post-btn");
    }});

    document.getElementById("cecm-generate-btn").addEventListener("click", function() {{
      var btn = document.getElementById("cecm-generate-btn");
      btn.disabled = true;
      btn.textContent = "Generating…";
      fetch("/public/ai-agents/" + TOKEN + "/site/generate?edit_token=" + encodeURIComponent(EDIT_TOKEN), {{ method: "POST" }})
        .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Couldn't generate content."); }}); return r.json(); }})
        .then(function() {{ window.location.reload(); }})
        .catch(function(err) {{
          btn.disabled = false;
          btn.textContent = "Regenerate & publish full site";
          showError("cecm-generate-error", err.message);
        }});
    }});

    document.getElementById("cecm-edit-btn").addEventListener("click", function() {{
      var instructionEl = document.getElementById("cecm-edit-instruction");
      var instruction = instructionEl.value.trim();
      if (!instruction) return;
      var btn = document.getElementById("cecm-edit-btn");
      var resultEl = document.getElementById("cecm-edit-result");
      resultEl.style.display = "none";
      document.getElementById("cecm-edit-error").style.display = "none";
      btn.disabled = true;
      btn.textContent = "Applying…";
      fetch("/public/ai-agents/" + TOKEN + "/site/edit", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ edit_token: EDIT_TOKEN, instruction: instruction }}),
      }})
        .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Couldn't apply that change."); }}); return r.json(); }})
        .then(function(data) {{
          resultEl.textContent = data.summary + " Reloading…";
          resultEl.style.display = "block";
          setTimeout(function() {{ window.location.reload(); }}, 900);
        }})
        .catch(function(err) {{
          btn.disabled = false;
          btn.textContent = "Apply change";
          showError("cecm-edit-error", err.message);
        }});
    }});

    // The pencil-edit buttons scattered across the page itself (hero,
    // About, page/post content, footer tagline, contact note) -- a
    // generic wrapper: any element carrying [data-cecm-field] inside a
    // .cecm-editable block gets swapped for an input/textarea on click,
    // and every field in that block is saved together via one PATCH to
    // the block's own data-cecm-endpoint (always PATCH -- every editable
    // block here targets either the site or one page/post, both PATCH).
    // This admin bar's own markup sits BEFORE the rest of the page in the
    // HTML (it's the first thing in <body>), so this script executes
    // before <header>/.page-wrap/<footer> -- where every .cecm-editable
    // block actually lives -- have even been parsed yet. Querying for
    // them here directly would always find nothing and silently wire up
    // zero pencil buttons; waiting for DOMContentLoaded is what makes
    // clicking them actually do something.
    document.addEventListener("DOMContentLoaded", function() {{
      document.querySelectorAll(".cecm-editable").forEach(function(block) {{
        var pencil = block.querySelector(".cecm-edit-pencil");
        var viewEl = block.querySelector(".cecm-editable-view");
        var endpoint = block.getAttribute("data-cecm-endpoint");
        if (!pencil || !viewEl || !endpoint) return;

        pencil.addEventListener("click", function() {{
          var controls = {{}};  // name -> object with a getValue() method
          var hasRichField = false;  // any Quill-backed field in this block? tells the server to sanitize on save
          var fieldEls = viewEl.querySelectorAll("[data-cecm-field]");
          fieldEls.forEach(function(el) {{
            var name = el.getAttribute("data-cecm-field");
            var isLong = el.hasAttribute("data-cecm-multiline");
            if (isLong) hasRichField = true;
            var rawSource = el.querySelector(".cecm-raw-source");
            var currentValue = rawSource ? rawSource.value : el.textContent;
            // Match the new control to the exact space the rendered
            // content was taking up, so swapping to edit mode doesn't
            // suddenly shrink a multi-paragraph block down to a few
            // generic input rows.
            var rect = el.getBoundingClientRect();

            if (isLong) {{
              var mount = document.createElement("div");
              mount.className = "cecm-quill-mount";
              mount.style.minHeight = Math.max(rect.height, 160) + "px";
              el.replaceWith(mount);
              var quill = cecmMakeQuill(mount);
              // currentValue is either real HTML (saved by this same
              // editor before) or legacy plain text (written before it
              // existed) -- Quill needs HTML either way.
              quill.clipboard.dangerouslyPasteHTML(cecmPlainTextToHtml(currentValue));
              controls[name] = {{ getValue: function() {{ return quill.root.innerHTML; }} }};
            }} else {{
              var control = document.createElement("input");
              control.className = "cecm-inline-input";
              control.value = currentValue;
              control.style.width = "100%";
              el.replaceWith(control);
              controls[name] = {{ getValue: function() {{ return control.value; }} }};
            }}
          }});

          var actions = document.createElement("div");
          actions.className = "cecm-inline-actions";
          var saveBtn = document.createElement("button");
          saveBtn.type = "button";
          saveBtn.textContent = "Save";
          saveBtn.className = "cecm-inline-btn";
          var aiBtn = document.createElement("button");
          aiBtn.type = "button";
          aiBtn.textContent = "✨ Generate with AI";
          aiBtn.className = "cecm-inline-btn cecm-inline-btn-secondary";
          var cancelBtn = document.createElement("button");
          cancelBtn.type = "button";
          cancelBtn.textContent = "Cancel";
          cancelBtn.className = "cecm-inline-btn cecm-inline-btn-secondary";
          actions.appendChild(saveBtn);
          actions.appendChild(aiBtn);
          actions.appendChild(cancelBtn);
          viewEl.appendChild(actions);
          pencil.style.display = "none";

          cancelBtn.addEventListener("click", function() {{ window.location.reload(); }});

          saveBtn.addEventListener("click", function() {{
            var payload = {{ edit_token: EDIT_TOKEN }};
            if (hasRichField) payload.is_rich_html = true;
            Object.keys(controls).forEach(function(name) {{ payload[name] = controls[name].getValue(); }});
            saveBtn.disabled = true;
            aiBtn.disabled = true;
            cancelBtn.disabled = true;
            saveBtn.textContent = "Saving…";
            fetch(endpoint, {{
              method: "PATCH",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify(payload),
            }})
              .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Couldn't save."); }}); return r.json(); }})
              .then(function() {{ window.location.reload(); }})
              .catch(function(err) {{
                saveBtn.disabled = false;
                aiBtn.disabled = false;
                cancelBtn.disabled = false;
                saveBtn.textContent = "Save";
                window.alert(err.message || "Couldn't save that change.");
              }});
          }});

          // Reads this block's own current content (via the same
          // knowledge-base-aware endpoint the admin bar's "describe a
          // specific change" box uses) and rewrites just this block --
          // no separate backend endpoint needed, since /site/edit
          // already reads the site's current content before deciding
          // what a free-form instruction should change.
          aiBtn.addEventListener("click", function() {{
            var guidance = window.prompt(
              "Optional: describe how you'd like this rewritten (leave blank to just improve it from the knowledge base):", "",
            );
            if (guidance === null) return;
            var label = block.getAttribute("data-cecm-label") || "this section";
            var instruction = "Rewrite " + label + " based on the knowledge base." + (guidance ? " " + guidance : "");
            saveBtn.disabled = true;
            aiBtn.disabled = true;
            cancelBtn.disabled = true;
            aiBtn.textContent = "Generating…";
            fetch("/public/ai-agents/" + TOKEN + "/site/edit", {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify({{ edit_token: EDIT_TOKEN, instruction: instruction }}),
            }})
              .then(function(r) {{ if (!r.ok) return r.json().then(function(e) {{ throw new Error(e.detail || "Couldn't generate content."); }}); return r.json(); }})
              .then(function() {{ window.location.reload(); }})
              .catch(function(err) {{
                saveBtn.disabled = false;
                aiBtn.disabled = false;
                cancelBtn.disabled = false;
                aiBtn.textContent = "✨ Generate with AI";
                window.alert(err.message || "Couldn't generate content.");
              }});
          }});
        }});
      }});
    }});

    var initialPanel = "{safe_active_panel}";
    if (initialPanel) {{
      showPanel(initialPanel);
      bar.scrollIntoView({{ block: "start" }});
    }}
  }})();
  </script>"""


def render_home(agent: dict, site: dict, links: Links, pages: list[dict], has_posts: bool, has_contact: bool,
                admin_bar: str = "", posts: list[dict] | None = None) -> str:
    site = site or {}
    posts = posts or []
    headline = site.get("headline") or agent["name"]
    subheadline = (
        site.get("subheadline") or agent["description"] or "Ask a question and get an instant, grounded answer."
    )
    body = (
        site.get("body")
        or "This assistant answers using a live, always-current knowledge base — no separate re-indexing "
           "step, ever. Ask it anything within its scope and get a grounded answer in seconds."
    )
    eyebrow = html.escape(f"{agent['resource_name']} · AI Knowledge Assistant")

    hero_actions = ['<button type="button" class="btn" onclick="cecmOpenChat()">Chat now</button>']
    if has_contact:
        hero_actions.append(f'<a class="btn btn-secondary" href="{links.contact()}">Contact us</a>')
    elif pages:
        hero_actions.append(f'<a class="btn btn-secondary" href="{links.page(pages[0]["slug"])}">Learn more</a>')

    sections = []
    if pages:
        cards = "\n".join(
            f"""      <a class="mini-card" href="{links.page(p['slug'])}">
        <h3>{html.escape(p['title'])}</h3>
        <p>{html.escape(_excerpt(p['content']))}</p>
        <span class="read-more">Read more →</span>
      </a>"""
            for p in pages
        )
        sections.append(f"""
    <div class="section">
      <div class="section-head"><h2>Explore</h2><p>Topics grounded in this assistant's own knowledge base.</p></div>
      <div class="grid-cards">
{cards}
      </div>
    </div>""")
    if posts:
        recent = posts[:3]
        cards = "\n".join(
            f"""      <a class="mini-card" href="{links.post(p['slug'])}">
        <h3>{html.escape(p['title'])}</h3>
        <p>{html.escape(p['excerpt'] or _excerpt(p['content']))}</p>
        <span class="read-more">Read more →</span>
      </a>"""
            for p in recent
        )
        sections.append(f"""
    <div class="section">
      <div class="section-head"><h2>From the blog</h2><p>Recent posts drawn from the same source material.</p></div>
      <div class="grid-cards">
{cards}
      </div>
      <div style="text-align:center;margin-top:20px;"><a class="btn btn-secondary" href="{links.blog()}">View all posts</a></div>
    </div>""")
    if has_contact:
        note = html.escape(site.get("contact_note") or "Get in touch and we'll get back to you.")
        sections.append(f"""
    <div class="cta-band">
      <h2>Still have questions?</h2>
      <p>{note}</p>
      <a class="btn" href="{links.contact()}">Contact us</a>
    </div>""")

    site_endpoint = f"{API_BASE_URL}/public/ai-agents/{links.token}/site"
    hero_editable = _editable_block(links.edit_token, site_endpoint, "the homepage headline and subheadline",
        [("headline", "h1", headline, False), ("subheadline", "p", subheadline, False)])
    about_editable = _editable_block(links.edit_token, site_endpoint, "the About section",
        [("body", "div", body, True)])

    body_html = f"""
    <div class="hero">
      <span class="eyebrow">{eyebrow}</span>
      {hero_editable}
      <div class="hero-actions">{"".join(hero_actions)}</div>
    </div>
    <div class="card about-card">
      <h2>About</h2>
      {about_editable}
    </div>{"".join(sections)}"""
    return _shell(agent, site, body_html, admin_bar, links, pages, has_posts, has_contact, "home")


def render_page(agent: dict, site: dict, page: dict, links: Links, pages: list[dict], has_posts: bool,
                has_contact: bool, admin_bar: str = "") -> str:
    page_endpoint = f"{API_BASE_URL}/public/ai-agents/{links.token}/site/pages/{page['id']}"
    page_editable = _editable_block(links.edit_token, page_endpoint, f"the page titled \"{page['title']}\"",
        [("title", "h1", page["title"], False), ("content", "div", page["content"], True)])
    body_html = f"""
    <div class="card">
      {page_editable}
    </div>"""
    return _shell(agent, site or {}, body_html, admin_bar, links, pages, has_posts, has_contact, f"page:{page['slug']}")


def render_blog_index(agent: dict, site: dict, posts: list[dict], links: Links, pages: list[dict],
                       has_contact: bool, admin_bar: str = "") -> str:
    if posts:
        items = "\n".join(
            f"""      <div class="post-list-item">
        <h2><a href="{links.post(p['slug'])}">{html.escape(p['title'])}</a></h2>
        <p>{html.escape(p['excerpt'] or _excerpt(p['content'], 180))}</p>
        <a class="read-more" href="{links.post(p['slug'])}">Read more →</a>
      </div>"""
            for p in posts
        )
    else:
        items = '      <p style="color:#57606a;">No posts yet.</p>'
    body_html = f"""
    <div class="hero"><h1>Blog</h1></div>
    <div class="post-list">
{items}
    </div>"""
    return _shell(agent, site or {}, body_html, admin_bar, links, pages, True, has_contact, "blog")


def render_post(agent: dict, site: dict, post: dict, links: Links, pages: list[dict], has_contact: bool,
                admin_bar: str = "") -> str:
    post_endpoint = f"{API_BASE_URL}/public/ai-agents/{links.token}/site/posts/{post['id']}"
    post_editable = _editable_block(links.edit_token, post_endpoint, f"the post titled \"{post['title']}\"",
        [("title", "h1", post["title"], False), ("content", "div", post["content"], True)])
    body_html = f"""
    <div class="card">
      {post_editable}
    </div>"""
    return _shell(agent, site or {}, body_html, admin_bar, links, pages, True, has_contact, f"post:{post['slug']}")


def render_contact(agent: dict, site: dict, links: Links, pages: list[dict], has_posts: bool,
                    admin_bar: str = "") -> str:
    site = site or {}
    note_raw = site.get("contact_note") or "Get in touch with any questions."
    rows = []
    if site.get("contact_email"):
        rows.append(f'<div class="contact-item"><strong>Email</strong> {html.escape(site["contact_email"])}</div>')
    if site.get("contact_phone"):
        rows.append(f'<div class="contact-item"><strong>Phone</strong> {html.escape(site["contact_phone"])}</div>')
    if site.get("contact_address"):
        rows.append(f'<div class="contact-item"><strong>Address</strong> {html.escape(site["contact_address"])}</div>')
    rows_html = "\n      ".join(rows) if rows else '<p style="color:#57606a;">No contact details added yet.</p>'
    note_editable = _editable_block(
        links.edit_token, f"{API_BASE_URL}/public/ai-agents/{links.token}/site", "the contact page note",
        [("contact_note", "p", note_raw, False)],
    )
    body_html = f"""
    <div class="card">
      <h1>Contact</h1>
      {note_editable}
      {rows_html}
    </div>"""
    return _shell(agent, site, body_html, admin_bar, links, pages, has_posts, True, "contact")


def render_static_site(agent: dict, site: dict | None, pages: list[dict], posts: list[dict]) -> dict[str, str]:
    """Every page of the site rendered in "static" mode — flat filenames,
    no admin bar, no edit token — as a {filename: html} dict ready to zip.
    This, not the live dynamic routes, is what "deployable on any server"
    actually ships: plain files with no dependency on this app except the
    one iframe on each page that still talks to the live chat endpoint."""
    site = site or {}
    links = Links(mode="static", token=agent["public_token"])
    has_posts = bool(posts)
    has_contact = bool(site.get("contact_email") or site.get("contact_phone") or site.get("contact_address") or site.get("contact_note"))

    files = {"index.html": render_home(agent, site, links, pages, has_posts, has_contact, posts=posts)}
    for p in pages:
        files[f"page-{p['slug']}.html"] = render_page(agent, site, p, links, pages, has_posts, has_contact)
    if has_posts:
        files["blog.html"] = render_blog_index(agent, site, posts, links, pages, has_contact)
        for p in posts:
            files[f"blog-{p['slug']}.html"] = render_post(agent, site, p, links, pages, has_contact)
    if has_contact:
        files["contact.html"] = render_contact(agent, site, links, pages, has_posts)
    return files
