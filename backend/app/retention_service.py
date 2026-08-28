"""Retention enforcement — the scheduled job that actually acts on due
records, as opposed to retention_store.run_due_check() which only
identifies them (main.py's scheduler was previously wired straight to
run_due_check and discarding its return value, so a record coming due did
nothing at all — no deletion, no archival, no notification — regardless
of what its policy's `action` said).

Split into its own module because applying an action needs the resolved
provider + connection creds (connections_store, the provider registry),
which a pure storage module like retention_store.py deliberately doesn't
depend on — same layering as activity_service.py wrapping events_store.py.
"""

import logging

from . import activity_service, connections_store, retention_store
from .storage_providers.base import ProviderError
from .storage_providers.registry import get_provider

logger = logging.getLogger("retention_service")


def _auto_delete(record: dict) -> None:
    entry = connections_store.get_creds(record["connection_id"])
    if entry is None:
        raise RuntimeError(f"Connection {record['connection_id']} no longer exists")
    provider_key, creds = entry
    provider = get_provider(provider_key)
    try:
        # Trash, not permanent delete — matches the policy's own documented
        # contract ("action: 'auto_delete' — trash the document
        # automatically") and keeps the existing Trash/restore safety net
        # in place for a policy that turns out to be misconfigured.
        if record["resource_type"] == "file":
            provider.trash_file(creds, record["resource_id"])
        else:
            provider.trash_folder(creds, record["resource_id"])
    except ProviderError as exc:
        raise RuntimeError(str(exc)) from exc


def apply_due_actions() -> list[dict]:
    """Called by the scheduler (and available to call on demand). For each
    due, non-legal-hold record, resolves its policy's configured action
    and actually performs it, recording the outcome — including a failure
    to act — as both a retention_store status update and an activity
    event, so what happened is visible without reading server logs."""
    due = retention_store.run_due_check()
    results = []
    for record in due:
        policy = retention_store.get_policy(record["policy_id"])
        if policy is None or not policy["active"]:
            continue
        action = policy["action"]
        try:
            if action == "auto_delete":
                _auto_delete(record)
                retention_store.mark_actioned(record["id"], "deleted")
            elif action == "archive":
                # No cold-storage migration here — a thin connector app has
                # nowhere of its own to move bytes to. "Archived" records
                # a decision that's been made and stops it being flagged
                # as due again; an admin (or a future backend-specific
                # integration) can act on the status from there.
                retention_store.mark_actioned(record["id"], "archived")
            else:
                # 'review' or any unrecognized action — the deliberately
                # safe default: flag it for a human, never destroy anything
                # automatically without an explicit auto_delete policy.
                retention_store.mark_actioned(record["id"], "under_review")

            activity_service.record_event(
                connection_id=record["connection_id"],
                provider_key=None,
                resource_type=record["resource_type"],
                resource_id=record["resource_id"],
                resource_name=record["resource_name"],
                event_type=f"retention_{action}",
                actor="system",
                payload={"policy_id": record["policy_id"], "record_id": record["id"]},
            )
            results.append({"record_id": record["id"], "action": action, "ok": True})
        except Exception as exc:
            logger.exception("Retention action '%s' failed for record %s", action, record["id"])
            activity_service.record_event(
                connection_id=record["connection_id"],
                provider_key=None,
                resource_type=record["resource_type"],
                resource_id=record["resource_id"],
                resource_name=record["resource_name"],
                event_type="retention_action_failed",
                actor="system",
                payload={"policy_id": record["policy_id"], "record_id": record["id"], "error": str(exc)[:300]},
            )
            results.append({"record_id": record["id"], "action": action, "ok": False, "error": str(exc)})
    return results
