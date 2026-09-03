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
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

workflow_definitions = sa.Table(
    "workflow_definitions", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("description", sa.Text),
    sa.Column("steps_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    # The original schema had a UNIQUE INDEX on name COLLATE NOCASE — SQLite's
    # NOCASE collation has no portable equivalent on postgres/oracle (neither
    # ships a collation literally named "NOCASE"), so this index is a plain,
    # case-sensitive uniqueness constraint on every dialect. list_definitions()
    # below uses func.lower() in ORDER BY instead of relying on a DB collation,
    # which keeps the case-insensitive *listing* order portable even though
    # the uniqueness constraint itself is now case-sensitive everywhere.
    sa.Index("idx_wfdef_name", "name", unique=True),
)

workflow_instances = sa.Table(
    "workflow_instances", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    # definition_id/connection_id name this app's own uuid.uuid4().hex ids,
    # same as the original REFERENCES clauses -- deliberately NOT declared as
    # real sa.ForeignKey constraints here. The original sqlite connection
    # never turned PRAGMA foreign_keys ON (see has_in_review_instances()
    # below), so a *completed* instance is allowed to keep pointing at a
    # definition that's since been deleted. Postgres/Oracle enforce FK
    # constraints by default, so a real FK here would make delete_definition()
    # start failing for exactly the case has_in_review_instances()/the router
    # deliberately allow (a definition with only completed instances left).
    sa.Column("definition_id", sa.String(32), nullable=False),
    sa.Column("connection_id", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("resource_name", sa.Text),
    sa.Column("status", sa.String(64), nullable=False, server_default="in_review"),
    sa.Column("current_step", sa.Integer, nullable=False, server_default="0"),
    sa.Column("requested_by", sa.String(255), nullable=False),
    sa.Column("comment", sa.Text),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("completed_at", sa.String(40)),
    sa.Column("steps_json", sa.Text),
    sa.Index("idx_wfi_resource", "connection_id", "resource_id"),
    sa.Index("idx_wfi_status", "status"),
)

workflow_step_actions = sa.Table(
    "workflow_step_actions", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("instance_id", sa.String(32), nullable=False),
    sa.Column("step_index", sa.Integer, nullable=False),
    sa.Column("reviewer", sa.String(255), nullable=False),
    sa.Column("action", sa.String(64), nullable=False),
    sa.Column("comment", sa.Text),
    sa.Column("acted_at", sa.String(40), nullable=False),
)

workflow_instance_resources = sa.Table(
    "workflow_instance_resources", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("instance_id", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("resource_name", sa.Text),
    sa.Column("added_at", sa.String(40), nullable=False),
    sa.Column("added_by", sa.String(255), nullable=False),
    sa.Index("idx_wfir_instance", "instance_id"),
    sa.Index("idx_wfir_resource", "resource_id"),
)

_engine = db.get_engine("workflows")


def init_db() -> None:
    db.create_all(_metadata, "workflows")


# ---------- definitions ----------------------------------------------------

def _def_row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "steps": json.loads(row["steps_json"]),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def list_definitions() -> list[dict]:
    stmt = sa.select(workflow_definitions).order_by(sa.func.lower(workflow_definitions.c.name))
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_def_row(r) for r in rows]


def get_definition(def_id: str) -> dict | None:
    stmt = sa.select(workflow_definitions).where(workflow_definitions.c.id == def_id)
    with _engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _def_row(row) if row else None


def create_definition(name: str, description: str | None, steps: list[dict], created_by: str) -> dict:
    did = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            workflow_definitions.insert().values(
                id=did, name=name, description=description, steps_json=json.dumps(steps),
                created_by=created_by, created_at=now,
            )
        )
        row = conn.execute(
            sa.select(workflow_definitions).where(workflow_definitions.c.id == did)
        ).mappings().first()
    return _def_row(row)


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
    stmt = (
        sa.select(sa.literal(1))
        .select_from(workflow_instances)
        .where(workflow_instances.c.definition_id == def_id, workflow_instances.c.status == "in_review")
        .limit(1)
    )
    with _engine.connect() as conn:
        row = conn.execute(stmt).first()
    return row is not None


def delete_definition(def_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(workflow_definitions.delete().where(workflow_definitions.c.id == def_id))


# ---------- instances -------------------------------------------------------

def _inst_row(conn, row) -> dict:
    actions = conn.execute(
        sa.select(workflow_step_actions)
        .where(workflow_step_actions.c.instance_id == row["id"])
        .order_by(workflow_step_actions.c.acted_at)
    ).mappings().all()
    resources = conn.execute(
        sa.select(workflow_instance_resources)
        .where(workflow_instance_resources.c.instance_id == row["id"])
        .order_by(workflow_instance_resources.c.added_at)
    ).mappings().all()
    return {
        "id": row["id"],
        "definition_id": row["definition_id"],
        "connection_id": row["connection_id"],
        "resources": [
            {
                "id": r["id"],
                "resource_id": r["resource_id"],
                "resource_type": r["resource_type"],
                "resource_name": r["resource_name"],
                "added_at": r["added_at"],
                "added_by": r["added_by"],
            }
            for r in resources
        ],
        "status": row["status"],
        "current_step": row["current_step"],
        "steps": json.loads(row["steps_json"] or "[]"),
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
    resources: list[dict],
    steps: list[dict],
    requested_by: str,
    comment: str | None,
) -> dict:
    """`resources` is [{resource_id, resource_type, resource_name}], at
    least one entry. `steps` is the definition's steps at creation time,
    snapshotted onto the instance so a later reassign() only ever touches
    this instance's own copy, never the shared, reusable definition."""
    iid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    primary = resources[0]
    with _engine.begin() as conn:
        conn.execute(
            workflow_instances.insert().values(
                id=iid, definition_id=definition_id, connection_id=connection_id,
                resource_id=primary["resource_id"], resource_type=primary["resource_type"],
                resource_name=primary.get("resource_name"), status="in_review", current_step=0,
                requested_by=requested_by, comment=comment, created_at=now, steps_json=json.dumps(steps),
            )
        )
        conn.execute(
            workflow_instance_resources.insert(),
            [
                {
                    "id": uuid.uuid4().hex, "instance_id": iid, "resource_id": r["resource_id"],
                    "resource_type": r["resource_type"], "resource_name": r.get("resource_name"),
                    "added_at": now, "added_by": requested_by,
                }
                for r in resources
            ],
        )
        row = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == iid)).mappings().first()
        return _inst_row(conn, row)


