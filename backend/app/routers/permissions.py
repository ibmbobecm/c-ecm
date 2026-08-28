from fastapi import APIRouter, Depends, Query

from ..access_helpers import to_http
from ..auth import CurrentSession, get_current_session
from ..schemas import PermissionEntryOut
from ..storage_providers.base import ProviderError

router = APIRouter(tags=["permissions"])


@router.get("/resources/{resource_id}/permissions", response_model=list[PermissionEntryOut])
def get_permissions(resource_id: str, resource_type: str = Query(default="file"), session: CurrentSession = Depends(get_current_session)):
    try:
        entries = session.provider.get_permissions(session.creds, resource_id, resource_type)
    except ProviderError as exc:
        raise to_http(exc)
    return [
        PermissionEntryOut(
            principal_type=e.principal_type,
            principal_id=e.principal_id,
            principal_display=e.principal_display,
            role=e.role,
            inherited=e.inherited,
            source=e.source,
        )
        for e in entries
    ]
