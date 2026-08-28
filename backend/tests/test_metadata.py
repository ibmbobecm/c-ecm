"""Tests for metadata_store and the /metadata router."""
import pytest
from fastapi.testclient import TestClient


def test_create_and_list_classes(client: TestClient, auth_headers):
    resp = client.post("/metadata/classes", headers=auth_headers, json={
        "name": "Invoice",
        "description": "Financial invoice documents",
        "fields": [
            {"key": "vendor", "label": "Vendor", "type": "text", "required": True, "options": []},
            {"key": "amount", "label": "Amount", "type": "number", "required": False, "options": []},
        ],
    })
    assert resp.status_code == 201, resp.text
    cls = resp.json()
    assert cls["name"] == "Invoice"
    assert len(cls["fields"]) == 2
    cid = cls["id"]

    # List
    resp2 = client.get("/metadata/classes", headers=auth_headers)
    assert resp2.status_code == 200
    assert any(c["id"] == cid for c in resp2.json())

    # Delete
    resp3 = client.delete(f"/metadata/classes/{cid}", headers=auth_headers)
    assert resp3.status_code == 204


def test_set_and_get_resource_metadata(client: TestClient, conn_headers, uploaded_file):
    fid = uploaded_file["id"]

    # Create a class first
    cls_resp = client.post("/metadata/classes", headers=conn_headers, json={
        "name": "Contract",
        "description": None,
        "fields": [{"key": "party", "label": "Party", "type": "text", "required": False, "options": []}],
    })
    cls_id = cls_resp.json()["id"]

    # Set metadata on the file
    resp = client.put(f"/metadata/resource/{fid}", headers=conn_headers, json={
        "resource_type": "file",
        "class_id": cls_id,
        "values": {"party": "Acme Corp"},
    })
    assert resp.status_code == 200, resp.text
    m = resp.json()
    assert m["class_id"] == cls_id
    assert m["values"]["party"] == "Acme Corp"

    # Get it back
    resp2 = client.get(f"/metadata/resource/{fid}", headers=conn_headers)
    assert resp2.status_code == 200
    assert resp2.json()["values"]["party"] == "Acme Corp"

    # Cleanup class
    client.delete(f"/metadata/classes/{cls_id}", headers=conn_headers)


def test_viewer_cannot_manage_document_classes(client: TestClient, auth_headers):
    # Document classes define the schema for the whole deployment's
    # metadata — a read-only viewer must not be able to create, edit, or
    # delete them (this was a real gap: the router defined an _admin
    # dependency but never actually applied it to these three routes).
    client.post("/users", headers=auth_headers, json={
        "username": "viewer_metadata_test", "password": "viewpass123",
        "display_name": "Viewer", "roles": ["viewer"],
    })
    login = client.post("/auth/login", json={"username": "viewer_metadata_test", "password": "viewpass123"})
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    create = client.post("/metadata/classes", headers=viewer_headers, json={
        "name": "ViewerAttempt", "description": None, "fields": [],
    })
    assert create.status_code == 403

    # A real class to try (and fail) to update/delete as the viewer.
    real = client.post("/metadata/classes", headers=auth_headers, json={
        "name": "RealClass", "description": None, "fields": [],
    }).json()
    try:
        update = client.patch(f"/metadata/classes/{real['id']}", headers=viewer_headers, json={"name": "Hacked"})
        assert update.status_code == 403
        delete = client.delete(f"/metadata/classes/{real['id']}", headers=viewer_headers)
        assert delete.status_code == 403
    finally:
        client.delete(f"/metadata/classes/{real['id']}", headers=auth_headers)
        users = client.get("/users", headers=auth_headers).json()
        for u in users:
            if u["username"] == "viewer_metadata_test":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_metadata_cleaned_on_permanent_delete(client: TestClient, conn_headers, uploaded_file):
    fid = uploaded_file["id"]

    # Set some metadata
    client.put(f"/metadata/resource/{fid}", headers=conn_headers, json={
        "resource_type": "file",
        "class_id": None,
        "values": {"note": "will be deleted"},
    })

    # Trash then permanently delete
    client.delete(f"/files/{fid}", headers=conn_headers)
    client.delete(f"/files/{fid}/permanent", headers=conn_headers)

    # Metadata endpoint should return null / 404
    resp = client.get(f"/metadata/resource/{fid}", headers=conn_headers)
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert resp.json() is None
