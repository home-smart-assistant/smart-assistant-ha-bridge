import asyncio
import json
from time import perf_counter
from typing import Any

import httpx
from fastapi import HTTPException

from app.core import settings
from app.models.schemas import ToolCallRequest, ToolCallResponse, ToolCatalogItem
from app.services.catalog_service import get_catalog_snapshot, get_tool_or_raise
from app.services.config_service import auth_headers
from app.services.log_service import log_operation


AREA_CATALOG_TEMPLATE = """{% set out = namespace(items=[]) %}
{% for a in areas() %}
{% set out.items = out.items + [{'area_id': a, 'area_name': area_name(a), 'entities': area_entities(a)}] %}
{% endfor %}
{{ out.items | to_json }}"""


async def execute_ha_service_call(
    *,
    tool_name: str,
    strategy: str,
    domain: str,
    service: str,
    service_data: dict[str, Any],
    trace_id: str | None = None,
    dry_run: bool = False,
) -> ToolCallResponse:
    ha_path = f"/api/services/{domain}/{service}"

    if dry_run:
        log_operation(
            event_type="ha_call",
            source="system",
            action="ha.call.dry_run",
            trace_id=trace_id,
            success=True,
            detail={
                "tool_name": tool_name,
                "domain": domain,
                "service": service,
                "service_data": service_data,
            },
        )
        return ToolCallResponse(
            success=True,
            message="dry run only, no HA call executed",
            trace_id=trace_id,
            data={
                "tool_name": tool_name,
                "strategy": strategy,
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "dry_run": True,
            },
        )

    if not settings.HA_TOKEN:
        log_operation(
            event_type="ha_call",
            source="system",
            action="ha.call",
            trace_id=trace_id,
            success=False,
            detail={
                "tool_name": tool_name,
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "message": "HA token missing",
            },
        )
        return ToolCallResponse(success=False, message="HA token missing", trace_id=trace_id)

    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.HA_TIMEOUT_SEC) as client:
            response = await client.post(
                f"{settings.HA_BASE_URL}{ha_path}",
                headers=auth_headers(),
                json=service_data,
            )

        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="POST",
            path=ha_path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            trace_id=trace_id,
            success=response.status_code < 400,
            detail={
                "context": "tool_call",
                "tool_name": tool_name,
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                    "json": service_data,
                },
            },
        )

        if response.status_code >= 400:
            response_text = response.text.strip()
            log_operation(
                event_type="ha_call",
                source="system",
                action="ha.call",
                trace_id=trace_id,
                success=False,
                detail={
                    "tool_name": tool_name,
                    "domain": domain,
                    "service": service,
                    "service_data": service_data,
                    "ha_status": response.status_code,
                    "ha_body": response_text,
                },
            )
            return ToolCallResponse(
                success=False,
                message=f"HA call failed: {response.status_code} {response_text}",
                trace_id=trace_id,
                data={
                    "tool_name": tool_name,
                    "domain": domain,
                    "service": service,
                    "service_data": service_data,
                    "ha_status": response.status_code,
                    "ha_body": response_text,
                },
            )

        payload = response.json() if response.content else {}
        log_operation(
            event_type="ha_call",
            source="system",
            action="ha.call",
            trace_id=trace_id,
            success=True,
            detail={
                "tool_name": tool_name,
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "ha_status": response.status_code,
            },
        )
        return ToolCallResponse(
            success=True,
            message="HA call succeeded",
            trace_id=trace_id,
            data={
                "tool_name": tool_name,
                "strategy": strategy,
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "ha_response": payload,
            },
        )
    except Exception as ex:
        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="POST",
            path=ha_path,
            status_code=0,
            duration_ms=duration_ms,
            trace_id=trace_id,
            success=False,
            detail={
                "context": "tool_call",
                "tool_name": tool_name,
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                    "json": service_data,
                },
                "message": str(ex),
            },
        )
        log_operation(
            event_type="ha_call",
            source="system",
            action="ha.call",
            trace_id=trace_id,
            success=False,
            detail={
                "tool_name": tool_name,
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "message": str(ex),
            },
        )
        return ToolCallResponse(success=False, message=f"HA bridge error: {ex}", trace_id=trace_id)


