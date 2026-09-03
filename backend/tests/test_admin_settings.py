"""Tests for /admin/settings — in particular the new AI/Watson keys added
alongside the existing Google/MS/Box/DocuSign ones (which had no test
coverage at all before this file).

Ordering note: conftest.py sets FD_DATA_DIR once at module scope, so
settings.db is shared across this ENTIRE pytest session, not reset per
test or even per file — and update_settings() treats a blank/omitted field
as "leave unchanged" (settings_store has no delete op), so a value set
here can never be cleared back to blank afterward. Tests that need a field
to still be unset (ibm_cloud_api_key, watsonx_project_id) are ordered
first, before any other test in this file touches them. Don't reorder
without re-checking this.
"""
from app import ai_service


def test_get_settings_requires_admin(client, auth_headers):
    resp = client.get("/admin/settings", headers=auth_headers)
    assert resp.status_code == 200


def test_get_settings_defaults_to_not_configured(client, auth_headers):
    body = client.get("/admin/settings", headers=auth_headers).json()
    # Fresh test data dir -- nothing saved yet, so these should reflect
    # whatever config.py resolved from the (unset, in tests) environment.
    assert body["ai_backend"] == "none"
    assert body["watsonx_configured"] is False
    assert body["watson_nlu_configured"] is False
    assert body["watson_disco_configured"] is False
    assert body["ibm_cloud_api_key_set"] is False
    assert body["anthropic_configured"] is False
    assert body["anthropic_api_key_set"] is False
    assert body["ai_openai_configured"] is False
    assert body["ai_api_key_set"] is False


def test_watsonx_configured_true_only_once_both_key_and_project_are_set(client, auth_headers):
    client.put("/admin/settings", headers=auth_headers, json={"ibm_cloud_api_key": "k"})
    assert client.get("/admin/settings", headers=auth_headers).json()["watsonx_configured"] is False

    client.put("/admin/settings", headers=auth_headers, json={"watsonx_project_id": "p"})
    assert client.get("/admin/settings", headers=auth_headers).json()["watsonx_configured"] is True


def test_anthropic_and_openai_configured_true_once_key_is_set(client, auth_headers):
    # Unlike watsonx (needs both a key AND a project id), Anthropic and the
    # OpenAI-compatible backend only need their one API key.
    client.put("/admin/settings", headers=auth_headers, json={"anthropic_api_key": "sk-ant-k"})
    assert client.get("/admin/settings", headers=auth_headers).json()["anthropic_configured"] is True

    client.put("/admin/settings", headers=auth_headers, json={"ai_api_key": "sk-oa-k"})
    assert client.get("/admin/settings", headers=auth_headers).json()["ai_openai_configured"] is True


def test_secrets_are_never_echoed_back_only_a_set_flag(client, auth_headers):
    client.put("/admin/settings", headers=auth_headers, json={
        "ibm_cloud_api_key": "super-secret-key",
        "watson_nlu_apikey": "another-secret",
        "watson_disco_apikey": "yet-another-secret",
        "anthropic_api_key": "claude-super-secret",
        "ai_api_key": "openai-super-secret",
    })
    body = client.get("/admin/settings", headers=auth_headers).json()
    assert body["ibm_cloud_api_key_set"] is True
    assert body["watson_nlu_apikey_set"] is True
    assert body["watson_disco_apikey_set"] is True
    assert body["anthropic_api_key_set"] is True
    assert body["ai_api_key_set"] is True
    # None of the raw secret values appear anywhere in the response
    dumped = str(body)
    assert "super-secret-key" not in dumped
    assert "another-secret" not in dumped
    assert "yet-another-secret" not in dumped
    assert "claude-super-secret" not in dumped
    assert "openai-super-secret" not in dumped


def test_non_secret_watson_fields_are_echoed_back_plainly(client, auth_headers):
    client.put("/admin/settings", headers=auth_headers, json={
        "watsonx_project_id": "proj-123",
        "watsonx_url": "https://example.watsonx.test",
        "watsonx_model": "ibm/granite-20b-multilingual",
        "watson_nlu_url": "https://nlu.example.test",
        "watson_disco_url": "https://disco.example.test",
        "watson_disco_project_id": "disco-proj-456",
    })
    body = client.get("/admin/settings", headers=auth_headers).json()
    assert body["watsonx_project_id"] == "proj-123"
    assert body["watsonx_url"] == "https://example.watsonx.test"
    assert body["watsonx_model"] == "ibm/granite-20b-multilingual"
    assert body["watson_nlu_url"] == "https://nlu.example.test"
    assert body["watson_disco_url"] == "https://disco.example.test"
    assert body["watson_disco_project_id"] == "disco-proj-456"


