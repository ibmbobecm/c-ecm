"""E-signature router — DocuSign integration.

POST /files/{file_id}/esignature        — send the file for signature
GET  /files/{file_id}/esignature         — list signature requests for a file
GET  /esignature/requests                — list all (optionally filtered)
GET  /esignature/requests/{id}           — get one, refreshing status live
POST /esignature/requests/{id}/void      — cancel a pending request
POST /esignature/webhook                 — DocuSign Connect status callback (public)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import activity_service, connections_store, esignature_service, esignature_store
from ..access_helpers import to_http
from ..auth import CurrentSession, CurrentUser, get_current_session, get_current_user, require_role
from ..config import APP_USERNAME
from ..schemas import ESignatureRequestCreate, ESignatureRequestOut
from ..storage_providers.base import ProviderError
from ..storage_providers.registry import get_provider

logger = logging.getLogger("esignature_router")

router = APIRouter(tags=["esignature"])
public_router = APIRouter(tags=["esignature-public"])

_editor = require_role("editor")


def _actor(session: CurrentSession) -> str:
    return session.user.get("username") or "unknown"


@router.post("/files/{file_id}/esignature", response_model=ESignatureRequestOut, status_code=201)
def send_for_signature(file_id: str, req: ESignatureRequestCreate, session: CurrentSession = Depends(get_current_session)):
    if "editor" not in session.user.get("roles", []) and "admin" not in session.user.get("roles", []):
        raise HTTPException(status_code=403, detail="This action requires the 'editor' role")
    if not esignature_service.is_configured():
        raise HTTPException(status_code=503, detail="DocuSign isn't configured on this server (set it up in Admin Settings)")
    try:
        info = session.provider.get_file(session.creds, file_id)
        content = session.provider.get_content(session.creds, file_id)
    except ProviderError as exc:
        raise to_http(exc)

    signers = [s.model_dump() for s in req.signers]
    try:
        envelope_id = esignature_service.create_envelope(content, info.name, signers, req.subject, req.message)
    except esignature_service.ESignatureError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    record = esignature_store.create(
        connection_id=session.connection_id,
        resource_id=file_id,
        resource_type="file",
        resource_name=info.name,
        envelope_id=envelope_id,
        signers=signers,
        subject=req.subject,
        requested_by=_actor(session),
    )
    activity_service.record_event(
        connection_id=session.connection_id,
        provider_key=session.provider_key,
        resource_type="file",
        resource_id=file_id,
        resource_name=info.name,
        event_type="sent_for_signature",
        actor=_actor(session),
        payload={"envelope_id": envelope_id, "signers": [s["email"] for s in signers]},
    )
    return ESignatureRequestOut(**record)


@router.get("/files/{file_id}/esignature", response_model=list[ESignatureRequestOut])
def list_signature_requests(file_id: str, session: CurrentSession = Depends(get_current_session)):
    return [ESignatureRequestOut(**r) for r in esignature_store.list_for_resource(session.connection_id, file_id)]


@router.get("/esignature/requests", response_model=list[ESignatureRequestOut])
def list_all_requests(
    connection_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    _user: CurrentUser = Depends(get_current_user),
):
    return [ESignatureRequestOut(**r) for r in esignature_store.list_all(connection_id=connection_id, status=status)]


@router.get("/esignature/requests/{request_id}", response_model=ESignatureRequestOut)
def get_request(request_id: str, refresh: bool = Query(default=True), _user: CurrentUser = Depends(get_current_user)):
    record = esignature_store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Signature request not found")
    if refresh and record["status"] not in ("completed", "declined", "voided"):
        try:
            live = esignature_service.get_envelope_status(record["envelope_id"])
            live_status = live.get("status", record["status"])
            if live_status != record["status"]:
                esignature_store.update_status(request_id, live_status, completed=live_status in ("completed", "declined", "voided"))
                record = esignature_store.get(request_id)
        except esignature_service.ESignatureError:
            pass  # best-effort — stale-but-present status beats a hard failure on a read
    return ESignatureRequestOut(**record)


@router.post("/esignature/requests/{request_id}/void", response_model=ESignatureRequestOut)
def void_request(request_id: str, reason: str = Query(default="Voided by C-ECM"), session: CurrentSession = Depends(get_current_session)):
    record = esignature_store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Signature request not found")
    is_admin = "admin" in session.user.get("roles", [])
    if record["requested_by"] != _actor(session) and not is_admin:
        raise HTTPException(status_code=403, detail="Only the requester (or an admin) can void this request")
    if record["status"] in ("completed", "declined", "voided"):
        raise HTTPException(status_code=409, detail=f"This request is already {record['status']}")
    try:
        esignature_service.void_envelope(record["envelope_id"], reason)
    except esignature_service.ESignatureError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    esignature_store.update_status(request_id, "voided", completed=True)
    return ESignatureRequestOut(**esignature_store.get(request_id))


@public_router.post("/esignature/webhook")
async def docusign_webhook(request: Request):
    """DocuSign Connect posts here on envelope status changes. Deliberately
    unauthenticated (DocuSign is the caller, not a C-ECM session) —
    HMAC verification is the actual security boundary here, matching how
    routers/sharing.py's public route relies on the token, not a session."""
    raw_body = await request.body()
    signature = request.headers.get("X-DocuSign-Signature-1")
    if not esignature_service.verify_webhook_signature(raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if not esignature_service.get_settings()["docusign_webhook_hmac_key"]:
        logger.warning("Processing a DocuSign webhook call with no HMAC key configured — this request wasn't actually verified")

    payload = await request.json()
    envelope_id = payload.get("data", {}).get("envelopeId") or payload.get("envelopeId")
    new_status = payload.get("data", {}).get("envelopeSummary", {}).get("status") or payload.get("status")
    if not envelope_id or not new_status:
        return {"ok": True}  # unrecognized shape — ack anyway so DocuSign doesn't retry forever

    record = esignature_store.get_by_envelope_id(envelope_id)
    if record is None:
        return {"ok": True}  # an envelope we don't know about — nothing to update

    completed = new_status in ("completed", "declined", "voided")
    signed_version_number = None

    if new_status == "completed" and record["status"] != "completed":
        # Attach the signed document back as a new version of the original
        # file — this is what makes the feature actually useful rather
        # than a disconnected status tracker: the completed, signed PDF
        # becomes part of the file's own version history.
        try:
            signed_bytes = esignature_service.download_signed_document(envelope_id)
            entry = connections_store.get_creds(record["connection_id"])
            if entry is not None:
                provider_key, creds = entry
                provider = get_provider(provider_key)
                updated = provider.create_version(creds, record["resource_id"], "application/pdf", signed_bytes)
                signed_version_number = updated.version_number
                activity_service.record_event(
                    connection_id=record["connection_id"],
                    provider_key=provider_key,
                    resource_type="file",
                    resource_id=record["resource_id"],
                    resource_name=record["resource_name"],
                    event_type="signature_completed",
                    actor=APP_USERNAME,
                    payload={"envelope_id": envelope_id, "version_number": signed_version_number},
                )
        except Exception:
            logger.exception("Couldn't attach the signed document for envelope %s as a new version", envelope_id)

    esignature_store.update_status(record["id"], new_status, completed=completed, signed_version_number=signed_version_number)
    return {"ok": True}
