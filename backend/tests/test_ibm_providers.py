"""Tests for IBM i and IBM Z provider registration and basic structure.

These tests verify that the new providers register correctly, expose the
expected `key` / `display_name` / `config_fields`, and implement the full
StorageProvider interface — without requiring a live IBM i or z/OS system.
"""
import pytest

from app.storage_providers.registry import list_providers, get_provider


# ---------------------------------------------------------------------------
# IBM i provider
# ---------------------------------------------------------------------------

class TestIBMiProviderRegistration:
    def test_ibmi_is_registered(self):
        keys = [p.key for p in list_providers()]
        assert "ibm_i" in keys, "IBM i provider must be registered in the provider registry"

    def test_ibmi_display_name(self):
        p = get_provider("ibm_i")
        assert "IBM i" in p.display_name or "AS/400" in p.display_name or "iSeries" in p.display_name

    def test_ibmi_has_required_config_fields(self):
        p = get_provider("ibm_i")
        field_keys = {f.key for f in p.config_fields}
        assert "hostname" in field_keys, "IBM i provider must require hostname"

    def test_ibmi_optional_fields_present(self):
        p = get_provider("ibm_i")
        field_keys = {f.key for f in p.config_fields}
        # IFS root, DB2 table, CMOD URL are all expected
        assert "ifs_root" in field_keys
        assert "db2_table" in field_keys
        assert "cmod_url" in field_keys

    def test_ibmi_auth_mode_is_credentials(self):
        from app.storage_providers.base import AuthMode
        p = get_provider("ibm_i")
        assert p.auth_mode == AuthMode.CREDENTIALS

    def test_ibmi_implements_all_abstract_methods(self):
        """Instantiation would fail at import time if abstract methods were
        missing, but verify the concrete class has all StorageProvider methods."""
        from app.storage_providers.ibmi_provider import IBMiProvider
        from app.storage_providers.base import StorageProvider
        required = [
            "get_children", "list_trash", "create_folder", "rename_folder",
            "move_folder", "delete_folder", "create_document", "get_file",
            "rename_file", "move_file", "delete_file", "get_content",
            "list_versions", "create_version", "get_version_content",
            "restore_version", "trash_folder", "restore_folder",
            "trash_file", "restore_file", "search",
        ]
        p = IBMiProvider()
        for method in required:
            assert hasattr(p, method), f"IBMiProvider must implement {method}"

    def test_ibmi_error_without_paramiko(self):
        """_require_paramiko() should raise ProviderError when paramiko
        is not installed — not a bare ImportError."""
        from unittest.mock import patch
        from app.storage_providers import ibmi_provider as _ibmi
        from app.storage_providers.base import ProviderError
        # _require_paramiko is called by _sftp(), so patch _HAS_PARAMIKO
        # at the function level where it's read (inside _require_paramiko).
        with patch.object(_ibmi, "_HAS_PARAMIKO", False):
            with pytest.raises(ProviderError) as exc_info:
                _ibmi._require_paramiko()
        assert "paramiko" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# IBM Z provider
# ---------------------------------------------------------------------------

class TestIBMZProviderRegistration:
    def test_ibmz_is_registered(self):
        keys = [p.key for p in list_providers()]
        assert "ibm_z" in keys, "IBM Z provider must be registered in the provider registry"

    def test_ibmz_display_name(self):
        p = get_provider("ibm_z")
        assert "IBM Z" in p.display_name or "z/OS" in p.display_name or "Mainframe" in p.display_name

    def test_ibmz_has_required_config_fields(self):
        p = get_provider("ibm_z")
        field_keys = {f.key for f in p.config_fields}
        assert "zosmf_url" in field_keys, "IBM Z provider must require z/OSMF URL"

    def test_ibmz_optional_fields_present(self):
        p = get_provider("ibm_z")
        field_keys = {f.key for f in p.config_fields}
        assert "uss_root" in field_keys
        assert "cm8_url" in field_keys
        assert "system" in field_keys

    def test_ibmz_auth_mode_is_credentials(self):
        from app.storage_providers.base import AuthMode
        p = get_provider("ibm_z")
        assert p.auth_mode == AuthMode.CREDENTIALS

    def test_ibmz_implements_all_abstract_methods(self):
        from app.storage_providers.ibmz_provider import IBMZProvider
        required = [
            "get_children", "list_trash", "create_folder", "rename_folder",
            "move_folder", "delete_folder", "create_document", "get_file",
            "rename_file", "move_file", "delete_file", "get_content",
            "list_versions", "create_version", "get_version_content",
            "restore_version", "trash_folder", "restore_folder",
            "trash_file", "restore_file", "search",
        ]
        p = IBMZProvider()
        for method in required:
            assert hasattr(p, method), f"IBMZProvider must implement {method}"

    def test_ibmz_use_cm8_returns_false_without_cm8_url(self):
        from app.storage_providers.ibmz_provider import IBMZProvider
        p = IBMZProvider()
        assert p._use_cm8({"cm8_url": ""}) is False
        assert p._use_cm8({"cm8_url": None}) is False  # type: ignore[arg-type]
        assert p._use_cm8({}) is False

    def test_ibmz_use_cm8_returns_true_with_cm8_url(self):
        from app.storage_providers.ibmz_provider import IBMZProvider
        p = IBMZProvider()
        assert p._use_cm8({"cm8_url": "https://mainframe:9080"}) is True

    def test_ibmz_error_without_requests(self):
        """authenticate() should raise ProviderError (not ImportError) when
        requests is not installed."""
        from unittest.mock import patch
        from app.storage_providers.ibmz_provider import IBMZProvider
        from app.storage_providers.base import ProviderError
        p = IBMZProvider()
        with patch("app.storage_providers.ibmz_provider._HAS_REQUESTS", False):
            with pytest.raises(ProviderError) as exc_info:
                p.authenticate("user", "pass", {"zosmf_url": "https://mf:10443"})
        assert "requests" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Provider count
# ---------------------------------------------------------------------------

def test_total_registered_providers():
    """After adding IBM i and IBM Z we should have at least 11 providers."""
    providers = list_providers()
    assert len(providers) >= 11, (
        f"Expected at least 11 registered providers, got {len(providers)}: "
        f"{[p.key for p in providers]}"
    )