async def execute_tool_call(req: ToolCallRequest) -> ToolCallResponse:
    item = get_tool_or_raise(req.tool_name)
    service_data = resolve_service_data(item, req.arguments)
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


async def build_context_summary() -> dict[str, Any]:
    catalog = get_catalog_snapshot()
    known_entities = build_known_entities()
    entity_ids = flatten_known_entity_ids(known_entities)

    enabled_tools = sorted(
        (item.model_dump(mode="json") for item in catalog.values() if item.enabled),
        key=lambda item: item["tool_name"],
    )

    context: dict[str, Any] = {
        "ha_base_url": settings.HA_BASE_URL,
        "ha_connected": False,
        "tool_catalog": enabled_tools,
        "known_entities": known_entities,
        "entity_states": {},
        "ha_services": [],
    }

    if not settings.HA_TOKEN:
        context["message"] = "HA token missing"
        return context

    services_task = fetch_ha_services()
    states_task = fetch_entity_states(entity_ids)
    services_result, states_result = await asyncio.gather(services_task, states_task)

    if services_result.get("ok") and states_result.get("ok"):
        context["ha_connected"] = True
    context["ha_services"] = services_result.get("data", [])
    context["entity_states"] = states_result.get("data", {})

    errors = [x for x in [services_result.get("error"), states_result.get("error")] if x]
    if errors:
        context["errors"] = errors

    return context


def resolve_service_data(item: ToolCatalogItem, arguments: dict[str, Any]) -> dict[str, Any]:
    merged = dict(item.default_arguments)
    merged.update(arguments)

    if item.strategy == "passthrough":
        return merged

    if item.strategy == "light_area":
        entity_id = resolve_area_entity("light", merged)
        return {"entity_id": entity_id}

    if item.strategy == "cover_area":
        entity_id = resolve_area_entity("cover", merged)
        return {"entity_id": entity_id}

    if item.strategy == "scene_id":
        scene_id = str(merged.get("scene_id", "")).strip()
        if not scene_id:
            raise HTTPException(status_code=400, detail="scene_id is required")
        return {"entity_id": scene_id}

    if item.strategy == "climate_area":
        entity_id = resolve_area_entity("climate", merged)
        return {"entity_id": entity_id}

    if item.strategy == "climate_area_temperature":
        entity_id = resolve_area_entity("climate", merged)
        temp_raw = merged.get("temperature")
        if temp_raw is None:
            raise HTTPException(status_code=400, detail="temperature is required")
        try:
            temperature = int(temp_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="temperature must be an integer")
        if not 16 <= temperature <= 30:
            raise HTTPException(status_code=400, detail="temperature must be between 16 and 30")
        return {"entity_id": entity_id, "temperature": temperature}

    raise HTTPException(status_code=400, detail=f"unsupported strategy: {item.strategy}")


def resolve_domain(item: ToolCatalogItem, service_data: dict[str, Any]) -> str:
    if item.domain not in {"", "auto", "entity"}:
        return item.domain
    return infer_domain_from_entity(service_data.get("entity_id"), fallback="homeassistant")


def resolve_area_entity(entity_type: str, merged_args: dict[str, Any]) -> str | list[str]:
    explicit = parse_entity_ids(merged_args.get("entity_id"))
    if explicit is not None:
        return explicit

    area = str(merged_args.get("area", "living_room")).strip().lower()
    area_map = extract_area_entity_map(merged_args.get("area_entity_map"), entity_type=entity_type)
    if not area_map:
        raw_area_map = settings.AREA_ENTITY_MAP.get(entity_type, {})
        for key, value in raw_area_map.items():
            parsed = parse_entity_ids(value)
            if parsed is not None:
                area_map[str(key).strip().lower()] = parsed

    raw_entity: Any = area_map.get(area)
    if raw_entity is None:
        raw_entity = area_map.get("living_room")
    if raw_entity is None:
        for candidate in area_map.values():
            parsed_candidate = parse_entity_ids(candidate)
            if parsed_candidate is not None:
                raw_entity = parsed_candidate
                break

    entity_id = parse_entity_ids(raw_entity)
    if entity_id is None:
        raise HTTPException(status_code=400, detail=f"{entity_type} entity is not configured for area: {area}")
    return entity_id


