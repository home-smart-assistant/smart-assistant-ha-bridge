from time import perf_counter

from fastapi import FastAPI, Request

from app.core.settings import APP_NAME
from app.routers.catalog import router as catalog_router
from app.routers.config import router as config_router
from app.routers.context import router as context_router
from app.routers.device import router as device_router
from app.routers.ha import router as ha_router
from app.routers.log import router as log_router
from app.routers.tool_call import router as tool_call_router
from app.routers.ui import router as ui_router
from app.services.catalog_service import initialize_catalog_state
from app.services.config_service import initialize_runtime_config_state
from app.services.log_service import log_http_request, start_log_worker, stop_log_worker
from app.storage.catalog_storage import bootstrap_storage


bootstrap_storage()
initialize_runtime_config_state()
initialize_catalog_state()


app = FastAPI(
    title=APP_NAME,
    version="0.4.0",
    description="Home Assistant bridge service with modularized API, service, storage, and UI layers.",
    openapi_tags=[
        {"name": "system", "description": "System configuration and runtime state"},
        {"name": "catalog", "description": "Read/write tool and API catalogs"},
        {"name": "tool-call", "description": "Tool execution endpoints"},
        {"name": "device", "description": "Structured device control endpoints"},
        {"name": "context", "description": "Home Assistant context endpoints"},
        {"name": "ha", "description": "Home Assistant discovery APIs for agents"},
    ],
)


@app.on_event("startup")
async def on_startup() -> None:
    start_log_worker()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    stop_log_worker()


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    start = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        try:
            source = (request.headers.get("X-HA-Bridge-Source", "external") or "external").strip().lower()
            if source not in {"ui", "external", "system"}:
                source = "external"

            query = str(request.url.query or "").strip()
            detail = {"query": query} if query else {}
            duration_ms = round((perf_counter() - start) * 1000, 2)
            client_ip = request.client.host if request.client else None
            log_http_request(
                source=source,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
                client_ip=client_ip,
                detail=detail,
            )
        except Exception:
            # Logging failure must never block business requests.
            pass


app.include_router(ui_router)
app.include_router(config_router)
app.include_router(catalog_router)
app.include_router(tool_call_router)
app.include_router(device_router)
app.include_router(context_router)
app.include_router(ha_router)
app.include_router(log_router)
