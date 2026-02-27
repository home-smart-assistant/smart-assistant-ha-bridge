from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.core import settings

router = APIRouter()


@router.get("/ui", include_in_schema=False)
async def serve_ui() -> HTMLResponse:
    if not settings.UI_PAGE_PATH.exists():
        raise HTTPException(status_code=404, detail="UI page not found")
    return HTMLResponse(settings.UI_PAGE_PATH.read_text(encoding="utf-8"))
