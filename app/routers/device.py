from fastapi import APIRouter

from app.models.schemas import (
    ClimateControlRequest,
    CurtainControlRequest,
    CustomDeviceControlRequest,
    LightControlRequest,
    ToolCallResponse,
)
from app.services.device_service import control_climate, control_curtains, control_custom_device, control_lights

router = APIRouter(prefix="/v1/device", tags=["device"])


@router.post("/lights/control", response_model=ToolCallResponse)
async def control_light_api(req: LightControlRequest) -> ToolCallResponse:
    return await control_lights(req)


@router.post("/curtains/control", response_model=ToolCallResponse)
async def control_curtain_api(req: CurtainControlRequest) -> ToolCallResponse:
    return await control_curtains(req)


@router.post("/climate/control", response_model=ToolCallResponse)
async def control_climate_api(req: ClimateControlRequest) -> ToolCallResponse:
    return await control_climate(req)


@router.post("/custom/control", response_model=ToolCallResponse)
async def control_custom_api(req: CustomDeviceControlRequest) -> ToolCallResponse:
    return await control_custom_device(req)
