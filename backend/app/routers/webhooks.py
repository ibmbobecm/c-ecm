"""Webhook management router (admin-only)."""

from fastapi import APIRouter, Depends, HTTPException

from .. import webhook_service
from ..auth import require_feature
from ..schemas import WebhookCreateRequest, WebhookOut, WebhookUpdateRequest

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("", response_model=list[WebhookOut])
def list_webhooks(_admin=Depends(require_feature("manage_webhooks"))):
    return [WebhookOut(**w) for w in webhook_service.list_webhooks()]


@router.post("", response_model=WebhookOut, status_code=201)
def create_webhook(req: WebhookCreateRequest, _admin=Depends(require_feature("manage_webhooks"))):
    try:
        wh = webhook_service.create_webhook(
            req.url, req.secret, req.event_types,
            connection_id=req.connection_id, resource_id=req.resource_id,
            resource_type=req.resource_type, resource_name=req.resource_name,
            destination_type=req.destination_type,
        )
    except webhook_service.WebhookUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return WebhookOut(**wh)


@router.patch("/{webhook_id}", response_model=WebhookOut)
def update_webhook(webhook_id: str, req: WebhookUpdateRequest, _admin=Depends(require_feature("manage_webhooks"))):
    try:
        updated = webhook_service.update_webhook(
            webhook_id, url=req.url, secret=req.secret, event_types=req.event_types, active=req.active,
            connection_id=req.connection_id, resource_id=req.resource_id,
            resource_type=req.resource_type, resource_name=req.resource_name, clear_scope=req.clear_scope,
            destination_type=req.destination_type,
        )
    except webhook_service.WebhookUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return WebhookOut(**updated)


@router.delete("/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: str, _admin=Depends(require_feature("manage_webhooks"))):
    webhook_service.delete_webhook(webhook_id)
