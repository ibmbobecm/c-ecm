"""Tests for the IBM Z (z/OS) provider — dataset browsing (new) and the
CMOD endpoint correction (previously used a fabricated /IBMcmRecordsView
path; now /cmod-rest/v1, confirmed against IBM's own CMOD REST Services
material). All z/OSMF/CMOD HTTP calls are mocked — no live mainframe
needed, matching how test_ai_watson.py mocks Watson's HTTP calls instead
of requiring live IBM Cloud credentials.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.storage_providers.base import ProviderError
from app.storage_providers.ibmz_provider import IBMZProvider


def _resp(json_data=None, status_code=200, content=b""):
    m = MagicMock()
    m.status_code = status_code
    m.content = content or (b"{}" if json_data is not None else b"")
    m.json.return_value = json_data if json_data is not None else {}
    m.text = str(json_data)
    return m


def _creds(**overrides):
    base = {
        "username": "user1", "password": "pw", "zosmf_url": "https://mainframe:10443",
        "system": "SYSA", "uss_root": "/u", "dataset_hlq": "", "cm8_url": "",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def provider():
    return IBMZProvider()


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------

def test_authenticate_success(provider):
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp({"zosmf_version": "1"})):
        creds = provider.authenticate("user1", "pw", {"zosmf_url": "https://mainframe:10443"})
    assert creds is not None
    assert creds["username"] == "user1"
    assert creds["dataset_hlq"] == ""


def test_authenticate_uppercases_dataset_hlq(provider):
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp({})):
        creds = provider.authenticate("user1", "pw", {"zosmf_url": "https://mainframe:10443", "dataset_hlq": "user1"})
    assert creds["dataset_hlq"] == "USER1"


def test_authenticate_failure_returns_none(provider):
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp(status_code=401)):
        creds = provider.authenticate("user1", "wrong", {"zosmf_url": "https://mainframe:10443"})
    assert creds is None


def test_authenticate_requires_zosmf_url(provider):
    with pytest.raises(ProviderError):
        provider.authenticate("user1", "pw", {})


# ---------------------------------------------------------------------------
# USS root + Datasets pseudo-folder injection
# ---------------------------------------------------------------------------

def test_root_listing_has_no_datasets_folder_when_hlq_not_configured(provider):
    creds = _creds(dataset_hlq="")
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp({"items": []})):
        result = provider.get_children(creds, None)
    assert all(f.id != "__datasets__" for f in result.folders)


def test_root_listing_includes_datasets_folder_when_hlq_configured(provider):
    creds = _creds(dataset_hlq="USER1")
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp({"items": []})):
        result = provider.get_children(creds, None)
    assert any(f.id == "__datasets__" and f.name == "z/OS Datasets" for f in result.folders)


def test_root_listing_still_shows_real_uss_entries_alongside_datasets_folder(provider):
    creds = _creds(dataset_hlq="USER1")
    items = {"items": [{"name": "myfile.txt", "mode": "-rwxr--r--", "size": 42, "mtime": "2026-01-01T00:00:00"}]}
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp(items)):
        result = provider.get_children(creds, None)
    assert any(f.name == "myfile.txt" for f in result.files)
    assert any(f.id == "__datasets__" for f in result.folders)


# ---------------------------------------------------------------------------
# Dataset browsing
# ---------------------------------------------------------------------------

def test_datasets_root_lists_pds_libraries_as_folders_and_sequential_as_files(provider):
    creds = _creds(dataset_hlq="USER1")
    data = {"items": [
        {"dsname": "USER1.MYLIB", "dsorg": "PO"},
        {"dsname": "USER1.FLATFILE", "dsorg": "PS"},
    ]}
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp(data)) as mock_get:
        result = provider.get_children(creds, "__datasets__")
    assert len(result.folders) == 1
    assert result.folders[0].id == "ds:USER1.MYLIB"
    assert result.folders[0].name == "USER1.MYLIB"
    assert len(result.files) == 1
    assert result.files[0].id == "ds:USER1.FLATFILE"
    # Confirms the dslevel query used the configured HLQ, not a hardcoded one
    called_url = mock_get.call_args[0][0]
    assert "/zosmf/restfiles/ds" in called_url
    assert mock_get.call_args[1]["params"]["dslevel"] == "USER1.*"


def test_browsing_into_a_pds_library_lists_its_members(provider):
    creds = _creds(dataset_hlq="USER1")
    data = {"items": [{"member": "MEMBER1"}, {"member": "MEMBER2"}]}
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp(data)) as mock_get:
        result = provider.get_children(creds, "ds:USER1.MYLIB")
    names = {f.name for f in result.files}
    assert names == {"MEMBER1", "MEMBER2"}
    assert all(f.id.startswith("ds:USER1.MYLIB(") for f in result.files)
    called_url = mock_get.call_args[0][0]
    assert called_url.endswith("/member") or "/member" in called_url


def test_get_content_for_dataset_member_hits_the_confirmed_endpoint(provider):
    creds = _creds()
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp(content=b"HELLO MAINFRAME")) as mock_get:
        content = provider.get_content(creds, "ds:USER1.MYLIB(MEMBER1)")
    assert content == b"HELLO MAINFRAME"
    called_url = mock_get.call_args[0][0]
    assert called_url.endswith("/zosmf/restfiles/ds/USER1.MYLIB(MEMBER1)")


def test_get_file_for_dataset_member_derives_library_as_folder_id(provider):
    creds = _creds()
    info = provider.get_file(creds, "ds:USER1.MYLIB(MEMBER1)")
    assert info.name == "USER1.MYLIB(MEMBER1)"
    assert info.folder_id == "ds:USER1.MYLIB"


def test_get_file_for_sequential_dataset_has_datasets_root_as_folder_id(provider):
    creds = _creds()
    info = provider.get_file(creds, "ds:USER1.FLATFILE")
    assert info.folder_id == "__datasets__"


# ---------------------------------------------------------------------------
# Dataset writes are deliberately unsupported (501), not silently attempted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda p, c: p.create_folder(c, "__datasets__", "NEWLIB"),
    lambda p, c: p.create_folder(c, "ds:USER1.MYLIB", "NEWLIB"),
    lambda p, c: p.create_document(c, "__datasets__", "NEW", "text/plain", b"x"),
    lambda p, c: p.rename_folder(c, "ds:USER1.MYLIB", "RENAMED"),
    lambda p, c: p.move_folder(c, "ds:USER1.MYLIB", None),
    lambda p, c: p.delete_folder(c, "ds:USER1.MYLIB"),
    lambda p, c: p.rename_file(c, "ds:USER1.MYLIB(MEMBER1)", "RENAMED"),
    lambda p, c: p.move_file(c, "ds:USER1.MYLIB(MEMBER1)", None),
    lambda p, c: p.delete_file(c, "ds:USER1.MYLIB(MEMBER1)"),
    lambda p, c: p.create_version(c, "ds:USER1.MYLIB(MEMBER1)", "text/plain", b"x"),
    lambda p, c: p.trash_file(c, "ds:USER1.MYLIB(MEMBER1)"),
    lambda p, c: p.trash_folder(c, "ds:USER1.MYLIB"),
])
def test_dataset_write_operations_are_rejected_with_501(provider, call):
    creds = _creds()
    with pytest.raises(ProviderError) as exc_info:
        call(provider, creds)
    assert exc_info.value.status_code == 501


# ---------------------------------------------------------------------------
# search() uses the same "ds:" id convention as browsing
# ---------------------------------------------------------------------------

def test_search_prefixes_dataset_results_with_ds_for_id_consistency(provider):
    creds = _creds()
    ds_result = {"items": [{"dsname": "USER1.FOUND", "dsorg": "PS"}]}
    uss_result = {"items": []}
    with patch("app.storage_providers.ibmz_provider._requests.get", side_effect=[_resp(ds_result), _resp(uss_result)]):
        folders, files = provider.search(creds, "FOUND")
    assert files[0].id == "ds:USER1.FOUND"


# ---------------------------------------------------------------------------
# CMOD — corrected endpoint prefix (/cmod-rest/v1, not /IBMcmRecordsView)
# ---------------------------------------------------------------------------

def test_cm8_get_children_uses_confirmed_cmod_rest_prefix(provider):
    creds = _creds(cm8_url="https://mainframe:9443")
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp({"folders": [], "documents": []})) as mock_get:
        provider.get_children(creds, None)
    called_url = mock_get.call_args[0][0]
    assert called_url == "https://mainframe:9443/cmod-rest/v1/folders"
    assert "IBMcmRecordsView" not in called_url


def test_cm8_get_children_with_folder_id_uses_folders_name_path(provider):
    creds = _creds(cm8_url="https://mainframe:9443")
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp({"folders": [], "documents": []})) as mock_get:
        provider.get_children(creds, "MYFOLDER")
    called_url = mock_get.call_args[0][0]
    assert called_url == "https://mainframe:9443/cmod-rest/v1/folders/MYFOLDER"


def test_cm8_get_content_uses_cmod_rest_prefix(provider):
    creds = _creds(cm8_url="https://mainframe:9443")
    with patch("app.storage_providers.ibmz_provider._requests.get", return_value=_resp(content=b"doc bytes")) as mock_get:
        content = provider.get_content(creds, "doc123")
    assert content == b"doc bytes"
    called_url = mock_get.call_args[0][0]
    assert called_url == "https://mainframe:9443/cmod-rest/v1/documents/doc123/content"


def test_cm8_write_operations_are_still_rejected_with_501(provider):
    creds = _creds(cm8_url="https://mainframe:9443")
    with pytest.raises(ProviderError) as exc_info:
        provider.create_document(creds, None, "new.txt", "text/plain", b"x")
    assert exc_info.value.status_code == 501
