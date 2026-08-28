def test_moving_folder_into_itself_is_rejected(client, conn_headers):
    a = client.post("/folders", headers=conn_headers, json={"name": "A", "parent_id": None}).json()
    resp = client.patch(f"/folders/{a['id']}", headers=conn_headers, json={"parent_id": a["id"]})
    assert resp.status_code == 400


def test_moving_folder_into_its_own_descendant_is_rejected(client, conn_headers):
    a = client.post("/folders", headers=conn_headers, json={"name": "A", "parent_id": None}).json()
    b = client.post("/folders", headers=conn_headers, json={"name": "B", "parent_id": a["id"]}).json()
    c = client.post("/folders", headers=conn_headers, json={"name": "C", "parent_id": b["id"]}).json()

    resp = client.patch(f"/folders/{a['id']}", headers=conn_headers, json={"parent_id": c["id"]})
    assert resp.status_code == 400

    # A itself must be untouched — still at root, not silently orphaned.
    contents = client.get("/folders/contents", headers=conn_headers).json()
    assert any(f["id"] == a["id"] for f in contents["folders"])


def test_moving_folder_to_a_sibling_still_works(client, conn_headers):
    a = client.post("/folders", headers=conn_headers, json={"name": "A", "parent_id": None}).json()
    b = client.post("/folders", headers=conn_headers, json={"name": "B", "parent_id": None}).json()
    resp = client.patch(f"/folders/{a['id']}", headers=conn_headers, json={"parent_id": b["id"]})
    assert resp.status_code == 200
    assert resp.json()["parent_id"] == b["id"]
