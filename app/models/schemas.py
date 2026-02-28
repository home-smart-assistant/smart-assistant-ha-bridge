from typing import Any, Literal

from pydantic import BaseModel, Field


StrategyName = Literal[
    "passthrough",
    "light_area",
    "scene_id",
    "climate_area",
    "climate_area_temperature",
    "cover_area",
    "area_sync",
    "area_audit",
    "area_assign",
]
PermissionLevel = Literal["low", "medium", "high", "critical"]


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
    tool_version: int = Field(default=1, ge=1, description="Tool definition version")
    schema_version: str = Field(default="1.0", min_length=1, description="Tool argument schema version")
    permission_level: PermissionLevel = Field(default="low", description="Risk level for permission policies")
    environment_tags: list[str] = Field(
        default_factory=lambda: ["home", "prod"],
        description="Runtime environments where this tool can be exposed",
    )
    allowed_agents: list[str] = Field(
        default_factory=lambda: ["home_automation_agent"],
        description="Agent ids that are allowed to see this tool",
    )
    rollout_percentage: int = Field(default=100, ge=0, le=100, description="Gray rollout percentage for sessions")
    description: str = Field(default="", description="Human-readable description")
    default_arguments: dict[str, Any] = Field(default_factory=dict, description="Default arguments")


class ToolCatalogUpsertRequest(BaseModel):
    domain: str = Field(min_length=1, description="HA service domain, or auto/entity for inference")
    service: str = Field(min_length=1, description="HA service name")
    strategy: StrategyName = Field(default="passthrough", description="Argument mapping strategy")
    enabled: bool = Field(default=True, description="Whether tool is callable")
    tool_version: int = Field(default=1, ge=1, description="Tool definition version")
    schema_version: str = Field(default="1.0", min_length=1, description="Tool argument schema version")
    permission_level: PermissionLevel = Field(default="low", description="Risk level for permission policies")
    environment_tags: list[str] = Field(
        default_factory=lambda: ["home", "prod"],
        description="Runtime environments where this tool can be exposed",
    )
    allowed_agents: list[str] = Field(
        default_factory=lambda: ["home_automation_agent"],
        description="Agent ids that are allowed to see this tool",
    )
    rollout_percentage: int = Field(default=100, ge=0, le=100, description="Gray rollout percentage for sessions")
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


class AreaSyncRequest(BaseModel):
    target_areas: list[str] = Field(default_factory=list, description="Target area display names")
    delete_unused: bool = Field(default=True, description="Delete areas outside target list")
    force_delete_in_use: bool = Field(
        default=False,
        description="Delete area even when entities/devices are still assigned",
    )
    trace_id: str | None = None
    dry_run: bool = False


class AreaAuditRequest(BaseModel):
    target_areas: list[str] = Field(default_factory=list, description="Target area display names")
    domains: list[str] = Field(default_factory=list, description="Entity domains to inspect, e.g. light/climate")
    include_unavailable: bool = Field(default=False, description="Include unavailable entities in unassigned list")
    trace_id: str | None = None
    dry_run: bool = False


class AreaAssignRequest(BaseModel):
    target_areas: list[str] = Field(default_factory=list, description="Target area display names")
    domains: list[str] = Field(default_factory=list, description="Entity domains to inspect before assignment")
    include_unavailable: bool = Field(default=False, description="Include unavailable entities in assignment candidates")
    only_with_suggestion: bool = Field(
        default=True,
        description="Assign only entities that have a suggested target area from audit",
    )
    max_updates: int = Field(default=200, ge=1, le=2000, description="Maximum number of entities to update")
    trace_id: str | None = None
    dry_run: bool = False


class EntityAreaAssignment(BaseModel):
    entity_id: str = Field(min_length=1, description="Entity id to reassign, e.g. switch.xxx")
    area: str = Field(min_length=1, description="Target area id or name")


class AreaReassignRequest(BaseModel):
    assignments: list[EntityAreaAssignment] = Field(
        default_factory=list,
        description="Explicit entity to area assignments",
    )
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