def add_resource(instance_id: str, resource_id: str, resource_type: str, resource_name: str | None, added_by: str) -> dict | None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        inst = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().first()
        if inst is None or inst["status"] != "in_review":
            return None
        conn.execute(
            workflow_instance_resources.insert().values(
                id=uuid.uuid4().hex, instance_id=instance_id, resource_id=resource_id,
                resource_type=resource_type, resource_name=resource_name, added_at=now, added_by=added_by,
            )
        )
        row = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().first()
        return _inst_row(conn, row)


class LastResourceError(Exception):
    """Refused: an instance must always have at least one attached document."""


def remove_resource(instance_id: str, resource_row_id: str) -> dict | None:
    with _engine.begin() as conn:
        inst = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().first()
        if inst is None or inst["status"] != "in_review":
            return None
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(workflow_instance_resources)
            .where(workflow_instance_resources.c.instance_id == instance_id)
        ).scalar_one()
        if count <= 1:
            raise LastResourceError()
        result = conn.execute(
            workflow_instance_resources.delete().where(
                workflow_instance_resources.c.id == resource_row_id,
                workflow_instance_resources.c.instance_id == instance_id,
            )
        )
        if result.rowcount == 0:
            return None
        # If the removed row was serving as the legacy "primary" columns,
        # repoint them at whatever's left so create_instance's NOT NULL
        # columns stay in sync with reality.
        remaining = conn.execute(
            sa.select(workflow_instance_resources)
            .where(workflow_instance_resources.c.instance_id == instance_id)
            .order_by(workflow_instance_resources.c.added_at)
            .limit(1)
        ).mappings().first()
        if remaining is not None:
            conn.execute(
                workflow_instances.update().where(workflow_instances.c.id == instance_id).values(
                    resource_id=remaining["resource_id"], resource_type=remaining["resource_type"],
                    resource_name=remaining["resource_name"],
                )
            )
        row = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().first()
        return _inst_row(conn, row)


