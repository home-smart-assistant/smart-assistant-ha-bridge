from fastapi import HTTPException

from app.core import settings
from app.models.schemas import ApiCatalogItem, ToolCatalogItem, ToolCatalogUpsertRequest
from app.services.catalog_defaults import default_tool_catalog_items
from app.storage.catalog_storage import (
    load_api_catalog_from_db,
    load_tool_catalog_from_db,
    save_tool_catalog_to_storage,
)

TOOL_CATALOG: dict[str, ToolCatalogItem] = {}


def initialize_catalog_state() -> None:
    global TOOL_CATALOG

    loaded = load_tool_catalog_from_db()
    if loaded:
        TOOL_CATALOG = loaded
        return

    TOOL_CATALOG = {x.tool_name: x for x in default_tool_catalog_items()}
    save_tool_catalog_to_storage(TOOL_CATALOG)


def get_catalog_snapshot() -> dict[str, ToolCatalogItem]:
    with settings.catalog_lock:
        return {name: item.model_copy(deep=True) for name, item in TOOL_CATALOG.items()}


def get_api_catalog_snapshot() -> list[ApiCatalogItem]:
    return [x.model_copy(deep=True) for x in load_api_catalog_from_db()]


def get_tool_or_raise(tool_name: str) -> ToolCatalogItem:
    with settings.catalog_lock:
        item = TOOL_CATALOG.get(tool_name)
        if item is None or not item.enabled:
            raise HTTPException(status_code=400, detail=f"tool not allowed: {tool_name}")
        return item.model_copy(deep=True)


def list_whitelist_tools() -> list[str]:
    return sorted(item.tool_name for item in get_catalog_snapshot().values() if item.enabled)


def list_tool_catalog_items() -> list[dict]:
    return sorted(
        (item.model_dump(mode="json") for item in get_catalog_snapshot().values()),
        key=lambda item: item["tool_name"],
    )


def upsert_tool_catalog_item(tool_name: str, req: ToolCatalogUpsertRequest) -> dict:
    item = ToolCatalogItem(tool_name=tool_name, **req.model_dump())
    with settings.catalog_lock:
        TOOL_CATALOG[tool_name] = item
        save_tool_catalog_to_storage(TOOL_CATALOG)
    return {"success": True, "tool": item.model_dump(mode="json")}


def delete_tool_catalog_item(tool_name: str) -> dict:
    with settings.catalog_lock:
        existed = TOOL_CATALOG.pop(tool_name, None)
        if existed is None:
            raise HTTPException(status_code=404, detail=f"tool not found: {tool_name}")
        save_tool_catalog_to_storage(TOOL_CATALOG)
    return {"success": True, "tool_name": tool_name}


def reload_tool_catalog() -> dict:
    with settings.catalog_lock:
        TOOL_CATALOG.clear()
        TOOL_CATALOG.update(load_tool_catalog_from_db())
        if not TOOL_CATALOG:
            defaults = {x.tool_name: x for x in default_tool_catalog_items()}
            TOOL_CATALOG.update(defaults)
            save_tool_catalog_to_storage(TOOL_CATALOG)
        size = len(TOOL_CATALOG)
    return {"success": True, "tool_catalog_count": size}
