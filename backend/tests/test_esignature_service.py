"""Unit tests for esignature_service — the DocuSign HTTP integration
layer. DocuSign's own API is mocked (there's no live account in this
environment); what's under test is C-ECM's side of the contract: JWT
construction, token caching, request/response shape, and webhook HMAC
verification.
"""
import base64
import hashlib
import hmac as hmac_mod
from unittest.mock import MagicMock, patch

import pytest


def _configure_docusign():
    from app import settings_store

    settings_store.set_setting("docusign_integration_key", "test-integration-key")
    settings_store.set_setting("docusign_user_id", "test-user-id")
    settings_store.set_setting("docusign_account_id", "test-account-id")
    settings_store.set_setting("docusign_private_key", _TEST_PRIVATE_KEY)
    settings_store.set_setting("docusign_environment", "demo")


def _clear_docusign():
    from app import settings_store

    for key in ["docusign_integration_key", "docusign_user_id", "docusign_account_id", "docusign_private_key", "docusign_environment", "docusign_webhook_hmac_key"]:
        settings_store.set_setting(key, "")


# A real (but throwaway, generated only for this test file) RSA key —
# needed because pyjwt.encode with RS256 actually validates it's a usable key.
_TEST_PRIVATE_KEY = None


def setup_module(module):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    global _TEST_PRIVATE_KEY
    _TEST_PRIVATE_KEY = pem.decode()


@pytest.fixture(autouse=True)
def _isolate_settings_and_cache():
    import app.esignature_service as es
    from app import settings_store

    settings_store.init_db()  # these are pure service-layer tests — no TestClient/lifespan to do this
    _clear_docusign()
    es._cached_token = None
    yield
    _clear_docusign()
    es._cached_token = None


def test_is_configured_false_until_all_fields_set():
    from app import esignature_service

    assert esignature_service.is_configured() is False
    _configure_docusign()
    assert esignature_service.is_configured() is True


def test_get_access_token_raises_when_not_configured():
    from app import esignature_service

    with pytest.raises(esignature_service.ESignatureError) as exc_info:
        esignature_service._get_access_token()
    assert exc_info.value.status_code == 503


@patch("app.esignature_service.requests.get")
@patch("app.esignature_service.requests.post")
def test_get_access_token_success_and_caching(mock_post, mock_get):
    from app import esignature_service

    _configure_docusign()
    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"access_token": "tok123", "expires_in": 3600})
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"accounts": [{"account_id": "test-account-id", "base_uri": "https://demo.docusign.net"}]},
    )
    mock_get.return_value.raise_for_status = lambda: None

    token, base_uri = esignature_service._get_access_token()
    assert token == "tok123"
    assert base_uri == "https://demo.docusign.net"
    assert mock_post.call_count == 1

    # Second call within the cache window must not hit the network again.
    token2, base_uri2 = esignature_service._get_access_token()
    assert token2 == "tok123"
    assert mock_post.call_count == 1  # still 1 — cached


@patch("app.esignature_service.requests.get")
@patch("app.esignature_service.requests.post")
def test_get_access_token_unknown_account_id_raises(mock_post, mock_get):
    from app import esignature_service

    _configure_docusign()
    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"access_token": "tok123", "expires_in": 3600})
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"accounts": [{"account_id": "some-other-account", "base_uri": "https://demo.docusign.net"}]})
    mock_get.return_value.raise_for_status = lambda: None

    with pytest.raises(esignature_service.ESignatureError):
        esignature_service._get_access_token()


@patch("app.esignature_service.requests.post")
def test_get_access_token_auth_failure_raises_with_status(mock_post):
    from app import esignature_service

    _configure_docusign()
    mock_post.return_value = MagicMock(status_code=400, text="invalid_grant")
    with pytest.raises(esignature_service.ESignatureError):
        esignature_service._get_access_token()


