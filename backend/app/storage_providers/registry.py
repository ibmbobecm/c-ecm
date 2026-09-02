"""All providers C-ECM knows about, keyed by their `key`. Providers that
need config which hasn't been supplied yet (an Alfresco URL, an OAuth app's
client id/secret) still register — `/providers` reports them as
`configured: false` so the UI can show "Connect" as disabled with an
explanation, rather than hiding the backend entirely.
"""

import threading

from .base import StorageProvider
from .filenet_provider import FileNetProvider
from .local_provider import LocalDiskProvider

_PROVIDERS: dict[str, StorageProvider] = {}
_build_lock = threading.Lock()


def _build_registry() -> None:
    """Lazily populates _PROVIDERS exactly once.

    Global search fans a request out across every connection on its own
    thread (routers/search.py), so this can genuinely be entered by several
    threads at once — most commonly on a fresh process's very first search.
    The old version registered providers one at a time directly into the
    module-level dict and used `if _PROVIDERS: return` as a "done" guard;
    that guard flips true the instant the FIRST provider registers, so any
    thread still queued behind the lock (or that simply reads the guard a
    moment later) would treat a still-partially-built registry as finished
    and raise "Unknown provider" for everything not yet added — a real,
    reproducible race, not a hypothetical one. Building into a local dict
    and only publishing it to the module-level name once, at the very end,
    means no thread can ever observe a partially-built registry: the name
    is either the empty initial dict or the fully-built one, never
    in-between (a single dict-reference assignment is atomic under the
    GIL). The lock just prevents redundant concurrent rebuilds, not
    torn reads.
    """
    global _PROVIDERS
    if _PROVIDERS:
        return
    with _build_lock:
        if _PROVIDERS:
            return
        registry: dict[str, StorageProvider] = {}

        def _register(provider: StorageProvider) -> None:
            registry[provider.key] = provider

        # IBM family + local disk first (this app's primary, most-supported
        # backends), then everything else in no particular priority order.
        _register(FileNetProvider())

        try:
            from .s3_provider import IBMCOSProvider
            _register(IBMCOSProvider())
        except Exception:
            pass

        try:
            from .ibmi_provider import IBMiProvider
            _register(IBMiProvider())
        except Exception:
            pass

        try:
            from .ibmz_provider import IBMZProvider
            _register(IBMZProvider())
        except Exception:
            pass

        _register(LocalDiskProvider())

        try:
            from .alfresco_provider import AlfrescoProvider
            _register(AlfrescoProvider())
        except Exception:
            pass

        try:
            from .oauth_providers import BoxProvider, GoogleDriveProvider, MicrosoftGraphProvider
            _register(GoogleDriveProvider())
            _register(MicrosoftGraphProvider())
            _register(BoxProvider())
        except Exception:
            pass

        try:
            from .oauth_providers import DropboxProvider
            _register(DropboxProvider())
        except Exception:
            pass

        try:
            from .oauth_providers import LaserficheProvider, ShareFileProvider
            _register(LaserficheProvider())
            _register(ShareFileProvider())
        except Exception:
            pass

        try:
            from .documentum_provider import DocumentumProvider
            _register(DocumentumProvider())
        except Exception:
            pass

        try:
            from .opentext_provider import OpenTextContentServerProvider
            _register(OpenTextContentServerProvider())
        except Exception:
            pass

        try:
            from .mfiles_provider import MFilesProvider
            _register(MFilesProvider())
        except Exception:
            pass

        try:
            from .onbase_provider import OnBaseProvider
            _register(OnBaseProvider())
        except Exception:
            pass

        try:
            from .nuxeo_provider import NuxeoProvider
            _register(NuxeoProvider())
        except Exception:
            pass

        try:
            from .docuware_provider import DocuWareProvider
            _register(DocuWareProvider())
        except Exception:
            pass

        try:
            from .docushare_provider import DocuShareProvider
            _register(DocuShareProvider())
        except Exception:
            pass

        try:
            from .s3_provider import WasabiProvider, BackblazeB2Provider, GCSProvider
            _register(WasabiProvider())
            _register(BackblazeB2Provider())
            _register(GCSProvider())
        except Exception:
            pass

        try:
            from .webdav_provider import NextcloudProvider, OwnCloudProvider, SynologyDriveProvider, QNAPProvider
            _register(NextcloudProvider())
            _register(OwnCloudProvider())
            _register(SynologyDriveProvider())
            _register(QNAPProvider())
        except Exception:
            pass

        try:
            from .cmis_provider import IBMContentNavigatorProvider, SAPDocumentManagementProvider
            _register(IBMContentNavigatorProvider())
            _register(SAPDocumentManagementProvider())
        except Exception:
            pass

        try:
            from .egnyte_provider import EgnyteProvider
            _register(EgnyteProvider())
        except Exception:
            pass

        try:
            from .confluence_provider import ConfluenceProvider
            _register(ConfluenceProvider())
        except Exception:
            pass

        try:
            from .huddle_provider import HuddleProvider
            _register(HuddleProvider())
        except Exception:
            pass

        try:
            from .netdocuments_provider import NetDocumentsProvider
            _register(NetDocumentsProvider())
        except Exception:
            pass

        try:
            from .zoho_workdrive_provider import ZohoWorkDriveProvider
            _register(ZohoWorkDriveProvider())
        except Exception:
            pass

        try:
            from .imanage_provider import IManageProvider
            _register(IManageProvider())
        except Exception:
            pass

        try:
            from .onehub_provider import OnehubProvider
            _register(OnehubProvider())
        except Exception:
            pass

        try:
            from .salesforce_files_provider import SalesforceFilesProvider
            _register(SalesforceFilesProvider())
        except Exception:
            pass

        try:
            from .oracle_content_management_provider import OracleContentManagementProvider
            _register(OracleContentManagementProvider())
        except Exception:
            pass

        try:
            from .kiteworks_provider import KiteworksProvider
            _register(KiteworksProvider())
        except Exception:
            pass

        try:
            from .aem_assets_provider import AEMAssetsProvider
            _register(AEMAssetsProvider())
        except Exception:
            pass

        try:
            from .filecloud_provider import FileCloudProvider
            _register(FileCloudProvider())
        except Exception:
            pass

        try:
            from .pcloud_provider import PCloudProvider
            _register(PCloudProvider())
        except Exception:
            pass

        try:
            from .seafile_provider import SeafileProvider
            _register(SeafileProvider())
        except Exception:
            pass

        try:
            from .logicaldoc_provider import LogicalDOCProvider
            _register(LogicalDOCProvider())
        except Exception:
            pass

        try:
            from .veeva_vault_provider import VeevaVaultProvider
            _register(VeevaVaultProvider())
        except Exception:
            pass

        try:
            from .mediafire_provider import MediaFireProvider
            _register(MediaFireProvider())
        except Exception:
            pass

        try:
            from .efilecabinet_provider import EFileCabinetProvider
            _register(EFileCabinetProvider())
        except Exception:
            pass

        try:
            from .firmex_provider import FirmexProvider
            _register(FirmexProvider())
        except Exception:
            pass

        try:
            from .sharevault_provider import ShareVaultProvider
            _register(ShareVaultProvider())
        except Exception:
            pass

        try:
            from .intralinks_provider import IntralinksProvider
            _register(IntralinksProvider())
        except Exception:
            pass

        try:
            from .highq_provider import HighQProvider
            _register(HighQProvider())
        except Exception:
            pass

        try:
            from .workshare_provider import WorkshareProvider
            _register(WorkshareProvider())
        except Exception:
            pass

        try:
            from .evernote_teams_provider import EvernoteTeamsProvider
            _register(EvernoteTeamsProvider())
        except Exception:
            pass

        try:
            from .s3_provider import AWSS3Provider
            _register(AWSS3Provider())
        except Exception:
            pass

        try:
            from .azure_provider import AzureBlobProvider
            _register(AzureBlobProvider())
        except Exception:
            pass

        _PROVIDERS = registry


def get_provider(key: str) -> StorageProvider:
    _build_registry()
    provider = _PROVIDERS.get(key)
    if provider is None:
        raise KeyError(f"Unknown provider '{key}'")
    return provider


def list_providers() -> list[StorageProvider]:
    _build_registry()
    return list(_PROVIDERS.values())
