"""Tests for locks_store and the /locks router."""
import pytest
from fastapi.testclient import TestClient


def test_checkout_and_checkin(client: TestClient, conn_headers, uploaded_file):
    fid = uploaded_file["id"]

    # Check out
    resp = client.post("/locks", headers=conn_headers, json={
        "resource_id": fid,
        "resource_type": "file",
        "comment": "working on it",
    })
    assert resp.status_code == 201, resp.text
    lock = resp.json()
    assert lock["resource_id"] == fid
    assert lock["locked_by"] == "admin"
    assert lock["comment"] == "working on it"

    # GET the lock
    resp2 = client.get(f"/locks/{fid}", headers=conn_headers)
    assert resp2.status_code == 200
    assert resp2.json()["resource_id"] == fid

    # Duplicate checkout → 409
    resp3 = client.post("/locks", headers=conn_headers, json={
        "resource_id": fid, "resource_type": "file",
    })
    assert resp3.status_code == 409

    # Check in
    resp4 = client.delete(f"/locks/{fid}", headers=conn_headers)
    assert resp4.status_code == 204

    # Lock is gone
    resp5 = client.get(f"/locks/{fid}", headers=conn_headers)
    assert resp5.status_code == 200
    assert resp5.json() is None


def test_upload_blocked_when_checked_out_by_other(client: TestClient, auth_headers, conn_headers, uploaded_file):
    """A second user (editor) cannot upload a new version while the file is
    checked out by admin."""
    # Create an editor user
    client.post("/users", headers=auth_headers, json={
        "username": "editor_lock_test",
        "password": "editpass",
        "display_name": "Editor",
        "roles": ["editor"],
    })
    editor_login = client.post("/auth/login", json={"username": "editor_lock_test", "password": "editpass"})
    editor_token = editor_login.json()["access_token"]

    fid = uploaded_file["id"]
    connection_id = conn_headers["X-Connection-Id"]
    editor_headers = {
        "Authorization": f"Bearer {editor_token}",
        "X-Connection-Id": connection_id,
    }

    # Admin checks out
    client.post("/locks", headers=conn_headers, json={"resource_id": fid, "resource_type": "file"})

    # Editor tries to upload new version → 423
    resp = client.post(
        f"/files/{fid}/versions",
        headers=editor_headers,
        files={"upload": ("hello_v2.txt", b"new content", "text/plain")},
    )
    assert resp.status_code == 423, resp.text

    # Admin checks in, then editor can upload
    client.delete(f"/locks/{fid}", headers=conn_headers)

    resp2 = client.post(
        f"/files/{fid}/versions",
        headers=conn_headers,
        files={"upload": ("hello_v2.txt", b"updated content", "text/plain")},
    )
    assert resp2.status_code == 201

    # Cleanup editor user
    users = client.get("/users", headers=auth_headers).json()
    for u in users:
        if u["username"] == "editor_lock_test":
            client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_admin_can_force_checkin_someone_elses_lock(client: TestClient, auth_headers, conn_headers, uploaded_file):
    # Without an admin override, a checkout from a departed/unavailable
    # user could never be released by anyone else — this was a real gap:
    # the check-in route's error message claimed "the lock holder or an
    # admin" but the code never actually checked for the admin role.
    client.post("/users", headers=auth_headers, json={
        "username": "editor_force_checkin_test", "password": "editpass",
        "display_name": "Editor", "roles": ["editor"],
    })
    connection_id = conn_headers["X-Connection-Id"]
    editor_login = client.post("/auth/login", json={"username": "editor_force_checkin_test", "password": "editpass"})
    editor_headers = {"Authorization": f"Bearer {editor_login.json()['access_token']}", "X-Connection-Id": connection_id}

    fid = uploaded_file["id"]
    try:
        # Editor checks it out.
        checkout = client.post("/locks", headers=editor_headers, json={"resource_id": fid, "resource_type": "file"})
        assert checkout.status_code == 201

        # Admin (not the lock holder) force-checks it in.
        forced = client.delete(f"/locks/{fid}", headers=conn_headers)
        assert forced.status_code == 204

        assert client.get(f"/locks/{fid}", headers=conn_headers).json() is None
    finally:
        client.delete(f"/locks/{fid}", headers=conn_headers)
        users = client.get("/users", headers=auth_headers).json()
        for u in users:
            if u["username"] == "editor_force_checkin_test":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_non_admin_non_holder_cannot_checkin(client: TestClient, auth_headers, conn_headers, uploaded_file):
    client.post("/users", headers=auth_headers, json={
        "username": "editor_denied_checkin_test", "password": "editpass",
        "display_name": "Editor", "roles": ["editor"],
    })
    connection_id = conn_headers["X-Connection-Id"]
    editor_login = client.post("/auth/login", json={"username": "editor_denied_checkin_test", "password": "editpass"})
    editor_headers = {"Authorization": f"Bearer {editor_login.json()['access_token']}", "X-Connection-Id": connection_id}

    fid = uploaded_file["id"]
    try:
        client.post("/locks", headers=conn_headers, json={"resource_id": fid, "resource_type": "file"})  # admin checks out
        resp = client.delete(f"/locks/{fid}", headers=editor_headers)  # a different, non-admin editor tries to check in
        assert resp.status_code == 403
    finally:
        client.delete(f"/locks/{fid}", headers=conn_headers)
        users = client.get("/users", headers=auth_headers).json()
        for u in users:
            if u["username"] == "editor_denied_checkin_test":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_list_locks(client: TestClient, conn_headers, uploaded_file):
    fid = uploaded_file["id"]
    client.post("/locks", headers=conn_headers, json={"resource_id": fid, "resource_type": "file"})
    resp = client.get("/locks", headers=conn_headers)
    assert resp.status_code == 200
    assert any(l["resource_id"] == fid for l in resp.json())
    client.delete(f"/locks/{fid}", headers=conn_headers)
