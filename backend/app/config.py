import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")
DATA_DIR = Path(os.environ.get("FD_DATA_DIR", str(BACKEND_DIR / "data")))


def _get_or_create_secret() -> str:
    secret_path = DATA_DIR / ".jwt_secret"
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    secret_path.write_text(secret, encoding="utf-8")
    return secret


JWT_SECRET = os.environ.get("FD_JWT_SECRET") or _get_or_create_secret()
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.environ.get("FD_JWT_EXPIRE_MINUTES", str(24 * 60)))

MAX_UPLOAD_BYTES = int(os.environ.get("FD_MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))

# --- FileDrive's own login ---
# Logging into the app itself is separate from any backend's credentials —
# you sign in as FileDrive once, then connect backends under Settings, each
# stored so you don't re-enter them per session. Single local account for
# now (same plaintext-locally, flagged-for-a-real-vault-later stance as
# everything else here) — this app isn't multi-tenant.
APP_USERNAME = os.environ.get("FD_APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("FD_APP_PASSWORD", "admin")

# --- Connections store ---
# Every backend credential/token a user has connected, persisted here (not
# just cached for the life of a session) so "set up once" actually means
# once. Plaintext SQLite for now — same vault caveat as above, flagged not
# silently glossed over.
CONNECTIONS_DB_PATH = DATA_DIR / "connections.db"

# --- FileNet Content Engine Web Services ---
FILENET_WSDL_URL = os.environ.get(
    "FD_FILENET_WSDL_URL", "http://localhost:9080/wsi/FNCEWS40MTOM/?WSDL"
)
FILENET_ENDPOINT_URL = os.environ.get(
    "FD_FILENET_ENDPOINT_URL", "http://localhost:9080/wsi/FNCEWS40MTOM/"
)
FILENET_OBJECT_STORE = os.environ.get("FD_FILENET_OBJECT_STORE", "HR2")
# All FileDrive content lives under this folder in the object store, kept
# separate from whatever else already exists there (e.g. /Invoices, /test).
FILENET_ROOT_PATH = os.environ.get("FD_FILENET_ROOT_PATH", "/FileDrive")

# --- Java/EJB bridge for content-write operations ---
# The WSI/SOAP transport has a server-side bug (NPE in PersisterBase) on any
# ContentElements write in this installation. Content-write calls (create
# document, checkin) go through the FileNet Java API over native EJB/IIOP
# instead, via this small CLI. Reads and everything else stay on WSI/zeep.
_WAS_JAVA_HOME = os.environ.get(
    "FD_WAS_JAVA_HOME", r"C:\Program Files\IBM\WebSphere\AppServer\java\8.0"
)
JAVA_BIN = os.environ.get("FD_JAVA_BIN", str(Path(_WAS_JAVA_HOME) / "bin" / "java.exe"))
JACE_JAR = os.environ.get(
    "FD_JACE_JAR", r"C:\Program Files\IBM\FileNet\CEClient\lib\Jace.jar"
)
_WAS_RUNTIMES = os.environ.get(
    "FD_WAS_RUNTIMES", r"C:\Program Files\IBM\WebSphere\AppServer\runtimes"
)
WAS_RUNTIME_JARS = [
    str(Path(_WAS_RUNTIMES) / "com.ibm.ws.ejb.thinclient_9.0.jar"),
    str(Path(_WAS_RUNTIMES) / "com.ibm.ws.orb_9.0.jar"),
    str(Path(_WAS_RUNTIMES) / "com.ibm.ws.admin.client_9.0.jar"),
]
_WAS_PROFILE_PROPS = os.environ.get(
    "FD_WAS_PROFILE_PROPS",
    r"C:\Program Files\IBM\WebSphere\AppServer\profiles\AppSrv01\properties",
)
SAS_CLIENT_PROPS = str(Path(_WAS_PROFILE_PROPS) / "sas.client.props")
SSL_CLIENT_PROPS = str(Path(_WAS_PROFILE_PROPS) / "ssl.client.props")
FILENET_IIOP_URI = os.environ.get(
    "FD_FILENET_IIOP_URI", "iiop://localhost:2809/FileNet/Engine"
)
JAVA_BRIDGE_DIR = BACKEND_DIR / "javabridge"

# --- Local disk provider ---
# A real filesystem backend. No remote identity to check credentials
# against — each connection just names a folder (defaulting to this one if
# left blank), set per-connection in the "Add a connection" form.
LOCAL_STORAGE_DIR = Path(os.environ.get("FD_LOCAL_STORAGE_DIR", str(DATA_DIR / "local_storage")))

# --- OAuth providers: Google Drive, Microsoft 365 (Graph), Box ---
# Each needs an app registered in the provider's own developer console —
# these can't be created programmatically, only the resulting client
# id/secret plugged in here (or, later, pulled from a real secrets vault).
OAUTH_REDIRECT_BASE = os.environ.get("FD_OAUTH_REDIRECT_BASE", "http://127.0.0.1:8020")

# The backend's own externally-reachable base URL — same value as above,
# named for its other use (building share-link URLs) so that call site
# doesn't read like it's OAuth-related.
API_BASE_URL = OAUTH_REDIRECT_BASE

GOOGLE_CLIENT_ID = os.environ.get("FD_GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("FD_GOOGLE_CLIENT_SECRET", "")

MS_CLIENT_ID = os.environ.get("FD_MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.environ.get("FD_MS_CLIENT_SECRET", "")
MS_TENANT = os.environ.get("FD_MS_TENANT", "common")

BOX_CLIENT_ID = os.environ.get("FD_BOX_CLIENT_ID", "")
BOX_CLIENT_SECRET = os.environ.get("FD_BOX_CLIENT_SECRET", "")

# --- E-signature: DocuSign (integration, not a reimplementation) ---
# DocuSign's JWT Grant ("Service Integration") flow, not the interactive
# Authorization Code popup the storage OAuth providers above use — sending
# an envelope is a backend action a connection's own end user shouldn't
# need to be present in a browser for every time, so this is one admin-
# authorized service identity (an "impersonated" DocuSign user) shared by
# the whole deployment, the same "one app-level credential" shape as
# Google/MS/Box's client id/secret above, just for DocuSign's own
# recommended server-to-server auth model.
DOCUSIGN_INTEGRATION_KEY = os.environ.get("FD_DOCUSIGN_INTEGRATION_KEY", "")
DOCUSIGN_USER_ID = os.environ.get("FD_DOCUSIGN_USER_ID", "")
DOCUSIGN_ACCOUNT_ID = os.environ.get("FD_DOCUSIGN_ACCOUNT_ID", "")
DOCUSIGN_PRIVATE_KEY = os.environ.get("FD_DOCUSIGN_PRIVATE_KEY", "")
DOCUSIGN_ENVIRONMENT = os.environ.get("FD_DOCUSIGN_ENVIRONMENT", "demo")  # "demo" | "production"
DOCUSIGN_WEBHOOK_HMAC_KEY = os.environ.get("FD_DOCUSIGN_WEBHOOK_HMAC_KEY", "")
