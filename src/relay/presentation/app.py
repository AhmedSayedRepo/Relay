import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from relay.infrastructure.config import Settings, load_settings
from relay.infrastructure.database import Database
from relay.infrastructure.dataset_store import FilesystemDatasetStore
from relay.infrastructure.device_security import DeviceTokens
from relay.infrastructure.logging import configure_logging
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.security import Argon2Passwords, JwtTokens
from relay.presentation.errors import ErrorResponse, register_error_handlers
from relay.presentation.middleware import (
    HostValidationMiddleware,
    JsonCORSMiddleware,
    RequestMiddleware,
)
from relay.presentation.routes import (
    agents,
    auth,
    binary_objects,
    devices,
    events,
    health,
    operations,
    processing,
    sms_verification,
    training,
)

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(config)
        database = Database(config)
        app.state.database = database
        try:
            app.state.passwords = Argon2Passwords()
            app.state.tokens = JwtTokens(config)
            app.state.device_tokens = DeviceTokens(config)
            app.state.object_storage = FilesystemObjectStorage(
                config.object_storage_root
            )
            app.state.dataset_store = FilesystemDatasetStore(
                config.training_dataset_root
            )
            logger.info("application.started")
            yield
        finally:
            await database.close()
            logger.info("application.stopped")

    app = FastAPI(
        title=config.app_name,
        version="0.6.0",
        lifespan=lifespan,
        debug=False,
        redirect_slashes=False,
        docs_url=None if config.app_env == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if config.app_env == "production" else "/openapi.json",
        responses={
            code: {"model": ErrorResponse}
            for code in (400, 401, 404, 409, 413, 422, 500)
        },
    )
    app.state.settings = config
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(devices.router, prefix="/api/v1")
    app.include_router(agents.admin_router, prefix="/api/v1")
    app.include_router(agents.device_router, prefix="/api/v1")
    app.include_router(events.device_router, prefix="/api/v1")
    app.include_router(events.admin_router, prefix="/api/v1")
    app.include_router(events.device_admin_router, prefix="/api/v1")
    app.include_router(binary_objects.device_router, prefix="/api/v1")
    app.include_router(binary_objects.admin_router, prefix="/api/v1")
    app.include_router(binary_objects.device_admin_router, prefix="/api/v1")
    app.include_router(processing.router, prefix="/api/v1")
    app.include_router(operations.router, prefix="/api/v1")
    app.include_router(training.router, prefix="/api/v1")
    app.include_router(sms_verification.router, prefix="/api/v1")
    frontend = config.operator_frontend_dir.expanduser().resolve()
    if frontend.is_dir():

        @app.get("/operator", include_in_schema=False)
        async def operator_redirect() -> RedirectResponse:
            return RedirectResponse("/operator/", status_code=307)

        app.mount(
            "/operator", StaticFiles(directory=frontend, html=True), name="operator"
        )
    app.add_middleware(
        JsonCORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(HostValidationMiddleware, allowed_hosts=config.allowed_hosts)
    app.add_middleware(RequestMiddleware, max_chunk_bytes=256 * 1024)
    return app
