import secrets
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr

from relay.application.auth import create_admin
from relay.domain.users import User
from relay.infrastructure.config import Settings
from relay.infrastructure.database import Base
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.presentation.app import create_app

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "Only-for-tests-a-strong-password!42"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_name="Relay Test",
        log_level="INFO",
        database_url=SecretStr(f"sqlite+aiosqlite:///{tmp_path / 'relay.sqlite'}"),
        jwt_secret=SecretStr(secrets.token_urlsafe(48)),
        jwt_algorithm="HS256",
        jwt_issuer="relay-test",
        jwt_audience="relay-test-admin",
        access_token_expire_minutes=15,
        cors_origins=[],
        allowed_hosts=["testserver"],
        readiness_timeout_seconds=0.2,
        object_storage_root=tmp_path / "objects",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with application.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as http_client:
        yield http_client


@pytest.fixture
async def admin(app: FastAPI) -> User:
    async with app.state.database.session() as session:
        return await create_admin(
            SqlUnitOfWork(session), app.state.passwords, ADMIN_EMAIL, ADMIN_PASSWORD
        )


@pytest.fixture
def token(app: FastAPI, admin: User) -> str:
    # Login is exercised separately; other cases still use the real signing adapter.
    return str(app.state.tokens.issue(admin.id).access_token)


@pytest.fixture
def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def device_input() -> dict[str, str]:
    return {
        "external_device_id": "collector-lab-001",
        "name": "Lab device",
        "platform": "android",
        "app_version": "0.1.0",
    }


@pytest.fixture
def assert_error() -> Callable[[Response, int], dict[str, Any]]:
    def check(response: Response, status: int) -> dict[str, Any]:
        assert response.status_code == status, response.text
        body = response.json()
        assert set(body) == {"error"}
        error = body["error"]
        assert set(error) == {"code", "message", "request_id"}
        assert isinstance(error["code"], str) and error["code"]
        assert isinstance(error["message"], str) and error["message"]
        assert error["request_id"] == response.headers["X-Request-ID"]
        if status == 401:
            assert response.headers["WWW-Authenticate"] == "Bearer"
        return error

    return check
