import concurrent.futures

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import access_control, connections_store, resource_permissions_store, saved_searches_store
from ..access_helpers import to_http
from ..auth import CurrentSession, CurrentUser, get_current_user, get_current_session
from ..schemas import (
    GlobalSearchHit,
    GlobalSearchResultOut,
    SavedSearchCreateRequest,
    SavedSearchOut,
    SavedSearchQuery,
    SearchResultOut,
)
from ..serializers import file_out, folder_out
from ..storage_providers.base import ProviderError
from ..storage_providers.registry import get_provider

router = APIRouter(prefix="/search", tags=["search"])


def _filter_by_access(session: CurrentSession, folders, files):
    # Post-query filtering, not a query constraint — results the caller
    # can't view are simply omitted rather than the whole search failing.
    # connection_has_any_grants() doesn't depend on the resource, so it's
    # checked once per request here and threaded through instead of each
    # effective_level() call re-opening its own connection to re-learn the
    # same answer once per result — with a broad search matching dozens of
    # results this was the dominant per-request cost under concurrent load.
    has_grants = (
        not session.user.get("is_superadmin")
        and resource_permissions_store.connection_has_any_grants(session.connection_id)
    )
    folders = [
        f for f in folders
        if access_control.effective_level(session, f.id, "folder", _connection_has_grants=has_grants) != "none"
    ]
    files = [
        f for f in files
        if access_control.effective_level(session, f.id, "file", _connection_has_grants=has_grants) != "none"
    ]
    return folders, files


@router.get("", response_model=SearchResultOut)
def search(q: str = Query(min_length=1, max_length=255), session: CurrentSession = Depends(get_current_session)):
    try:
        folders, files = session.provider.search(session.creds, q)
    except ProviderError as exc:
        raise to_http(exc)
    folders, files = _filter_by_access(session, folders, files)
    return SearchResultOut(folders=[folder_out(f) for f in folders], files=[file_out(f) for f in files])


@router.get("/global", response_model=GlobalSearchResultOut)
def global_search(
    q: str = Query(min_length=1, max_length=255),
    _user: CurrentUser = Depends(get_current_user),
):
    """Search across ALL configured connections in parallel.

    Each connection is queried concurrently using a thread-pool so the total
    latency is bounded by the slowest single backend, not the sum of all of
    them.  Connections that error out are reported in `connection_errors` but
    never block the rest of the results.

    This is the key enterprise differentiator — no other lightweight ECM
    client searches all backends simultaneously.
    """
    connections = connections_store.list_connections()
    hits: list[GlobalSearchHit] = []
    errors: dict[str, str] = {}

    def _search_one(conn: dict) -> tuple[list[GlobalSearchHit], str | None]:
        cid = conn["id"]
        try:
            provider_key = conn["provider_key"]
            provider = get_provider(provider_key)
            entry = connections_store.get_creds(cid)
            if entry is None:
                return [], f"Connection {cid!r} credentials not found"
            _pk, creds = entry
            # Refresh stale OAuth tokens — and PERSIST the result. Microsoft
            # Graph and Box both rotate refresh tokens (each issues a new one
            # on every refresh, invalidating the old one); discarding the
            # refreshed creds here would leave the stale, now-dead refresh
            # token in connections_store while throwing away the one that
            # would have actually worked next time — silently and
            # permanently breaking the connection on its very next refresh,
            # anywhere in the app, not just in search. Matches what
            # auth.py's get_current_session already does for every other
            # code path that touches a connection's credentials.
            try:
                refreshed, changed = provider.refresh_if_needed(creds)
                if changed:
                    connections_store.update_creds(cid, refreshed)
                    creds = refreshed
            except Exception:
                pass
            folders, files = provider.search(creds, q)
            # Same per-hit access filtering as the single-connection search
            # below — a lightweight CurrentSession built inline since this
            # runs in a thread-pool worker, off the request's own Depends()
            # chain.
            fake_session = CurrentSession(connection_id=cid, provider_key=provider_key, provider=provider, creds=creds, user=_user)
            folders, files = _filter_by_access(fake_session, folders, files)
            conn_hits: list[GlobalSearchHit] = []
            for folder in folders:
                conn_hits.append(
                    GlobalSearchHit(
                        connection_id=cid,
                        connection_name=conn["display_name"],
                        provider_key=provider_key,
                        resource_type="folder",
                        resource_id=folder.id,
                        name=folder.name,
                    )
                )
            for file in files:
                conn_hits.append(
                    GlobalSearchHit(
                        connection_id=cid,
                        connection_name=conn["display_name"],
                        provider_key=provider_key,
                        resource_type="file",
                        resource_id=file.id,
                        name=file.name,
                        size_bytes=file.size_bytes,
                        content_type=file.content_type,
                        updated_at=file.updated_at,
                    )
                )
            return conn_hits, None
        except Exception as exc:
            return [], str(exc)[:300]

    # Fast-path: no connections configured → return empty result immediately.
    if not connections:
        return GlobalSearchResultOut(query=q, hits=[], connection_errors={})

    # Fan-out: one thread per connection, capped at 15 seconds TOTAL — not
    # per connection. Iterating futures.items() in submission order and
    # calling .result(timeout=15) on each was a real bug: that blocks on
    # the first future for up to 15s even if a later one already finished,
    # so N slow/hanging connections could take N*15s instead of the ~15s
    # the docstring above promises. as_completed() with one shared timeout
    # yields whichever future finishes next, so fast connections' results
    # are processed immediately and the whole call is genuinely capped.
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(connections), 10)) as pool:
        futures = {pool.submit(_search_one, c): c for c in connections}
        done: set[concurrent.futures.Future] = set()
        try:
            for future in concurrent.futures.as_completed(futures, timeout=15):
                done.add(future)
                conn = futures[future]
                try:
                    conn_hits, err = future.result()
                    hits.extend(conn_hits)
                    if err:
                        errors[conn["id"]] = err
                except Exception as exc:
                    errors[conn["id"]] = str(exc)[:300]
        except concurrent.futures.TimeoutError:
            pass  # whatever's still not done gets marked below
        for future, conn in futures.items():
            if future not in done:
                errors[conn["id"]] = "Search timed out after 15 seconds"

    # Sort: files before folders for the same name, then alphabetically
    hits.sort(key=lambda h: (h.name.lower(), h.resource_type))
    return GlobalSearchResultOut(query=q, hits=hits, connection_errors=errors)


