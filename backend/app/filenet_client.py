"""FileNet Content Engine Web Services (CEWS) integration.

Every function takes a `FileNetConn` — the target server's own WSDL/
endpoint/object-store/IIOP details plus the caller's username/password —
rather than a fixed, single hardcoded server. Different connections can
point at entirely different FileNet installations; nothing here assumes
there's only one.

Two calling conventions are mixed here, for a documented reason:
- Most operations go through `zeep`'s high-level `client.service.X(...)`
  interface — GetObjects, ExecuteChanges, GetContent all work cleanly
  through it (verified against the live server).
- `ExecuteSearch` (used for folder listing) does NOT: zeep mis-serializes
  the top-level abstract `SearchRequestType` request (a `RepositorySearch`
  subtype), silently dropping the `xsi:type` marker and confusing field
  ordering. Verified as a zeep limitation, not a FileNet issue — a hand-
  built SOAP request in `_raw_search` is the reliable workaround.
"""

import base64
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from email import message_from_bytes
from email.policy import compat32
from pathlib import Path

import requests
import zeep
from lxml import etree
from requests.auth import HTTPBasicAuth
from zeep.transports import Transport
from zeep.wsse.username import UsernameToken

from .config import (
    JACE_JAR,
    JAVA_BIN,
    JAVA_BRIDGE_DIR,
    SAS_CLIENT_PROPS,
    SSL_CLIENT_PROPS,
    WAS_RUNTIME_JARS,
)

logger = logging.getLogger("filenet_client")

_NS = "http://www.filenet.com/ns/fnce/2006/11/ws/schema"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"


@dataclass(frozen=True)
class FileNetConn:
    """Everything needed to reach one specific FileNet installation as one
    specific user. Built fresh from a connection's stored creds on every
    call — cheap, and keeps this module free of any notion of "the" server."""

    username: str
    password: str
    wsdl_url: str
    endpoint_url: str
    object_store: str
    root_path: str
    iiop_uri: str


class FileNetError(Exception):
    """A FileNet Content Engine SOAP fault, or a client-side failure
    talking to it. `detail` carries the raw fault string when available."""

    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message)
        self.detail = detail or message


def get_client(conn: FileNetConn) -> zeep.Client:
    session = requests.Session()
    session.auth = HTTPBasicAuth(conn.username, conn.password)
    return zeep.Client(
        wsdl=conn.wsdl_url,
        transport=Transport(session=session),
        wsse=UsernameToken(conn.username, conn.password),
    )


def authenticate(conn: FileNetConn) -> bool:
    """Validates credentials against Content Engine itself — this *is* the
    login check, there's no separate local user store."""
    try:
        client = get_client(conn)
        client.service.GetObjects(
            ObjectRequest=[
                {
                    "SourceSpecification": {
                        "classId": "Folder",
                        "objectId": "/",
                        "objectStore": conn.object_store,
                    },
                    "PropertyFilter": {"IncludeProperties": [{"_value_1": "Id"}]},
                }
            ]
        )
        return True
    except Exception:
        logger.info("Authentication failed for user %r against %r", conn.username, conn.wsdl_url)
        return False


def get_object(conn: FileNetConn, class_id: str, object_id: str, properties: list[str]) -> dict:
    client = get_client(conn)
    try:
        resp = client.service.GetObjects(
            ObjectRequest=[
                {
                    "SourceSpecification": {
                        "classId": class_id,
                        "objectId": object_id,
                        "objectStore": conn.object_store,
                    },
                    "PropertyFilter": {
                        "IncludeProperties": [{"_value_1": p, "maxRecursion": 2} for p in properties]
                    },
                }
            ]
        )
    except zeep.exceptions.Fault as exc:
        raise FileNetError(f"Couldn't fetch {class_id} {object_id}", str(exc)) from exc
    if not resp or not hasattr(resp[0], "Object"):
        raise FileNetError(f"{class_id} {object_id} not found")
    return _object_to_dict(resp[0]["Object"])