def test_non_secret_anthropic_openai_ollama_fields_are_echoed_back_plainly(client, auth_headers):
    client.put("/admin/settings", headers=auth_headers, json={
        "anthropic_model": "claude-opus-5",
        "ai_base_url": "https://my-proxy.example.test/v1",
        "ai_model": "gpt-4o",
        "ollama_url": "http://gpu-box.internal:11434",
        "ollama_model": "llama3.1",
    })
    body = client.get("/admin/settings", headers=auth_headers).json()
    assert body["anthropic_model"] == "claude-opus-5"
    assert body["ai_base_url"] == "https://my-proxy.example.test/v1"
    assert body["ai_model"] == "gpt-4o"
    assert body["ollama_url"] == "http://gpu-box.internal:11434"
    assert body["ollama_model"] == "llama3.1"


def test_blank_or_omitted_fields_leave_existing_values_unchanged(client, auth_headers):
    client.put("/admin/settings", headers=auth_headers, json={"watsonx_project_id": "keep-me"})
    client.put("/admin/settings", headers=auth_headers, json={"watsonx_url": "https://still-here.test"})
    body = client.get("/admin/settings", headers=auth_headers).json()
    assert body["watsonx_project_id"] == "keep-me"
    assert body["watsonx_url"] == "https://still-here.test"


def test_saving_ai_backend_takes_effect_immediately_without_restart(client, auth_headers):
    # This is the actual gap being closed: previously FD_AI_BACKEND was
    # read once at process start and nothing could change it at runtime.
    try:
        resp = client.put("/admin/settings", headers=auth_headers, json={"ai_backend": "watsonx"})
        assert resp.status_code == 200
        assert resp.json()["ai_backend"] == "watsonx"
        # The live module global (what summarize()/answer_question()/etc.
        # actually branch on) must reflect it right away.
        assert ai_service._BACKEND == "watsonx"
        assert ai_service.is_enabled() is True
    finally:
        client.put("/admin/settings", headers=auth_headers, json={"ai_backend": "none"})


def test_saving_anthropic_backend_takes_effect_immediately_without_restart(client, auth_headers):
    try:
        resp = client.put("/admin/settings", headers=auth_headers, json={"ai_backend": "anthropic"})
        assert resp.status_code == 200
        assert resp.json()["ai_backend"] == "anthropic"
        assert ai_service._BACKEND == "anthropic"
        assert ai_service.is_enabled() is True
    finally:
        client.put("/admin/settings", headers=auth_headers, json={"ai_backend": "none"})


def test_saving_anthropic_and_openai_credentials_takes_effect_on_live_module_globals(client, auth_headers):
    # Before this backend/settings wiring existed, these globals were only
    # ever set once from the environment at import time — this is the gap
    # being closed (mirrors test_saving_watson_credentials_... below). No
    # cleanup for the same reason as that test: settings_store has no
    # delete op and nothing else reads these keys back afterward.
    client.put("/admin/settings", headers=auth_headers, json={
        "anthropic_api_key": "live-refresh-claude-key",
        "anthropic_model": "claude-haiku-4-5",
        "ai_api_key": "live-refresh-openai-key",
        "ai_base_url": "https://live-refresh-proxy.example.test/v1",
        "ai_model": "gpt-4o",
        "ollama_url": "http://live-refresh-ollama.internal:11434",
        "ollama_model": "llama3.1",
    })
    assert ai_service._ANTHROPIC_API_KEY == "live-refresh-claude-key"
    assert ai_service._ANTHROPIC_MODEL == "claude-haiku-4-5"
    assert ai_service._AI_API_KEY == "live-refresh-openai-key"
    assert ai_service._AI_BASE_URL == "https://live-refresh-proxy.example.test/v1"
    assert ai_service._AI_MODEL == "gpt-4o"
    assert ai_service._OLLAMA_URL == "http://live-refresh-ollama.internal:11434"
    assert ai_service._OLLAMA_MODEL == "llama3.1"


def test_saving_watson_credentials_takes_effect_on_live_module_globals(client, auth_headers):
    # No cleanup here: settings_store has no delete operation and
    # update_settings() treats a blank value as "leave unchanged" (that's
    # how a form re-submits without clobbering secrets it can't see back),
    # so there's no way to reset this to empty through the API. Harmless
    # left set for the rest of the session — nothing else calls
    # refresh_from_settings() and reads these two keys back.
    client.put("/admin/settings", headers=auth_headers, json={
        "watson_nlu_url": "https://live-refresh.example.test",
        "watson_nlu_apikey": "live-refresh-key",
    })
    assert ai_service._WATSON_NLU_URL == "https://live-refresh.example.test"
    assert ai_service._WATSON_NLU_APIKEY == "live-refresh-key"


def test_viewer_cannot_read_or_write_settings(client, auth_headers):
    client.post("/users", headers=auth_headers, json={
        "username": "settings_viewer", "password": "viewpass123", "display_name": "Viewer", "roles": ["viewer"],
    })
    login = client.post("/auth/login", json={"username": "settings_viewer", "password": "viewpass123"})
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    try:
        assert client.get("/admin/settings", headers=viewer_headers).status_code == 403
        assert client.put("/admin/settings", headers=viewer_headers, json={"ai_backend": "watsonx"}).status_code == 403
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "settings_viewer":
                client.delete(f"/users/{u['id']}", headers=auth_headers)
