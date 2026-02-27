from typing import Any

from fastapi import HTTPException

from app.core import settings
from app.models.schemas import HAConfigUpdateRequest, HAConfigView
from app.storage.catalog_storage import load_runtime_config_from_db, save_runtime_config_to_db


def mask_token(token: str) -> str | None:
    if not token:
        return None
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def get_ha_config_view() -> HAConfigView:
    with settings.runtime_config_lock:
        return HAConfigView(
            ha_base_url=settings.HA_BASE_URL,
            ha_token_set=bool(settings.HA_TOKEN),
            ha_token_preview=mask_token(settings.HA_TOKEN),
            ha_timeout_sec=settings.HA_TIMEOUT_SEC,
            ha_context_timeout_sec=settings.HA_CONTEXT_TIMEOUT_SEC,
        )


def initialize_runtime_config_state() -> None:
    persisted = load_runtime_config_from_db()
    with settings.runtime_config_lock:
        settings.HA_BASE_URL = str(persisted["ha_base_url"]).strip().rstrip("/")
        settings.HA_TOKEN = str(persisted["ha_token"]).strip()
        settings.HA_TIMEOUT_SEC = float(persisted["ha_timeout_sec"])
        settings.HA_CONTEXT_TIMEOUT_SEC = float(persisted["ha_context_timeout_sec"])


def apply_ha_config_update(req: HAConfigUpdateRequest) -> list[str]:
    updated_fields: list[str] = []
    with settings.runtime_config_lock:
        if req.ha_base_url is not None:
            normalized = req.ha_base_url.strip().rstrip("/")
            if not normalized:
                raise HTTPException(status_code=400, detail="ha_base_url cannot be empty")
            settings.HA_BASE_URL = normalized
            updated_fields.append("ha_base_url")

        if req.ha_token is not None:
            settings.HA_TOKEN = req.ha_token.strip()
            updated_fields.append("ha_token")

        if req.ha_timeout_sec is not None:
            settings.HA_TIMEOUT_SEC = req.ha_timeout_sec
            updated_fields.append("ha_timeout_sec")

        if req.ha_context_timeout_sec is not None:
            settings.HA_CONTEXT_TIMEOUT_SEC = req.ha_context_timeout_sec
            updated_fields.append("ha_context_timeout_sec")

        persisted_payload = {
            "ha_base_url": settings.HA_BASE_URL,
            "ha_token": settings.HA_TOKEN,
            "ha_timeout_sec": settings.HA_TIMEOUT_SEC,
            "ha_context_timeout_sec": settings.HA_CONTEXT_TIMEOUT_SEC,
        }

    save_runtime_config_to_db(**persisted_payload)
    return updated_fields


def auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.HA_TOKEN}",
        "Content-Type": "application/json",
    }


def update_ha_config_response(req: HAConfigUpdateRequest) -> dict[str, Any]:
    updated_fields = apply_ha_config_update(req)
    return {
        "success": True,
        "updated_fields": updated_fields,
        "config": get_ha_config_view().model_dump(mode="json"),
    }
