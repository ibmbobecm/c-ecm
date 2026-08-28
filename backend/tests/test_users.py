"""Tests for users_store and the /users router."""
import pytest
from fastapi.testclient import TestClient


def test_list_users_requires_auth(client: TestClient):
    resp = client.get("/users")
    assert resp.status_code == 403  # No Bearer token


def test_list_users_requires_admin(client: TestClient, auth_headers):
    # The default admin user is an admin, so this should pass
    resp = client.get("/users", headers=auth_headers)
    assert resp.status_code == 200
    users = resp.json()
    assert any(u["username"] == "admin" for u in users)


def test_create_and_delete_user(client: TestClient, auth_headers):
    resp = client.post("/users", headers=auth_headers, json={
        "username": "testuser_crud",
        "password": "password123",
        "display_name": "Test User",
        "email": "test@example.com",
        "roles": ["viewer"],
    })
    assert resp.status_code == 201, resp.text
    user = resp.json()
    assert user["username"] == "testuser_crud"
    assert user["roles"] == ["viewer"]
    assert user["is_active"] is True

    uid = user["id"]

    # Duplicate username rejected (password valid length so the validation passes and we get 409)
    resp2 = client.post("/users", headers=auth_headers, json={
        "username": "testuser_crud",
        "password": "password123",
        "display_name": "Dup",
        "roles": ["viewer"],
    })
    assert resp2.status_code == 409

    # Delete
    resp3 = client.delete(f"/users/{uid}", headers=auth_headers)
    assert resp3.status_code == 204

    # Confirm gone
    resp4 = client.get("/users", headers=auth_headers)
    assert not any(u["id"] == uid for u in resp4.json())


def test_update_user_roles(client: TestClient, auth_headers):
    resp = client.post("/users", headers=auth_headers, json={
        "username": "testuser_role",
        "password": "password123",
        "display_name": "Role Tester",
        "roles": ["viewer"],
    })
    assert resp.status_code == 201
    uid = resp.json()["id"]

    resp2 = client.patch(f"/users/{uid}", headers=auth_headers, json={"roles": ["viewer", "editor"]})
    assert resp2.status_code == 200
    assert set(resp2.json()["roles"]) == {"viewer", "editor"}

    client.delete(f"/users/{uid}", headers=auth_headers)


def test_me_endpoint(client: TestClient, auth_headers):
    resp = client.get("/users/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_cannot_demote_or_deactivate_the_last_active_admin(client: TestClient, auth_headers):
    # update_user() had no guard against this at all: an admin could strip
    # their own "admin" role or deactivate their own account via a plain
    # PATCH. Since is_active is checked live on every request (auth.py's
    # get_current_user) and init_db() only seeds a username that doesn't
    # exist yet, that would be an unrecoverable lockout the moment there's
    # no other active admin left to undo it.
    me = client.get("/users/me", headers=auth_headers).json()
    admin_id = me["id"]
    assert "admin" in me["roles"]

    resp = client.patch(f"/users/{admin_id}", headers=auth_headers, json={"roles": ["editor", "viewer"]})
    assert resp.status_code == 400

    resp2 = client.patch(f"/users/{admin_id}", headers=auth_headers, json={"is_active": False})
    assert resp2.status_code == 400

    # Confirm neither call actually took effect.
    me_after = client.get("/users/me", headers=auth_headers).json()
    assert "admin" in me_after["roles"]
    assert me_after["is_active"] is True


def test_can_demote_admin_when_another_active_admin_exists(client: TestClient, auth_headers):
    resp = client.post("/users", headers=auth_headers, json={
        "username": "second_admin", "password": "password123", "display_name": "Second Admin", "roles": ["admin"],
    })
    assert resp.status_code == 201
    second_id = resp.json()["id"]
    try:
        # Demoting the second admin is fine — "admin" (the original) is
        # still an active admin, so the system isn't left without one.
        resp2 = client.patch(f"/users/{second_id}", headers=auth_headers, json={"roles": ["viewer"]})
        assert resp2.status_code == 200
        assert resp2.json()["roles"] == ["viewer"]
    finally:
        client.delete(f"/users/{second_id}", headers=auth_headers)


def test_viewer_cannot_create_user(client: TestClient, auth_headers):
    # Create a viewer
    client.post("/users", headers=auth_headers, json={
        "username": "viewer_user",
        "password": "viewpass",
        "display_name": "Viewer",
        "roles": ["viewer"],
    })
    login = client.post("/auth/login", json={"username": "viewer_user", "password": "viewpass"})
    viewer_token = login.json()["access_token"]
    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

    # Viewer should not be able to create a user
    resp = client.post("/users", headers=viewer_headers, json={
        "username": "another",
        "password": "password123",
        "display_name": "Another",
        "roles": ["viewer"],
    })
    assert resp.status_code == 403

    # Cleanup
    users = client.get("/users", headers=auth_headers).json()
    for u in users:
        if u["username"] == "viewer_user":
            client.delete(f"/users/{u['id']}", headers=auth_headers)
