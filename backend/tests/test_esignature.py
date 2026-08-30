"""Router-level tests for /files/{id}/esignature and /esignature/*.
esignature_service's DocuSign HTTP calls are mocked here — this exercises
C-ECM's own request handling, storage, role-gating, and the
webhook-driven "attach signed document as a new version" flow.
"""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _configured_docusign(client):
    # Depends on `client` so the app's lifespan (settings_store.init_db())
    # has already run before this touches the settings table.
    from app import settings_store

    settings_store.set_setting("docusign_integration_key", "key")
    settings_store.set_setting("docusign_user_id", "user")
    settings_store.set_setting("docusign_account_id", "acct")
    settings_store.set_setting("docusign_private_key", "not-a-real-key-but-is-configured() only checks truthiness")
    yield
    for key in ["docusign_integration_key", "docusign_user_id", "docusign_account_id", "docusign_private_key", "docusign_webhook_hmac_key"]:
        settings_store_module_reset(key)


def settings_store_module_reset(key):
    from app import settings_store

    settings_store.set_setting(key, "")


def test_send_for_signature_not_configured_returns_503(client, conn_headers, uploaded_file):
    for key in ["docusign_integration_key", "docusign_user_id", "docusign_account_id", "docusign_private_key"]:
        settings_store_module_reset(key)
    resp = client.post(f"/files/{uploaded_file['id']}/esignature", headers=conn_headers, json={
        "signers": [{"name": "Alice", "email": "alice@example.com"}], "subject": "Sign please",
    })
    assert resp.status_code == 503


