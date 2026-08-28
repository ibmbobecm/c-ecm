def test_create_and_open_share_link_publicly(client, conn_headers, uploaded_file):
    created = client.post(f"/resources/{uploaded_file['id']}/share-links", headers=conn_headers, json={"resource_type": "file", "role": "view"})
    assert created.status_code == 201
    link = created.json()
    assert link["password_protected"] is False

    token = link["url"].rsplit("/", 1)[-1]
    # No auth headers at all — this is the one deliberately public route.
    opened = client.get(f"/share/{token}")
    assert opened.status_code == 200
    assert opened.content == b"hello world"


def test_password_protected_link_requires_correct_password(client, conn_headers, uploaded_file):
    created = client.post(
        f"/resources/{uploaded_file['id']}/share-links", headers=conn_headers,
        json={"resource_type": "file", "role": "view", "password": "s3cret"},
    )
    token = created.json()["url"].rsplit("/", 1)[-1]

    wrong = client.get(f"/share/{token}", params={"password": "nope"})
    assert wrong.status_code == 401

    missing = client.get(f"/share/{token}")
    assert missing.status_code == 401

    right = client.get(f"/share/{token}", params={"password": "s3cret"})
    assert right.status_code == 200


def test_password_brute_force_gets_locked_out(client, conn_headers, uploaded_file):
    created = client.post(
        f"/resources/{uploaded_file['id']}/share-links", headers=conn_headers,
        json={"resource_type": "file", "role": "view", "password": "correct"},
    )
    token = created.json()["url"].rsplit("/", 1)[-1]

    for _ in range(5):
        resp = client.get(f"/share/{token}", params={"password": "wrong"})
        assert resp.status_code == 401

    locked = client.get(f"/share/{token}", params={"password": "wrong"})
    assert locked.status_code == 429

    # Even the correct password is blocked during the lockout window.
    still_locked = client.get(f"/share/{token}", params={"password": "correct"})
    assert still_locked.status_code == 429


def test_revoked_link_returns_404(client, conn_headers, uploaded_file):
    created = client.post(f"/resources/{uploaded_file['id']}/share-links", headers=conn_headers, json={"resource_type": "file", "role": "view"}).json()
    revoke = client.delete(f"/share-links/{uploaded_file['id']}/{created['id']}", headers=conn_headers, params={"resource_type": "file"})
    assert revoke.status_code == 204

    token = created["url"].rsplit("/", 1)[-1]
    resp = client.get(f"/share/{token}")
    assert resp.status_code == 404


def test_nonexistent_token_returns_404(client):
    resp = client.get("/share/this-token-does-not-exist")
    assert resp.status_code == 404


def test_share_links_cleaned_up_when_connection_deleted(client, auth_headers):
    conn = client.post(
        "/connections", headers=auth_headers,
        json={"provider_key": "local", "display_name": "share-cascade-test", "username": "", "password": "", "config": {}},
    ).json()
    headers = {**auth_headers, "X-Connection-Id": conn["id"]}
    f = client.post("/files", headers=headers, files={"upload": ("a.txt", b"x", "text/plain")}).json()
    link = client.post(f"/resources/{f['id']}/share-links", headers=headers, json={"resource_type": "file", "role": "view"}).json()
    token = link["url"].rsplit("/", 1)[-1]

    assert client.delete(f"/connections/{conn['id']}", headers=auth_headers).status_code == 204

    # The registry no longer resolves the token to a live connection.
    resp = client.get(f"/share/{token}")
    assert resp.status_code == 404
