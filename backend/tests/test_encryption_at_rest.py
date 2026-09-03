"""Tests for crypto_util.py and its use in connections_store.py /
settings_store.py — connection credentials and admin-configured secrets
must never be recoverable by just reading the SQLite file directly."""
import sqlite3

from app import crypto_util
from app.config import DATA_DIR


def test_encrypt_decrypt_round_trip():
    plaintext = "super-secret-value"
    encrypted = crypto_util.encrypt(plaintext)
    assert encrypted != plaintext
    assert crypto_util.decrypt(encrypted) == plaintext


def test_decrypt_passes_through_legacy_plaintext_unchanged():
    # Data written before encryption-at-rest existed must still be
    # readable after upgrading — decrypt() must not raise on it.
    assert crypto_util.decrypt("plain-legacy-value") == "plain-legacy-value"


def test_ensure_encrypted_is_idempotent():
    once = crypto_util.ensure_encrypted("some-plaintext")
    twice = crypto_util.ensure_encrypted(once)
    assert once == twice
    assert crypto_util.decrypt(twice) == "some-plaintext"


def test_connection_credentials_are_encrypted_on_disk(client, auth_headers):
    conn = client.post(
        "/connections", headers=auth_headers,
        json={"provider_key": "local", "display_name": "encryption-test", "username": "", "password": "", "config": {}},
    ).json()
    try:
        db = sqlite3.connect(str(DATA_DIR / "connections.db"))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT creds_json FROM connections WHERE id = ?", (conn["id"],)).fetchone()
        db.close()
        # The raw stored value must not contain the plaintext config we sent
        # (a real credential-shaped field would leak straight into a
        # filesystem/backup/db-dump otherwise), and must be a valid Fernet
        # token for this server's key.
        assert "local" not in row["creds_json"] or crypto_util.is_encrypted(row["creds_json"])
        assert crypto_util.is_encrypted(row["creds_json"])
    finally:
        client.delete(f"/connections/{conn['id']}", headers=auth_headers)


def test_admin_secret_settings_are_encrypted_on_disk_but_non_secrets_stay_plain(client, auth_headers):
    resp = client.put(
        "/admin/settings", headers=auth_headers,
        json={"google_client_id": "plain-client-id-123", "google_client_secret": "super-secret-oauth-value"},
    )
    assert resp.status_code == 200, resp.text
    try:
        db = sqlite3.connect(str(DATA_DIR / "settings.db"))
        db.row_factory = sqlite3.Row
        secret_row = db.execute("SELECT value FROM settings WHERE key = 'google_client_secret'").fetchone()
        id_row = db.execute("SELECT value FROM settings WHERE key = 'google_client_id'").fetchone()
        db.close()

        assert crypto_util.is_encrypted(secret_row["value"])
        assert secret_row["value"] != "super-secret-oauth-value"

        # A non-secret-shaped field (a client ID, not a client secret) is
        # left as plain text — encrypting it would add no security benefit.
        assert id_row["value"] == "plain-client-id-123"

        # And it still round-trips correctly through the API.
        settings = client.get("/admin/settings", headers=auth_headers).json()
        assert settings["google_client_id"] == "plain-client-id-123"
        assert settings["google_client_secret_set"] is True
    finally:
        db = sqlite3.connect(str(DATA_DIR / "settings.db"))
        db.execute("DELETE FROM settings WHERE key IN ('google_client_id', 'google_client_secret')")
        db.commit()
        db.close()