def _object_to_dict(obj) -> dict:
    result = {"classId": obj.classId, "objectId": obj.objectId}
    for prop in obj.Property or []:
        result[prop.propertyId] = getattr(prop, "Value", None)
    return result


def create_folder(conn: FileNetConn, parent_path: str, name: str) -> dict:
    client = get_client(conn)
    ns = _NS
    CreateAction = client.get_type(f"{{{ns}}}CreateAction")
    SingletonString = client.get_type(f"{{{ns}}}SingletonString")
    SingletonObject = client.get_type(f"{{{ns}}}SingletonObject")
    ObjectReference = client.get_type(f"{{{ns}}}ObjectReference")

    try:
        resp = client.service.ExecuteChanges(
            ChangeRequest=[
                {
                    "TargetSpecification": {"classId": "Folder", "objectStore": conn.object_store},
                    "Action": [CreateAction(classId="Folder")],
                    "ActionProperties": {
                        "Property": [
                            SingletonString(propertyId="FolderName", Value=name),
                            SingletonObject(
                                propertyId="Parent",
                                Value=ObjectReference(
                                    classId="Folder", objectId=parent_path, objectStore=conn.object_store
                                ),
                            ),
                        ]
                    },
                    "RefreshFilter": {
                        "IncludeProperties": [{"_value_1": "Id"}, {"_value_1": "PathName"}]
                    },
                }
            ],
            refresh=True,
        )
    except zeep.exceptions.Fault as exc:
        raise FileNetError(f"Couldn't create folder '{name}'", str(exc)) from exc
    return _object_to_dict(resp[0])


# --- ExecuteSearch: hand-built SOAP request (see module docstring) ---

_SEARCH_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
  <soap:Header>
    <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" soap:mustUnderstand="1">
      <wsse:UsernameToken>
        <wsse:Username>{username}</wsse:Username>
        <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-username-token-profile-1.0#PasswordText">{password}</wsse:Password>
      </wsse:UsernameToken>
    </wsse:Security>
  </soap:Header>
  <soap:Body>
    <ExecuteSearchRequest xmlns="{ns}" xsi:type="RepositorySearch" xmlns:xsi="{xsi}">
      <SearchScope xsi:type="ObjectStoreScope" objectStore="{object_store}"/>
      <SearchSQL>{sql}</SearchSQL>
    </ExecuteSearchRequest>
  </soap:Body>
