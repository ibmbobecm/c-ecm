from fastapi import APIRouter, Depends, Query

from .. import notification_service
from ..auth import CurrentUser, get_current_user
from ..schemas import NotificationOut, NotificationSummaryOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationSummaryOut)
def list_notifications(
    unread_only: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
):
    owner = current_user["username"]
    return NotificationSummaryOut(
        unread_count=notification_service.unread_count(owner),
        notifications=[
            NotificationOut(**n)
            for n in notification_service.list_for_owner(owner, unread_only=unread_only)
        ],
    )


@router.post("/{notification_id}/read", status_code=204)
def mark_read(notification_id: str, _user: CurrentUser = Depends(get_current_user)):
    notification_service.mark_read(notification_id)


@router.post("/read-all", status_code=204)
def mark_all_read(current_user: CurrentUser = Depends(get_current_user)):
    notification_service.mark_all_read(current_user["username"])
