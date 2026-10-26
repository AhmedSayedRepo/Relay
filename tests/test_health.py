import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response


async def test_health_is_public_and_correlated(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "Relay Test"}
    assert UUID(response.headers["X-Request-ID"])


async def test_readiness_checks_database(client: AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"database": "ready"},
    }
    assert response.headers["X-Request-ID"]


async def test_readiness_failure_is_safe_and_health_remains_alive(
    client: AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    ping = AsyncMock(side_effect=RuntimeError("postgres://secret:password@internal"))
    monkeypatch.setattr(app.state.database, "ping", ping)
    response = await client.get("/ready", headers={"X-Request-ID": "probe-db-1"})
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"database": "unavailable"},
    }
    assert response.headers["X-Request-ID"] == "probe-db-1"
    ping.assert_awaited_once()
    assert "secret" not in response.text
    assert (await client.get("/health")).status_code == 200


async def test_readiness_timeout_is_bounded(
    client: AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancelled = asyncio.Event()

    async def stalled_ping() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(app.state.database, "ping", stalled_ping)
    async with asyncio.timeout(2):
        response = await client.get("/ready")
    assert response.status_code == 503
    assert cancelled.is_set()
    assert response.json()["dependencies"]["database"] == "unavailable"


@pytest.mark.parametrize("request_id", ["operator-123.test_4", "a", "a" * 64])
async def test_valid_request_id_is_propagated(
    client: AsyncClient, request_id: str
) -> None:
    response = await client.get("/health", headers={"X-Request-ID": request_id})
    assert response.headers["X-Request-ID"] == request_id


@pytest.mark.parametrize(
    "request_id", ["", "a" * 65, "unsafe value", "-invalid", "x/y"]
)
async def test_invalid_request_id_is_replaced(
    client: AsyncClient, request_id: str
) -> None:
    response = await client.get("/health", headers={"X-Request-ID": request_id})
    replacement = response.headers["X-Request-ID"]
    assert replacement != request_id
    assert UUID(replacement)


@pytest.mark.parametrize("path", ["/missing", "/health/"])
async def test_unknown_route_has_json_error_without_redirect(
    client: AsyncClient,
    assert_error: Callable[[Response, int], dict[str, Any]],
    path: str,
) -> None:
    response = await client.get(path, headers={"X-Request-ID": "not-found-1"})
    assert_error(response, 404)
    assert response.headers["X-Request-ID"] == "not-found-1"
    assert "location" not in response.headers
