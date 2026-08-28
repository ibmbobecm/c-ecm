def test_create_folder_and_list_contents(client, conn_headers):
    created = client.post("/folders", headers=conn_headers, json={"name": "Reports", "parent_id": None})
    assert created.status_code == 201
    folder = created.json()
    assert folder["name"] == "Reports"

    listed = client.get("/folders/contents", headers=conn_headers)
    assert any(f["id"] == folder["id"] for f in listed.json()["folders"])


def test_rename_and_move_folder(client, conn_headers):
    a = client.post("/folders", headers=conn_headers, json={"name": "A", "parent_id": None}).json()
    b = client.post("/folders", headers=conn_headers, json={"name": "B", "parent_id": None}).json()

    renamed = client.patch(f"/folders/{a['id']}", headers=conn_headers, json={"name": "A-renamed"})
    assert renamed.json()["name"] == "A-renamed"

    moved = client.patch(f"/folders/{a['id']}", headers=conn_headers, json={"parent_id": b["id"]})
    assert moved.json()["parent_id"] == b["id"]

    contents_of_b = client.get("/folders/contents", headers=conn_headers, params={"folder_id": b["id"]})
    assert any(f["id"] == a["id"] for f in contents_of_b.json()["folders"])


def test_upload_download_roundtrip(client, conn_headers):
    up = client.post("/files", headers=conn_headers, files={"upload": ("doc.txt", b"round trip content", "text/plain")})
    assert up.status_code == 201
    file_id = up.json()["id"]

    down = client.get(f"/files/{file_id}/download", headers=conn_headers)
    assert down.status_code == 200
    assert down.content == b"round trip content"


def test_versioning_create_list_restore(client, conn_headers):
    f = client.post("/files", headers=conn_headers, files={"upload": ("v.txt", b"version 1", "text/plain")}).json()

    v2 = client.post(f"/files/{f['id']}/versions", headers=conn_headers, files={"upload": ("v.txt", b"version 2", "text/plain")})
    assert v2.status_code == 201
    assert v2.json()["version_number"] == 2

    versions = client.get(f"/files/{f['id']}/versions", headers=conn_headers).json()
    assert len(versions) == 2
    v1_id = next(v["id"] for v in versions if v["version_number"] == 1)

    restored = client.post(f"/files/{f['id']}/versions/{v1_id}/restore", headers=conn_headers)
    assert restored.status_code == 200

    current = client.get(f"/files/{f['id']}/download", headers=conn_headers)
    assert current.content == b"version 1"


def test_trash_restore_and_permanent_delete_file(client, conn_headers):
    f = client.post("/files", headers=conn_headers, files={"upload": ("t.txt", b"x", "text/plain")}).json()

    assert client.delete(f"/files/{f['id']}", headers=conn_headers).status_code == 204
    trash = client.get("/folders/contents", headers=conn_headers, params={"view": "trash"}).json()
    assert any(x["id"] == f["id"] for x in trash["files"])

    restored = client.post(f"/files/{f['id']}/restore", headers=conn_headers)
    assert restored.status_code == 200
    mine = client.get("/folders/contents", headers=conn_headers).json()
    assert any(x["id"] == f["id"] for x in mine["files"])

    client.delete(f"/files/{f['id']}", headers=conn_headers)
    perm = client.delete(f"/files/{f['id']}/permanent", headers=conn_headers)
    assert perm.status_code == 204
    trash_after = client.get("/folders/contents", headers=conn_headers, params={"view": "trash"}).json()
    assert not any(x["id"] == f["id"] for x in trash_after["files"])


def test_search_finds_by_name(client, conn_headers):
    client.post("/files", headers=conn_headers, files={"upload": ("findme-unique.txt", b"x", "text/plain")})
    resp = client.get("/search", headers=conn_headers, params={"q": "findme-unique"})
    assert resp.status_code == 200
    assert any("findme-unique" in f["name"] for f in resp.json()["files"])


def test_missing_connection_header_is_400(client, auth_headers):
    resp = client.get("/folders/contents", headers=auth_headers)
    assert resp.status_code == 400


def test_unknown_connection_id_is_404(client, auth_headers):
    resp = client.get("/folders/contents", headers={**auth_headers, "X-Connection-Id": "does-not-exist"})
    assert resp.status_code == 404
