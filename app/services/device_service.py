from typing import Any

from app.models.schemas import (
    ClimateControlRequest,
    CurtainControlRequest,
    CustomDeviceControlRequest,
    LightControlRequest,
    ToolCallRequest,
    ToolCallResponse,
    ToolCatalogItem,
)
from app.services.ha_service import execute_ha_service_call, execute_tool_call, resolve_domain, resolve_service_data


def _build_arguments(
    *,
    area: str | None = None,
    entity_id: str | list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if area is not None and str(area).strip():
        payload["area"] = str(area).strip()
    if entity_id is not None:
        payload["entity_id"] = entity_id
    if extra:
        for key, value in extra.items():
            if value is not None:
                payload[key] = value
    return payload


async def control_lights(req: LightControlRequest) -> ToolCallResponse:
    service = "turn_on" if req.action == "on" else "turn_off"
    item = ToolCatalogItem(
        tool_name=f"device.lights.{req.action}",
        domain="auto",
        service=service,
        strategy="light_area",
        description="Built-in light control API",
        default_arguments={},
    )

    arguments = _build_arguments(area=req.area, entity_id=req.entity_id)
    service_data = resolve_service_data(item, arguments)
    domain = resolve_domain(item, service_data)
    return await execute_ha_service_call(
        tool_name=item.tool_name,
        strategy=item.strategy,
        domain=domain,
        service=item.service,
        service_data=service_data,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


async def control_curtains(req: CurtainControlRequest) -> ToolCallResponse:
    service_map = {
        "open": "open_cover",
        "close": "close_cover",
        "stop": "stop_cover",
    }
    item = ToolCatalogItem(
        tool_name=f"device.curtains.{req.action}",
        domain="cover",
        service=service_map[req.action],
        strategy="cover_area",
        description="Built-in curtain control API",
        default_arguments={},
    )

    arguments = _build_arguments(area=req.area, entity_id=req.entity_id)
    service_data = resolve_service_data(item, arguments)
    domain = resolve_domain(item, service_data)
    return await execute_ha_service_call(
        tool_name=item.tool_name,
        strategy=item.strategy,
        domain=domain,
        service=item.service,
        service_data=service_data,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


async def control_climate(req: ClimateControlRequest) -> ToolCallResponse:
    if req.action == "set_temperature":
        strategy = "climate_area_temperature"
        service = "set_temperature"
    else:
        strategy = "climate_area"
        service = req.action

    item = ToolCatalogItem(
        tool_name=f"device.climate.{req.action}",
        domain="climate",
        service=service,
        strategy=strategy,
        description="Built-in climate control API",
        default_arguments={},
    )

    arguments = _build_arguments(
        area=req.area,
        entity_id=req.entity_id,
        extra={"temperature": req.temperature},
    )
    service_data = resolve_service_data(item, arguments)
    domain = resolve_domain(item, service_data)
    return await execute_ha_service_call(
        tool_name=item.tool_name,
        strategy=item.strategy,
        domain=domain,
        service=item.service,
        service_data=service_data,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


async def control_custom_device(req: CustomDeviceControlRequest) -> ToolCallResponse:
    tool_req = ToolCallRequest(
        tool_name=req.tool_name,
        arguments=req.arguments,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )
    return await execute_tool_call(tool_req)