def extract_area_entity_map(raw: Any, *, entity_type: str) -> dict[str, str | list[str]]:
    if not isinstance(raw, dict):
        return {}

    source = raw
    nested = raw.get(entity_type)
    if isinstance(nested, dict):
        source = nested

    resolved: dict[str, str | list[str]] = {}
    for area, value in source.items():
        normalized_area = str(area).strip().lower()
        if not normalized_area:
            continue
        parsed = parse_entity_ids(value)
        if parsed is None:
            continue
        resolved[normalized_area] = parsed
    return resolved


def parse_entity_ids(raw: Any) -> str | list[str] | None:
    if raw is None:
        return None

    if isinstance(raw, (list, tuple, set)):
        parts: list[str] = []
        for item in raw:
            parsed = parse_entity_ids(item)
            if parsed is None:
                continue
            if isinstance(parsed, list):
                parts.extend(str(x).strip() for x in parsed if str(x).strip())
            else:
                value = str(parsed).strip()
                if value:
                    parts.append(value)

        deduped = list(dict.fromkeys(parts))
        if not deduped:
            return None
        if len(deduped) == 1:
            return deduped[0]
        return deduped

    value = str(raw).strip()
    if not value:
        return None
    if "," not in value:
        return value

    parts = [x.strip() for x in value.split(",") if x.strip()]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return parts


def infer_domain_from_entity(entity_id: Any, fallback: str) -> str:
    first = ""
    if isinstance(entity_id, list):
        if entity_id:
            first = str(entity_id[0])
    else:
        first = str(entity_id or "")

    if "." in first:
        return first.split(".", 1)[0]
    return fallback


def merge_entity_refs(
    left: str | list[str] | None,
    right: str | list[str] | None,
) -> str | list[str] | None:
    values: list[str] = []
    for raw in (left, right):
        parsed = parse_entity_ids(raw)
        if parsed is None:
            continue
        if isinstance(parsed, list):
            values.extend(str(item).strip() for item in parsed if str(item).strip())
        else:
            text = str(parsed).strip()
            if text:
                values.append(text)

    deduped = list(dict.fromkeys(values))
    if not deduped:
        return None
    if len(deduped) == 1:
        return deduped[0]
    return deduped


def strategy_to_entity_type(strategy: str) -> str | None:
    normalized = strategy.strip().lower()
    if normalized == "light_area":
        return "light"
    if normalized == "cover_area":
        return "cover"
    if normalized in {"climate_area", "climate_area_temperature"}:
        return "climate"
    return None


def build_known_entities() -> dict[str, dict[str, str | list[str] | None]]:
    known: dict[str, dict[str, str | list[str] | None]] = {}
    for entity_type, area_map in settings.AREA_ENTITY_MAP.items():
        known[entity_type] = {}
        for area, raw in area_map.items():
            parsed = parse_entity_ids(raw)
            if parsed is not None:
                known[entity_type][str(area).strip().lower()] = parsed

    catalog = get_catalog_snapshot()
    for item in catalog.values():
        if not item.enabled:
            continue
        entity_type = strategy_to_entity_type(item.strategy)
        if entity_type is None:
            continue
        area_map = extract_area_entity_map(item.default_arguments.get("area_entity_map"), entity_type=entity_type)
        if not area_map:
            continue
        bucket = known.setdefault(entity_type, {})
        for area, entity_ref in area_map.items():
            bucket[area] = merge_entity_refs(bucket.get(area), entity_ref)

    return known


def flatten_known_entity_ids(
    known_entities: dict[str, dict[str, str | list[str] | None]],
) -> list[str]:
    ids: list[str] = []
    for group in known_entities.values():
        for value in group.values():
            if isinstance(value, list):
                ids.extend(str(x) for x in value if str(x).strip())
            elif isinstance(value, str) and value.strip():
                ids.append(value.strip())

    return list(dict.fromkeys(ids))


