from typing import Any, Literal

from pydantic import BaseModel, Field


StrategyName = Literal[
    "passthrough",
    "light_area",
    "scene_id",
    "climate_area",
    "climate_area_temperature",
    "cover_area",
]


class ToolCallRequest(BaseModel):
    tool_name: str = Field(min_length=1, description="Tool name in catalog, e.g. home.lights.on")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    trace_id: str | None = Field(default=None, description="Optional trace id for logs")
    dry_run: bool = Field(default=False, description="Resolve call without invoking Home Assistant")

    model_config = {
        "json_schema_extra": {
            "example": {
                "tool_name": "home.lights.on",
                "arguments": {"area": "study"},
                "trace_id": "req-001",
                "dry_run": False,
            }
        }
    }


class ToolCallResponse(BaseModel):
    success: bool = Field(description="Whether the tool call succeeded")
    message: str = Field(description="Result message")
    trace_id: str | None = Field(default=None, description="Trace id echoed from request")
    data: dict[str, Any] | None = Field(default=None, description="Execution details")


class ToolCatalogItem(BaseModel):
    tool_name: str = Field(min_length=1, description="Unique tool name")
    domain: str = Field(min_length=1, description="HA service domain, or auto/entity for inference")
    service: str = Field(min_length=1, description="HA service name")
    strategy: StrategyName = Field(default="passthrough", description="Argument mapping strategy")
    enabled: bool = Field(default=True, description="Whether tool is callable")
    description: str = Field(default="", description="Human-readable description")
    default_arguments: dict[str, Any] = Field(default_factory=dict, description="Default arguments")


class ToolCatalogUpsertRequest(BaseModel):
    domain: str = Field(min_length=1, description="HA service domain, or auto/entity for inference")
    service: str = Field(min_length=1, description="HA service name")
    strategy: StrategyName = Field(default="passthrough", description="Argument mapping strategy")
    enabled: bool = Field(default=True, description="Whether tool is callable")
    description: str = Field(default="", description="Human-readable description")
    default_arguments: dict[str, Any] = Field(default_factory=dict, description="Default arguments")


class ToolCatalogFile(BaseModel):
    version: int = 1
    tools: list[ToolCatalogItem] = Field(default_factory=list)


class ApiCatalogItem(BaseModel):
    endpoint_key: str = Field(min_length=1, description="Unique endpoint key")
    display_name: str = Field(min_length=1, description="Display name for UI")
    method: Literal["GET", "POST", "PUT", "DELETE"] = Field(description="HTTP method")
    path: str = Field(min_length=1, description="API path")
    description: str = Field(default="", description="Human-readable description")
    request_example: dict[str, Any] | list[Any] | None = Field(default=None)
    read_only: bool = Field(default=True)
    sort_order: int = Field(default=0)


class HAConfigView(BaseModel):
    ha_base_url: str
    ha_token_set: bool
    ha_token_preview: str | None = None
    ha_timeout_sec: float
    ha_context_timeout_sec: float


class HAConfigUpdateRequest(BaseModel):
    ha_base_url: str | None = None
    ha_token: str | None = None
    ha_timeout_sec: float | None = Field(default=None, gt=0)
    ha_context_timeout_sec: float | None = Field(default=None, gt=0)


class LightControlRequest(BaseModel):
    action: Literal["on", "off"]
    area: str | None = None
    entity_id: str | list[str] | None = None
    trace_id: str | None = None
    dry_run: bool = False


class CurtainControlRequest(BaseModel):
    action: Literal["open", "close", "stop"]
    area: str | None = None
    entity_id: str | list[str] | None = None
    trace_id: str | None = None
    dry_run: bool = False


class ClimateControlRequest(BaseModel):
    action: Literal["turn_on", "turn_off", "set_temperature"]
    area: str | None = None
    entity_id: str | list[str] | None = None
    temperature: int | None = None
    trace_id: str | None = None
    dry_run: bool = False


class CustomDeviceControlRequest(BaseModel):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    dry_run: bool = False


class UiActionLogRequest(BaseModel):
    action: str = Field(min_length=1, max_length=128)
    view: str | None = Field(default=None, max_length=64)
    detail: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, max_length=128)
    success: bool | None = None


class OperationLogItem(BaseModel):
    event_id: str
    created_at: str
    event_type: str
    source: str
    action: str
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    duration_ms: float | None = None
    client_ip: str | None = None
    trace_id: str | None = None
    success: bool | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
