"""Tests for webhook_service and /webhooks router."""
import pytest
from fastapi.testclient import TestClient


def _scope(local_connection, uploaded_file) -> dict:
    """WebhookCreateRequest requires a connection_id/resource_id/
    resource_type/resource_name — a webhook is scoped-by-default (see
    webhook_service.py's own docstring on why an unscoped webhook is
    deliberately not creatable directly through this endpoint)."""
    return {
        "connection_id": local_connection["id"],
        "resource_id": uploaded_file["id"],
        "resource_type": "file",
        "resource_name": uploaded_file["name"],
    }


def test_create_list_delete_webhook(client: TestClient, auth_headers, local_connection, uploaded_file):
    resp = client.post("/webhooks", headers=auth_headers, json={
        "url": "https://example.com/hook",
        "secret": "mysecret1",
        "event_types": ["created", "deleted"],
        **_scope(local_connection, uploaded_file),
    })
    assert resp.status_code == 201, resp.text
    wh = resp.json()
    assert wh["url"] == "https://example.com/hook"
    assert wh["secret_set"] is True
    assert wh["active"] is True
    assert set(wh["event_types"]) == {"created", "deleted"}
    wid = wh["id"]

    # List
    resp2 = client.get("/webhooks", headers=auth_headers)
    assert resp2.status_code == 200
    assert any(w["id"] == wid for w in resp2.json())

    # Toggle active
    resp3 = client.patch(f"/webhooks/{wid}", headers=auth_headers, json={"active": False})
    assert resp3.status_code == 200
    assert resp3.json()["active"] is False

    # Delete
    resp4 = client.delete(f"/webhooks/{wid}", headers=auth_headers)
    assert resp4.status_code == 204

    # Confirm gone
    resp5 = client.get("/webhooks", headers=auth_headers)
    assert not any(w["id"] == wid for w in resp5.json())


def test_webhook_requires_admin(client: TestClient, auth_headers, local_connection, uploaded_file):
    client.post("/users", headers=auth_headers, json={
        "username": "webhook_viewer",
        "password": "viewpass123",
        "display_name": "Viewer",
        "is_superadmin": False,
    })
    login = client.post("/auth/login", json={"username": "webhook_viewer", "password": "viewpass123"})
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.post("/webhooks", headers=viewer_headers, json={
        "url": "https://example.com/hook",
        "secret": "somesecret",
        "event_types": [],
        **_scope(local_connection, uploaded_file),
    })
    assert resp.status_code == 403

    # Cleanup
    users = client.get("/users", headers=auth_headers).json()
    for u in users:
        if u["username"] == "webhook_viewer":
            client.delete(f"/users/{u['id']}", headers=auth_headers)


@pytest.mark.parametrize("bad_url", [
    "http://127.0.0.1/hook",
    "http://localhost:8020/hook",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
    "http://10.0.0.5/hook",
    "http://192.168.1.1/hook",
    "ftp://example.com/hook",  # non-http(s) scheme
])
def test_webhook_ssrf_targets_are_rejected(client: TestClient, auth_headers, local_connection, uploaded_file, bad_url):
    resp = client.post("/webhooks", headers=auth_headers, json={
        "url": bad_url, "secret": "supersecret", "event_types": [], **_scope(local_connection, uploaded_file),
    })
    assert resp.status_code == 400, f"{bad_url} should have been rejected, got {resp.status_code}: {resp.text}"


def test_webhook_update_also_validates_url(client: TestClient, auth_headers, local_connection, uploaded_file):
    created = client.post("/webhooks", headers=auth_headers, json={
        "url": "https://example.com/hook", "secret": "supersecret", "event_types": [],
        **_scope(local_connection, uploaded_file),
    }).json()
    try:
        resp = client.patch(f"/webhooks/{created['id']}", headers=auth_headers, json={"url": "http://127.0.0.1/evil"})
        assert resp.status_code == 400
    finally:
        client.delete(f"/webhooks/{created['id']}", headers=auth_headers)


def test_webhook_secret_is_mandatory(client: TestClient, auth_headers, local_connection, uploaded_file):
    """Signing is not optional by design (every delivery is HMAC-signed so
    receivers can verify authenticity) — locking this in as an explicit
    test after the frontend was found advertising the secret field as
    optional ("leave blank for no signature") when the schema has always
    required a string of at least 8 characters. That was a frontend/
    backend contract mismatch, not a backend bug, so the fix was in the
    UI copy — but it's worth a test guarding against the schema quietly
    becoming optional later without a matching UI decision to allow it."""
    scope = _scope(local_connection, uploaded_file)

    resp = client.post("/webhooks", headers=auth_headers, json={
        "url": "https://example.com/hook", "secret": None, "event_types": [], **scope,
    })
    assert resp.status_code == 422

    resp2 = client.post("/webhooks", headers=auth_headers, json={
        "url": "https://example.com/hook", "event_types": [], **scope,
    })
    assert resp2.status_code == 422

    resp3 = client.post("/webhooks", headers=auth_headers, json={
        "url": "https://example.com/hook", "secret": "short", "event_types": [], **scope,
    })
    assert resp3.status_code == 422


def test_hmac_signing():
    """Unit test that the signing function produces a valid HMAC-SHA256."""
    import hashlib
    import hmac as hmac_mod
    from app.webhook_service import _sign

    secret = "test-secret"
    payload = b'{"event_type": "created"}'
    sig = _sign(secret, payload)
    assert sig.startswith("sha256=")
    expected = "sha256=" + hmac_mod.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert sig == expected
