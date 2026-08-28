from fastapi import APIRouter, Depends, Query

from .. import activity_service
from ..auth import get_app_session
from ..schemas import ActivityEventOut

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=list[ActivityEventOut])
def list_activity(
    connection_id: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    since: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    _session_id: str = Depends(get_app_session),
):
    events = activity_service.list_events(
        connection_id=connection_id, resource_id=resource_id, event_type=event_type, since=since, limit=limit
    )
    return [ActivityEventOut(**e) for e in events]