async def fetch_ha_services() -> dict[str, Any]:
    ha_path = "/api/services"
    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.HA_CONTEXT_TIMEOUT_SEC) as client:
            resp = await client.get(f"{settings.HA_BASE_URL}{ha_path}", headers=auth_headers())

        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="GET",
            path=ha_path,
            status_code=resp.status_code,
            duration_ms=duration_ms,
            success=resp.status_code < 400,
            detail={
                "context": "summary.services",
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                },
            },
        )
        resp.raise_for_status()
        payload = resp.json() if resp.content else []

        services: list[dict[str, Any]] = []
        if isinstance(payload, list):
            for row in payload:
                if not isinstance(row, dict):
                    continue
                domain = str(row.get("domain", "")).strip()
                service_map = row.get("services", {})
                if not domain or not isinstance(service_map, dict):
                    continue
                services.append({"domain": domain, "services": sorted(service_map.keys())})

        services.sort(key=lambda x: x["domain"])
        return {"ok": True, "data": services}
    except Exception as ex:
        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="GET",
            path=ha_path,
            status_code=0,
            duration_ms=duration_ms,
            success=False,
            detail={
                "context": "summary.services",
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                },
                "message": str(ex),
            },
        )
        return {"ok": False, "error": f"fetch_ha_services_failed: {ex}", "data": []}


async def fetch_entity_states(entity_ids: list[str]) -> dict[str, Any]:
    if not entity_ids:
        return {"ok": True, "data": {}}

    async with httpx.AsyncClient(timeout=settings.HA_CONTEXT_TIMEOUT_SEC) as client:
        tasks = [fetch_single_entity_state(client, entity_id) for entity_id in entity_ids]
        results = await asyncio.gather(*tasks)

    states: dict[str, Any] = {}
    errors: list[str] = []

    for result in results:
        entity_id = result["entity_id"]
        if result["ok"]:
            states[entity_id] = result["data"]
        else:
            errors.append(result["error"])
            states[entity_id] = {"available": False, "error": result["error"]}

    if errors:
        return {"ok": False, "error": "; ".join(errors), "data": states}
    return {"ok": True, "data": states}


async def fetch_single_entity_state(client: httpx.AsyncClient, entity_id: str) -> dict[str, Any]:
    ha_path = f"/api/states/{entity_id}"
    started = perf_counter()
    try:
        resp = await client.get(f"{settings.HA_BASE_URL}{ha_path}", headers=auth_headers())
        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="GET",
            path=ha_path,
            status_code=resp.status_code,
            duration_ms=duration_ms,
            success=resp.status_code < 400,
            detail={
                "context": "summary.entity_state",
                "entity_id": entity_id,
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                },
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        attributes = payload.get("attributes", {}) if isinstance(payload, dict) else {}
        return {
            "ok": True,
            "entity_id": entity_id,
            "data": {
                "available": True,
                "state": payload.get("state"),
                "last_changed": payload.get("last_changed"),
                "last_updated": payload.get("last_updated"),
                "friendly_name": attributes.get("friendly_name"),
            },
        }
    except Exception as ex:
        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="GET",
            path=ha_path,
            status_code=0,
            duration_ms=duration_ms,
            success=False,
            detail={
                "context": "summary.entity_state",
                "entity_id": entity_id,
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                },
                "message": str(ex),
            },
        )
        return {"ok": False, "entity_id": entity_id, "error": str(ex)}


def _to_entity_id_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    parsed = parse_entity_ids(raw)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return [str(x).strip() for x in parsed if str(x).strip()]
    return [str(parsed).strip()]


def _build_config_area_map() -> dict[str, list[str]]:
    area_map: dict[str, list[str]] = {}
    known_entities = build_known_entities()
    for group in known_entities.values():
        for area, raw in group.items():
            ids = _to_entity_id_list(raw)
            if area not in area_map:
                area_map[area] = []
            area_map[area].extend(ids)

    for area in list(area_map.keys()):
        area_map[area] = list(dict.fromkeys(area_map[area]))
    return area_map


