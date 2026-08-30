import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from . import (
    activity_service,
    ai_service,
    comments_store,
    connections_store,
    esignature_store,
    events_store,
    locks_store,
    metadata_store,
    notification_service,
    notifications_store,
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
    activity,
    admin,
    ai,
    auth,
    comments,
    connections,
    esignature,
    files,
    folders,
    locks,
    metadata,
    notifications,
    permissions,
    retention,
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
    users_store.init_db()
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


app = FastAPI(title="C-ECM", lifespan=lifespan)

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
app.include_router(sharing.router)
app.include_router(sharing.public_router)
app.include_router(locks.router)
app.include_router(metadata.router)
app.include_router(webhooks.router)
app.include_router(workflows.router)
app.include_router(retention.router)
app.include_router(esignature.router)
app.include_router(esignature.public_router)


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
