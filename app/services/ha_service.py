import asyncio
import json
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from app.core import settings
from app.core.text_codec import EncodingNormalizationError, normalize_payload, normalize_text
from app.models.schemas import ToolCallRequest, ToolCallResponse, ToolCatalogItem
from app.services.catalog_service import get_catalog_snapshot, get_tool_or_raise
from app.services.config_service import auth_headers
from app.services.log_service import log_operation


AREA_CATALOG_TEMPLATE = """{% set out = namespace(items=[]) %}
{% for a in areas() %}
{% set out.items = out.items + [{'area_id': a, 'area_name': area_name(a), 'entities': area_entities(a)}] %}
{% endfor %}
{{ out.items | to_json }}"""

CANONICAL_AREA_ORDER = ["玄关", "厨房", "客厅", "主卧", "次卧", "餐厅", "书房", "卫生间", "走廊"]
AREA_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "玄关": ("玄关", "xuan_guan", "xuanguan", "entryway", "foyer"),
    "厨房": ("厨房", "kitchen", "chu_fang", "chufang"),
    "客厅": ("客厅", "living_room", "living room", "livingroom", "ke_ting", "keting"),
    "主卧": (
        "主卧",
        "主卧室",
        "bedroom",
        "master bedroom",
        "master_bedroom",
        "zhu_wo",
        "zhuwo",
    ),
    "次卧": (
        "次卧",
        "次卧室",
        "guest bedroom",
        "guest_bedroom",
        "second bedroom",
        "secondary bedroom",
        "ci_wo",
        "ciwo",
    ),
    "餐厅": ("餐厅", "dining room", "dining_room", "diningroom", "can_ting", "canting"),
    "书房": ("书房", "study", "shu_fang", "shufang"),
    "卫生间": ("卫生间", "浴室", "bathroom", "wc", "toilet", "wei_sheng_jian", "weishengjian"),
    "走廊": ("走廊", "corridor", "hallway", "zou_lang", "zoulang"),
}
AREA_CREATE_SEED: dict[str, str] = {
    "玄关": "xuan_guan",
    "厨房": "kitchen",
    "客厅": "living_room",
    "主卧": "master_bedroom",
    "次卧": "guest_bedroom",
    "餐厅": "dining_room",
    "书房": "study",
    "卫生间": "bathroom",
    "走廊": "corridor",
}