async def fetch_ha_states_raw() -> dict[str, Any]:
    if not settings.HA_TOKEN:
        return {"ok": False, "error": "HA token missing", "data": []}

    ha_path = "/api/states"
    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.HA_CONTEXT_TIMEOUT_SEC) as client:
            resp = await client.get(f"{settings.HA_BASE_URL}{ha_path}", headers=auth_headers())
        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="GET",
            path=ha_path,
            status_code=resp.status_code,
            duration_ms=duration_ms,
            success=resp.status_code < 400,
            detail={
                "context": "ha.states",
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                },
            },
        )
        resp.raise_for_status()
        payload = resp.json() if resp.content else []
        if not isinstance(payload, list):
            return {"ok": False, "error": "unexpected states payload", "data": []}
        return {"ok": True, "data": payload}
    except Exception as ex:
        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="GET",
            path=ha_path,
            status_code=0,
            duration_ms=duration_ms,
            success=False,
            detail={
                "context": "ha.states",
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                },
                "message": str(ex),
            },
        )
        return {"ok": False, "error": f"fetch_ha_states_failed: {ex}", "data": []}


async def render_ha_template_json(template: str, *, context: str) -> dict[str, Any]:
    if not settings.HA_TOKEN:
        return {"ok": False, "error": "HA token missing", "data": None}

    ha_path = "/api/template"
    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.HA_CONTEXT_TIMEOUT_SEC) as client:
            resp = await client.post(
                f"{settings.HA_BASE_URL}{ha_path}",
                headers=auth_headers(),
                json={"template": template},
            )
        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="POST",
            path=ha_path,
            status_code=resp.status_code,
            duration_ms=duration_ms,
            success=resp.status_code < 400,
            detail={
                "context": context,
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                    "json": {"template": template},
                },
            },
        )
        resp.raise_for_status()

        text = resp.text.strip()
        if not text:
            return {"ok": True, "data": None}
        try:
            parsed = json.loads(text)
            return {"ok": True, "data": parsed}
        except json.JSONDecodeError:
            return {"ok": True, "data": text}
    except Exception as ex:
        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="POST",
            path=ha_path,
            status_code=0,
            duration_ms=duration_ms,
            success=False,
            detail={
                "context": context,
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                    "json": {"template": template},
                },
                "message": str(ex),
            },
        )
        return {"ok": False, "error": f"render_ha_template_failed: {ex}", "data": None}


def _compact_state_row(row: dict[str, Any], include_attributes: bool) -> dict[str, Any]:
    entity_id = str(row.get("entity_id", "")).strip()
    attrs = row.get("attributes", {}) if isinstance(row.get("attributes", {}), dict) else {}
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""

    result: dict[str, Any] = {
        "entity_id": entity_id,
        "domain": domain,
        "state": row.get("state"),
        "friendly_name": attrs.get("friendly_name"),
        "device_class": attrs.get("device_class"),
        "unit_of_measurement": attrs.get("unit_of_measurement"),
        "last_changed": row.get("last_changed"),
        "last_updated": row.get("last_updated"),
    }
    if include_attributes:
        result["attributes"] = attrs
    return result


def _match_area(areas: list[dict[str, Any]], area_input: str) -> dict[str, Any] | None:
    normalized = area_input.strip().lower()
    for row in areas:
        area_id = str(row.get("area_id", "")).strip().lower()
        area_name = str(row.get("area_name", "")).strip().lower()
        if normalized in {area_id, area_name}:
            return row
    return None


