"""Tests for saved searches (POST/GET/DELETE /search/saved, POST .../run)."""


def test_create_list_run_delete_saved_search(client, conn_headers, uploaded_file):
    created = client.post(
        "/search/saved", headers=conn_headers,
        json={"name": "My Search", "connection_id": None, "query": {"text": "hello", "file_types": [], "tag_ids": []}},
    )
    assert created.status_code == 201, created.text
    search_id = created.json()["id"]

    listed = client.get("/search/saved", headers=conn_headers)
    assert any(s["id"] == search_id for s in listed.json())

    ran = client.post(f"/search/saved/{search_id}/run", headers=conn_headers)
    assert ran.status_code == 200

    deleted = client.delete(f"/search/saved/{search_id}", headers=conn_headers)
    assert deleted.status_code == 204
    assert not any(s["id"] == search_id for s in client.get("/search/saved", headers=conn_headers).json())


def test_only_owner_or_superadmin_can_delete_a_saved_search(client, conn_headers, auth_headers):
    # Previously DELETE /search/saved/{id} performed no ownership check at
    # all — any authenticated user who knew or guessed another user's
    # search_id could delete it.
    client.post("/users", headers=auth_headers, json={
        "username": "search_owner", "password": "testpass123", "display_name": "Owner", "is_superadmin": False,
    })
    owner_login = client.post("/auth/login", json={"username": "search_owner", "password": "testpass123"})
    owner_headers = {"Authorization": f"Bearer {owner_login.json()['access_token']}"}

    client.post("/users", headers=auth_headers, json={
        "username": "search_bystander", "password": "testpass123", "display_name": "Bystander", "is_superadmin": False,
    })
    bystander_login = client.post("/auth/login", json={"username": "search_bystander", "password": "testpass123"})
    bystander_headers = {"Authorization": f"Bearer {bystander_login.json()['access_token']}"}

    try:
        created = client.post(
            "/search/saved", headers=owner_headers,
            json={"name": "Owner's Search", "connection_id": None, "query": {"text": "x", "file_types": [], "tag_ids": []}},
        ).json()

        forbidden = client.delete(f"/search/saved/{created['id']}", headers=bystander_headers)
        assert forbidden.status_code == 403

        # The owner themself still can.
        assert client.delete(f"/search/saved/{created['id']}", headers=owner_headers).status_code == 204
    finally:
        for username in ("search_owner", "search_bystander"):
            for u in client.get("/users", headers=auth_headers).json():
                if u["username"] == username:
                    client.delete(f"/users/{u['id']}", headers=auth_headers)
