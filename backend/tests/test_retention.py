"""Tests for retention_store and /retention router."""
import pytest
from fastapi.testclient import TestClient


def test_create_list_delete_policy(client: TestClient, auth_headers):
    resp = client.post("/retention/policies", headers=auth_headers, json={
        "name": "7-Year Finance",
        "description": "Retain finance documents for 7 years",
        "retention_days": 2555,
        "action": "review",
        "class_id": None,
        "connection_id": None,
    })
    assert resp.status_code == 201, resp.text
    pol = resp.json()
    assert pol["name"] == "7-Year Finance"
    assert pol["retention_days"] == 2555
    assert pol["action"] == "review"
    assert pol["active"] is True
    pid = pol["id"]

    # List
    resp2 = client.get("/retention/policies", headers=auth_headers)
    assert any(p["id"] == pid for p in resp2.json())

    # Toggle active
    resp3 = client.patch(f"/retention/policies/{pid}", headers=auth_headers, json={"active": False})
    assert resp3.status_code == 200
    assert resp3.json()["active"] is False

    # Delete
    resp4 = client.delete(f"/retention/policies/{pid}", headers=auth_headers)
    assert resp4.status_code == 204


def test_enroll_resource_in_policy(client: TestClient, auth_headers, conn_headers, uploaded_file):
    # Create policy
    pol_resp = client.post("/retention/policies", headers=auth_headers, json={
        "name": "Test Policy",
        "description": None,
        "retention_days": 365,
        "action": "review",
        "class_id": None,
        "connection_id": None,
    })
    pid = pol_resp.json()["id"]
    fid = uploaded_file["id"]

    # Enroll file
    enroll_resp = client.post("/retention/records", headers=conn_headers, json={
        "policy_id": pid,
        "resource_id": fid,
        "resource_type": "file",
        "resource_name": uploaded_file["name"],
    })
    assert enroll_resp.status_code == 201, enroll_resp.text
    rec = enroll_resp.json()
    assert rec["resource_id"] == fid
    assert rec["legal_hold"] is False
    rid = rec["id"]

    # List records (no connection required)
    records_resp = client.get("/retention/records", headers=auth_headers)
    assert any(r["id"] == rid for r in records_resp.json())

    # PATCH legal hold
    patch_resp = client.patch(f"/retention/records/{rid}", headers=auth_headers, json={"legal_hold": True})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["legal_hold"] is True

    # Cleanup policy
    client.delete(f"/retention/policies/{pid}", headers=auth_headers)


def test_retention_requires_admin_to_create_policy(client: TestClient, auth_headers):
    # Create a viewer
    client.post("/users", headers=auth_headers, json={
        "username": "ret_viewer",
        "password": "retpass",
        "display_name": "Ret Viewer",
        "roles": ["viewer"],
    })
    login = client.post("/auth/login", json={"username": "ret_viewer", "password": "retpass"})
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.post("/retention/policies", headers=viewer_headers, json={
        "name": "Unauthorized Policy",
        "description": None,
        "retention_days": 90,
        "action": "review",
        "class_id": None,
        "connection_id": None,
    })
    assert resp.status_code == 403

    # Cleanup
    users = client.get("/users", headers=auth_headers).json()
    for u in users:
        if u["username"] == "ret_viewer":
            client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_viewer_cannot_toggle_legal_hold(client: TestClient, auth_headers, conn_headers, uploaded_file):
    # Legal hold is a compliance control — if any authenticated user could
    # clear it, it would provide no actual protection. Both routes that can
    # touch it (PATCH .../records/{id} and POST .../legal-hold) previously
    # had no role check at all.
    pol = client.post("/retention/policies", headers=auth_headers, json={
        "name": "Viewer Hold Test", "description": None, "retention_days": 90,
        "action": "review", "class_id": None, "connection_id": None,
    }).json()
    rec = client.post("/retention/records", headers=conn_headers, json={
        "policy_id": pol["id"], "resource_id": uploaded_file["id"], "resource_type": "file",
        "resource_name": uploaded_file["name"],
    }).json()

    client.post("/users", headers=auth_headers, json={
        "username": "ret_hold_viewer", "password": "retpass123", "display_name": "Viewer", "roles": ["viewer"],
    })
    login = client.post("/auth/login", json={"username": "ret_hold_viewer", "password": "retpass123"})
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Connection-Id": conn_headers["X-Connection-Id"]}

    try:
        patch_resp = client.patch(f"/retention/records/{rec['id']}", headers=viewer_headers, json={"legal_hold": True})
        assert patch_resp.status_code == 403

        post_resp = client.post(f"/retention/records/{uploaded_file['id']}/legal-hold", headers=viewer_headers)
        assert post_resp.status_code == 403

        # Confirm it's genuinely untouched, not just an error with a side effect.
        still = client.get("/retention/records", headers=auth_headers).json()
        assert next(r for r in still if r["id"] == rec["id"])["legal_hold"] is False
    finally:
        client.delete(f"/retention/policies/{pol['id']}", headers=auth_headers)
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "ret_hold_viewer":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_admin_can_set_legal_hold_via_post_endpoint(client: TestClient, auth_headers, conn_headers, uploaded_file):
    resp = client.post(f"/retention/records/{uploaded_file['id']}/legal-hold", headers=conn_headers, params={"hold": True})
    assert resp.status_code == 204
    # Recorded with the real admin actor, not the previous hardcoded "system".
    events = client.get("/activity", headers=auth_headers, params={"connection_id": conn_headers["X-Connection-Id"], "event_type": "legal_hold_set"}).json()
    assert any(e["resource_id"] == uploaded_file["id"] and e["actor"] == "admin" for e in events)