async def get_ha_areas(include_state_validation: bool = True) -> dict[str, Any]:
    config_map = _build_config_area_map()
    template_result = await render_ha_template_json(AREA_CATALOG_TEMPLATE, context="ha.areas")
    errors: list[str] = []

    ha_area_rows: list[dict[str, Any]] = []
    if template_result.get("ok"):
        payload = template_result.get("data")
        if isinstance(payload, list):
            for row in payload:
                if not isinstance(row, dict):
                    continue
                area_id = str(row.get("area_id", "")).strip()
                if not area_id:
                    continue
                area_name = str(row.get("area_name", "")).strip() or area_id.replace("_", " ").title()
                ha_entities = _to_entity_id_list(row.get("entities"))
                ha_area_rows.append(
                    {
                        "area_id": area_id,
                        "area_name": area_name,
                        "ha_entities": ha_entities,
                    }
                )
        else:
            errors.append("ha areas template returned non-list payload")
    else:
        errors.append(str(template_result.get("error", "ha areas template failed")))

    area_index: dict[str, dict[str, Any]] = {}
    for row in ha_area_rows:
        area_index[row["area_id"]] = row

    for area_id, entities in config_map.items():
        if area_id not in area_index:
            area_index[area_id] = {
                "area_id": area_id,
                "area_name": area_id.replace("_", " ").title(),
                "ha_entities": [],
            }
        area_index[area_id]["configured_entities"] = entities

    state_ids: set[str] = set()
    state_error: str | None = None
    if include_state_validation:
        states_result = await fetch_ha_states_raw()
        if states_result.get("ok"):
            for row in states_result.get("data", []):
                if isinstance(row, dict):
                    entity_id = str(row.get("entity_id", "")).strip()
                    if entity_id:
                        state_ids.add(entity_id)
        else:
            state_error = str(states_result.get("error", "failed to fetch states"))
            errors.append(state_error)

    areas: list[dict[str, Any]] = []
    for area_id in sorted(area_index.keys()):
        row = area_index[area_id]
        ha_entities = _to_entity_id_list(row.get("ha_entities"))
        configured_entities = _to_entity_id_list(row.get("configured_entities"))
        merged_entities = list(dict.fromkeys(ha_entities + configured_entities))
        area_item: dict[str, Any] = {
            "area_id": area_id,
            "area_name": row.get("area_name", area_id.replace("_", " ").title()),
            "ha_entities": ha_entities,
            "configured_entities": configured_entities,
            "entities": merged_entities,
        }
        if include_state_validation:
            existing = [x for x in merged_entities if x in state_ids]
            missing = [x for x in merged_entities if x not in state_ids]
            area_item["existing_entities"] = existing
            area_item["missing_entities"] = missing
            area_item["existing_count"] = len(existing)
            area_item["missing_count"] = len(missing)
        areas.append(area_item)

    success = bool(areas) or template_result.get("ok")
    source = "ha_template+config_map" if template_result.get("ok") else "config_map"
    result: dict[str, Any] = {
        "success": success,
        "source": source,
        "area_count": len(areas),
        "areas": areas,
    }
    if errors:
        result["errors"] = errors
    return result


async def list_ha_entities(
    *,
    domain: str | None = None,
    area: str | None = None,
    q: str | None = None,
    limit: int = 500,
    include_attributes: bool = False,
) -> dict[str, Any]:
    states_result = await fetch_ha_states_raw()
    if not states_result.get("ok"):
        return {
            "success": False,
            "message": states_result.get("error", "failed to fetch ha states"),
            "entities": [],
            "returned": 0,
            "total": 0,
        }

    rows = [x for x in states_result.get("data", []) if isinstance(x, dict)]
    domain_filter = (domain or "").strip().lower()
    query_filter = (q or "").strip().lower()
    area_filter = (area or "").strip()

    area_entity_set: set[str] | None = None
    area_match: dict[str, Any] | None = None
    if area_filter:
        areas_result = await get_ha_areas(include_state_validation=False)
        area_rows = areas_result.get("areas", []) if isinstance(areas_result.get("areas"), list) else []
        area_match = _match_area(area_rows, area_filter)
        area_entity_set = set(area_match.get("entities", [])) if area_match else set()

    filtered: list[dict[str, Any]] = []
    for row in rows:
        entity_id = str(row.get("entity_id", "")).strip()
        if not entity_id:
            continue
        entity_domain = entity_id.split(".", 1)[0].lower() if "." in entity_id else ""
        attrs = row.get("attributes", {}) if isinstance(row.get("attributes", {}), dict) else {}
        friendly_name = str(attrs.get("friendly_name", "")).lower()
        state_text = str(row.get("state", "")).lower()

        if domain_filter and entity_domain != domain_filter:
            continue
        if area_entity_set is not None and entity_id not in area_entity_set:
            continue
        if query_filter and query_filter not in entity_id.lower() and query_filter not in friendly_name and query_filter not in state_text:
            continue

        filtered.append(row)

    filtered.sort(key=lambda x: str(x.get("entity_id", "")))
    total = len(filtered)
    safe_limit = max(1, min(limit, 2000))
    selected = filtered[:safe_limit]
    entities = [_compact_state_row(x, include_attributes=include_attributes) for x in selected]

    result: dict[str, Any] = {
        "success": True,
        "total": total,
        "returned": len(entities),
        "limit": safe_limit,
        "filters": {
            "domain": domain_filter or None,
            "area": area_filter or None,
            "query": query_filter or None,
        },
        "entities": entities,
    }
    if area_filter:
        result["area_match"] = area_match
    return result


