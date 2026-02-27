from typing import Any

from fastapi import APIRouter

from app.services.ha_service import build_context_summary

router = APIRouter(prefix="/v1", tags=["context"])


@router.get("/context/summary")
async def get_context_summary() -> dict[str, Any]:
    return await build_context_summary()