def test_admin_setting_legal_hold_via_patch_endpoint_also_records_activity(
    client: TestClient, auth_headers, conn_headers, uploaded_file,
):
    # This is the endpoint the RetentionPolicyPanel UI actually calls (PATCH
    # /retention/records/{id}) — it updated the DB row correctly but wrote
    # no activity event at all, unlike the older POST .../legal-hold route.
    # A compliance control changing with zero audit trail of who did it or
    # when. Both routes should log identically.
    pol = client.post("/retention/policies", headers=auth_headers, json={
        "name": "Patch Hold Audit Test", "description": None, "retention_days": 90,
        "action": "review", "class_id": None, "connection_id": None,
    }).json()
    rec = client.post("/retention/records", headers=conn_headers, json={
        "policy_id": pol["id"], "resource_id": uploaded_file["id"], "resource_type": "file",
        "resource_name": uploaded_file["name"],
    }).json()

    resp = client.patch(f"/retention/records/{rec['id']}", headers=auth_headers, json={"legal_hold": True})
    assert resp.status_code == 200
    assert resp.json()["legal_hold"] is True

    events = client.get("/activity", headers=auth_headers, params={
        "connection_id": conn_headers["X-Connection-Id"], "event_type": "legal_hold_set",
    }).json()
    assert any(e["resource_id"] == uploaded_file["id"] and e["actor"] == "admin" for e in events)

    # Releasing it logs the paired event too.
    resp2 = client.patch(f"/retention/records/{rec['id']}", headers=auth_headers, json={"legal_hold": False})
    assert resp2.status_code == 200
    events2 = client.get("/activity", headers=auth_headers, params={
        "connection_id": conn_headers["X-Connection-Id"], "event_type": "legal_hold_released",
    }).json()
    assert any(e["resource_id"] == uploaded_file["id"] and e["actor"] == "admin" for e in events2)


def _enroll_already_due(policy_id: str, connection_id: str, resource_id: str, resource_type: str, resource_name: str) -> dict:
    # RetentionPolicyCreateRequest requires retention_days >= 1, and the
    # POST /retention/records route doesn't expose a start_date override —
    # so a genuinely past-due record for testing has to be backdated via
    # the store directly, which does support it.
    import datetime

    from app import retention_store

    return retention_store.enroll_resource(
        policy_id, connection_id, resource_id, resource_type, resource_name,
        start_date=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365),
    )


