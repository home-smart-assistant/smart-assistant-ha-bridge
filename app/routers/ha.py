from typing import Any

from fastapi import APIRouter, Query

from app.models.schemas import AreaAssignRequest, AreaAuditRequest, AreaReassignRequest, AreaSyncRequest, ToolCallResponse
from app.services.ha_service import (
    get_ha_areas,
    get_ha_entity_state,
    get_ha_overview,
    assign_ha_areas,
    audit_ha_areas,
    sync_ha_areas,
    reassign_ha_entities,
    list_ha_entities,
    list_ha_services,
)

router = APIRouter(prefix="/v1/ha", tags=["ha"])


@router.get("/overview")
async def ha_overview() -> dict[str, Any]:
    return await get_ha_overview()


@router.get("/areas")
async def ha_areas(
    include_state_validation: bool = Query(default=True, description="Validate mapped entities against current HA states"),
) -> dict[str, Any]:
    return await get_ha_areas(include_state_validation=include_state_validation)


@router.post("/areas/sync", response_model=ToolCallResponse)
async def ha_areas_sync(req: AreaSyncRequest) -> ToolCallResponse:
    return await sync_ha_areas(
        target_areas=req.target_areas,
        delete_unused=req.delete_unused,
        force_delete_in_use=req.force_delete_in_use,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


@router.post("/areas/audit", response_model=ToolCallResponse)
async def ha_areas_audit(req: AreaAuditRequest) -> ToolCallResponse:
    return await audit_ha_areas(
        target_areas=req.target_areas,
        domains=req.domains,
        include_unavailable=req.include_unavailable,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


@router.post("/areas/assign", response_model=ToolCallResponse)
async def ha_areas_assign(req: AreaAssignRequest) -> ToolCallResponse:
    return await assign_ha_areas(
        target_areas=req.target_areas,
        domains=req.domains,
        include_unavailable=req.include_unavailable,
        only_with_suggestion=req.only_with_suggestion,
        max_updates=req.max_updates,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


@router.post("/areas/reassign", response_model=ToolCallResponse)
async def ha_areas_reassign(req: AreaReassignRequest) -> ToolCallResponse:
    return await reassign_ha_entities(
        assignments=[item.model_dump() for item in req.assignments],
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


@router.get("/entities")
async def ha_entities(
    domain: str | None = Query(default=None, description="Filter by domain, e.g. light/switch/climate"),
    area: str | None = Query(default=None, description="Filter by area_id or area_name"),
    q: str | None = Query(default=None, description="Keyword match on entity_id/friendly_name/state"),
    limit: int = Query(default=500, ge=1, le=2000),
    include_attributes: bool = Query(default=False),
) -> dict[str, Any]:
    return await list_ha_entities(
        domain=domain,
        area=area,
        q=q,
        limit=limit,
        include_attributes=include_attributes,
    )


@router.get("/entities/{entity_id}")
async def ha_entity_state(
    entity_id: str,
    include_attributes: bool = Query(default=True),
) -> dict[str, Any]:
    return await get_ha_entity_state(entity_id=entity_id, include_attributes=include_attributes)


@router.get("/services")
async def ha_services(
    domain: str | None = Query(default=None, description="Optional domain filter"),
) -> dict[str, Any]:
    return await list_ha_services(domain=domain)
