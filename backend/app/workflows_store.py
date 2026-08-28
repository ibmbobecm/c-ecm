"""Lightweight approval workflow engine.

A workflow *definition* describes a sequence of approval steps (reviewers,
required quorum, etc.).  A workflow *instance* is created when a user
requests approval for a specific resource.  Each step transitions through
a State Machine:

  pending → approved | rejected
  instance: in_review → approved | rejected | cancelled

The notification_service is called on each transition so reviewers receive
an in-app notification.  All transitions are recorded in the activity log.

This module is pure storage + state transitions (Repository + State Machine
patterns).  The router in routers/workflows.py owns the HTTP boundary, and
calls activity_service.record_event() after each transition.
"""

import datetime
import json
import sqlite3
import uuid

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "workflows.db"


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_definitions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    steps_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wfdef_name ON workflow_definitions (name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS workflow_instances (
    id TEXT PRIMARY KEY,
    definition_id TEXT NOT NULL REFERENCES workflow_definitions(id),
    connection_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_name TEXT,
    status TEXT NOT NULL DEFAULT 'in_review',
    current_step INTEGER NOT NULL DEFAULT 0,
    requested_by TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wfi_resource ON workflow_instances (connection_id, resource_id);
CREATE INDEX IF NOT EXISTS idx_wfi_status ON workflow_instances (status);

CREATE TABLE IF NOT EXISTS workflow_step_actions (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES workflow_instances(id),
    step_index INTEGER NOT NULL,
    reviewer TEXT NOT NULL,
    action TEXT NOT NULL,
    comment TEXT,
    acted_at TEXT NOT NULL
);
"""


def init_db() -> None:
    conn = _conn()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------- definitions ----------------------------------------------------

def _def_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "steps": json.loads(row["steps_json"]),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def list_definitions() -> list[dict]:
    conn = _conn()
    try:
        return [_def_row(r) for r in conn.execute("SELECT * FROM workflow_definitions ORDER BY name COLLATE NOCASE").fetchall()]
    finally:
        conn.close()


def get_definition(def_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM workflow_definitions WHERE id = ?", (def_id,)).fetchone()
        return _def_row(row) if row else None
    finally:
        conn.close()


def create_definition(name: str, description: str | None, steps: list[dict], created_by: str) -> dict:
    did = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO workflow_definitions (id, name, description, steps_json, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (did, name, description, json.dumps(steps), created_by, now),
        )
        conn.commit()
        return _def_row(conn.execute("SELECT * FROM workflow_definitions WHERE id = ?", (did,)).fetchone())
    finally:
        conn.close()


def has_in_review_instances(def_id: str) -> bool:
    """Foreign keys aren't enforced on this connection (no `PRAGMA
    foreign_keys = ON`), so deleting a definition would silently orphan any
    instance still pointing at it rather than raising an IntegrityError.
    For a completed instance that's harmless (defName() in the UI just
    falls back to displaying the raw id); for an in_review instance it's
    much worse — act_on_step() looks up the definition and returns None
    if it's gone, permanently freezing that instance in in_review limbo
    with no way to ever approve or reject it again (cancel is still
    possible since cancel_instance() doesn't need the definition). The
    router checks this before deleting so that case becomes a clear 409
    instead of a silent, unrecoverable dead end."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM workflow_instances WHERE definition_id = ? AND status = 'in_review' LIMIT 1", (def_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def delete_definition(def_id: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM workflow_definitions WHERE id = ?", (def_id,))
        conn.commit()
    finally:
        conn.close()


# ---------- instances -------------------------------------------------------

def _inst_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    actions = conn.execute(
        "SELECT * FROM workflow_step_actions WHERE instance_id = ? ORDER BY acted_at", (row["id"],)
    ).fetchall()
    return {
        "id": row["id"],
        "definition_id": row["definition_id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "resource_name": row["resource_name"],
        "status": row["status"],
        "current_step": row["current_step"],
        "requested_by": row["requested_by"],
        "comment": row["comment"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "step_actions": [
            {
                "id": a["id"],
                "step_index": a["step_index"],
                "reviewer": a["reviewer"],
                "action": a["action"],
                "comment": a["comment"],
                "acted_at": a["acted_at"],
            }
            for a in actions
        ],
    }


def create_instance(
    definition_id: str,
    connection_id: str,
    resource_id: str,
    resource_type: str,
    resource_name: str | None,
    requested_by: str,
    comment: str | None,
) -> dict:
    iid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO workflow_instances "
            "(id, definition_id, connection_id, resource_id, resource_type, resource_name, "
            "status, current_step, requested_by, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'in_review', 0, ?, ?, ?)",
            (iid, definition_id, connection_id, resource_id, resource_type, resource_name, requested_by, comment, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM workflow_instances WHERE id = ?", (iid,)).fetchone()
        return _inst_row(conn, row)
    finally:
        conn.close()


def get_instance(instance_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM workflow_instances WHERE id = ?", (instance_id,)).fetchone()
        return _inst_row(conn, row) if row else None
    finally:
        conn.close()


def list_instances(
    *,
    connection_id: str | None = None,
    resource_id: str | None = None,
    status: str | None = None,
    reviewer: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if connection_id:
        clauses.append("wi.connection_id = ?")
        params.append(connection_id)
    if resource_id:
        clauses.append("wi.resource_id = ?")
        params.append(resource_id)
    if status:
        clauses.append("wi.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT DISTINCT wi.* FROM workflow_instances wi {where} ORDER BY wi.created_at DESC",
            params,
        ).fetchall()
        return [_inst_row(conn, r) for r in rows]
    finally:
        conn.close()


class NotAnAuthorizedReviewerError(Exception):
    """The step's `reviewers` list is non-empty and the caller isn't on it."""


class AlreadyActedOnStepError(Exception):
    """The caller already recorded an action on this step — one vote each,
    not because a second vote would be technically ambiguous, but because
    letting someone re-vote is how a quorum requirement gets defeated."""


def act_on_step(instance_id: str, reviewer: str, action: str, comment: str | None) -> dict | None:
    """Applies an approve/reject action to the current step. Returns the
    updated instance, or None if the instance/definition doesn't exist or
    isn't awaiting action (the router maps that to 404/409). Raises
    NotAnAuthorizedReviewerError / AlreadyActedOnStepError for the two
    other rejection reasons, so the router can give a precise 403 instead
    of lumping every failure into the same generic response.

    Enforces what WorkflowStepDef already declares but this function
    previously ignored entirely: a non-empty `reviewers` list restricts
    who can act, and `required_approvals` is a real quorum — one
    "approved" vote was advancing the whole step regardless of how many
    approvals were actually configured, silently defeating the entire
    point of a multi-approver step."""
    conn = _conn()
    try:
        inst = conn.execute("SELECT * FROM workflow_instances WHERE id = ?", (instance_id,)).fetchone()
        if inst is None or inst["status"] != "in_review":
            return None
        wf_def = conn.execute("SELECT * FROM workflow_definitions WHERE id = ?", (inst["definition_id"],)).fetchone()
        if wf_def is None:
            return None

        steps = json.loads(wf_def["steps_json"])
        step_idx = inst["current_step"]
        step = steps[step_idx]
        reviewers = step.get("reviewers") or []
        if reviewers and reviewer not in reviewers:
            raise NotAnAuthorizedReviewerError(reviewer)

        existing_actions = conn.execute(
            "SELECT * FROM workflow_step_actions WHERE instance_id = ? AND step_index = ?", (instance_id, step_idx)
        ).fetchall()
        if any(a["reviewer"] == reviewer for a in existing_actions):
            raise AlreadyActedOnStepError(reviewer)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO workflow_step_actions (id, instance_id, step_index, reviewer, action, comment, acted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, instance_id, step_idx, reviewer, action, comment, now),
        )

        if action == "rejected":
            # A single authorized rejection kills the whole instance — no
            # quorum on rejection, matching how approval workflows
            # conventionally work (unanimity is required to pass, not to fail).
            conn.execute(
                "UPDATE workflow_instances SET status = 'rejected', completed_at = ? WHERE id = ?", (now, instance_id)
            )
        elif action == "approved":
            approvals_so_far = sum(1 for a in existing_actions if a["action"] == "approved") + 1
            required = step.get("required_approvals", 1)
            if approvals_so_far >= required:
                next_step = step_idx + 1
                if next_step >= len(steps):
                    conn.execute(
                        "UPDATE workflow_instances SET status = 'approved', completed_at = ? WHERE id = ?",
                        (now, instance_id),
                    )
                else:
                    conn.execute(
                        "UPDATE workflow_instances SET current_step = ? WHERE id = ?", (next_step, instance_id)
                    )
            # else: recorded, but the step stays in_review — quorum not met yet.
        conn.commit()
        row = conn.execute("SELECT * FROM workflow_instances WHERE id = ?", (instance_id,)).fetchone()
        return _inst_row(conn, row)
    finally:
        conn.close()


def cancel_instance(instance_id: str) -> dict | None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _conn()
    try:
        conn.execute(
            "UPDATE workflow_instances SET status = 'cancelled', completed_at = ? WHERE id = ? AND status = 'in_review'",
            (now, instance_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM workflow_instances WHERE id = ?", (instance_id,)).fetchone()
        return _inst_row(conn, row) if row else None
    finally:
        conn.close()
