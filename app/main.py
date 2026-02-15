import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


APP_NAME = "smart_assistant_ha_bridge"
HA_ENABLED = os.getenv("HA_ENABLED", "false").lower() == "true"
HA_BASE_URL = os.getenv("HA_BASE_URL", "http://homeassistant.local:8123").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_TIMEOUT_SEC = float(os.getenv("HA_TIMEOUT_SEC", "6"))

app = FastAPI(title=APP_NAME, version="0.1.0")

# 白名单能力定义（只暴露可控范围，避免越权）。
TOOL_WHITELIST: dict[str, dict[str, Any]] = {
    "home.lights.on": {"domain": "light", "service": "turn_on"},
    "home.lights.off": {"domain": "light", "service": "turn_off"},
    "home.scene.activate": {"domain": "scene", "service": "turn_on"},
    "home.climate.set_temperature": {"domain": "climate", "service": "set_temperature"},
}

AREA_ENTITY_MAP = {
    "light": {
        "living_room": "light.living_room",
        "bedroom": "light.bedroom",
        "study": "light.study",
    },
    "climate": {
        "living_room": "climate.living_room_ac",
        "bedroom": "climate.bedroom_ac",
        "study": "climate.study_ac",
    },
}


class ToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None


class ToolCallResponse(BaseModel):
    success: bool
    message: str
    trace_id: str | None = None
    data: dict[str, Any] | None = None


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "service": APP_NAME,
        "status": "ok",
        "ha_enabled": HA_ENABLED,
        "ha_base_url": HA_BASE_URL,
        "whitelist_count": len(TOOL_WHITELIST),
    }


@app.get("/v1/tools/whitelist")
async def list_whitelist() -> dict[str, Any]:
    return {
        "tools": sorted(TOOL_WHITELIST.keys())
    }


@app.post("/v1/tools/call", response_model=ToolCallResponse)
async def call_tool(req: ToolCallRequest) -> ToolCallResponse:
    if req.tool_name not in TOOL_WHITELIST:
        raise HTTPException(status_code=400, detail=f"tool not allowed: {req.tool_name}")

    service_def = TOOL_WHITELIST[req.tool_name]
    domain = service_def["domain"]
    service = service_def["service"]

    service_data = normalize_service_data(req.tool_name, req.arguments)

    if not HA_ENABLED:
        return ToolCallResponse(
            success=True,
            message="HA mock mode: command accepted",
            trace_id=req.trace_id,
            data={
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "mode": "mock",
            },
        )

    if not HA_TOKEN:
        return ToolCallResponse(
            success=False,
            message="HA token missing",
            trace_id=req.trace_id,
        )

    path = f"/api/services/{domain}/{service}"

    try:
        async with httpx.AsyncClient(timeout=HA_TIMEOUT_SEC) as client:
            resp = await client.post(
                f"{HA_BASE_URL}{path}",
                headers={
                    "Authorization": f"Bearer {HA_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=service_data,
            )

            if resp.status_code >= 400:
                return ToolCallResponse(
                    success=False,
                    message=f"HA call failed: {resp.status_code} {resp.text}",
                    trace_id=req.trace_id,
                )

            payload = resp.json() if resp.content else {}
            return ToolCallResponse(
                success=True,
                message="HA call succeeded",
                trace_id=req.trace_id,
                data={
                    "domain": domain,
                    "service": service,
                    "service_data": service_data,
                    "ha_response": payload,
                },
            )
    except Exception as ex:
        return ToolCallResponse(
            success=False,
            message=f"HA bridge error: {ex}",
            trace_id=req.trace_id,
        )


def normalize_service_data(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name in {"home.lights.on", "home.lights.off"}:
        area = str(arguments.get("area", "living_room"))
        entity_id = AREA_ENTITY_MAP["light"].get(area, AREA_ENTITY_MAP["light"]["living_room"])
        return {"entity_id": entity_id}

    if tool_name == "home.scene.activate":
        scene_id = str(arguments.get("scene_id", "scene.home"))
        return {"entity_id": scene_id}

    if tool_name == "home.climate.set_temperature":
        area = str(arguments.get("area", "living_room"))
        entity_id = AREA_ENTITY_MAP["climate"].get(area, AREA_ENTITY_MAP["climate"]["living_room"])

        temp_raw = arguments.get("temperature")
        if temp_raw is None:
            raise HTTPException(status_code=400, detail="temperature is required")

        try:
            temperature = int(temp_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="temperature must be an integer")

        if not 16 <= temperature <= 30:
            raise HTTPException(status_code=400, detail="temperature must be between 16 and 30")

        return {"entity_id": entity_id, "temperature": temperature}

    raise HTTPException(status_code=400, detail=f"unsupported tool: {tool_name}")