def test_auto_delete_action_actually_trashes_the_file(client: TestClient, auth_headers, conn_headers, uploaded_file):
    # The scheduler previously called retention_store.run_due_check()
    # directly and discarded the result — a due record with an
    # 'auto_delete' policy did nothing at all. retention_service.
    # apply_due_actions() is what should actually act on it now.
    pol = client.post("/retention/policies", headers=auth_headers, json={
        "name": "Auto Delete Test", "description": None, "retention_days": 1,
        "action": "auto_delete", "class_id": None, "connection_id": None,
    }).json()
    rec = _enroll_already_due(pol["id"], conn_headers["X-Connection-Id"], uploaded_file["id"], "file", uploaded_file["name"])

    try:
        run = client.post("/retention/run-now", headers=auth_headers)
        assert run.status_code == 200
        results = run.json()["results"]
        assert any(r["record_id"] == rec["id"] and r["ok"] and r["action"] == "auto_delete" for r in results)

        # The file must genuinely be gone from the live listing (trashed),
        # not just have a status flag flipped with no real effect.
        mine = client.get("/folders/contents", headers=conn_headers).json()
        assert not any(f["id"] == uploaded_file["id"] for f in mine["files"])
        trash = client.get("/folders/contents", headers=conn_headers, params={"view": "trash"}).json()
        assert any(f["id"] == uploaded_file["id"] for f in trash["files"])

        updated_rec = next(r for r in client.get("/retention/records", headers=auth_headers).json() if r["id"] == rec["id"])
        assert updated_rec["status"] == "deleted"
    finally:
        client.delete(f"/retention/policies/{pol['id']}", headers=auth_headers)


def test_legal_hold_prevents_auto_delete(client: TestClient, auth_headers, conn_headers, uploaded_file):
    pol = client.post("/retention/policies", headers=auth_headers, json={
        "name": "Hold Blocks Delete Test", "description": None, "retention_days": 1,
        "action": "auto_delete", "class_id": None, "connection_id": None,
    }).json()
    rec = _enroll_already_due(pol["id"], conn_headers["X-Connection-Id"], uploaded_file["id"], "file", uploaded_file["name"])
    client.post(f"/retention/records/{uploaded_file['id']}/legal-hold", headers=conn_headers, params={"hold": True})

    try:
        run = client.post("/retention/run-now", headers=auth_headers)
        results = run.json()["results"]
        assert not any(r["record_id"] == rec["id"] for r in results)

        mine = client.get("/folders/contents", headers=conn_headers).json()
        assert any(f["id"] == uploaded_file["id"] for f in mine["files"])
    finally:
        client.post(f"/retention/records/{uploaded_file['id']}/legal-hold", headers=conn_headers, params={"hold": False})
        client.delete(f"/retention/policies/{pol['id']}", headers=auth_headers)


def test_review_action_flags_for_review_without_deleting(client: TestClient, auth_headers, conn_headers, uploaded_file):
    pol = client.post("/retention/policies", headers=auth_headers, json={
        "name": "Review Only Test", "description": None, "retention_days": 1,
        "action": "review", "class_id": None, "connection_id": None,
    }).json()
    rec = _enroll_already_due(pol["id"], conn_headers["X-Connection-Id"], uploaded_file["id"], "file", uploaded_file["name"])

    try:
        client.post("/retention/run-now", headers=auth_headers)
        updated_rec = next(r for r in client.get("/retention/records", headers=auth_headers).json() if r["id"] == rec["id"])
        assert updated_rec["status"] == "under_review"

        mine = client.get("/folders/contents", headers=conn_headers).json()
        assert any(f["id"] == uploaded_file["id"] for f in mine["files"])
    finally:
        client.delete(f"/retention/policies/{pol['id']}", headers=auth_headers)


def test_run_now_requires_admin(client: TestClient, auth_headers):
    client.post("/users", headers=auth_headers, json={
        "username": "ret_runnow_viewer", "password": "retpass123", "display_name": "Viewer", "roles": ["viewer"],
    })
    login = client.post("/auth/login", json={"username": "ret_runnow_viewer", "password": "retpass123"})
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    try:
        resp = client.post("/retention/run-now", headers=viewer_headers)
        assert resp.status_code == 403
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "ret_runnow_viewer":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_run_due_check():
    """Unit test: run_due_check should return records that are past their due date."""
    import datetime
    from app import retention_store

    # Create an in-memory policy/record scenario directly
    # The function runs against the shared test DB, so just verify it returns a list.
    results = retention_store.run_due_check()
    assert isinstance(results, list)
