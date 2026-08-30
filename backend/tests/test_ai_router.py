"""Router-level tests for app/routers/ai.py — GET /ai/status and the
POST /files/{id}/ai/suggest_workflow confidence field. Neither had any
router-level test coverage before this file (test_ai_service.py and
test_ai_watson.py both call ai_service functions directly).
"""
from unittest.mock import patch

from app import ai_service


def test_ai_status_requires_auth(client):
    resp = client.get("/ai/status")
    assert resp.status_code == 403


def test_ai_status_disabled_by_default(client, auth_headers):
    resp = client.get("/ai/status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"enabled": False, "backend": "none"}


def test_ai_status_reflects_admin_configured_backend(client, auth_headers):
    try:
        client.put("/admin/settings", headers=auth_headers, json={"ai_backend": "watsonx"})
        resp = client.get("/ai/status", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"enabled": True, "backend": "watsonx"}
    finally:
        client.put("/admin/settings", headers=auth_headers, json={"ai_backend": "none"})


def test_ai_status_visible_to_a_viewer_not_just_admin(client, auth_headers):
    # Deliberately NOT admin-gated -- any authenticated user needs this to
    # know whether to show AI features at all, unlike /admin/settings.
    client.post("/users", headers=auth_headers, json={
        "username": "ai_status_viewer", "password": "viewpass123", "display_name": "Viewer", "roles": ["viewer"],
    })
    login = client.post("/auth/login", json={"username": "ai_status_viewer", "password": "viewpass123"})
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    try:
        resp = client.get("/ai/status", headers=viewer_headers)
        assert resp.status_code == 200
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "ai_status_viewer":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def _create_definition(client, auth_headers, conn_headers, name):
    return client.post("/workflows/definitions", headers=conn_headers, json={
        "name": name, "description": None,
        "steps": [{"name": "Review", "reviewers": [], "required_approvals": 1}],
    }).json()


# suggest_workflow's keyword matcher looks for one of its raw keywords (e.g.
# "invoice") as a substring of the workflow's own NAME -- not just the
# document text -- so the definition name below must contain one, unlike a
# plain "Finance Approval <uuid>" which wouldn't match anything (the exact
# unsuffixed "Finance Approval" fallback label can't be used here either
# since definition names must be unique across the whole test session).
_INVOICE_WORKFLOW_NAME = "Invoice Approval"


def test_suggest_workflow_no_definitions_returns_null(client, auth_headers, conn_headers, uploaded_file):
    resp = client.post(f"/files/{uploaded_file['id']}/ai/suggest_workflow", headers=conn_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["suggested_workflow_id"] is None
    assert body["suggested_workflow_name"] is None


def test_suggest_workflow_confidence_is_medium_for_keyword_match(client, auth_headers, conn_headers, uploaded_file):
    defn = _create_definition(client, auth_headers, conn_headers, f"{_INVOICE_WORKFLOW_NAME} {uploaded_file['id']}")
    try:
        with patch.object(ai_service, "extract_text", return_value="Invoice #1234 total $5000 payment due."):
            resp = client.post(f"/files/{uploaded_file['id']}/ai/suggest_workflow", headers=conn_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["suggested_workflow_id"] == defn["id"]
        assert body["confidence"] == "medium"
    finally:
        client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)


def test_suggest_workflow_confidence_is_high_only_when_nlu_actually_matched(
    client, auth_headers, conn_headers, uploaded_file
):
    """Regression test for the confidence bug: NLU configured but returning
    no confident label must NOT report "high" just because credentials are
    present -- the keyword fallback found it, so it's "medium"."""
    defn = _create_definition(client, auth_headers, conn_headers, f"{_INVOICE_WORKFLOW_NAME} {uploaded_file['id']}")
    try:
        with patch.object(ai_service, "extract_text", return_value="Invoice #1234 total $5000 payment due."), \
             patch.object(ai_service, "_WATSON_NLU_URL", "https://nlu.example.com"), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", "nlu-key"), \
             patch.object(ai_service, "_call_watson_nlu_classify", return_value=None):
            resp = client.post(f"/files/{uploaded_file['id']}/ai/suggest_workflow", headers=conn_headers)
        assert resp.status_code == 200
        assert resp.json()["confidence"] == "medium"

        with patch.object(ai_service, "extract_text", return_value="Invoice #1234 total $5000 payment due."), \
             patch.object(ai_service, "_WATSON_NLU_URL", "https://nlu.example.com"), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", "nlu-key"), \
             patch.object(ai_service, "_call_watson_nlu_classify", return_value=defn["name"]):
            resp2 = client.post(f"/files/{uploaded_file['id']}/ai/suggest_workflow", headers=conn_headers)
        assert resp2.status_code == 200
        assert resp2.json()["confidence"] == "high"
    finally:
        client.delete(f"/workflows/definitions/{defn['id']}", headers=auth_headers)
