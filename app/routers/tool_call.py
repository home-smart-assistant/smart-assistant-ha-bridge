from fastapi import APIRouter

from app.models.schemas import ToolCallRequest, ToolCallResponse
from app.services.ha_service import execute_tool_call

router = APIRouter(prefix="/v1", tags=["tool-call"])


@router.post("/tools/call", response_model=ToolCallResponse)
async def call_tool(req: ToolCallRequest) -> ToolCallResponse:
    return await execute_tool_call(req)
