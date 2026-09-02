"""SAML SSO — this app as a Service Provider (SP), via python3-saml
(OneLogin's toolkit; verified to install and import cleanly on this
Windows/Python 3.12 setup, prebuilt `xmlsec` wheel and all — no native
build was needed).

UNVERIFIED end-to-end — there's no real Identity Provider (IdP) in this
environment to complete a live login against, same "structurally correct,
not live-tested" caveat already applied to every OAuth storage provider in
this app. What IS verified: the library installs and imports, and its own
`OneLogin_Saml2_Settings` validation (`check_sp_certs`/settings-shape
checks) runs — see routers/saml.py's `/saml/metadata` endpoint, which
calls into this module for real.

The SP needs its own signing keypair — rather than making the admin
generate and paste one in (the OAuth providers' pattern of "go create an
app in a developer console" doesn't apply here; there's no third-party
console for an SP cert), one is auto-generated on first use and persisted
via settings_store, the same spirit as config.py's auto-generated
JWT_SECRET. The admin only ever has to hand their IdP this app's SP
metadata (GET /saml/metadata) — never touches the private key.
"""

import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.settings import OneLogin_Saml2_Settings

from . import settings_store
from .config import API_BASE_URL


def _generate_sp_keypair() -> tuple[str, str]:
    """Returns (cert_pem, key_pem) for a fresh self-signed SP cert —
    SAML SPs sign their own AuthnRequests with a cert the IdP doesn't need
    to independently trust via a real CA chain (the IdP trusts it because
    the admin uploads/pastes it via the IdP's own SAML app config, the same
    way they'd paste this app's metadata) — self-signed is the normal,
    expected shape for an SP cert, not a shortcut."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "c-ecm-saml-sp")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def _sp_keypair() -> tuple[str, str]:
    cert_pem = settings_store.get_setting("saml_sp_x509_cert", "")
    key_pem = settings_store.get_setting("saml_sp_private_key", "")
    if not cert_pem or not key_pem:
        cert_pem, key_pem = _generate_sp_keypair()
        settings_store.set_setting("saml_sp_x509_cert", cert_pem)
        settings_store.set_setting("saml_sp_private_key", key_pem)
    return cert_pem, key_pem


def is_enabled() -> bool:
    return settings_store.get_setting("saml_enabled", "") == "1"


def is_configured() -> bool:
    return bool(
        settings_store.get_setting("saml_idp_entity_id", "")
        and settings_store.get_setting("saml_idp_sso_url", "")
        and settings_store.get_setting("saml_idp_x509_cert", "")
    )


def _acs_url() -> str:
    return f"{API_BASE_URL}/saml/acs"


def build_settings_dict() -> dict:
    cert_pem, key_pem = _sp_keypair()
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": API_BASE_URL,
            "assertionConsumerService": {
                "url": _acs_url(),
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            "x509cert": cert_pem,
            "privateKey": key_pem,
        },
        "idp": {
            "entityId": settings_store.get_setting("saml_idp_entity_id", ""),
            "singleSignOnService": {
                "url": settings_store.get_setting("saml_idp_sso_url", ""),
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": settings_store.get_setting("saml_idp_x509_cert", ""),
        },
    }


def sp_metadata_xml() -> str:
    settings = OneLogin_Saml2_Settings(settings=build_settings_dict(), sp_validation_only=True)
    metadata = settings.get_sp_metadata()
    errors = settings.validate_metadata(metadata)
    if errors:
        raise ValueError(f"Invalid SP metadata: {', '.join(errors)}")
    return metadata


def build_auth(request_data: dict) -> OneLogin_Saml2_Auth:
    return OneLogin_Saml2_Auth(request_data, build_settings_dict())


def fastapi_request_to_saml_request_data(
    scheme: str, host: str, path: str, query_string: str, get_params: dict, post_params: dict,
) -> dict:
    """python3-saml wants a plain dict shaped like this (its documented
    request-data contract), not a framework-specific Request object —
    build it from whatever ASGI framework is calling in."""
    return {
        "https": "on" if scheme == "https" else "off",
        "http_host": host,
        "script_name": path,
        "server_port": "443" if scheme == "https" else "80",
        "get_data": get_params,
        "post_data": post_params,
        "query_string": query_string,
    }
