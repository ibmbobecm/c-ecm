def test_create_list_resolve_delete_comment(client, conn_headers, uploaded_file):
    created = client.post(f"/resources/{uploaded_file['id']}/comments", headers=conn_headers, json={"resource_type": "file", "body": "looks good"})
    assert created.status_code == 201
    comment = created.json()
    assert comment["body"] == "looks good"
    assert comment["resolved_at"] is None

    listed = client.get(f"/resources/{uploaded_file['id']}/comments", headers=conn_headers)
    assert len(listed.json()) == 1

    resolved = client.patch(f"/comments/{comment['id']}", headers=conn_headers, json={"resolved": True})
    assert resolved.status_code == 200
    assert resolved.json()["resolved_at"] is not None
    assert resolved.json()["resolved_by"] == "admin"

    reopened = client.patch(f"/comments/{comment['id']}", headers=conn_headers, json={"resolved": False})
    assert reopened.json()["resolved_at"] is None

    deleted = client.delete(f"/comments/{comment['id']}", headers=conn_headers)
    assert deleted.status_code == 204
    assert client.get(f"/resources/{uploaded_file['id']}/comments", headers=conn_headers).json() == []


def test_comment_creation_records_activity_and_notification(client, conn_headers, uploaded_file, auth_headers, local_connection):
    client.post(f"/resources/{uploaded_file['id']}/comments", headers=conn_headers, json={"resource_type": "file", "body": "notify me"})

    events = client.get("/activity", headers=auth_headers, params={"connection_id": local_connection["id"], "event_type": "commented"}).json()
    assert any(e["resource_id"] == uploaded_file["id"] and e["resource_name"] == "hello.txt" for e in events)

    notifs = client.get("/notifications", headers=auth_headers).json()
    assert any("commented" in n["message"] or "hello.txt" in n["message"] for n in notifs["notifications"])


def test_editing_nonexistent_comment_is_404(client, conn_headers):
    resp = client.patch("/comments/does-not-exist", headers=conn_headers, json={"body": "x"})
    assert resp.status_code == 404


def test_comments_cleaned_up_when_connection_deleted(client, auth_headers):
    conn = client.post(
        "/connections", headers=auth_headers,
        json={"provider_key": "local", "display_name": "comment-cascade-test", "username": "", "password": "", "config": {}},
    ).json()
    headers = {**auth_headers, "X-Connection-Id": conn["id"]}
    f = client.post("/files", headers=headers, files={"upload": ("a.txt", b"x", "text/plain")}).json()
    client.post(f"/resources/{f['id']}/comments", headers=headers, json={"resource_type": "file", "body": "bye"})

    assert client.delete(f"/connections/{conn['id']}", headers=auth_headers).status_code == 204

    from app import comments_store

    assert comments_store.list_for_resource(conn["id"], f["id"]) == []
