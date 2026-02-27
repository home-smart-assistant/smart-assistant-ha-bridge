from typing import Any

from fastapi import APIRouter

from app.models.schemas import HAConfigUpdateRequest, HAConfigView
from app.services.config_service import get_ha_config_view, update_ha_config_response

router = APIRouter(prefix="/v1/config", tags=["system"])


@router.get("/ha", response_model=HAConfigView)
async def get_ha_config() -> HAConfigView:
    return get_ha_config_view()


@router.put("/ha")
async def update_ha_config(req: HAConfigUpdateRequest) -> dict[str, Any]:
    return update_ha_config_response(req)