@patch("app.esignature_service.create_envelope", return_value="env-abc")
def test_send_for_signature_creates_request_and_activity_event(mock_create, client, auth_headers, conn_headers, uploaded_file):
    resp = client.post(f"/files/{uploaded_file['id']}/esignature", headers=conn_headers, json={
        "signers": [{"name": "Alice", "email": "alice@example.com", "routing_order": 1}],
        "subject": "Please sign this", "message": "Thanks",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["envelope_id"] == "env-abc"
    assert body["status"] == "sent"
    assert body["resource_id"] == uploaded_file["id"]
    assert body["signers"][0]["email"] == "alice@example.com"

    mock_create.assert_called_once()
    call_kwargs_or_args = mock_create.call_args
    assert call_kwargs_or_args[0][1] == "hello.txt"  # document_name, forwarded from the real file's own name

    events = client.get("/activity", headers=auth_headers, params={"connection_id": conn_headers["X-Connection-Id"], "event_type": "sent_for_signature"}).json()
    assert any(e["resource_id"] == uploaded_file["id"] for e in events)


@patch("app.esignature_service.create_envelope", return_value="env-abc")
def test_viewer_cannot_send_for_signature(mock_create, client, auth_headers, conn_headers, uploaded_file):
    client.post("/users", headers=auth_headers, json={
        "username": "esig_viewer", "password": "viewpass123", "display_name": "Viewer", "roles": ["viewer"],
    })
    login = client.post("/auth/login", json={"username": "esig_viewer", "password": "viewpass123"})
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Connection-Id": conn_headers["X-Connection-Id"]}
    try:
        resp = client.post(f"/files/{uploaded_file['id']}/esignature", headers=viewer_headers, json={
            "signers": [{"name": "Alice", "email": "alice@example.com"}], "subject": "Sign please",
        })
        assert resp.status_code == 403
        mock_create.assert_not_called()
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "esig_viewer":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


@patch("app.esignature_service.create_envelope", return_value="env-list-test")
def test_list_requests_for_file(mock_create, client, conn_headers, uploaded_file):
    client.post(f"/files/{uploaded_file['id']}/esignature", headers=conn_headers, json={
        "signers": [{"name": "Alice", "email": "alice@example.com"}], "subject": "Sign please",
    })
    resp = client.get(f"/files/{uploaded_file['id']}/esignature", headers=conn_headers)
    assert resp.status_code == 200
    assert any(r["envelope_id"] == "env-list-test" for r in resp.json())


@patch("app.esignature_service.get_envelope_status", return_value={"status": "delivered"})
@patch("app.esignature_service.create_envelope", return_value="env-refresh-test")
def test_get_request_refreshes_status_live(mock_create, mock_status, client, conn_headers, uploaded_file):
    created = client.post(f"/files/{uploaded_file['id']}/esignature", headers=conn_headers, json={
        "signers": [{"name": "Alice", "email": "alice@example.com"}], "subject": "Sign please",
    }).json()
    assert created["status"] == "sent"

    resp = client.get(f"/esignature/requests/{created['id']}", headers=conn_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "delivered"
    mock_status.assert_called_once()


@patch("app.esignature_service.void_envelope")
@patch("app.esignature_service.create_envelope", return_value="env-void-test")
def test_requester_can_void_their_own_request(mock_create, mock_void, client, conn_headers, uploaded_file):
    created = client.post(f"/files/{uploaded_file['id']}/esignature", headers=conn_headers, json={
        "signers": [{"name": "Alice", "email": "alice@example.com"}], "subject": "Sign please",
    }).json()
    resp = client.post(f"/esignature/requests/{created['id']}/void", headers=conn_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "voided"
    mock_void.assert_called_once()


@patch("app.esignature_service.create_envelope", return_value="env-void-denied-test")
def test_non_requester_non_admin_cannot_void(mock_create, client, auth_headers, conn_headers, uploaded_file):
    created = client.post(f"/files/{uploaded_file['id']}/esignature", headers=conn_headers, json={
        "signers": [{"name": "Alice", "email": "alice@example.com"}], "subject": "Sign please",
    }).json()

    client.post("/users", headers=auth_headers, json={
        "username": "esig_other_editor", "password": "editpass123", "display_name": "Editor", "roles": ["editor"],
    })
    login = client.post("/auth/login", json={"username": "esig_other_editor", "password": "editpass123"})
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Connection-Id": conn_headers["X-Connection-Id"]}
    try:
        resp = client.post(f"/esignature/requests/{created['id']}/void", headers=other_headers)
        assert resp.status_code == 403
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "esig_other_editor":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


@patch("app.esignature_service.download_signed_document", return_value=b"%PDF signed content")
@patch("app.esignature_service.create_envelope", return_value="env-webhook-test")
def test_webhook_completion_attaches_signed_document_as_new_version(mock_create, mock_download, client, auth_headers, conn_headers, uploaded_file):
    created = client.post(f"/files/{uploaded_file['id']}/esignature", headers=conn_headers, json={
        "signers": [{"name": "Alice", "email": "alice@example.com"}], "subject": "Sign please",
    }).json()

    # No HMAC key configured -> verification is skipped (matches
    # DocuSign's own unverified-Connect-config behavior).
    resp = client.post("/esignature/webhook", json={
        "data": {"envelopeId": "env-webhook-test", "envelopeSummary": {"status": "completed"}},
    })
    assert resp.status_code == 200
    mock_download.assert_called_once()

    updated = client.get(f"/esignature/requests/{created['id']}", headers=conn_headers, params={"refresh": False}).json()
    assert updated["status"] == "completed"
    assert updated["completed_at"] is not None
    assert updated["signed_version_number"] is not None

    versions = client.get(f"/files/{uploaded_file['id']}/versions", headers=conn_headers).json()
    assert len(versions) >= 2  # original + the signed version attached by the webhook


def test_webhook_rejects_bad_signature_when_hmac_key_configured(client):
    from app import settings_store

    settings_store.set_setting("docusign_webhook_hmac_key", "real-key")
    try:
        resp = client.post(
            "/esignature/webhook",
            json={"data": {"envelopeId": "whatever", "envelopeSummary": {"status": "completed"}}},
            headers={"X-DocuSign-Signature-1": "wrong"},
        )
        assert resp.status_code == 401
    finally:
        settings_store.set_setting("docusign_webhook_hmac_key", "")


def test_webhook_for_unknown_envelope_is_a_harmless_noop(client):
    resp = client.post("/esignature/webhook", json={
        "data": {"envelopeId": "no-such-envelope", "envelopeSummary": {"status": "completed"}},
    })
    assert resp.status_code == 200


@patch("app.esignature_service.create_envelope", return_value="env-cascade-test")
def test_esignature_requests_cleaned_up_when_connection_deleted(mock_create, client, auth_headers):
    conn = client.post(
        "/connections", headers=auth_headers,
        json={"provider_key": "local", "display_name": "esig-cascade-test", "username": "", "password": "", "config": {}},
    ).json()
    headers = {**auth_headers, "X-Connection-Id": conn["id"]}
    f = client.post("/files", headers=headers, files={"upload": ("a.txt", b"x", "text/plain")}).json()
    client.post(f"/files/{f['id']}/esignature", headers=headers, json={
        "signers": [{"name": "Alice", "email": "alice@example.com"}], "subject": "Sign please",
    })

    assert client.delete(f"/connections/{conn['id']}", headers=auth_headers).status_code == 204

    from app import esignature_store

    assert esignature_store.list_for_resource(conn["id"], f["id"]) == []