@patch("app.esignature_service._get_access_token", return_value=("tok123", "https://demo.docusign.net"))
@patch("app.esignature_service.requests.post")
def test_create_envelope_builds_expected_payload(mock_post, _mock_token):
    from app import esignature_service

    _configure_docusign()
    mock_post.return_value = MagicMock(status_code=201, json=lambda: {"envelopeId": "env-123"})

    envelope_id = esignature_service.create_envelope(
        document_bytes=b"hello world",
        document_name="contract.pdf",
        signers=[{"name": "Alice", "email": "alice@example.com", "routing_order": 1}, {"name": "Bob", "email": "bob@example.com", "routing_order": 2}],
        subject="Please sign",
        message="Thanks!",
    )
    assert envelope_id == "env-123"

    call_args = mock_post.call_args
    assert "https://demo.docusign.net/restapi/v2.1/accounts/test-account-id/envelopes" in call_args[0][0]
    body = call_args[1]["json"]
    assert body["emailSubject"] == "Please sign"
    assert body["status"] == "sent"
    assert len(body["recipients"]["signers"]) == 2
    assert body["recipients"]["signers"][0]["email"] == "alice@example.com"
    assert body["recipients"]["signers"][1]["routingOrder"] == "2"
    # The document must be sent as base64, and round-trip correctly.
    decoded = base64.b64decode(body["documents"][0]["documentBase64"])
    assert decoded == b"hello world"


@patch("app.esignature_service._get_access_token", return_value=("tok123", "https://demo.docusign.net"))
@patch("app.esignature_service.requests.post")
def test_create_envelope_error_response_raises(mock_post, _mock_token):
    from app import esignature_service

    _configure_docusign()
    mock_post.return_value = MagicMock(status_code=400, text="Bad recipient email")
    with pytest.raises(esignature_service.ESignatureError):
        esignature_service.create_envelope(b"x", "a.pdf", [{"name": "A", "email": "bad"}], "Subject", None)


@patch("app.esignature_service._get_access_token", return_value=("tok123", "https://demo.docusign.net"))
@patch("app.esignature_service.requests.get")
def test_get_envelope_status(mock_get, _mock_token):
    from app import esignature_service

    _configure_docusign()
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"status": "delivered"})
    result = esignature_service.get_envelope_status("env-123")
    assert result["status"] == "delivered"


@patch("app.esignature_service._get_access_token", return_value=("tok123", "https://demo.docusign.net"))
@patch("app.esignature_service.requests.get")
def test_download_signed_document(mock_get, _mock_token):
    from app import esignature_service

    _configure_docusign()
    mock_get.return_value = MagicMock(status_code=200, content=b"%PDF-1.4 signed content")
    result = esignature_service.download_signed_document("env-123")
    assert result == b"%PDF-1.4 signed content"


@patch("app.esignature_service._get_access_token", return_value=("tok123", "https://demo.docusign.net"))
@patch("app.esignature_service.requests.put")
def test_void_envelope(mock_put, _mock_token):
    from app import esignature_service

    _configure_docusign()
    mock_put.return_value = MagicMock(status_code=200)
    esignature_service.void_envelope("env-123", "No longer needed")
    body = mock_put.call_args[1]["json"]
    assert body["status"] == "voided"
    assert body["voidedReason"] == "No longer needed"


def test_webhook_signature_skipped_when_no_key_configured():
    from app import esignature_service

    assert esignature_service.verify_webhook_signature(b"any body", None) is True
    assert esignature_service.verify_webhook_signature(b"any body", "garbage") is True


def test_webhook_signature_verified_when_key_configured():
    from app import esignature_service, settings_store

    settings_store.set_setting("docusign_webhook_hmac_key", "my-hmac-key")
    body = b'{"event": "envelope-completed"}'
    correct_sig = base64.b64encode(hmac_mod.new(b"my-hmac-key", body, hashlib.sha256).digest()).decode()

    assert esignature_service.verify_webhook_signature(body, correct_sig) is True
    assert esignature_service.verify_webhook_signature(body, "wrong-signature") is False
    assert esignature_service.verify_webhook_signature(body, None) is False
    assert esignature_service.verify_webhook_signature(b"tampered body", correct_sig) is False
