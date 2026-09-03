"""Per-resource (file/folder) access enforcement — the "view"/"edit" layer
on top of the app-wide Group/Feature system (auth.require_feature).
Restrictions are opt-in: a connection with no resource_permissions rows
at all behaves exactly like it always has (any authenticated session has
full access) — this module only starts doing real work once someone has
granted access on at least one resource in that connection.

require_resource_level() is called directly at the top of a route handler
(not a FastAPI Depends(), since the resource id lives in a path/body
param whose name varies per route) — same call shape as
access_helpers.to_http(), just for a different kind of failure.
"""

from fastapi import HTTPException

from . import groups_store, resource_permissions_store
from .auth import CurrentSession

_MAX_ANCESTOR_DEPTH = 50

_LEVEL_RANK = {"view": 0, "edit": 1}


def _ancestor_chain(session: CurrentSession, resource_id: str, resource_type: str) -> list[tuple[str, str]]:
    """[(resource_id, resource_type), its parent folder, its grandparent,
    ...] up to the root-most folder reachable — same live-walk approach
    already used by routers/folders.py's _is_in_subtree/
    collect_descendants, since this app doesn't mirror the folder tree
    locally. Capped depth guards against a cyclic/malformed tree from a
    misbehaving provider; any lookup failure along the way just stops the
    walk early rather than raising — a resource that can't be introspected
    further is treated as having no more (uncheckable) ancestors, not as
    an error here.

    Carries resource_type alongside every id, not just the leaf — at
    least one provider in this app (local disk) hands out ids that aren't
    unique across files vs. folders (a file and a folder can share the
    same numeric id), so a grant lookup keyed on id alone could apply a
    folder's grant to an unrelated file of the same id. Every grant
    lookup in this module matches on the (id, type) pair, never id alone."""
    chain = [(resource_id, resource_type)]
    current_id, current_type = resource_id, resource_type
    for _ in range(_MAX_ANCESTOR_DEPTH):
        try:
            if current_type == "file":
                info = session.provider.get_file(session.creds, current_id)
                parent_id = info.folder_id
            else:
                contents = session.provider.get_children(session.creds, current_id)
                parent_id = contents.folder.parent_id if contents.folder else None
        except Exception:
            break
        if parent_id is None:
            break
        chain.append((parent_id, "folder"))
        current_id, current_type = parent_id, "folder"
    return chain


def effective_level(
    session: CurrentSession, resource_id: str, resource_type: str, *, _connection_has_grants: bool | None = None,
) -> str | None:
    """Returns "view", "edit", or None (meaning: no restriction applies —
    the resource is fully open, today's default). Superadmins always get
    "edit" without any lookup. Used both by require_resource_level() below
    and by the GET .../effective-access endpoint the frontend uses to show
    an access indicator.

    `_connection_has_grants`: pass the result of
    resource_permissions_store.connection_has_any_grants(session.connection_id)
    when calling this in a loop over many resources on the same connection
    (e.g. filtering a search-result or folder-listing) — that check doesn't
    depend on resource_id, so computing it once per request instead of once
    per resource avoids the dominant cost for the common case of a
    connection with no ACLs at all (every call would otherwise open its own
    SQLite connection just to learn the same "no" it already knows). Leave
    it None for single-resource callers; behavior is identical either way.
    """
    user = session.user
    if user.get("is_superadmin"):
        return "edit"
    has_grants = (
        _connection_has_grants if _connection_has_grants is not None
        else resource_permissions_store.connection_has_any_grants(session.connection_id)
    )
    if not has_grants:
        return None

    chain = _ancestor_chain(session, resource_id, resource_type)
    grants_by_id = resource_permissions_store.grants_for_resource_batch(
        session.connection_id, [rid for rid, _rtype in chain]
    )
    if not grants_by_id:
        return None  # nothing in this specific chain is restricted, even though the connection has grants elsewhere

    user_id = user["id"]
    group_ids = {g["id"] for g in groups_store.list_user_groups(user_id)}

    for rid, rtype in chain:
        # Match on (id, type), not id alone — see _ancestor_chain's
        # docstring on why (local disk's ids collide between files and
        # folders).
        grants = [g for g in grants_by_id.get(rid, []) if g["resource_type"] == rtype]
        if not grants:
            continue
        # Nearest ancestor (starting from the resource itself) that has
        # any explicit grants at all -- this is the level that decides
        # access, regardless of anything further up the chain.
        applicable = [
            g["level"] for g in grants
            if (g["principal_type"] == "user" and g["principal_id"] == user_id)
            or (g["principal_type"] == "group" and g["principal_id"] in group_ids)
        ]
        if not applicable:
            return "none"  # this level is restricted and this user has no applicable grant here
        return "edit" if "edit" in applicable else "view"
    return None


def require_resource_level(session: CurrentSession, resource_id: str, resource_type: str, level: str) -> None:
    effective = effective_level(session, resource_id, resource_type)
    if effective is None:
        return  # unrestricted -- today's default
    if effective == "none" or _LEVEL_RANK.get(effective, -1) < _LEVEL_RANK[level]:
        detail = "You don't have access to this resource" if effective == "none" else \
            "You have view-only access to this resource"
        raise HTTPException(status_code=403, detail=detail)