</soap:Envelope>"""


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _raw_search(conn: FileNetConn, sql: str) -> list[dict]:
    envelope = _SEARCH_ENVELOPE.format(
        username=_xml_escape(conn.username),
        password=_xml_escape(conn.password),
        ns=_NS,
        xsi=_XSI,
        object_store=conn.object_store,
        sql=_xml_escape(sql),
    )
    resp = requests.post(
        conn.endpoint_url,
        data=envelope.encode("utf-8"),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
        timeout=30,
    )
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "multipart" in content_type:
        # MTOM wraps the SOAP body as one MIME part even with no binary
        # attachment; use the stdlib email parser to pull it out reliably.
        raw = b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + resp.content
        msg = message_from_bytes(raw, policy=compat32)
        soap_bytes = msg.get_payload(0).get_payload(decode=True)
    else:
        soap_bytes = resp.content

    root = etree.fromstring(soap_bytes)
    fault = root.find(".//{http://www.w3.org/2003/05/soap-envelope}Fault")
    if fault is not None:
        raise FileNetError("FileNet search failed", etree.tostring(fault).decode())

    rows = []
    for obj in root.findall(f".//{{{_NS}}}Object"):
        row = {}
        for prop in obj.findall(f"{{{_NS}}}Property"):
            prop_id = prop.get("propertyId")
            value_el = prop.find(f"{{{_NS}}}Value")
            row[prop_id] = value_el.text if value_el is not None else None
        rows.append(row)
    return rows


def get_children(conn: FileNetConn, path: str) -> tuple[list[dict], list[dict]]:
    escaped_path = path.replace("'", "''")
    folder_sql = (
        f"SELECT Id, FolderName, PathName FROM Folder "
        f"WHERE Folder.This INFOLDER('{escaped_path}')"
    )
    doc_sql = (
        f"SELECT Id, DocumentTitle, MimeType, ContentSize, DateLastModified FROM Document "
        f"WHERE Document.This INFOLDER('{escaped_path}')"
    )
    folders = _raw_search(conn, folder_sql)
    documents = _raw_search(conn, doc_sql)
    return folders, documents


def rename_folder(conn: FileNetConn, folder_path: str, new_name: str) -> dict:
    client = get_client(conn)
    ns = _NS
    UpdateAction = client.get_type(f"{{{ns}}}UpdateAction")
    SingletonString = client.get_type(f"{{{ns}}}SingletonString")
    try:
        resp = client.service.ExecuteChanges(
            ChangeRequest=[
                {
                    "TargetSpecification": {
                        "classId": "Folder", "objectId": folder_path, "objectStore": conn.object_store
                    },
                    "Action": [UpdateAction()],
                    "ActionProperties": {
                        "Property": [SingletonString(propertyId="FolderName", Value=new_name)]
                    },
                    "RefreshFilter": {"IncludeProperties": [{"_value_1": "Id"}, {"_value_1": "PathName"}]},
                }
            ],
            refresh=True,
        )
    except zeep.exceptions.Fault as exc:
        raise FileNetError(f"Couldn't rename folder '{folder_path}'", str(exc)) from exc
    return _object_to_dict(resp[0])


def move_folder(conn: FileNetConn, folder_path: str, new_parent_path: str) -> dict:
    client = get_client(conn)
    ns = _NS
    UpdateAction = client.get_type(f"{{{ns}}}UpdateAction")
    SingletonObject = client.get_type(f"{{{ns}}}SingletonObject")
    ObjectReference = client.get_type(f"{{{ns}}}ObjectReference")
    try:
        resp = client.service.ExecuteChanges(
            ChangeRequest=[
                {
                    "TargetSpecification": {
                        "classId": "Folder", "objectId": folder_path, "objectStore": conn.object_store
                    },
                    "Action": [UpdateAction()],
                    "ActionProperties": {
                        "Property": [
                            SingletonObject(
                                propertyId="Parent",
                                Value=ObjectReference(
                                    classId="Folder", objectId=new_parent_path, objectStore=conn.object_store
                                ),
                            )
                        ]
                    },
                    "RefreshFilter": {"IncludeProperties": [{"_value_1": "Id"}, {"_value_1": "PathName"}]},
                }
            ],
            refresh=True,
        )
    except zeep.exceptions.Fault as exc:
        raise FileNetError(f"Couldn't move folder '{folder_path}'", str(exc)) from exc
    return _object_to_dict(resp[0])


def delete_folder(conn: FileNetConn, folder_path: str) -> None:
    client = get_client(conn)
    ns = _NS
    DeleteAction = client.get_type(f"{{{ns}}}DeleteAction")
    try:
        client.service.ExecuteChanges(
            ChangeRequest=[
                {
                    "TargetSpecification": {
                        "classId": "Folder", "objectId": folder_path, "objectStore": conn.object_store
                    },
                    "Action": [DeleteAction()],
                }
            ]
        )
    except zeep.exceptions.Fault as exc:
        raise FileNetError(f"Couldn't delete folder '{folder_path}'", str(exc)) from exc


def rename_document(conn: FileNetConn, document_id: str, new_name: str) -> dict:
    client = get_client(conn)
    ns = _NS
    UpdateAction = client.get_type(f"{{{ns}}}UpdateAction")
    SingletonString = client.get_type(f"{{{ns}}}SingletonString")
    try:
        resp = client.service.ExecuteChanges(
            ChangeRequest=[
                {
                    "TargetSpecification": {
                        "classId": "Document", "objectId": document_id, "objectStore": conn.object_store
                    },
                    "Action": [UpdateAction()],
                    "ActionProperties": {
                        "Property": [SingletonString(propertyId="DocumentTitle", Value=new_name)]
                    },
                    "RefreshFilter": {"IncludeProperties": [{"_value_1": "Id"}, {"_value_1": "DocumentTitle"}]},
                }
            ],
            refresh=True,
        )
    except zeep.exceptions.Fault as exc:
        raise FileNetError(f"Couldn't rename document '{document_id}'", str(exc)) from exc
    return _object_to_dict(resp[0])


def delete_document(conn: FileNetConn, document_id: str) -> None:
    client = get_client(conn)
    ns = _NS
    DeleteAction = client.get_type(f"{{{ns}}}DeleteAction")
    try:
        client.service.ExecuteChanges(
            ChangeRequest=[
                {
                    "TargetSpecification": {
                        "classId": "Document", "objectId": document_id, "objectStore": conn.object_store
                    },
                    "Action": [DeleteAction()],
                }
            ]
        )
    except zeep.exceptions.Fault as exc:
        raise FileNetError(f"Couldn't delete document '{document_id}'", str(exc)) from exc


def list_versions(conn: FileNetConn, document_id: str) -> list[dict]:
    obj = get_object(conn, "Document", document_id, ["Id", "VersionSeries"])
    vs = obj.get("VersionSeries")
    series_id = getattr(vs, "objectId", None) if vs is not None else None
    if series_id is None:
        return [obj]
    sql = (
        "SELECT Id, DocumentTitle, MajorVersionNumber, MinorVersionNumber, ContentSize, "
        "MimeType, DateLastModified, IsCurrentVersion FROM Document "
        f"WHERE VersionSeries = '{series_id}'"
    )
    return _raw_search(conn, sql)


# --- Content-write bridge (see module docstring / config.py: WSI content
# writes NPE server-side in this installation; these shell out to the
# FileNet Java API over native EJB/IIOP instead). ---

_BRIDGE_CLASSPATH = ";".join([JACE_JAR, *WAS_RUNTIME_JARS, str(JAVA_BRIDGE_DIR)])


def _run_bridge_once(conn: FileNetConn, op_args: tuple[str, ...], timeout: int) -> str:
    iiop_host_port = conn.iiop_uri.split("://", 1)[-1].split("/", 1)[0]
    # Was hardcoded to localhost:2809 regardless of which server this
    # connection actually points at — fine for this machine's own FileNet,
    # but for a remote one it made the JVM validate credentials against
    # *this* machine's WebSphere instead of the real target, failing with
    # "Authentication Failed" even for genuinely correct remote credentials.
    iiop_host, _, iiop_port = iiop_host_port.partition(":")
    cmd = [
        JAVA_BIN,
        "-cp",
        _BRIDGE_CLASSPATH,
        "-Djava.naming.factory.initial=com.ibm.websphere.naming.WsnInitialContextFactory",
        f"-Djava.naming.provider.url=corbaloc:iiop:{iiop_host_port}",
        f"-Dcom.ibm.CORBA.ConfigURL=file:///{Path(SAS_CLIENT_PROPS).as_posix().replace(' ', '%20')}",
        f"-Dcom.ibm.SSL.ConfigURL=file:///{Path(SSL_CLIENT_PROPS).as_posix().replace(' ', '%20')}",
        f"-Dcom.ibm.CORBA.securityServerHost={iiop_host}",
        f"-Dcom.ibm.CORBA.securityServerPort={iiop_port or '2809'}",
        # The default sas.client.props ships with performTransportAssocSSLTLSRequired=true
        # but performTransportAssocSSLTLSSupported=false, a self-contradiction that makes
        # the CSIv2 SecurityServer handshake unresolvable (SECJ0395E). Overriding both to
        # false here is what makes JAAS/CSIv2 auth actually propagate over IIOP.
        "-Dcom.ibm.CSI.performTransportAssocSSLTLSRequired=false",
        "-Dcom.ibm.CSI.performTransportAssocSSLTLSSupported=false",
        "FileNetBridge",
        conn.iiop_uri,
        conn.username,
        conn.password,
        conn.object_store,
        *op_args,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FileNetError("FileNet content operation timed out", str(exc)) from exc
    out = result.stdout.strip().splitlines()
    last_line = out[-1] if out else ""
    if result.returncode != 0 or not last_line.startswith("OK "):
        raise FileNetError("FileNet content operation failed", result.stdout + result.stderr)
    return last_line[len("OK "):].strip()


_NON_RETRYABLE_MARKERS = (
    "E_NOT_AUTHENTICATED", "SECJ", "AuthenticationFailedException", "WSLoginFailedException",
)


def _run_bridge(conn: FileNetConn, *op_args: str) -> str:
    # Each call launches a fresh JVM/IIOP connection against the CE server.
    # Under this installation's connection pool, that occasionally sits
    # waiting for a slot instead of erroring — not a logic bug (fast and
    # correct when it isn't contended), so one retry at a shorter timeout
    # clears the great majority of these before giving up for real.
    #
    # But a credential/CSIv2 authentication failure is deterministic — it
    # will never succeed on retry, so retrying it only doubles the wait
    # (up to ~18s of nothing) before failing the exact same way. Fail fast
    # for those instead.
    try:
        return _run_bridge_once(conn, op_args, timeout=180)
    except FileNetError as exc:
        if any(marker in exc.detail for marker in _NON_RETRYABLE_MARKERS):
            raise
        return _run_bridge_once(conn, op_args, timeout=60)


def create_document(conn: FileNetConn, folder_path: str, file_name: str, mime_type: str, content: bytes) -> dict:
    with tempfile.NamedTemporaryFile(delete=False, suffix="_" + file_name) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        object_id = _run_bridge(conn, "createDocument", folder_path, file_name, mime_type, tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return get_object(
        conn, "Document", object_id,
        ["Id", "DocumentTitle", "MimeType", "ContentSize", "DateLastModified", "MajorVersionNumber"],
    )


def checkin(conn: FileNetConn, document_id: str, file_name: str, mime_type: str, content: bytes, major: bool = True) -> dict:
    with tempfile.NamedTemporaryFile(delete=False, suffix="_" + file_name) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        object_id = _run_bridge(
            conn, "checkin", document_id, file_name, mime_type, tmp_path, "true" if major else "false",
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return get_object(
        conn, "Document", object_id,
        ["Id", "DocumentTitle", "MimeType", "ContentSize", "DateLastModified", "MajorVersionNumber"],
    )


def move_document(conn: FileNetConn, document_id: str, old_folder_path: str, new_folder_path: str, new_name: str) -> None:
    # Two separate bridge calls (two separate JVMs/transactions), not one
    # combined unfile+file call — see FileNetBridge.java: doing both in a
    # single transaction reliably hangs in this installation even though
    # each step is fast and reliable on its own.
    _run_bridge(conn, "unfileDocument", document_id)
    _run_bridge(conn, "fileDocument", document_id, new_folder_path, new_name)


def get_content(conn: FileNetConn, document_id: str) -> bytes:
    # Unlike ContentElements writes, reads over WSI/zeep work fine here — this
    # is GetObjects/ExecuteChanges territory the way the rest of the module
    # is, not the broken write path, so it stays off the Java bridge (whose
    # accessContentStream() reliably hangs for reads — a separate, unrelated
    # issue discovered while wiring this up).
    client = get_client(conn)
    try:
        resp = client.service.GetContent(
            ContentRequest=[
                {
                    "SourceSpecification": {
                        "classId": "Document", "objectId": document_id, "objectStore": conn.object_store
                    },
                    "ElementSpecification": {"itemIndex": 0},
                }
            ]
        )
    except zeep.exceptions.Fault as exc:
        raise FileNetError(f"Couldn't fetch content for {document_id}", str(exc)) from exc
    if not resp or not hasattr(resp[0], "Content") or resp[0].Content is None:
        raise FileNetError(f"No content available for {document_id}")
    return resp[0].Content.Binary
