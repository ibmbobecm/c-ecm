"""Server-side sanitization for admin-authored rich-text content -- the
inline pencil-edit editor's Quill output (site body, page/post content).

Quill's own toolbar restricts what a well-behaved browser client can
produce, but that is a UX nicety, not a security boundary: a request can
always be sent to the API directly, bypassing the UI entirely. Every
rich-text field is run through sanitize_rich_html() at write time
whenever the caller declares the value as rich HTML (via that request's
own is_rich_html flag -- see schemas.py), so a compromised or malicious
edit_token can inject at most inert, harmless markup -- never a script,
an event handler, or a javascript: link -- into a page every visitor's
browser will render unescaped.

Sanitization is opt-in per request, not automatic for every write to a
content field: bleach parses EVERY input as HTML regardless of the
author's intent, so running it unconditionally over plain text written
through the site's older plain-textarea forms (or AI-generated prose)
would silently mangle any coincidental "<word>"-shaped substring, e.g.
"note the <important> flag" or "wrap it in <div> tags" written as
ordinary prose, not markup.
"""

import bleach

_ALLOWED_TAGS = [
    "p", "br", "strong", "b", "em", "i", "u", "s", "strike",
    "h2", "h3", "ul", "ol", "li", "blockquote", "a", "img",
]
_ALLOWED_ATTRIBUTES = {"a": ["href", "target", "rel"], "img": ["src", "alt"]}
# Deliberately excludes "data" -- the editor's image button inserts a URL
# the admin typed, never a local file read as a base64 data: URI (which
# would blow past the field's own length limit almost immediately), so
# there's no legitimate reason for a data: URI to appear here.
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def sanitize_rich_html(value: str | None) -> str | None:
    """Cleans `value` down to the allowlist above. Only ever call this
    for a field the caller has explicitly declared as rich HTML -- see
    the module docstring for why this must not run unconditionally over
    every content write."""
    if value is None:
        return None
    return bleach.clean(
        value, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES, protocols=_ALLOWED_PROTOCOLS, strip=True,
    )