def reassign_current_step(instance_id: str, assignees: list[dict]) -> dict | None:
    with _engine.begin() as conn:
        inst = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().first()
        if inst is None or inst["status"] != "in_review":
            return None
        steps = json.loads(inst["steps_json"] or "[]")
        step_idx = inst["current_step"]
        if step_idx >= len(steps):
            return None
        steps[step_idx]["assignees"] = assignees
        conn.execute(
            workflow_instances.update().where(workflow_instances.c.id == instance_id).values(
                steps_json=json.dumps(steps)
            )
        )
        row = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().first()
        return _inst_row(conn, row)


def get_instance(instance_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().first()
        return _inst_row(conn, row) if row else None


def list_instances(
    *,
    connection_id: str | None = None,
    resource_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    stmt = sa.select(workflow_instances)
    if resource_id:
        stmt = stmt.select_from(
            workflow_instances.join(
                workflow_instance_resources,
                workflow_instance_resources.c.instance_id == workflow_instances.c.id,
            )
        ).where(workflow_instance_resources.c.resource_id == resource_id)
    if connection_id:
        stmt = stmt.where(workflow_instances.c.connection_id == connection_id)
    if status:
        stmt = stmt.where(workflow_instances.c.status == status)
    stmt = stmt.distinct().order_by(workflow_instances.c.created_at.desc())
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
        return [_inst_row(conn, r) for r in rows]


class NotAnAuthorizedReviewerError(Exception):
    """The step's `assignees` list is non-empty and the caller isn't on it,
    directly or via group membership."""


class AlreadyActedOnStepError(Exception):
    """The caller already recorded an action on this step — one vote each,
    not because a second vote would be technically ambiguous, but because
    letting someone re-vote is how a quorum requirement gets defeated."""


def act_on_step(instance_id: str, reviewer: str, group_ids: set[str], action: str, comment: str | None) -> dict | None:
    """Applies an approve/reject action to the current step. `reviewer` is
    the acting user's username (what gets recorded as the vote), `group_ids`
    is the set of group ids they belong to (for matching a group-type
    assignee) — both resolved by the caller, same layering
    access_control.effective_level uses for resource grants. Returns the
    updated instance, or None if the instance doesn't exist or isn't
    awaiting action (the router maps that to 404/409). Raises
    NotAnAuthorizedReviewerError / AlreadyActedOnStepError for the two
    other rejection reasons, so the router can give a precise 403 instead
    of lumping every failure into the same generic response.

    Reads steps from the instance's OWN steps_json snapshot, not the
    shared, reusable definition — this is what makes reassign_current_step()
    safe: it only ever mutates one instance's copy, never every instance
    that happens to share the same definition.

    Enforces what WorkflowStepDef already declares but this function
    previously ignored entirely: a non-empty `assignees` list restricts
    who can act, and `required_approvals` is a real quorum — one
    "approved" vote was advancing the whole step regardless of how many
    approvals were actually configured, silently defeating the entire
    point of a multi-approver step."""
    with _engine.begin() as conn:
        inst = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().first()
        if inst is None or inst["status"] != "in_review":
            return None

        steps = json.loads(inst["steps_json"] or "[]")
        step_idx = inst["current_step"]
        step = steps[step_idx]
        assignees = step.get("assignees") or []
        if assignees and not any(
            (a["type"] == "user" and a["id"] == reviewer) or (a["type"] == "group" and a["id"] in group_ids)
            for a in assignees
        ):
            raise NotAnAuthorizedReviewerError(reviewer)

        existing_actions = conn.execute(
            sa.select(workflow_step_actions).where(
                workflow_step_actions.c.instance_id == instance_id,
                workflow_step_actions.c.step_index == step_idx,
            )
        ).mappings().all()
        if any(a["reviewer"] == reviewer for a in existing_actions):
            raise AlreadyActedOnStepError(reviewer)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            workflow_step_actions.insert().values(
                id=uuid.uuid4().hex, instance_id=instance_id, step_index=step_idx,
                reviewer=reviewer, action=action, comment=comment, acted_at=now,
            )
        )

        if action == "rejected":
            # A single authorized rejection kills the whole instance — no
            # quorum on rejection, matching how approval workflows
            # conventionally work (unanimity is required to pass, not to fail).
            conn.execute(
                workflow_instances.update().where(workflow_instances.c.id == instance_id).values(
                    status="rejected", completed_at=now
                )
            )
        elif action == "approved":
            approvals_so_far = sum(1 for a in existing_actions if a["action"] == "approved") + 1
            required = step.get("required_approvals", 1)
            if approvals_so_far >= required:
                next_step = step_idx + 1
                if next_step >= len(steps):
                    conn.execute(
                        workflow_instances.update().where(workflow_instances.c.id == instance_id).values(
                            status="approved", completed_at=now
                        )
                    )
                else:
                    conn.execute(
                        workflow_instances.update().where(workflow_instances.c.id == instance_id).values(
                            current_step=next_step
                        )
                    )
            # else: recorded, but the step stays in_review — quorum not met yet.
        row = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().first()
        return _inst_row(conn, row)


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    """Called when a single file/folder is permanently deleted — mirrors
    tags_store/comments_store/etc.'s delete_for_resource. Without this, a
    permanently-deleted resource's workflow instances (including ones still
    in_review) linger forever: invisible to everyone since nothing can list
    instances for a resource that no longer exists, but never cleaned up
    either — reviewing/approving a document that's gone is meaningless, and
    those rows would otherwise never leave the database.

    A multi-document instance survives losing one of several attachments —
    only the affected resource row is removed, and the legacy "primary"
    columns are repointed if that's the one that was deleted. An instance
    left with zero attached documents (including every pre-multi-doc,
    single-resource instance, unchanged from before) is deleted outright."""
    with _engine.begin() as conn:
        affected = conn.execute(
            sa.select(workflow_instance_resources.c.instance_id)
            .distinct()
            .select_from(
                workflow_instance_resources.join(
                    workflow_instances, workflow_instances.c.id == workflow_instance_resources.c.instance_id
                )
            )
            .where(
                workflow_instances.c.connection_id == connection_id,
                workflow_instance_resources.c.resource_id == resource_id,
            )
        ).mappings().all()
        instance_ids = [r["instance_id"] for r in affected]
        conn.execute(
            workflow_instance_resources.delete().where(
                workflow_instance_resources.c.resource_id == resource_id,
                workflow_instance_resources.c.instance_id.in_(
                    sa.select(workflow_instances.c.id).where(workflow_instances.c.connection_id == connection_id)
                ),
            )
        )
        for iid in instance_ids:
            remaining = conn.execute(
                sa.select(workflow_instance_resources)
                .where(workflow_instance_resources.c.instance_id == iid)
                .order_by(workflow_instance_resources.c.added_at)
                .limit(1)
            ).mappings().first()
            if remaining is None:
                conn.execute(workflow_step_actions.delete().where(workflow_step_actions.c.instance_id == iid))
                conn.execute(workflow_instances.delete().where(workflow_instances.c.id == iid))
            else:
                conn.execute(
                    workflow_instances.update().where(workflow_instances.c.id == iid).values(
                        resource_id=remaining["resource_id"], resource_type=remaining["resource_type"],
                        resource_name=remaining["resource_name"],
                    )
                )


def delete_for_resources_batch(connection_id: str, resource_ids: list[str]) -> None:
    """Same cleanup as delete_for_resource(), for many resources in one
    connection — used when permanently deleting a folder with descendants,
    which previously called delete_for_resource() once per descendant
    (each opening its own connection). The per-affected-instance loop below
    still runs in Python, but against the one already-open connection, not
    a fresh one per resource — instance count is bounded by how many
    workflow instances reference any of the deleted resources, not by the
    (potentially much larger) descendant count."""
    if not resource_ids:
        return
    with _engine.begin() as conn:
        affected = conn.execute(
            sa.select(workflow_instance_resources.c.instance_id)
            .distinct()
            .select_from(
                workflow_instance_resources.join(
                    workflow_instances, workflow_instances.c.id == workflow_instance_resources.c.instance_id
                )
            )
            .where(
                workflow_instances.c.connection_id == connection_id,
                workflow_instance_resources.c.resource_id.in_(resource_ids),
            )
        ).mappings().all()
        instance_ids = [r["instance_id"] for r in affected]
        conn.execute(
            workflow_instance_resources.delete().where(
                workflow_instance_resources.c.resource_id.in_(resource_ids),
                workflow_instance_resources.c.instance_id.in_(
                    sa.select(workflow_instances.c.id).where(workflow_instances.c.connection_id == connection_id)
                ),
            )
        )
        for iid in instance_ids:
            remaining = conn.execute(
                sa.select(workflow_instance_resources)
                .where(workflow_instance_resources.c.instance_id == iid)
                .order_by(workflow_instance_resources.c.added_at)
                .limit(1)
            ).mappings().first()
            if remaining is None:
                conn.execute(workflow_step_actions.delete().where(workflow_step_actions.c.instance_id == iid))
                conn.execute(workflow_instances.delete().where(workflow_instances.c.id == iid))
            else:
                conn.execute(
                    workflow_instances.update().where(workflow_instances.c.id == iid).values(
                        resource_id=remaining["resource_id"], resource_type=remaining["resource_type"],
                        resource_name=remaining["resource_name"],
                    )
                )


def delete_for_group(group_id: str) -> None:
    """Called when a group is deleted — a step's assignees may name it, and
    a workflow definition referencing a group that no longer exists would
    otherwise silently become uneditable-by-anyone at that step forever.
    Only touches *definitions*; per-instance steps_json snapshots keep
    their own copy and are left as a historical record (mirrors how a
    definition delete never touches already-created instances either)."""
    with _engine.begin() as conn:
        rows = conn.execute(sa.select(workflow_definitions)).mappings().all()
        for row in rows:
            steps = json.loads(row["steps_json"])
            changed = False
            for step in steps:
                assignees = step.get("assignees") or []
                filtered = [a for a in assignees if not (a["type"] == "group" and a["id"] == group_id)]
                if len(filtered) != len(assignees):
                    step["assignees"] = filtered
                    changed = True
            if changed:
                conn.execute(
                    workflow_definitions.update().where(workflow_definitions.c.id == row["id"]).values(
                        steps_json=json.dumps(steps)
                    )
                )


def delete_for_connection(connection_id: str) -> None:
    """Called when a connection is removed — same orphaning concern as
    delete_for_resource, scoped to every instance that connection ever
    created. Workflow *definitions* are global (shared across connections,
    like tag definitions), so only instances are removed here."""
    with _engine.begin() as conn:
        subq = sa.select(workflow_instances.c.id).where(workflow_instances.c.connection_id == connection_id)
        conn.execute(workflow_instance_resources.delete().where(workflow_instance_resources.c.instance_id.in_(subq)))
        conn.execute(workflow_step_actions.delete().where(workflow_step_actions.c.instance_id.in_(subq)))
        conn.execute(workflow_instances.delete().where(workflow_instances.c.connection_id == connection_id))


def cancel_instance(instance_id: str) -> dict | None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            workflow_instances.update().where(
                workflow_instances.c.id == instance_id, workflow_instances.c.status == "in_review"
            ).values(status="cancelled", completed_at=now)
        )
        row = conn.execute(sa.select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().first()
        return _inst_row(conn, row) if row else None
