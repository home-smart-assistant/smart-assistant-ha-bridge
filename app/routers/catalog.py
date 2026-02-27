from typing import Any

from fastapi import APIRouter

from app.core import settings
from app.models.schemas import ToolCatalogUpsertRequest
from app.services.catalog_service import (
    delete_tool_catalog_item,
    get_api_catalog_snapshot,
    list_tool_catalog_items,
    list_whitelist_tools,
    reload_tool_catalog,
    upsert_tool_catalog_item,
)

router = APIRouter(prefix="/v1", tags=["catalog"])


@router.get("/apis/catalog")
async def get_api_catalog() -> dict[str, Any]:
    return {
        "storage": "sqlite",
        "db_path": str(settings.HA_DB_PATH),
        "apis": [x.model_dump(mode="json") for x in get_api_catalog_snapshot()],
    }


@router.get("/tools/whitelist")
async def list_whitelist() -> dict[str, Any]:
    return {"tools": list_whitelist_tools()}


@router.get("/tools/catalog")
async def get_tool_catalog() -> dict[str, Any]:
    return {
        "storage": "sqlite",
        "db_path": str(settings.HA_DB_PATH),
        "legacy_path": str(settings.HA_TOOL_CATALOG_PATH),
        "tools": list_tool_catalog_items(),
    }


@router.put("/tools/catalog/{tool_name}")
async def upsert_catalog_item(tool_name: str, req: ToolCatalogUpsertRequest) -> dict[str, Any]:
    return upsert_tool_catalog_item(tool_name, req)


@router.delete("/tools/catalog/{tool_name}")
async def delete_catalog_item(tool_name: str) -> dict[str, Any]:
    return delete_tool_catalog_item(tool_name)


@router.post("/tools/catalog/reload")
async def reload_catalog() -> dict[str, Any]:
    return reload_tool_catalog()