async def get_ha_entity_state(entity_id: str, include_attributes: bool = True) -> dict[str, Any]:
    normalized = entity_id.strip()
    if not normalized:
        return {"success": False, "message": "entity_id is required"}
    if not settings.HA_TOKEN:
        return {"success": False, "message": "HA token missing"}

    ha_path = f"/api/states/{normalized}"
    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.HA_CONTEXT_TIMEOUT_SEC) as client:
            resp = await client.get(f"{settings.HA_BASE_URL}{ha_path}", headers=auth_headers())
        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="GET",
            path=ha_path,
            status_code=resp.status_code,
            duration_ms=duration_ms,
            success=resp.status_code < 400,
            detail={
                "context": "ha.entity.get",
                "entity_id": normalized,
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                },
            },
        )
        if resp.status_code == 404:
            return {"success": False, "message": "entity not found", "entity_id": normalized}
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
        if not isinstance(payload, dict):
            return {"success": False, "message": "unexpected entity payload", "entity_id": normalized}
        return {
            "success": True,
            "entity": _compact_state_row(payload, include_attributes=include_attributes),
        }
    except Exception as ex:
        duration_ms = round((perf_counter() - started) * 1000, 2)
        log_operation(
            event_type="ha_request",
            source="system",
            action="ha.request",
            method="GET",
            path=ha_path,
            status_code=0,
            duration_ms=duration_ms,
            success=False,
            detail={
                "context": "ha.entity.get",
                "entity_id": normalized,
                "request": {
                    "base_url": settings.HA_BASE_URL,
                    "path": ha_path,
                },
                "message": str(ex),
            },
        )
        return {"success": False, "message": f"get_ha_entity_state_failed: {ex}", "entity_id": normalized}


async def list_ha_services(domain: str | None = None) -> dict[str, Any]:
    result = await fetch_ha_services()
    if not result.get("ok"):
        return {
            "success": False,
            "message": result.get("error", "failed to fetch ha services"),
            "services": [],
            "count": 0,
        }

    services = result.get("data", []) if isinstance(result.get("data"), list) else []
    domain_filter = (domain or "").strip().lower()
    if domain_filter:
        services = [x for x in services if str(x.get("domain", "")).lower() == domain_filter]

    return {
        "success": True,
        "count": len(services),
        "domain": domain_filter or None,
        "services": services,
    }


async def get_ha_overview() -> dict[str, Any]:
    states_task = fetch_ha_states_raw()
    areas_task = get_ha_areas(include_state_validation=False)
    services_task = list_ha_services()
    states_result, areas_result, services_result = await asyncio.gather(states_task, areas_task, services_task)

    entities: list[dict[str, Any]] = []
    if states_result.get("ok"):
        rows = [x for x in states_result.get("data", []) if isinstance(x, dict)]
        entities = [_compact_state_row(x, include_attributes=False) for x in rows]

    domain_stats: dict[str, int] = {}
    for row in entities:
        domain = str(row.get("domain", "")).strip()
        if not domain:
            continue
        domain_stats[domain] = domain_stats.get(domain, 0) + 1

    top_domains = sorted(
        ({"domain": k, "count": v} for k, v in domain_stats.items()),
        key=lambda x: (-x["count"], x["domain"]),
    )[:20]

    return {
        "success": bool(states_result.get("ok")),
        "ha_connected": bool(states_result.get("ok")),
        "entity_count": len(entities),
        "area_count": int(areas_result.get("area_count", 0) or 0),
        "service_domain_count": int(services_result.get("count", 0) or 0),
        "top_domains": top_domains,
        "areas_source": areas_result.get("source"),
        "errors": [x for x in [states_result.get("error"), services_result.get("message")] if x],
    }