DEFAULT_AREA_AUDIT_DOMAINS = ("light", "switch", "climate", "cover", "fan")
DEFAULT_AREA_ASSIGN_MAX_UPDATES = 200
AREA_AUDIT_IGNORED_ENTITY_PREFIXES = (
    "switch.zigbee2mqtt_bridge_",
)
AREA_AUDIT_SUGGESTION_ALIASES: dict[str, tuple[str, ...]] = {
    "玄关": ("玄关", "xuan_guan", "xuanguan", "entryway", "foyer"),
    "厨房": ("厨房", "kitchen", "chu_fang", "chufang"),
    "客厅": ("客厅", "living_room", "living room", "livingroom", "ke_ting", "keting"),
    "主卧": (
        "主卧",
        "主卧室",
        "master bedroom",
        "master_bedroom",
        "bedroom",
        "zhu_wo",
        "zhuwo",
    ),
    "次卧": (
        "次卧",
        "次卧室",
        "guest bedroom",
        "guest_bedroom",
        "second bedroom",
        "ci_wo",
        "ciwo",
    ),
    "餐厅": ("餐厅", "dining room", "dining_room", "diningroom", "can_ting", "canting"),
    "书房": ("书房", "study", "shu_fang", "shufang"),
    "卫生间": ("卫生间", "浴室", "bathroom", "wc", "toilet", "wei_sheng_jian", "weishengjian"),
    "走廊": ("走廊", "corridor", "hallway", "zou_lang", "zoulang"),
}
ENTITY_TYPE_DOMAIN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "light": ("light", "switch"),
    "climate": ("climate",),
    "cover": ("cover",),
}
ALL_AREA_ALIASES = (
    "all",
    "all_areas",
    "all_rooms",
    "whole_home",
    "entire_home",
    "全部",
    "所有",
    "全屋",
    "全家",
    "整屋",
    "整个家",
)
LIGHT_SWITCH_HINTS = (
    "deng",
    "light",
    "lamp",
    "lighting",
    "zhao_ming",
    "zhaoming",
)
LIGHT_EXCLUDE_HINTS = (
    "indicator",
)


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
    trace_id = req.trace_id
    normalized_tool_name = _normalize_text_or_raise(req.tool_name, field_path="tool_name", trace_id=trace_id)
    normalized_arguments = _normalize_payload_or_raise(req.arguments, field_path="arguments", trace_id=trace_id)

    item = get_tool_or_raise(normalized_tool_name)
    if item.strategy == "area_sync":
        req = req.model_copy(update={"tool_name": normalized_tool_name, "arguments": normalized_arguments})
        return await _execute_area_sync_tool(req, item)
    if item.strategy == "area_audit":
        req = req.model_copy(update={"tool_name": normalized_tool_name, "arguments": normalized_arguments})
        return await _execute_area_audit_tool(req, item)
    if item.strategy == "area_assign":
        req = req.model_copy(update={"tool_name": normalized_tool_name, "arguments": normalized_arguments})
        return await _execute_area_assign_tool(req, item)

    service_data = await resolve_service_data(item, normalized_arguments)
    domain = resolve_domain(item, service_data)
    if item.strategy == "climate_area_temperature" and domain == "climate":
        return await execute_climate_temperature_with_retry(
            item=item,
            service_data=service_data,
            trace_id=req.trace_id,
            dry_run=req.dry_run,
        )
    return await execute_ha_service_call(
        tool_name=item.tool_name,
        strategy=item.strategy,
        domain=domain,
        service=item.service,
        service_data=service_data,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


async def execute_climate_temperature_with_retry(
    *,
    item: ToolCatalogItem,
    service_data: dict[str, Any],
    trace_id: str | None,
    dry_run: bool,
) -> ToolCallResponse:
    first = await execute_ha_service_call(
        tool_name=item.tool_name,
        strategy=item.strategy,
        domain="climate",
        service=item.service,
        service_data=service_data,
        trace_id=trace_id,
        dry_run=dry_run,
    )
    if dry_run or first.success or not should_retry_climate_temperature(first):
        return first

    entity_id = service_data.get("entity_id")
    wake_response = await execute_ha_service_call(
        tool_name=f"{item.tool_name}.turn_on_retry",
        strategy=item.strategy,
        domain="climate",
        service="turn_on",
        service_data={"entity_id": entity_id},
        trace_id=trace_id,
        dry_run=dry_run,
    )
    if not wake_response.success:
        return ToolCallResponse(
            success=False,
            message=f"{first.message}; auto_turn_on_failed: {wake_response.message}",
            trace_id=trace_id,
            data={
                "first_attempt": first.data,
                "turn_on_attempt": wake_response.data,
            },
        )

    await asyncio.sleep(0.5)
    retry = await execute_ha_service_call(
        tool_name=item.tool_name,
        strategy=item.strategy,
        domain="climate",
        service=item.service,
        service_data=service_data,
        trace_id=trace_id,
        dry_run=dry_run,
    )
    if retry.success:
        merged = dict(retry.data or {})
        merged["retry_after_turn_on"] = True
        merged["turn_on_attempt"] = wake_response.data
        return ToolCallResponse(
            success=True,
            message="HA call succeeded after turn_on retry",
            trace_id=trace_id,
            data=merged,
        )

    return ToolCallResponse(
        success=False,
        message=f"{retry.message}; retry_after_turn_on_failed",
        trace_id=trace_id,
        data={
            "first_attempt": first.data,
            "turn_on_attempt": wake_response.data,
            "retry_attempt": retry.data,
        },
    )


def should_retry_climate_temperature(response: ToolCallResponse) -> bool:
    if response.success:
        return False
    message = str(response.message or "").lower()
    if "ha call failed: 500" in message:
        return True
    # Some integrations may return transient bridge/network errors when HVAC is off.
    if "ha bridge error" in message:
        return True
    return False


def _encoding_error_to_http(ex: EncodingNormalizationError) -> HTTPException:
    return HTTPException(status_code=400, detail=ex.to_error_detail())


def _sample_text(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 80:
        return text
    return f"{text[:77]}..."


def _log_encoding_repair(
    *,
    trace_id: str | None,
    field_path: str,
    original: Any,
    normalized: Any,
) -> None:
    if original == normalized:
        return
    log_operation(
        event_type="text_encoding",
        source="system",
        action="text.encoding.repair",
        trace_id=trace_id,
        success=True,
        detail={
            "field": field_path,
            "before_sample": _sample_text(original),
            "after_sample": _sample_text(normalized),
        },
    )


def _log_encoding_reject(*, trace_id: str | None, ex: EncodingNormalizationError) -> None:
    log_operation(
        event_type="text_encoding",
        source="system",
        action="text.encoding.reject",
        trace_id=trace_id,
        success=False,
        detail=ex.to_error_detail(),
    )


def _normalize_text_or_raise(value: Any, *, field_path: str, trace_id: str | None) -> str:
    raw = str(value or "")
    try:
        normalized = normalize_text(raw, field_path=field_path, strict=settings.TEXT_ENCODING_STRICT)
    except EncodingNormalizationError as ex:
        _log_encoding_reject(trace_id=trace_id, ex=ex)
        raise _encoding_error_to_http(ex) from ex
    _log_encoding_repair(trace_id=trace_id, field_path=field_path, original=raw, normalized=normalized)
    return normalized


def _normalize_payload_or_raise(value: Any, *, field_path: str, trace_id: str | None) -> Any:
    try:
        normalized = normalize_payload(value, field_path=field_path, strict=settings.TEXT_ENCODING_STRICT)
    except EncodingNormalizationError as ex:
        _log_encoding_reject(trace_id=trace_id, ex=ex)
        raise _encoding_error_to_http(ex) from ex
    _log_encoding_repair(trace_id=trace_id, field_path=field_path, original=value, normalized=normalized)
    return normalized


def _string_or_empty(raw: Any) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def _normalize_area_label(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    return text.replace(" ", "").replace("_", "").replace("-", "").replace("/", "")


def _canonical_area_name(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    normalized = _normalize_area_label(text)
    for name, aliases in AREA_NAME_ALIASES.items():
        alias_norm = {_normalize_area_label(alias) for alias in aliases}
        if normalized in alias_norm:
            return name
    return text


def _iter_area_lookup_candidates(raw: Any) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return ["living_room", "客厅"]

    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        candidate = str(value or "").strip()
        key = _normalize_area_label(candidate)
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append(candidate)

    add(text)
    canonical = _canonical_area_name(text)
    if canonical:
        add(canonical)
        seed = AREA_CREATE_SEED.get(canonical)
        if seed:
            add(seed)
        for alias in AREA_NAME_ALIASES.get(canonical, ()):
            add(alias)

    return candidates


def _is_all_area_request(raw: Any) -> bool:
    normalized = _normalize_area_label(raw)
    if not normalized:
        return False
    return normalized in {_normalize_area_label(alias) for alias in ALL_AREA_ALIASES}


def _parse_area_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",") if item.strip()]
        return values
    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _find_entity_for_area_map(
    area_map: dict[str, str | list[str]],
    *,
    area_candidates: list[str],
) -> str | list[str] | None:
    if not area_map:
        return None

    normalized_map: dict[str, str | list[str]] = {}
    for key, value in area_map.items():
        normalized = _normalize_area_label(key)
        if not normalized:
            continue
        normalized_map[normalized] = value

    for area in area_candidates:
        normalized = _normalize_area_label(area)
        if not normalized:
            continue
        raw_entity = normalized_map.get(normalized)
        parsed = parse_entity_ids(raw_entity)
        if parsed is not None:
            return parsed
    return None


def _collect_area_match_tokens(*, area_id: str, area_name: str) -> set[str]:
    tokens: set[str] = set()
    for value in (area_id, area_name):
        normalized = _normalize_area_label(value)
        if normalized:
            tokens.add(normalized)

    canonical = _canonical_area_name(area_name) or _canonical_area_name(area_id)
    if canonical:
        canonical_norm = _normalize_area_label(canonical)
        if canonical_norm:
            tokens.add(canonical_norm)
        seed = AREA_CREATE_SEED.get(canonical)
        if seed:
            seed_norm = _normalize_area_label(seed)
            if seed_norm:
                tokens.add(seed_norm)
        for alias in AREA_NAME_ALIASES.get(canonical, ()):
            alias_norm = _normalize_area_label(alias)
            if alias_norm:
                tokens.add(alias_norm)

    return tokens


def _filter_entities_by_type(entity_ids: list[str], *, entity_type: str) -> list[str]:
    allowed_domains = set(ENTITY_TYPE_DOMAIN_CANDIDATES.get(entity_type, (entity_type,)))
    filtered: list[str] = []
    for entity_id in entity_ids:
        value = str(entity_id).strip()
        if "." not in value:
            continue
        domain = value.split(".", 1)[0].strip().lower()
        if domain in allowed_domains:
            normalized = _normalize_area_label(value)
            if entity_type == "light" and domain == "switch":
                if not any(token in normalized for token in LIGHT_SWITCH_HINTS):
                    continue
            if entity_type == "light" and any(token in normalized for token in LIGHT_EXCLUDE_HINTS):
                continue
            filtered.append(value)
    return list(dict.fromkeys(filtered))


async def _resolve_entities_from_ha_area(
    *,
    entity_type: str,
    area_candidates: list[str],
) -> str | list[str] | None:
    candidate_norms = {_normalize_area_label(item) for item in area_candidates if _normalize_area_label(item)}
    if not candidate_norms:
        return None

    try:
        areas_result = await get_ha_areas(include_state_validation=False)
    except Exception:
        return None

    rows = areas_result.get("areas", [])
    if not isinstance(rows, list):
        return None

    for row in rows:
        if not isinstance(row, dict):
            continue
        area_id = str(row.get("area_id", "")).strip()
        area_name = str(row.get("area_name", "")).strip() or area_id
        if not area_id:
            continue
        row_tokens = _collect_area_match_tokens(area_id=area_id, area_name=area_name)
        if not row_tokens.intersection(candidate_norms):
            continue

        source = row.get("ha_entities")
        if not isinstance(source, list) or not source:
            source = row.get("entities")
        entity_ids = _to_entity_id_list(source)
        matched = _filter_entities_by_type(entity_ids, entity_type=entity_type)
        parsed = parse_entity_ids(matched)
        if parsed is not None:
            return parsed

    return None


async def _resolve_all_entities_from_ha(
    entity_type: str,
    *,
    exclude_areas: list[str] | None = None,
) -> str | list[str] | None:
    excluded_norms: set[str] = set()
    for area in exclude_areas or []:
        for candidate in _iter_area_lookup_candidates(area):
            normalized = _normalize_area_label(candidate)
            if normalized:
                excluded_norms.add(normalized)

    included_from_areas: list[str] = []
    assigned_all: set[str] = set()
    try:
        areas_result = await get_ha_areas(include_state_validation=False)
        area_rows = areas_result.get("areas", [])
        if isinstance(area_rows, list):
            for row in area_rows:
                if not isinstance(row, dict):
                    continue
                area_id = str(row.get("area_id", "")).strip()
                area_name = str(row.get("area_name", "")).strip() or area_id
                if not area_id:
                    continue
                source = row.get("ha_entities")
                if not isinstance(source, list) or not source:
                    source = row.get("entities")
                entity_ids = _to_entity_id_list(source)
                matched = _filter_entities_by_type(entity_ids, entity_type=entity_type)
                for entity_id in matched:
                    assigned_all.add(entity_id)
                if excluded_norms:
                    row_tokens = _collect_area_match_tokens(area_id=area_id, area_name=area_name)
                    if row_tokens.intersection(excluded_norms):
                        continue
                included_from_areas.extend(matched)
    except Exception:
        included_from_areas = []
        assigned_all = set()

    states_result = await fetch_ha_states_raw()
    if not states_result.get("ok"):
        return None

    rows = states_result.get("data", [])
    if not isinstance(rows, list):
        return None

    all_from_states: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        entity_id = str(row.get("entity_id", "")).strip()
        if entity_id:
            all_from_states.append(entity_id)

    matched_all = _filter_entities_by_type(all_from_states, entity_type=entity_type)
    unassigned = [entity_id for entity_id in matched_all if entity_id not in assigned_all]
    merged = list(dict.fromkeys(included_from_areas + unassigned))
    if merged:
        return parse_entity_ids(merged)
    return parse_entity_ids(matched_all)


def _normalize_target_areas(raw: Any) -> list[str]:
    values: list[str] = []
    if isinstance(raw, str):
        values = [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]
    elif isinstance(raw, (list, tuple, set)):
        values = [str(item).strip() for item in raw if str(item).strip()]

    if not values:
        values = list(CANONICAL_AREA_ORDER)

    canonical = [_canonical_area_name(item) for item in values if _canonical_area_name(item)]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in canonical:
        key = _normalize_area_label(item)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _as_bool(raw: Any, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _normalize_domains(raw: Any) -> list[str]:
    values: list[str] = []
    if isinstance(raw, str):
        values = [item.strip().lower() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple, set)):
        values = [str(item).strip().lower() for item in raw if str(item).strip()]
    deduped = list(dict.fromkeys(values))
    if deduped:
        return deduped
    return list(DEFAULT_AREA_AUDIT_DOMAINS)


def _is_area_audit_ignored_entity(entity_id: str) -> bool:
    normalized = str(entity_id or "").strip().lower()
    if not normalized:
        return False
    return any(normalized.startswith(prefix) for prefix in AREA_AUDIT_IGNORED_ENTITY_PREFIXES)


def _to_clamped_int(raw: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _build_area_suggestion_tokens(area_name: str) -> set[str]:
    tokens: set[str] = set()
    normalized_area = _normalize_area_label(area_name)
    if normalized_area:
        tokens.add(normalized_area)

    lower = str(area_name or "").strip().lower()
    for chunk in lower.replace("-", " ").replace("/", " ").replace("_", " ").split():
        normalized = _normalize_area_label(chunk)
        if len(normalized) >= 2:
            tokens.add(normalized)

    for canonical, aliases in AREA_AUDIT_SUGGESTION_ALIASES.items():
        alias_tokens = {_normalize_area_label(canonical)}
        alias_tokens.update(_normalize_area_label(alias) for alias in aliases)
        if normalized_area in alias_tokens:
            tokens.update(token for token in alias_tokens if len(token) >= 2)
            break

    return {token for token in tokens if len(token) >= 2}


def _suggest_area_for_entity(
    *,
    entity_id: str,
    friendly_name: str,
    target_areas: list[str],
) -> tuple[str | None, str | None]:
    corpus = _normalize_area_label(f"{friendly_name} {entity_id}")
    if not corpus:
        return None, None

    best_area: str | None = None
    best_token: str | None = None
    best_score = 0
    best_token_len = 0

    for area_name in target_areas:
        tokens = sorted(_build_area_suggestion_tokens(area_name), key=len, reverse=True)
        matched = [token for token in tokens if token and token in corpus]
        if not matched:
            continue
        score = sum(len(token) for token in matched)
        token_len = len(matched[0])
        if score > best_score or (score == best_score and token_len > best_token_len):
            best_area = area_name
            best_token = matched[0]
            best_score = score
            best_token_len = token_len

    return best_area, best_token


def _ha_websocket_url(base_url: str) -> str:
    parsed = urlparse((base_url or "").strip().rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    netloc = parsed.netloc or parsed.path
    return f"{scheme}://{netloc}/api/websocket"


async def _ha_ws_send_command(ws: Any, request_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    message = dict(payload)
    message["id"] = request_id
    await ws.send(json.dumps(message, ensure_ascii=False))
    while True:
        raw = await ws.recv()
        data = json.loads(raw)
        if data.get("type") == "event":
            continue
        if data.get("id") == request_id:
            return data


def _area_rows_from_ws_list(raw_rows: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(raw_rows, list):
        return rows
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        area_id = str(row.get("area_id", "")).strip()
        if not area_id:
            continue
        name = str(row.get("name", "")).strip() or area_id
        rows.append({"area_id": area_id, "name": name})
    return rows


def _find_area_by_name(rows: list[dict[str, str]], target_name: str, *, used_ids: set[str]) -> dict[str, str] | None:
    target_norm = _normalize_area_label(target_name)
    for row in rows:
        if row["area_id"] in used_ids:
            continue
        if _normalize_area_label(row["name"]) == target_norm:
            return row
    return None


def _find_area_by_alias(rows: list[dict[str, str]], target_name: str, *, used_ids: set[str]) -> dict[str, str] | None:
    aliases = AREA_NAME_ALIASES.get(target_name, ())
    alias_norm = {_normalize_area_label(item) for item in aliases}
    if not alias_norm:
        return None
    for row in rows:
        if row["area_id"] in used_ids:
            continue
        if _normalize_area_label(row["name"]) in alias_norm:
            return row
        if _normalize_area_label(row["area_id"]) in alias_norm:
            return row
    return None


async def _build_area_entity_count() -> dict[str, int]:
    result = await get_ha_areas(include_state_validation=False)
    rows = result.get("areas", [])
    if not isinstance(rows, list):
        return {}

    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        area_id = str(row.get("area_id", "")).strip()
        if not area_id:
            continue
        ha_entities = row.get("ha_entities")
        entities = row.get("entities")
        source = ha_entities if isinstance(ha_entities, list) and ha_entities else entities
        if not isinstance(source, list):
            counts[area_id] = 0
            continue
        counts[area_id] = len([item for item in source if isinstance(item, str) and item.strip()])
    return counts


def _build_area_lookup(area_rows: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in area_rows:
        if not isinstance(row, dict):
            continue
        area_id = str(row.get("area_id", "")).strip()
        area_name = str(row.get("area_name", "")).strip()
        if not area_id:
            continue
        lookup[_normalize_area_label(area_id)] = area_id
        if area_name:
            lookup[_normalize_area_label(area_name)] = area_id
            canonical = _canonical_area_name(area_name)
            if canonical:
                lookup[_normalize_area_label(canonical)] = area_id
                for alias in AREA_NAME_ALIASES.get(canonical, ()):
                    lookup[_normalize_area_label(alias)] = area_id
    return lookup


def _resolve_area_id_from_lookup(area_lookup: dict[str, str], area_input: Any) -> str | None:
    for candidate in _iter_area_lookup_candidates(area_input):
        area_id = area_lookup.get(_normalize_area_label(candidate))
        if area_id:
            return area_id
    return None


async def _execute_area_sync_tool(req: ToolCallRequest, item: ToolCatalogItem) -> ToolCallResponse:
    merged = dict(item.default_arguments)
    merged.update(req.arguments or {})
    merged = _normalize_payload_or_raise(merged, field_path="arguments", trace_id=req.trace_id)
    target_areas = merged.get("target_areas", [])
    delete_unused = _as_bool(merged.get("delete_unused"), True)
    force_delete_in_use = _as_bool(merged.get("force_delete_in_use"), False)
    return await sync_ha_areas(
        target_areas=target_areas,
        delete_unused=delete_unused,
        force_delete_in_use=force_delete_in_use,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


async def _execute_area_audit_tool(req: ToolCallRequest, item: ToolCatalogItem) -> ToolCallResponse:
    merged = dict(item.default_arguments)
    merged.update(req.arguments or {})
    merged = _normalize_payload_or_raise(merged, field_path="arguments", trace_id=req.trace_id)
    target_areas = merged.get("target_areas", [])
    domains = merged.get("domains", [])
    include_unavailable = _as_bool(merged.get("include_unavailable"), False)
    return await audit_ha_areas(
        target_areas=target_areas,
        domains=domains,
        include_unavailable=include_unavailable,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


async def _execute_area_assign_tool(req: ToolCallRequest, item: ToolCatalogItem) -> ToolCallResponse:
    merged = dict(item.default_arguments)
    merged.update(req.arguments or {})
    merged = _normalize_payload_or_raise(merged, field_path="arguments", trace_id=req.trace_id)
    target_areas = merged.get("target_areas", [])
    domains = merged.get("domains", [])
    include_unavailable = _as_bool(merged.get("include_unavailable"), False)
    only_with_suggestion = _as_bool(merged.get("only_with_suggestion"), True)
    max_updates = _to_clamped_int(
        merged.get("max_updates"),
        default=DEFAULT_AREA_ASSIGN_MAX_UPDATES,
        minimum=1,
        maximum=2000,
    )
    return await assign_ha_areas(
        target_areas=target_areas,
        domains=domains,
        include_unavailable=include_unavailable,
        only_with_suggestion=only_with_suggestion,
        max_updates=max_updates,
        trace_id=req.trace_id,
        dry_run=req.dry_run,
    )


async def assign_ha_areas(
    *,
    target_areas: list[str],
    domains: list[str] | str | None = None,
    include_unavailable: bool = False,
    only_with_suggestion: bool = True,
    max_updates: int = DEFAULT_AREA_ASSIGN_MAX_UPDATES,
    trace_id: str | None = None,
    dry_run: bool = False,
) -> ToolCallResponse:
    normalized_target_input = _normalize_payload_or_raise(target_areas, field_path="target_areas", trace_id=trace_id)
    normalized_domain_input = _normalize_payload_or_raise(domains, field_path="domains", trace_id=trace_id)
    normalized_targets = _normalize_target_areas(normalized_target_input)
    if not normalized_targets:
        return ToolCallResponse(success=False, message="target_areas is required", trace_id=trace_id)

    if not settings.HA_TOKEN:
        return ToolCallResponse(success=False, message="HA token missing", trace_id=trace_id)

    normalized_domains = _normalize_domains(normalized_domain_input)
    safe_max_updates = _to_clamped_int(max_updates, default=DEFAULT_AREA_ASSIGN_MAX_UPDATES, minimum=1, maximum=2000)

    # Run audit first to get unassigned candidates and suggestions.
    audit_result = await audit_ha_areas(
        target_areas=normalized_targets,
        domains=normalized_domains,
        include_unavailable=include_unavailable,
        trace_id=trace_id,
        dry_run=True,
    )
    audit_detail = audit_result.data if isinstance(audit_result.data, dict) else {}
    unassigned_rows = audit_detail.get("unassigned_entities", [])
    if not isinstance(unassigned_rows, list):
        unassigned_rows = []

    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in unassigned_rows:
        if not isinstance(row, dict):
            continue
        entity_id = str(row.get("entity_id", "")).strip()
        suggested_area = _string_or_empty(row.get("suggested_area"))
        if not entity_id:
            continue
        if only_with_suggestion and not suggested_area:
            skipped.append(
                {
                    "entity_id": entity_id,
                    "reason": "no_suggestion",
                    "suggested_area": None,
                }
            )
            continue
        candidates.append(
            {
                "entity_id": entity_id,
                "suggested_area": suggested_area or None,
                "domain": row.get("domain"),
                "reason": row.get("reason"),
            }
        )

    if safe_max_updates < len(candidates):
        for row in candidates[safe_max_updates:]:
            skipped.append(
                {
                    "entity_id": row.get("entity_id"),
                    "reason": "limit_exceeded",
                    "suggested_area": row.get("suggested_area"),
                }
            )
        candidates = candidates[:safe_max_updates]

    areas_result = await get_ha_areas(include_state_validation=False)
    area_rows = areas_result.get("areas", []) if isinstance(areas_result.get("areas"), list) else []
    area_lookup = _build_area_lookup(area_rows)

    if not candidates:
        detail = {
            "target_areas": normalized_targets,
            "domains": sorted(set(normalized_domains)),
            "include_unavailable": include_unavailable,
            "only_with_suggestion": only_with_suggestion,
            "max_updates": safe_max_updates,
            "dry_run": dry_run,
            "candidate_count": 0,
            "planned_count": 0,
            "updated_count": 0,
            "failed_count": 0,
            "skipped_count": len(skipped),
            "planned_updates": [],
            "updated": [],
            "failed": [],
            "skipped": skipped,
            "errors": [],
            "audit": {
                "unassigned_entity_count": audit_detail.get("unassigned_entity_count"),
                "suggested_assignment_count": audit_detail.get("suggested_assignment_count"),
                "scanned_entity_count": audit_detail.get("scanned_entity_count"),
            },
        }
        log_operation(
            event_type="ha_area_assign",
            source="system",
            action="ha.areas.assign",
            trace_id=trace_id,
            success=True,
            detail=detail,
        )
        return ToolCallResponse(success=True, message="no area assignment needed", trace_id=trace_id, data=detail)

    try:
        import websockets
    except Exception as ex:
        return ToolCallResponse(success=False, message=f"websocket dependency missing: {ex}", trace_id=trace_id)

    started = perf_counter()
    planned_updates: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        async with websockets.connect(
            _ha_websocket_url(settings.HA_BASE_URL),
            open_timeout=settings.HA_CONTEXT_TIMEOUT_SEC,
            close_timeout=5,
            max_size=4_000_000,
        ) as ws:
            first = json.loads(await ws.recv())
            if first.get("type") != "auth_required":
                return ToolCallResponse(
                    success=False,
                    message=f"unexpected websocket handshake: {first.get('type')}",
                    trace_id=trace_id,
                )

            await ws.send(json.dumps({"type": "auth", "access_token": settings.HA_TOKEN}, ensure_ascii=False))
            second = json.loads(await ws.recv())
            if second.get("type") != "auth_ok":
                return ToolCallResponse(
                    success=False,
                    message=f"websocket auth failed: {second}",
                    trace_id=trace_id,
                )

            request_id = 1
            entity_registry_resp = await _ha_ws_send_command(ws, request_id, {"type": "config/entity_registry/list"})
            request_id += 1
            registry_rows = entity_registry_resp.get("result", [])
            registry_by_entity: dict[str, dict[str, Any]] = {}
            if isinstance(registry_rows, list):
                for row in registry_rows:
                    if not isinstance(row, dict):
                        continue
                    entity_id = str(row.get("entity_id", "")).strip()
                    if entity_id:
                        registry_by_entity[entity_id] = row

            for row in candidates:
                entity_id = str(row.get("entity_id", "")).strip()
                suggested_area = _string_or_empty(row.get("suggested_area"))
                if not entity_id:
                    continue
                if not suggested_area:
                    skipped.append({"entity_id": entity_id, "reason": "no_suggestion", "suggested_area": None})
                    continue

                area_id = area_lookup.get(_normalize_area_label(suggested_area))
                if not area_id:
                    skipped.append(
                        {
                            "entity_id": entity_id,
                            "reason": "suggested_area_not_found",
                            "suggested_area": suggested_area,
                        }
                    )
                    continue

                registry_row = registry_by_entity.get(entity_id, {})
                current_area_id = str(registry_row.get("area_id") or "").strip() if isinstance(registry_row, dict) else ""
                if current_area_id and _normalize_area_label(current_area_id) == _normalize_area_label(area_id):
                    skipped.append(
                        {
                            "entity_id": entity_id,
                            "reason": "already_assigned",
                            "suggested_area": suggested_area,
                            "area_id": area_id,
                        }
                    )
                    continue

                planned_item = {
                    "entity_id": entity_id,
                    "area_id": area_id,
                    "area_name": suggested_area,
                    "from_area_id": current_area_id or None,
                }
                planned_updates.append(planned_item)

                if dry_run:
                    continue

                update_resp = await _ha_ws_send_command(
                    ws,
                    request_id,
                    {
                        "type": "config/entity_registry/update",
                        "entity_id": entity_id,
                        "area_id": area_id,
                    },
                )
                request_id += 1
                if update_resp.get("success"):
                    updated.append(planned_item)
                    if isinstance(registry_row, dict):
                        registry_row["area_id"] = area_id
                    continue

                error_message = str(update_resp.get("error", {}).get("message", "unknown")).strip() or "unknown"
                failed.append(
                    {
                        "entity_id": entity_id,
                        "area_id": area_id,
                        "area_name": suggested_area,
                        "error": error_message,
                    }
                )
                errors.append(f"assign_failed:{entity_id}:{error_message}")
    except Exception as ex:
        errors.append(str(ex))

    duration_ms = round((perf_counter() - started) * 1000, 2)
    success = not errors
    detail = {
        "target_areas": normalized_targets,
        "domains": sorted(set(normalized_domains)),
        "include_unavailable": include_unavailable,
        "only_with_suggestion": only_with_suggestion,
        "max_updates": safe_max_updates,
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "planned_count": len(planned_updates),
        "updated_count": len(updated),
        "failed_count": len(failed),
        "skipped_count": len(skipped),
        "planned_updates": planned_updates,
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "audit": {
            "unassigned_entity_count": audit_detail.get("unassigned_entity_count"),
            "suggested_assignment_count": audit_detail.get("suggested_assignment_count"),
            "scanned_entity_count": audit_detail.get("scanned_entity_count"),
        },
    }

    log_operation(
        event_type="ha_area_assign",
        source="system",
        action="ha.areas.assign",
        trace_id=trace_id,
        success=success,
        duration_ms=duration_ms,
        detail=detail,
    )

    message = "HA area assignment completed" if success else "HA area assignment completed with errors"
    return ToolCallResponse(success=success, message=message, trace_id=trace_id, data=detail)


async def reassign_ha_entities(
    *,
    assignments: list[dict[str, Any]],
    trace_id: str | None = None,
    dry_run: bool = False,
) -> ToolCallResponse:
    normalized_input = _normalize_payload_or_raise(assignments, field_path="assignments", trace_id=trace_id)
    if not isinstance(normalized_input, list) or not normalized_input:
        return ToolCallResponse(success=False, message="assignments is required", trace_id=trace_id)

    if not settings.HA_TOKEN:
        return ToolCallResponse(success=False, message="HA token missing", trace_id=trace_id)

    areas_result = await get_ha_areas(include_state_validation=False)
    area_rows = areas_result.get("areas", []) if isinstance(areas_result.get("areas"), list) else []
    area_lookup = _build_area_lookup(area_rows)

    normalized_assignments: list[dict[str, str]] = []
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[str] = []

    for row in normalized_input:
        if not isinstance(row, dict):
            continue
        entity_id = str(row.get("entity_id", "")).strip()
        area_raw = row.get("area")
        if not entity_id:
            failed.append({"entity_id": None, "area": area_raw, "error": "entity_id_required"})
            continue

        area_id = _resolve_area_id_from_lookup(area_lookup, area_raw)
        if not area_id:
            failed.append({"entity_id": entity_id, "area": area_raw, "error": "area_not_found"})
            continue

        normalized_assignments.append({"entity_id": entity_id, "area_id": area_id})

    if not normalized_assignments and not failed:
        return ToolCallResponse(success=False, message="no valid assignment payload", trace_id=trace_id)

    try:
        import websockets
    except Exception as ex:
        return ToolCallResponse(success=False, message=f"websocket dependency missing: {ex}", trace_id=trace_id)

    started = perf_counter()
    planned_updates: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []

    try:
        async with websockets.connect(
            _ha_websocket_url(settings.HA_BASE_URL),
            open_timeout=settings.HA_CONTEXT_TIMEOUT_SEC,
            close_timeout=5,
            max_size=4_000_000,
        ) as ws:
            first = json.loads(await ws.recv())
            if first.get("type") != "auth_required":
                return ToolCallResponse(
                    success=False,
                    message=f"unexpected websocket handshake: {first.get('type')}",
                    trace_id=trace_id,
                )

            await ws.send(json.dumps({"type": "auth", "access_token": settings.HA_TOKEN}, ensure_ascii=False))
            second = json.loads(await ws.recv())
            if second.get("type") != "auth_ok":
                return ToolCallResponse(
                    success=False,
                    message=f"websocket auth failed: {second}",
                    trace_id=trace_id,
                )

            request_id = 1
            entity_registry_resp = await _ha_ws_send_command(ws, request_id, {"type": "config/entity_registry/list"})
            request_id += 1
            registry_rows = entity_registry_resp.get("result", [])
            registry_by_entity: dict[str, dict[str, Any]] = {}
            if isinstance(registry_rows, list):
                for row in registry_rows:
                    if not isinstance(row, dict):
                        continue
                    entity_id = str(row.get("entity_id", "")).strip()
                    if entity_id:
                        registry_by_entity[entity_id] = row

            for row in normalized_assignments:
                entity_id = row["entity_id"]
                area_id = row["area_id"]
                registry_row = registry_by_entity.get(entity_id)
                if not isinstance(registry_row, dict):
                    failed.append({"entity_id": entity_id, "area_id": area_id, "error": "entity_not_found"})
                    continue

                current_area_id = str(registry_row.get("area_id") or "").strip()
                if current_area_id and _normalize_area_label(current_area_id) == _normalize_area_label(area_id):
                    skipped.append(
                        {
                            "entity_id": entity_id,
                            "area_id": area_id,
                            "reason": "already_assigned",
                        }
                    )
                    continue

                planned_item = {
                    "entity_id": entity_id,
                    "area_id": area_id,
                    "from_area_id": current_area_id or None,
                }
                planned_updates.append(planned_item)
                if dry_run:
                    continue

                update_resp = await _ha_ws_send_command(
                    ws,
                    request_id,
                    {
                        "type": "config/entity_registry/update",
                        "entity_id": entity_id,
                        "area_id": area_id,
                    },
                )
                request_id += 1
                if update_resp.get("success"):
                    updated.append(planned_item)
                    registry_row["area_id"] = area_id
                    continue

                error_message = str(update_resp.get("error", {}).get("message", "unknown")).strip() or "unknown"
                failed.append(
                    {
                        "entity_id": entity_id,
                        "area_id": area_id,
                        "error": error_message,
                    }
                )
                errors.append(f"assign_failed:{entity_id}:{error_message}")
    except Exception as ex:
        errors.append(str(ex))

    duration_ms = round((perf_counter() - started) * 1000, 2)
    success = not errors and not failed
    detail = {
        "dry_run": dry_run,
        "input_count": len(normalized_input),
        "valid_assignment_count": len(normalized_assignments),
        "planned_count": len(planned_updates),
        "updated_count": len(updated),
        "failed_count": len(failed),
        "skipped_count": len(skipped),
        "planned_updates": planned_updates,
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
    }
    log_operation(
        event_type="ha_area_reassign",
        source="system",
        action="ha.areas.reassign",
        trace_id=trace_id,
        success=success,
        duration_ms=duration_ms,
        detail=detail,
    )

    message = "HA entity area reassignment completed" if success else "HA entity area reassignment completed with errors"
    return ToolCallResponse(success=success, message=message, trace_id=trace_id, data=detail)


async def audit_ha_areas(
    *,
    target_areas: list[str],
    domains: list[str] | str | None = None,
    include_unavailable: bool = False,
    trace_id: str | None = None,
    dry_run: bool = False,
) -> ToolCallResponse:
    normalized_target_input = _normalize_payload_or_raise(target_areas, field_path="target_areas", trace_id=trace_id)
    normalized_domain_input = _normalize_payload_or_raise(domains, field_path="domains", trace_id=trace_id)
    normalized_targets = _normalize_target_areas(normalized_target_input)
    if not normalized_targets:
        return ToolCallResponse(success=False, message="target_areas is required", trace_id=trace_id)

    domain_list = _normalize_domains(normalized_domain_input)
    domain_set = {item.strip().lower() for item in domain_list if item.strip()}
    if not domain_set:
        domain_set = set(DEFAULT_AREA_AUDIT_DOMAINS)
        domain_list = sorted(domain_set)

    started = perf_counter()
    errors: list[str] = []

    areas_result = await get_ha_areas(include_state_validation=False)
    area_rows = areas_result.get("areas", []) if isinstance(areas_result.get("areas"), list) else []
    if not area_rows and not areas_result.get("success"):
        errors.append(str(areas_result.get("errors", "failed to load ha areas")))

    target_norms = {_normalize_area_label(name) for name in normalized_targets}
    target_entities: set[str] = set()
    all_assigned_entities: set[str] = set()
    found_target_norms: set[str] = set()
    target_area_rows: list[dict[str, Any]] = []
    outside_target_rows: list[dict[str, Any]] = []

    for row in area_rows:
        if not isinstance(row, dict):
            continue
        area_id = str(row.get("area_id", "")).strip()
        area_name = str(row.get("area_name", "")).strip() or area_id
        if not area_id:
            continue

        ha_entities = _to_entity_id_list(row.get("ha_entities"))
        if not ha_entities:
            ha_entities = _to_entity_id_list(row.get("entities"))
        all_assigned_entities.update(ha_entities)

        normalized_name = _normalize_area_label(area_name)
        normalized_id = _normalize_area_label(area_id)
        is_target = normalized_name in target_norms or normalized_id in target_norms

        area_info = {
            "area_id": area_id,
            "area_name": area_name,
            "entity_count": len(ha_entities),
            "entity_ids": ha_entities,
        }
        if is_target:
            found_target_norms.add(normalized_name)
            found_target_norms.add(normalized_id)
            target_entities.update(ha_entities)
            target_area_rows.append(area_info)
        elif ha_entities:
            outside_target_rows.append(area_info)

    missing_targets = [name for name in normalized_targets if _normalize_area_label(name) not in found_target_norms]

    states_result = await fetch_ha_states_raw()
    if not states_result.get("ok"):
        errors.append(str(states_result.get("error", "failed to fetch ha states")))
        state_rows: list[dict[str, Any]] = []
    else:
        state_rows = [row for row in states_result.get("data", []) if isinstance(row, dict)]

    unassigned_entities: list[dict[str, Any]] = []
    assigned_target_count = 0
    scanned_count = 0
    ignored_count = 0

    for row in state_rows:
        entity_id = str(row.get("entity_id", "")).strip()
        if not entity_id or "." not in entity_id:
            continue
        entity_domain = entity_id.split(".", 1)[0].strip().lower()
        if entity_domain not in domain_set:
            continue
        if _is_area_audit_ignored_entity(entity_id):
            ignored_count += 1
            continue
        scanned_count += 1

        state_text = str(row.get("state", "")).strip().lower()
        if not include_unavailable and state_text in {"unknown", "unavailable"}:
            continue

        if entity_id in all_assigned_entities:
            if entity_id in target_entities:
                assigned_target_count += 1
            continue

        attrs = row.get("attributes", {}) if isinstance(row.get("attributes"), dict) else {}
        friendly_name = str(attrs.get("friendly_name", "")).strip()
        suggested_area, match_token = _suggest_area_for_entity(
            entity_id=entity_id,
            friendly_name=friendly_name,
            target_areas=normalized_targets,
        )

        unassigned_entities.append(
            {
                "entity_id": entity_id,
                "domain": entity_domain,
                "friendly_name": friendly_name or None,
                "state": row.get("state"),
                "suggested_area": suggested_area,
                "reason": f"name_match:{match_token}" if match_token else "no_match",
            }
        )

    target_area_rows.sort(key=lambda item: str(item.get("area_name", "")))
    outside_target_rows.sort(key=lambda item: str(item.get("area_name", "")))
    unassigned_entities.sort(
        key=lambda item: (
            0 if item.get("suggested_area") else 1,
            str(item.get("suggested_area") or ""),
            str(item.get("entity_id") or ""),
        )
    )

    suggested_count = len([item for item in unassigned_entities if item.get("suggested_area")])
    detail = {
        "target_areas": normalized_targets,
        "missing_target_areas": missing_targets,
        "domains": sorted(domain_set),
        "include_unavailable": include_unavailable,
        "dry_run": dry_run,
        "target_area_summary": target_area_rows,
        "outside_target_area_summary": outside_target_rows,
        "scanned_entity_count": scanned_count,
        "ignored_entity_count": ignored_count,
        "assigned_in_target_count": assigned_target_count,
        "unassigned_entity_count": len(unassigned_entities),
        "suggested_assignment_count": suggested_count,
        "unassigned_entities": unassigned_entities,
        "errors": errors,
    }

    duration_ms = round((perf_counter() - started) * 1000, 2)
    success = not errors
    log_operation(
        event_type="ha_area_audit",
        source="system",
        action="ha.areas.audit",
        trace_id=trace_id,
        success=success,
        duration_ms=duration_ms,
        detail=detail,
    )

    return ToolCallResponse(
        success=success,
        message="HA area audit completed" if success else "HA area audit completed with errors",
        trace_id=trace_id,
        data=detail,
    )


async def sync_ha_areas(
    *,
    target_areas: list[str],
    delete_unused: bool = True,
    force_delete_in_use: bool = False,
    trace_id: str | None = None,
    dry_run: bool = False,
) -> ToolCallResponse:
    normalized_target_input = _normalize_payload_or_raise(target_areas, field_path="target_areas", trace_id=trace_id)
    normalized_targets = _normalize_target_areas(normalized_target_input)
    if not normalized_targets:
        return ToolCallResponse(
            success=False,
            message="target_areas is required",
            trace_id=trace_id,
        )

    if not settings.HA_TOKEN:
        return ToolCallResponse(success=False, message="HA token missing", trace_id=trace_id)

    try:
        import websockets
    except Exception as ex:
        return ToolCallResponse(success=False, message=f"websocket dependency missing: {ex}", trace_id=trace_id)

    created: list[dict[str, Any]] = []
    renamed: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[str] = []

    started = perf_counter()
    ws_url = _ha_websocket_url(settings.HA_BASE_URL)

    try:
        async with websockets.connect(
            ws_url,
            open_timeout=settings.HA_CONTEXT_TIMEOUT_SEC,
            close_timeout=5,
            max_size=4_000_000,
        ) as ws:
            first = json.loads(await ws.recv())
            if first.get("type") != "auth_required":
                return ToolCallResponse(
                    success=False,
                    message=f"unexpected websocket handshake: {first.get('type')}",
                    trace_id=trace_id,
                )

            await ws.send(json.dumps({"type": "auth", "access_token": settings.HA_TOKEN}, ensure_ascii=False))
            second = json.loads(await ws.recv())
            if second.get("type") != "auth_ok":
                return ToolCallResponse(
                    success=False,
                    message=f"websocket auth failed: {second}",
                    trace_id=trace_id,
                )

            request_id = 1
            listed = await _ha_ws_send_command(ws, request_id, {"type": "config/area_registry/list"})
            request_id += 1
            if not listed.get("success"):
                detail = listed.get("error", {})
                return ToolCallResponse(
                    success=False,
                    message=f"list area registry failed: {detail}",
                    trace_id=trace_id,
                )

            rows = _area_rows_from_ws_list(listed.get("result"))
            used_ids: set[str] = set()

            for target_name in normalized_targets:
                exact = _find_area_by_name(rows, target_name, used_ids=used_ids)
                if exact is not None:
                    kept.append({"area_id": exact["area_id"], "name": exact["name"]})
                    used_ids.add(exact["area_id"])
                    continue

                alias = _find_area_by_alias(rows, target_name, used_ids=used_ids)
                if alias is not None:
                    if _normalize_area_label(alias["name"]) == _normalize_area_label(target_name):
                        kept.append({"area_id": alias["area_id"], "name": alias["name"]})
                    elif dry_run:
                        renamed.append({"area_id": alias["area_id"], "from": alias["name"], "to": target_name})
                        alias["name"] = target_name
                    else:
                        update_resp = await _ha_ws_send_command(
                            ws,
                            request_id,
                            {
                                "type": "config/area_registry/update",
                                "area_id": alias["area_id"],
                                "name": target_name,
                            },
                        )
                        request_id += 1
                        if update_resp.get("success"):
                            renamed.append({"area_id": alias["area_id"], "from": alias["name"], "to": target_name})
                            alias["name"] = target_name
                        else:
                            errors.append(
                                f"rename_failed:{alias['area_id']}:{update_resp.get('error', {}).get('message', 'unknown')}"
                            )
                            continue
                    used_ids.add(alias["area_id"])
                    continue

                seed = AREA_CREATE_SEED.get(target_name, target_name.lower().replace(" ", "_"))
                candidate_name = seed
                existing_name_set = {_normalize_area_label(row["name"]) for row in rows}
                index = 2
                while _normalize_area_label(candidate_name) in existing_name_set:
                    candidate_name = f"{seed}_{index}"
                    index += 1

                if dry_run:
                    created.append({"area_id": candidate_name, "name": target_name})
                    rows.append({"area_id": candidate_name, "name": target_name})
                    used_ids.add(candidate_name)
                    continue

                create_resp = await _ha_ws_send_command(
                    ws,
                    request_id,
                    {
                        "type": "config/area_registry/create",
                        "name": candidate_name,
                    },
                )
                request_id += 1
                if not create_resp.get("success"):
                    errors.append(
                        f"create_failed:{target_name}:{create_resp.get('error', {}).get('message', 'unknown')}"
                    )
                    continue

                result_row = create_resp.get("result", {}) if isinstance(create_resp.get("result"), dict) else {}
                area_id = str(result_row.get("area_id", "")).strip() or candidate_name
                current_name = str(result_row.get("name", "")).strip() or candidate_name

                if _normalize_area_label(current_name) != _normalize_area_label(target_name):
                    update_resp = await _ha_ws_send_command(
                        ws,
                        request_id,
                        {
                            "type": "config/area_registry/update",
                            "area_id": area_id,
                            "name": target_name,
                        },
                    )
                    request_id += 1
                    if update_resp.get("success"):
                        current_name = target_name
                    else:
                        errors.append(
                            f"rename_after_create_failed:{area_id}:{update_resp.get('error', {}).get('message', 'unknown')}"
                        )

                created.append({"area_id": area_id, "name": current_name})
                rows.append({"area_id": area_id, "name": current_name})
                used_ids.add(area_id)

            if delete_unused:
                area_entity_count = await _build_area_entity_count() if not force_delete_in_use else {}
                target_name_set = {_normalize_area_label(name) for name in normalized_targets}
                for row in rows:
                    area_id = row["area_id"]
                    area_name = row["name"]
                    if _normalize_area_label(area_name) in target_name_set:
                        continue

                    if not force_delete_in_use:
                        entity_count = int(area_entity_count.get(area_id, 0) or 0)
                        if entity_count > 0:
                            skipped.append(
                                {
                                    "area_id": area_id,
                                    "name": area_name,
                                    "reason": "in_use",
                                    "entity_count": entity_count,
                                }
                            )
                            continue

                    if dry_run:
                        deleted.append({"area_id": area_id, "name": area_name})
                        continue

                    delete_resp = await _ha_ws_send_command(
                        ws,
                        request_id,
                        {
                            "type": "config/area_registry/delete",
                            "area_id": area_id,
                        },
                    )
                    request_id += 1
                    if delete_resp.get("success"):
                        deleted.append({"area_id": area_id, "name": area_name})
                    else:
                        errors.append(
                            f"delete_failed:{area_id}:{delete_resp.get('error', {}).get('message', 'unknown')}"
                        )

    except Exception as ex:
        errors.append(str(ex))

    duration_ms = round((perf_counter() - started) * 1000, 2)
    success = not errors
    detail = {
        "target_areas": normalized_targets,
        "delete_unused": delete_unused,
        "force_delete_in_use": force_delete_in_use,
        "dry_run": dry_run,
        "created": created,
        "renamed": renamed,
        "kept": kept,
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
    }
    log_operation(
        event_type="ha_area_sync",
        source="system",
        action="ha.areas.sync",
        trace_id=trace_id,
        success=success,
        duration_ms=duration_ms,
        detail=detail,
    )

    message = "HA areas synchronized" if success else "HA areas sync completed with errors"
    return ToolCallResponse(
        success=success,
        message=message,
        trace_id=trace_id,
        data=detail,
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


async def resolve_service_data(item: ToolCatalogItem, arguments: dict[str, Any]) -> dict[str, Any]:
    merged = dict(item.default_arguments)
    merged.update(arguments)

    if item.strategy == "passthrough":
        return merged

    if item.strategy == "light_area":
        entity_id = await resolve_area_entity("light", merged)
        return {"entity_id": entity_id}

    if item.strategy == "cover_area":
        entity_id = await resolve_area_entity("cover", merged)
        return {"entity_id": entity_id}

    if item.strategy == "scene_id":
        scene_id = str(merged.get("scene_id", "")).strip()
        if not scene_id:
            raise HTTPException(status_code=400, detail="scene_id is required")
        return {"entity_id": scene_id}

    if item.strategy == "climate_area":
        entity_id = await resolve_area_entity("climate", merged)
        return {"entity_id": entity_id}

    if item.strategy == "climate_area_temperature":
        entity_id = await resolve_area_entity("climate", merged)
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


async def resolve_area_entity(entity_type: str, merged_args: dict[str, Any]) -> str | list[str]:
    explicit = parse_entity_ids(merged_args.get("entity_id"))
    if explicit is not None:
        return explicit

    area_raw = merged_args.get("area", "living_room")
    area = str(area_raw).strip().lower()
    exclude_areas = _parse_area_list(merged_args.get("exclude_areas"))
    if _is_all_area_request(area_raw):
        all_entities = await _resolve_all_entities_from_ha(entity_type, exclude_areas=exclude_areas)
        if all_entities is not None:
            return all_entities

    area_candidates = _iter_area_lookup_candidates(area)

    from_ha = await _resolve_entities_from_ha_area(entity_type=entity_type, area_candidates=area_candidates)
    if from_ha is not None:
        return from_ha

    area_map = extract_area_entity_map(merged_args.get("area_entity_map"), entity_type=entity_type)
    if not area_map:
        raw_area_map = settings.AREA_ENTITY_MAP.get(entity_type, {})
        for key, value in raw_area_map.items():
            parsed = parse_entity_ids(value)
            if parsed is not None:
                area_map[str(key).strip().lower()] = parsed

    entity_id = _find_entity_for_area_map(area_map, area_candidates=area_candidates)
    if entity_id is not None:
        return entity_id

    raise HTTPException(status_code=400, detail=f"{entity_type} entity is not configured for area: {area}")


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
    if isinstance(entity_id, list):
        domains: list[str] = []
        for raw in entity_id:
            text = str(raw or "").strip()
            if "." not in text:
                continue
            domain = text.split(".", 1)[0].strip().lower()
            if domain:
                domains.append(domain)
        unique_domains = list(dict.fromkeys(domains))
        if len(unique_domains) == 1:
            return unique_domains[0]
        if len(unique_domains) > 1:
            return fallback
        return fallback

    first = str(entity_id or "")
    if "." in first:
        return first.split(".", 1)[0].strip().lower()
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
