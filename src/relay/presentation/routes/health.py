import asyncio
import logging
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from relay.infrastructure.config import Settings
from relay.infrastructure.database import Database

router = APIRouter(tags=["Operations"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    settings = cast(Settings, request.app.state.settings)
    return {"status": "ok", "service": settings.app_name}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    database = cast(Database, request.app.state.database)
    settings = cast(Settings, request.app.state.settings)
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            await database.ping()
    except Exception:
        logger.warning("readiness.failed")
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "dependencies": {"database": "unavailable"},
            },
        )
    return JSONResponse({"status": "ready", "dependencies": {"database": "ready"}})
