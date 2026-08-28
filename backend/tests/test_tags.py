def test_create_attach_list_detach_tag(client, conn_headers, uploaded_file, auth_headers):
    tag = client.post("/tags", headers=auth_headers, json={"name": "Urgent", "color": "#D93025"})
    assert tag.status_code == 201
    tag_id = tag.json()["id"]

    attach = client.post(f"/resources/{uploaded_file['id']}/tags", headers=conn_headers, json={"resource_type": "file", "tag_id": tag_id})
    assert attach.status_code == 201
    assert any(t["id"] == tag_id for t in attach.json())

    listed = client.get(f"/resources/{uploaded_file['id']}/tags", headers=conn_headers)
    assert any(t["id"] == tag_id for t in listed.json())

    detach = client.delete(f"/resources/{uploaded_file['id']}/tags/{tag_id}", headers=conn_headers)
    assert detach.status_code == 204
    after = client.get(f"/resources/{uploaded_file['id']}/tags", headers=conn_headers)
    assert not any(t["id"] == tag_id for t in after.json())


def test_get_or_create_tag_is_idempotent_by_name(client, auth_headers):
    a = client.post("/tags", headers=auth_headers, json={"name": "Shared", "color": "#000000"})
    b = client.post("/tags", headers=auth_headers, json={"name": "shared", "color": "#ffffff"})  # different case, different color
    assert a.json()["id"] == b.json()["id"]  # same tag, matched case-insensitively — color from the first create wins


def test_bulk_tags_endpoint(client, conn_headers, uploaded_file, auth_headers):
    tag = client.post("/tags", headers=auth_headers, json={"name": "Bulk", "color": "#000000"}).json()
    client.post(f"/resources/{uploaded_file['id']}/tags", headers=conn_headers, json={"resource_type": "file", "tag_id": tag["id"]})

    resp = client.post("/resources/tags/bulk", headers=conn_headers, json={"resource_ids": [uploaded_file["id"], "nonexistent"]})
    assert resp.status_code == 200
    body = resp.json()
    assert any(t["id"] == tag["id"] for t in body[uploaded_file["id"]])
    assert body["nonexistent"] == []


def test_attaching_unknown_tag_id_is_404(client, conn_headers, uploaded_file):
    resp = client.post(f"/resources/{uploaded_file['id']}/tags", headers=conn_headers, json={"resource_type": "file", "tag_id": "does-not-exist"})
    assert resp.status_code == 404


def test_tag_attachments_cleaned_up_when_connection_deleted(client, auth_headers):
    conn = client.post(
        "/connections", headers=auth_headers,
        json={"provider_key": "local", "display_name": "tag-cascade-test", "username": "", "password": "", "config": {}},
    ).json()
    headers = {**auth_headers, "X-Connection-Id": conn["id"]}
    f = client.post("/files", headers=headers, files={"upload": ("a.txt", b"x", "text/plain")}).json()
    tag = client.post("/tags", headers=auth_headers, json={"name": "CascadeTag", "color": "#000"}).json()
    client.post(f"/resources/{f['id']}/tags", headers=headers, json={"resource_type": "file", "tag_id": tag["id"]})

    assert client.delete(f"/connections/{conn['id']}", headers=auth_headers).status_code == 204

    # The connection is gone, so tags_store has to be inspected directly —
    # the API itself now 404s for this connection_id, which is expected.
    from app import tags_store

    assert tags_store.get_tags_for_resource(conn["id"], f["id"]) == []


def test_tag_attachments_cleaned_up_on_permanent_delete(client, conn_headers, uploaded_file, auth_headers, local_connection):
    tag = client.post("/tags", headers=auth_headers, json={"name": "PermDeleteTag", "color": "#000"}).json()
    client.post(f"/resources/{uploaded_file['id']}/tags", headers=conn_headers, json={"resource_type": "file", "tag_id": tag["id"]})

    client.delete(f"/files/{uploaded_file['id']}", headers=conn_headers)  # trash
    resp = client.delete(f"/files/{uploaded_file['id']}/permanent", headers=conn_headers)
    assert resp.status_code == 204

    from app import tags_store

    assert tags_store.get_tags_for_resource(local_connection["id"], uploaded_file["id"]) == []


def test_tag_on_file_inside_deleted_folder_is_cleaned_up(client, conn_headers, auth_headers, local_connection):
    # A trashed folder's own get_children() 404s on most providers, so the
    # subtree has to be snapshotted at trash time to still be cleanable —
    # this exercises that whole path, not just the single-resource case.
    folder = client.post("/folders", headers=conn_headers, json={"name": "Subtree", "parent_id": None}).json()
    inner = client.post(
        "/files", headers=conn_headers,
        files={"upload": ("inner.txt", b"x", "text/plain")}, data={"folder_id": folder["id"]},
    ).json()
    tag = client.post("/tags", headers=auth_headers, json={"name": "SubtreeTag", "color": "#000"}).json()
    client.post(f"/resources/{inner['id']}/tags", headers=conn_headers, json={"resource_type": "file", "tag_id": tag["id"]})

    assert client.delete(f"/folders/{folder['id']}", headers=conn_headers).status_code == 204
    assert client.delete(f"/folders/{folder['id']}/permanent", headers=conn_headers).status_code == 204

    from app import tags_store

    assert tags_store.get_tags_for_resource(local_connection["id"], inner["id"]) == []
