import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from . import (
    activity_service,
    ai_agents_store,
    ai_service,
    comments_store,
    connections_store,
    esignature_store,
    events_store,
    groups_store,
    locks_store,
    metadata_store,
    notification_service,
    notifications_store,
    resource_permissions_store,
    retention_service,
    retention_store,
    saved_searches_store,
    settings_store,
    share_links_store,
    tags_store,
    users_store,
    webhook_service,
    workflows_store,
)
from .routers import (
    access_grants,
    activity,
    admin,
    ai,
    ai_agents,
    auth,
    comments,
    connections,
    esignature,
    files,
    folders,
    groups,
    locks,
    metadata,
    notifications,
    permissions,
    public_ai_agents,
    retention,
    saml,
    search,
    sharing,
    tags,
    users,
    webhooks,
    workflows,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Stores — order matters only for foreign-key-style init dependencies;
    # users_store must come first so the admin seed account is available.
    # (users_store.init_db() also calls groups_store.init_db() itself if it
    # needs to run the roles->groups migration, but that path only fires on
    # an existing pre-groups DB — call it here too so the tables always
    # exist for a fresh install, before any group-editor route can be hit.)
    users_store.init_db()
    groups_store.init_db()
    connections_store.init_db()
    settings_store.init_db()
    events_store.init_db()
    tags_store.init_db()
    notifications_store.init_db()
    comments_store.init_db()
    saved_searches_store.init_db()
    share_links_store.init_db()
    locks_store.init_db()
    metadata_store.init_db()
    webhook_service.init_db()
    workflows_store.init_db()
    retention_store.init_db()
    esignature_store.init_db()
    ai_agents_store.init_db()
    resource_permissions_store.init_db()

    # Picks up any AI/Watson settings saved via Admin Settings on a previous
    # run — otherwise a restart would silently fall back to whatever's in
    # the environment, discarding what was configured through the UI.
    ai_service.refresh_from_settings()

    # Observer wiring — notification_service and webhook_service both react
    # to activity events.  Each is registered once so routers never need to
    # know they exist.
    activity_service.subscribe(notification_service.on_event)
    activity_service.subscribe(webhook_service.on_event)

    # Retention scheduler — check for due records once every hour.
    # APScheduler runs in a background thread so it doesn't block the
    # async event loop.  The job is deliberately lenient: if the host
    # restarts mid-window the next run will catch any missed records.
    scheduler = BackgroundScheduler(timezone="UTC")
    # retention_store.run_due_check() only IDENTIFIES due records — it was
    # previously wired here directly, so the scheduler ran hourly and threw
    # away the result: nothing was ever actually deleted, archived, or
    # flagged. retention_service.apply_due_actions() is what performs each
    # record's configured action and records the outcome.
    scheduler.add_job(retention_service.apply_due_actions, "interval", hours=1, id="retention_due_check")
    scheduler.start()
    logger.info("Retention scheduler started (interval=1h)")

    yield

    scheduler.shutdown(wait=False)
    logger.info("Retention scheduler stopped")


_API_DESCRIPTION = """
C-ECM is a unified REST API over many document/content-management backends —
IBM FileNet, S3-compatible object storage, Google Drive, Box, SharePoint,
and 50+ other connectors — behind one consistent set of endpoints for
files, folders, search, sharing, workflows, and more.

### Authentication
1. `POST /auth/login` with a username and password to get an `access_token`
   (valid 24 hours by default — configurable via `FD_JWT_EXPIRE_MINUTES`).
2. Click **Authorize** above and paste the token as-is; Swagger adds the
   `Bearer ` prefix for you. Every request made from this page will then
   carry it.
3. SAML single sign-on (`/saml/...`) reaches the same kind of session
   through a browser redirect instead — not practical for a non-interactive
   integration, so most integrators will use step 1.

### Working with a connection
Most endpoints under `folders`, `files`, `tags`, `comments`, `metadata`,
`locks`, `sharing`, `workflows`, and `esignature` require an
`X-Connection-Id` header naming *which* backend connection to operate
against. Call `GET /connections` once authenticated to list the
connections available to your account and their ids.

### Permissions
Some endpoints require a specific feature grant (e.g. `manage_users`,
`manage_workflow_definitions`) via the group(s) your account belongs to —
a superadmin account bypasses every check. A `403` response means your
account is missing the required feature; a `401` means the token is
missing, invalid, or expired.
""".strip()

_OPENAPI_TAGS = [
    {"name": "auth", "description": "Log in, log out, and check who you are. Start here to get a bearer token."},
    {"name": "connections", "description": "Connect to a storage backend and list your connections. Most other endpoints need an X-Connection-Id from here."},
    {"name": "folders", "description": "Browse, create, rename, move, and trash folders within a connection."},
    {"name": "files", "description": "Upload, download, version, rename, move, and trash files within a connection."},
    {"name": "search", "description": "Search within one connection, or across every connection at once."},
    {"name": "tags", "description": "Attach and remove color-coded tags on files and folders."},
    {"name": "comments", "description": "Threaded comments and @mentions on a file or folder."},
    {"name": "metadata", "description": "Document classes (custom field schemas) and the metadata values attached to a resource."},
    {"name": "locks", "description": "Check a file out for exclusive editing, then check it back in."},
    {"name": "sharing", "description": "Create shareable links (view/comment/edit) for files and folders, including password-protected anonymous access."},
    {"name": "sharing-public", "description": "The public, unauthenticated endpoint a share link itself resolves to."},
    {"name": "permissions", "description": "Read a resource's native permissions as reported by its storage backend."},
    {"name": "access-grants", "description": "C-ECM's own view/edit access control for individual files and folders — separate from, and layered on top of, the backend's native permissions."},
    {"name": "workflows", "description": "Multi-step approval workflows: design a workflow, request approval on one or more documents, approve/reject, reassign, or cancel."},
    {"name": "esignature", "description": "Send a document out for e-signature (DocuSign) and track its status."},
    {"name": "esignature-public", "description": "The public DocuSign webhook callback."},
    {"name": "retention", "description": "Retention policies and legal holds for compliance."},
    {"name": "ai", "description": "AI-powered document intelligence: summarize, classify, extract, and chat with document content."},
    {"name": "ai-agents", "description": "Create a scoped AI agent (chat / embed / demo) over a folder or file for a specific audience."},
    {"name": "ai-agents-admin", "description": "Admin management of AI agents across the whole app."},
    {"name": "ai-agents-public-page", "description": "The public-facing agent chat page and widget embed — no login required, gated by the agent's own unguessable public token."},
    {"name": "webhooks", "description": "Subscribe an external URL (or Slack/Discord) to activity events."},
    {"name": "activity", "description": "The audit log: every event recorded across the app, with summaries and alerting."},
    {"name": "notifications", "description": "The in-app notification inbox."},
    {"name": "users", "description": "Manage user accounts. Requires superadmin or the 'manage_users' feature."},
    {"name": "groups", "description": "Manage groups, their feature grants, and membership — the core of C-ECM's access-control model."},
    {"name": "saml", "description": "SAML single sign-on configuration and the SSO login flow itself."},
    {"name": "admin", "description": "Server-wide settings: OAuth app credentials, AI backend configuration, SAML settings."},
]

app = FastAPI(
    title="C-ECM API",
    description=_API_DESCRIPTION,
    version="1.0.0",
    openapi_tags=_OPENAPI_TAGS,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # A plain allowlist can't name every LAN IP/hostname this dev server
    # might be reached at, so match any http origin on the frontend's dev
    # port instead of hardcoding localhost/127.0.0.1 only — but the previous
    # pattern (`http://[^/]+:5174`) matched *any* hostname whatsoever, not
    # just LAN ones as this comment claims: a page served from any public
    # domain on port 5174 (an attacker's own server, port-forwarded or
    # otherwise) would satisfy it and be granted CORS access. Scoped down to
    # loopback + the three private-network ranges (RFC 1918) actually
    # reachable from a LAN, which is what this was meant to allow.
    allow_origin_regex=(
        r"http://(localhost|127\.0\.0\.1|"
        r"10(?:\.\d{1,3}){3}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|"
        r"192\.168(?:\.\d{1,3}){2}"
        r"):5174"
    ),
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(groups.router)
app.include_router(saml.router)
app.include_router(connections.router)
app.include_router(admin.router)
app.include_router(folders.router)
app.include_router(files.router)
app.include_router(ai.router)
app.include_router(ai.status_router)
app.include_router(search.router)
app.include_router(activity.router)
app.include_router(tags.router)
app.include_router(notifications.router)
app.include_router(comments.router)
app.include_router(permissions.router)
app.include_router(access_grants.router)
app.include_router(sharing.router)
app.include_router(sharing.public_router)
app.include_router(locks.router)
app.include_router(metadata.router)
app.include_router(webhooks.router)
app.include_router(workflows.router)
app.include_router(retention.router)
app.include_router(esignature.router)
app.include_router(esignature.public_router)
app.include_router(ai_agents.router)
app.include_router(ai_agents.admin_router)
app.include_router(public_ai_agents.page_router, prefix="/public")

# The public agent-chat JSON API is mounted as its own sub-application so it
# can carry a permissive, origin-agnostic CORS policy (any external site's
# own JS can fetch() it directly) without loosening the main app's
# LAN-only CORS policy above — access here is scoped by each agent's own
# unguessable public_token, not by origin, the same trust model as a
# public share link.
_public_agent_api = FastAPI()
_public_agent_api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
_public_agent_api.include_router(public_ai_agents.router)
app.mount("/public/ai-agents", _public_agent_api)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/oauth-complete.html", response_class=HTMLResponse)
def oauth_complete():
    return """<!doctype html><html><body>
<script>
  var hash = window.location.hash.slice(1);
  var params = new URLSearchParams(hash);
  var connected = params.get("connected");
  var error = params.get("error");
  if (window.opener) {
    window.opener.postMessage({ type: "filedrive-oauth", connected: connected, error: error }, "*");
    window.close();
  } else {
    document.body.textContent = error ? ("Sign-in failed: " + error) : "Sign-in complete. You can close this window.";
  }
</script>
</body></html>"""


@app.get("/sso-complete.html", response_class=HTMLResponse)
def sso_complete():
    # Unlike oauth-complete.html (a popup, used for connecting a storage
    # backend from inside the already-logged-in app), SAML login is a full
    # top-level navigation away and back — there's no window.opener to post
    # a message to. Just write the token to the same localStorage key
    # AuthContext.tsx already reads on mount and navigate home; the SPA
    # picks the session up on its own from there.
    return """<!doctype html><html><body>
<script>
  var hash = window.location.hash.slice(1);
  var params = new URLSearchParams(hash);
  var token = params.get("token");
  var error = params.get("error");
  if (token) {
    localStorage.setItem("filedrive_token", token);
    window.location.href = "/";
  } else {
    document.body.textContent = "Sign-in failed" + (error ? (": " + decodeURIComponent(error)) : ".") + " Go back and try again.";
  }
</script>
</body></html>"""
