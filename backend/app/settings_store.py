"""Admin-level settings: OAuth app credentials (client id/secret) that this
whole C-ECM deployment registers ONE of with Google/Microsoft/Box —
unlike per-connection details (a FileNet server, an Alfresco URL), an OAuth
app is inherently shared by every connection to that provider, the same way
a Slack or Zapier install has one registered Google app that all its users
consent through. Edited via Admin Settings in the UI; persisted here so it
survives restarts without touching .env. Falls back to the .env-configured
default for anyone who set that up already.

This is a generic key/value store shared by every admin-configurable
integration, holding both secrets (client secrets, API keys, the SAML SP
private key, the DocuSign private key) and plain config (a tenant name,
a model id, a backend selector) side by side under one schema. Any key
whose name looks secret-shaped (see _looks_like_secret_key below) is
encrypted at rest via crypto_util.py; everything else is left as plain
text on purpose, since encrypting a non-secret value like watsonx_url
would only make it harder to inspect for no security benefit.
"""

import sqlalchemy as sa

from . import crypto_util, db

# Substring match against the lowercased key name — matches every actual
# secret-shaped setting key in this codebase today (google_client_secret,
# docusign_private_key, docusign_webhook_hmac_key, ibm_cloud_api_key,
# watson_nlu_apikey, ...) without needing an exhaustive, easy-to-forget
# per-key allowlist. A non-secret key never accidentally matches one of
# these (e.g. saml_idp_x509_cert is a public certificate, not a secret,
# and correctly doesn't match any pattern here).
_SECRET_KEY_PATTERNS = ("secret", "apikey", "api_key", "private_key", "hmac_key", "password")

_metadata = sa.MetaData()

# key: a config key name, e.g. "google_client_secret" / "ai_backend" / "saml_sp_private_key" —
# always looked up by exact equality, longest real key today is ~40 chars, so String(64) is a
# safe generous bound. value: NEVER compared/ordered/indexed on -- only ever selected/updated by
# key -- and holds everything from a short model id up to an encrypted secret blob or a PEM
# private key, so it's unbounded free text (Text), not a CLOB-in-an-index footgun.
settings = sa.Table(
    "settings", _metadata,
    sa.Column("key", sa.String(64), primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
)

_engine = db.get_engine("settings")


def _looks_like_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(p in lowered for p in _SECRET_KEY_PATTERNS)


def init_db() -> None:
    db.create_all(_metadata, "settings")

    # One-time migration: encrypt any secret-shaped row still holding
    # plain text from before encryption-at-rest existed.
    # ensure_encrypted is idempotent, so this is safe on every startup.
    with _engine.begin() as conn:
        rows = conn.execute(sa.select(settings)).mappings().all()
        for row in rows:
            if not _looks_like_secret_key(row["key"]):
                continue
            upgraded = crypto_util.ensure_encrypted(row["value"])
            if upgraded != row["value"]:
                conn.execute(settings.update().where(settings.c.key == row["key"]).values(value=upgraded))


def get_setting(key: str, default: str = "") -> str:
    with _engine.connect() as conn:
        row = conn.execute(sa.select(settings.c.value).where(settings.c.key == key)).mappings().first()
    if row is None:
        return default
    return crypto_util.decrypt(row["value"]) if _looks_like_secret_key(key) else row["value"]


def set_setting(key: str, value: str) -> None:
    stored_value = crypto_util.encrypt(value) if _looks_like_secret_key(key) else value
    # Portable upsert: SQLite/Postgres both support "INSERT ... ON CONFLICT DO UPDATE" (what the
    # raw-sqlite3 version used) but Oracle has no such syntax (it needs MERGE INTO instead), and
    # SQLAlchemy Core has no single construct that compiles to all three dialects. Try the UPDATE
    # first; if it matched no row (key not present yet), INSERT it -- identical net effect
    # (existing key's value replaced, new key created), expressed with only portable Core calls.
    with _engine.begin() as conn:
        result = conn.execute(settings.update().where(settings.c.key == key).values(value=stored_value))
        if result.rowcount == 0:
            conn.execute(settings.insert().values(key=key, value=stored_value))


def get_settings(keys: list[str], defaults: dict[str, str]) -> dict[str, str]:
    with _engine.connect() as conn:
        rows = conn.execute(sa.select(settings).where(settings.c.key.in_(keys))).mappings().all()
    stored = {
        r["key"]: (crypto_util.decrypt(r["value"]) if _looks_like_secret_key(r["key"]) else r["value"])
        for r in rows
    }
    return {k: stored.get(k, defaults.get(k, "")) for k in keys}
