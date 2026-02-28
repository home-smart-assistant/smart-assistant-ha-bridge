from typing import Any

from fastapi import APIRouter, Query, Request

from app.models.schemas import UiActionLogRequest
from app.services.log_service import get_log_storage_meta, list_recent_logs, log_ui_action

router = APIRouter(prefix="/v1/logs", tags=["system"])


def _normalize_sources(sources: list[str] | None) -> list[str] | None:
    if not sources:
        return None

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in sources:
        for value in raw.split(","):
            source = value.strip()
            if not source or source in seen:
                continue
            seen.add(source)
            normalized.append(source)
    return normalized or None


@router.post("/ui")
async def write_ui_log(req: UiActionLogRequest, request: Request) -> dict[str, Any]:
    item = log_ui_action(req, request.client.host if request.client else None)
    return {"success": True, "event_id": item.event_id}


@router.get("/recent")
async def get_recent_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    source: list[str] | None = Query(default=None),
    event_type: str | None = Query(default=None),
) -> dict[str, Any]:
    logs = list_recent_logs(
        limit=limit,
        sources=_normalize_sources(source),
        event_type=event_type,
    )
    return {
        **get_log_storage_meta(),
        "logs": [x.model_dump(mode="json") for x in logs],
    }