@router.get("/saved", response_model=list[SavedSearchOut])
def list_saved_searches(user: CurrentUser = Depends(get_current_user)):
    return [
        SavedSearchOut(id=s["id"], name=s["name"], connection_id=s["connection_id"], query=SavedSearchQuery(**s["query"]),
                        created_at=s["created_at"], last_run_at=s["last_run_at"])
        for s in saved_searches_store.list_for_owner(user["username"])
    ]


@router.post("/saved", response_model=SavedSearchOut, status_code=201)
def create_saved_search(req: SavedSearchCreateRequest, user: CurrentUser = Depends(get_current_user)):
    s = saved_searches_store.create(user["username"], req.name, req.connection_id, req.query.model_dump())
    return SavedSearchOut(id=s["id"], name=s["name"], connection_id=s["connection_id"], query=SavedSearchQuery(**s["query"]),
                           created_at=s["created_at"], last_run_at=s["last_run_at"])


@router.delete("/saved/{search_id}", status_code=204)
def delete_saved_search(search_id: str, user: CurrentUser = Depends(get_current_user)):
    existing = saved_searches_store.get(search_id)
    if existing is not None and existing["owner"] != user["username"] and not user.get("is_superadmin", False):
        raise HTTPException(status_code=403, detail="Only the owner or a superadmin can delete this saved search")
    saved_searches_store.delete(search_id)


@router.post("/saved/{search_id}/run", response_model=SearchResultOut)
def run_saved_search(search_id: str, session: CurrentSession = Depends(get_current_session)):
    saved = saved_searches_store.get(search_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved search not found")
    saved_searches_store.touch_last_run(search_id)
    query = saved["query"]
    try:
        folders, files = session.provider.search(session.creds, query.get("text", ""))
    except ProviderError as exc:
        raise to_http(exc)
    types = set(query.get("file_types") or [])
    if types:
        files = [f for f in files if (f.name.rsplit(".", 1)[-1].lower() if "." in f.name else "") in types]
    folders, files = _filter_by_access(session, folders, files)
    return SearchResultOut(folders=[folder_out(f) for f in folders], files=[file_out(f) for f in files])
