from typing import Any

from fastapi import APIRouter, Query

from app.services.ha_service import (
    get_ha_areas,
    get_ha_entity_state,
    get_ha_overview,
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
