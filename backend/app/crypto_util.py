"""Encryption at rest for stored secrets — connection credentials
(connections_store.py) and admin-configured secret values (settings_store.py:
OAuth client secrets, the SAML SP private key, the DocuSign private key,
IBM Watson/watsonx API keys, and similar). Everything else in those two
stores (display names, provider keys, non-secret settings like a tenant
name or a model id) is left as plain text — encrypting values nothing
reads as sensitive would just make them harder to inspect/debug for no
security benefit.

Key management mirrors config.py's JWT secret: FD_SECRETS_KEY overrides it
for a real deployment that wants to manage the key externally (e.g. inject
it from a secrets manager); otherwise a key is generated once and persisted
to a file next to the JWT secret. Losing this file makes every previously
stored connection/secret unrecoverable — same operational tradeoff this
app already accepts for the JWT secret, not a new one introduced here.

decrypt() is deliberately backward-compatible with data written before
this module existed: a value that isn't a valid Fernet token for this
key is returned unchanged rather than raising, so an upgrade doesn't
break on an untouched pre-encryption row. ensure_encrypted() is the
one-time migration helper each store's init_db() calls to re-write any
such legacy plaintext row through encrypt() exactly once.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

from .config import DATA_DIR

_KEY_PATH = DATA_DIR / ".secrets_key"


def _get_or_create_key() -> bytes:
    env_key = os.environ.get("FD_SECRETS_KEY")
    if env_key:
        return env_key.encode()
    if _KEY_PATH.exists():
        return _KEY_PATH.read_text(encoding="utf-8").strip().encode()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    _KEY_PATH.write_text(key.decode(), encoding="utf-8")
    return key


_fernet = Fernet(_get_or_create_key())


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    """Decrypts a Fernet token produced by encrypt(). If `value` isn't one —
    plain text written before encryption-at-rest existed, or already
    invalid/corrupt — it's returned unchanged rather than raising, so a
    read never hard-fails on legacy data."""
    try:
        return _fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        return value


def is_encrypted(value: str) -> bool:
    try:
        _fernet.decrypt(value.encode("utf-8"))
        return True
    except (InvalidToken, ValueError, UnicodeDecodeError):
        return False


def ensure_encrypted(value: str) -> str:
    """Idempotent: encrypts `value` if it isn't already a valid Fernet
    token for this server's key, otherwise returns it unchanged. Used by
    each store's one-time startup migration."""
    return value if is_encrypted(value) else encrypt(value)
