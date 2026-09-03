"""Tests for workflows_store and /workflows router."""
import pytest
from fastapi.testclient import TestClient


def _assignee(username: str) -> dict:
    return {"type": "user", "id": username}


def test_create_definition(client: TestClient, auth_headers, conn_headers):
    resp = client.post("/workflows/definitions", headers=conn_headers, json={
        "name": "Standard Approval",
        "description": "Two-step approval workflow",
        "steps": [
            {"name": "Legal Review", "assignees": [_assignee("admin")], "required_approvals": 1},
        ],
    })
    assert resp.status_code == 201, resp.text
    defn = resp.json()
    assert defn["name"] == "Standard Approval"
    assert len(defn["steps"]) == 1
    # Cleanup
    client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)


def test_list_definitions(client: TestClient, auth_headers, conn_headers):
    resp = client.get("/workflows/definitions", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def _make_user(client: TestClient, auth_headers, username: str) -> dict:
    client.post("/users", headers=auth_headers, json={
        "username": username, "password": "testpass123", "display_name": username,
    })
    login = client.post("/auth/login", json={"username": username, "password": "testpass123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _delete_user(client: TestClient, auth_headers, username: str) -> None:
    for u in client.get("/users", headers=auth_headers).json():
        if u["username"] == username:
            client.delete(f"/users/{u['id']}", headers=auth_headers)


def _start_instance(client: TestClient, headers, definition_id: str, resource_id: str, comment: str | None = None) -> dict:
    return client.post("/workflows/instances", headers=headers, json={
        "definition_id": definition_id,
        "resources": [{"resource_id": resource_id, "resource_type": "file"}],
        "comment": comment,
    }).json()


def test_non_designated_reviewer_cannot_act_on_step(client: TestClient, auth_headers, conn_headers, uploaded_file):
    # The step names only "admin" as an assignee — a different authenticated
    # user must not be able to approve/reject it. Previously this wasn't
    # checked at all: the assignees list was stored but never consulted.
    outsider_headers = _make_user(client, auth_headers, "wf_outsider")
    outsider_headers["X-Connection-Id"] = conn_headers["X-Connection-Id"]
    try:
        defn = client.post("/workflows/definitions", headers=conn_headers, json={
            "name": f"Admin Only {uploaded_file['id']}", "description": None,
            "steps": [{"name": "Review", "assignees": [_assignee("admin")], "required_approvals": 1}],
        }).json()
        inst = _start_instance(client, conn_headers, defn["id"], uploaded_file["id"])

        resp = client.post(f"/workflows/instances/{inst['id']}/action", headers=outsider_headers, json={"action": "approved"})
        assert resp.status_code == 403

        client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)
    finally:
        _delete_user(client, auth_headers, "wf_outsider")


def test_quorum_requires_all_approvals_before_advancing(client: TestClient, auth_headers, conn_headers, uploaded_file):
    approver_headers = _make_user(client, auth_headers, "wf_approver2")
    approver_headers["X-Connection-Id"] = conn_headers["X-Connection-Id"]
    try:
        defn = client.post("/workflows/definitions", headers=conn_headers, json={
            "name": f"Quorum Two {uploaded_file['id']}", "description": None,
            "steps": [{"name": "Review", "assignees": [_assignee("admin"), _assignee("wf_approver2")], "required_approvals": 2}],
        }).json()
        inst = _start_instance(client, conn_headers, defn["id"], uploaded_file["id"])
        iid = inst["id"]

        # First approval: quorum not met yet, must still be in_review.
        first = client.post(f"/workflows/instances/{iid}/action", headers=conn_headers, json={"action": "approved"})
        assert first.status_code == 200
        assert first.json()["status"] == "in_review"

        # admin voting again on the same step must be rejected (no double-voting).
        dup = client.post(f"/workflows/instances/{iid}/action", headers=conn_headers, json={"action": "approved"})
        assert dup.status_code == 409

        # Second, distinct authorized approver completes the quorum.
        second = client.post(f"/workflows/instances/{iid}/action", headers=approver_headers, json={"action": "approved"})
        assert second.status_code == 200
        assert second.json()["status"] == "approved"

        client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)
    finally:
        _delete_user(client, auth_headers, "wf_approver2")


def test_empty_assignees_list_allows_any_authenticated_user(client: TestClient, auth_headers, conn_headers, uploaded_file):
    outsider_headers = _make_user(client, auth_headers, "wf_open_reviewer")
    outsider_headers["X-Connection-Id"] = conn_headers["X-Connection-Id"]
    try:
        defn = client.post("/workflows/definitions", headers=conn_headers, json={
            "name": f"Open Review {uploaded_file['id']}", "description": None,
            "steps": [{"name": "Review", "assignees": [], "required_approvals": 1}],
        }).json()
        inst = _start_instance(client, conn_headers, defn["id"], uploaded_file["id"])
        resp = client.post(f"/workflows/instances/{inst['id']}/action", headers=outsider_headers, json={"action": "approved"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)
    finally:
        _delete_user(client, auth_headers, "wf_open_reviewer")


def test_start_and_act_on_workflow(client: TestClient, auth_headers, conn_headers, uploaded_file):
    # Create definition
    defn_resp = client.post("/workflows/definitions", headers=conn_headers, json={
        "name": "File Approval",
        "description": None,
        "steps": [{"name": "Review", "assignees": [_assignee("admin")], "required_approvals": 1}],
    })
    def_id = defn_resp.json()["id"]
    fid = uploaded_file["id"]

    # Start instance
    inst = _start_instance(client, conn_headers, def_id, fid, comment="Please approve this file")
    assert inst["status"] == "in_review"
    assert len(inst["resources"]) == 1
    assert inst["resources"][0]["resource_id"] == fid
    iid = inst["id"]

    # Approve — schema requires "approved" not "approve"
    action_resp = client.post(f"/workflows/instances/{iid}/action", headers=conn_headers, json={
        "action": "approved",
        "comment": "Looks good",
    })
    assert action_resp.status_code == 200, action_resp.text
    assert action_resp.json()["status"] == "approved"

    # Cleanup definition
    client.delete(f"/workflows/definitions/{def_id}", headers=auth_headers)


def test_start_workflow_with_multiple_documents(client: TestClient, auth_headers, conn_headers, uploaded_file):
    defn_resp = client.post("/workflows/definitions", headers=conn_headers, json={
        "name": f"Multi Doc {uploaded_file['id']}",
        "description": None,
        "steps": [{"name": "Review", "assignees": [_assignee("admin")], "required_approvals": 1}],
    })
    def_id = defn_resp.json()["id"]
    fid = uploaded_file["id"]

    inst_resp = client.post("/workflows/instances", headers=conn_headers, json={
        "definition_id": def_id,
        "resources": [
            {"resource_id": fid, "resource_type": "file"},
            {"resource_id": fid, "resource_type": "file"},
        ],
        "comment": None,
    })
    assert inst_resp.status_code == 201, inst_resp.text
    inst = inst_resp.json()
    assert len(inst["resources"]) == 2

    # Cannot remove the last remaining document.
    row_id = inst["resources"][0]["id"]
    client.delete(f"/workflows/instances/{inst['id']}/resources/{row_id}", headers=conn_headers)
    last_row_id = inst["resources"][1]["id"]
    resp = client.delete(f"/workflows/instances/{inst['id']}/resources/{last_row_id}", headers=conn_headers)
    assert resp.status_code == 400

    client.post(f"/workflows/instances/{inst['id']}/cancel", headers=conn_headers)
    client.delete(f"/workflows/definitions/{def_id}", headers=auth_headers)


def test_reassign_step_changes_who_can_act(client: TestClient, auth_headers, conn_headers, uploaded_file):
    first_headers = _make_user(client, auth_headers, "wf_reassign_from")
    second_headers = _make_user(client, auth_headers, "wf_reassign_to")
    first_headers["X-Connection-Id"] = conn_headers["X-Connection-Id"]
    second_headers["X-Connection-Id"] = conn_headers["X-Connection-Id"]
    try:
        defn = client.post("/workflows/definitions", headers=conn_headers, json={
            "name": f"Reassign {uploaded_file['id']}", "description": None,
            "steps": [{"name": "Review", "assignees": [_assignee("wf_reassign_from")], "required_approvals": 1}],
        }).json()
        inst = _start_instance(client, conn_headers, defn["id"], uploaded_file["id"])

        # Requester (admin, via conn_headers) reassigns to the second user.
        reassign_resp = client.post(
            f"/workflows/instances/{inst['id']}/reassign", headers=conn_headers,
            json={"assignees": [_assignee("wf_reassign_to")]},
        )
        assert reassign_resp.status_code == 200, reassign_resp.text

        # The original assignee can no longer act; the new one can.
        blocked = client.post(f"/workflows/instances/{inst['id']}/action", headers=first_headers, json={"action": "approved"})
        assert blocked.status_code == 403

        allowed = client.post(f"/workflows/instances/{inst['id']}/action", headers=second_headers, json={"action": "approved"})
        assert allowed.status_code == 200
        assert allowed.json()["status"] == "approved"

        # The shared definition itself must be untouched by the reassign.
        still_shared = client.get("/workflows/definitions", headers=auth_headers).json()
        shared_def = next(d for d in still_shared if d["id"] == defn["id"])
        assert shared_def["steps"][0]["assignees"] == [_assignee("wf_reassign_from")]

        client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)
    finally:
        _delete_user(client, auth_headers, "wf_reassign_from")
        _delete_user(client, auth_headers, "wf_reassign_to")


def test_cancel_workflow(client: TestClient, auth_headers, conn_headers, uploaded_file):
    defn_resp = client.post("/workflows/definitions", headers=conn_headers, json={
        "name": "Cancellable",
        "description": None,
        "steps": [{"name": "Step 1", "assignees": [_assignee("admin")], "required_approvals": 1}],
    })
    def_id = defn_resp.json()["id"]

    inst = _start_instance(client, conn_headers, def_id, uploaded_file["id"])
    iid = inst["id"]

    cancel_resp = client.post(f"/workflows/instances/{iid}/cancel", headers=conn_headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    client.delete(f"/workflows/definitions/{def_id}", headers=auth_headers)


def test_only_requester_or_admin_can_cancel_instance(client: TestClient, auth_headers, conn_headers, uploaded_file):
    # Previously cancel_instance() had no ownership check at all — any
    # authenticated user could cancel anyone else's pending approval
    # request, silently killing a workflow they had no part in.
    other_headers = _make_user(client, auth_headers, "wf_bystander")
    other_headers["X-Connection-Id"] = conn_headers["X-Connection-Id"]
    try:
        defn = client.post("/workflows/definitions", headers=conn_headers, json={
            "name": f"Cancel Ownership {uploaded_file['id']}", "description": None,
            "steps": [{"name": "Review", "assignees": [_assignee("admin")], "required_approvals": 1}],
        }).json()
        inst = _start_instance(client, conn_headers, defn["id"], uploaded_file["id"])

        # requested_by is "admin" (conn_headers) — a different, non-admin
        # user must not be able to cancel it.
        resp = client.post(f"/workflows/instances/{inst['id']}/cancel", headers=other_headers)
        assert resp.status_code == 403

        # The requester themself still can.
        resp2 = client.post(f"/workflows/instances/{inst['id']}/cancel", headers=conn_headers)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "cancelled"

        client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)
    finally:
        _delete_user(client, auth_headers, "wf_bystander")


def test_admin_can_cancel_someone_elses_instance(client: TestClient, auth_headers, conn_headers, uploaded_file):
    requester_headers = _make_user(client, auth_headers, "wf_requester")
    requester_headers["X-Connection-Id"] = conn_headers["X-Connection-Id"]
    try:
        defn = client.post("/workflows/definitions", headers=conn_headers, json={
            "name": f"Admin Cancel {uploaded_file['id']}", "description": None,
            "steps": [{"name": "Review", "assignees": [_assignee("admin")], "required_approvals": 1}],
        }).json()
        inst = _start_instance(client, requester_headers, defn["id"], uploaded_file["id"])

        admin_conn_headers = {**auth_headers, "X-Connection-Id": conn_headers["X-Connection-Id"]}
        resp = client.post(f"/workflows/instances/{inst['id']}/cancel", headers=admin_conn_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)
    finally:
        _delete_user(client, auth_headers, "wf_requester")


def test_deleting_definition_with_in_review_instance_is_blocked(client: TestClient, auth_headers, conn_headers, uploaded_file):
    # Foreign keys aren't enforced in workflows.db, so this delete would
    # otherwise succeed silently and leave the in_review instance pointing
    # at a definition_id that no longer exists — act_on_step() then returns
    # None for it forever (looked up via a definition that's gone), freezing
    # it in in_review status with no way to ever approve or reject it again.
    defn = client.post("/workflows/definitions", headers=conn_headers, json={
        "name": f"Blocked Delete {uploaded_file['id']}", "description": None,
        "steps": [{"name": "Review", "assignees": [_assignee("admin")], "required_approvals": 1}],
    }).json()
    inst = _start_instance(client, conn_headers, defn["id"], uploaded_file["id"])

    resp = client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)
    assert resp.status_code == 409

    # Once the instance is resolved, deletion is allowed again.
    client.post(f"/workflows/instances/{inst['id']}/cancel", headers=conn_headers)
    resp2 = client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)
    assert resp2.status_code == 204


def test_delete_definition_requires_admin(client: TestClient, auth_headers, conn_headers):
    defn_resp = client.post("/workflows/definitions", headers=conn_headers, json={
        "name": "Admin Only Delete",
        "description": None,
        "steps": [],
    })
    def_id = defn_resp.json()["id"]

    # Create a plain (non-superadmin) user and try to delete
    client.post("/users", headers=auth_headers, json={
        "username": "wf_viewer",
        "password": "viewpass",
        "display_name": "WF Viewer",
    })
    viewer_login = client.post("/auth/login", json={"username": "wf_viewer", "password": "viewpass"})
    viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}

    resp = client.delete(f"/workflows/definitions/{def_id}", headers=viewer_headers)
    assert resp.status_code == 403

    # Cleanup
    client.delete(f"/workflows/definitions/{def_id}", headers=auth_headers)
    users = client.get("/users", headers=auth_headers).json()
    for u in users:
        if u["username"] == "wf_viewer":
            client.delete(f"/users/{u['id']}", headers=auth_headers)
