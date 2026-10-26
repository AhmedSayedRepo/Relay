import io
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from relay.domain.users import User
from relay.infrastructure.config import Settings
from relay.infrastructure.logging import JsonFormatter, request_id_context
from relay.presentation.app import create_app

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD


async def test_invalid_signed_token_claims_are_rejected(
    client: AsyncClient,
    admin: User,
    settings: Settings,
    assert_error: Callable[[Response, int], dict[str, Any]],
) -> None:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(admin.id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=15),
        "jti": str(uuid4()),
        "token_type": "access",
    }
    invalid_variants = [
        {**claims, "exp": now - timedelta(seconds=1)},
        {**claims, "nbf": now + timedelta(minutes=5)},
        {**claims, "iat": now + timedelta(minutes=5)},
        {**claims, "iss": "another-service"},
        {**claims, "aud": "another-audience"},
        {**claims, "sub": "not-a-uuid"},
        {**claims, "sub": str(uuid4())},
        {**claims, "token_type": "refresh"},
    ]
    invalid_variants.extend(
        {key: value for key, value in claims.items() if key != missing}
        for missing in claims
    )
    for payload in invalid_variants:
        token = jwt.encode(
            payload, settings.jwt_secret.get_secret_value(), algorithm="HS256"
        )
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert_error(response, 401)
        assert token not in response.text


async def test_wrong_signature_and_unapproved_algorithm_are_rejected(
    client: AsyncClient,
    admin: User,
    settings: Settings,
    assert_error: Callable[[Response, int], dict[str, Any]],
) -> None:
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin.id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=15),
        "jti": str(uuid4()),
        "token_type": "access",
    }
    for token in (
        jwt.encode(payload, "attacker-key-" * 8, algorithm="HS256"),
        jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS384"),
        jwt.encode(payload, "", algorithm="none"),
    ):
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert_error(response, 401)


async def test_default_cors_does_not_grant_other_origins(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"Origin": "https://untrusted.test"})
    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Credentials" not in response.headers


async def test_explicit_cors_grants_only_configured_origin(settings: Settings) -> None:
    settings.cors_origins = ["https://console.example.com"]
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            allowed = await client.get(
                "/health", headers={"Origin": "https://console.example.com"}
            )
            assert (
                allowed.headers["Access-Control-Allow-Origin"]
                == settings.cors_origins[0]
            )
            assert "X-Request-ID" in allowed.headers["Access-Control-Expose-Headers"]
            assert "Access-Control-Allow-Credentials" not in allowed.headers
            preflight = await client.options(
                "/api/v1/devices",
                headers={
                    "Origin": settings.cors_origins[0],
                    "Access-Control-Request-Method": "PATCH",
                    "Access-Control-Request-Headers": (
                        "authorization,content-type,x-request-id"
                    ),
                },
            )
            assert preflight.status_code == 200
            assert (
                preflight.headers["Access-Control-Allow-Origin"]
                == settings.cors_origins[0]
            )
            rejected = await client.options(
                "/api/v1/devices",
                headers={
                    "Origin": "https://untrusted.test",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert rejected.status_code == 400
            assert "Access-Control-Allow-Origin" not in rejected.headers


async def test_untrusted_host_is_rejected_with_correlated_error(
    client: AsyncClient,
    assert_error: Callable[[Response, int], dict[str, Any]],
) -> None:
    response = await client.get("/health", headers={"Host": "attacker.example"})
    assert_error(response, 400)


async def test_unhandled_error_has_no_traceback_or_secret(
    client: AsyncClient,
    app: FastAPI,
    assert_error: Callable[[Response, int], dict[str, Any]],
) -> None:
    @app.get("/test-only-error")
    async def failing_endpoint() -> None:
        raise RuntimeError("database-password-SHOULD-NOT-LEAK")

    response = await client.get(
        "/test-only-error", headers={"X-Request-ID": "failure-correlation"}
    )
    assert_error(response, 500)
    assert response.headers["X-Request-ID"] == "failure-correlation"
    for value in ("Traceback", "RuntimeError", "database-password-SHOULD-NOT-LEAK"):
        assert value not in response.text


async def test_malformed_json_does_not_echo_body(
    client: AsyncClient,
    assert_error: Callable[[Response, int], dict[str, Any]],
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        content='{"password":"private-input-marker",',
        headers={"Content-Type": "application/json"},
    )
    assert_error(response, 422)
    assert "private-input-marker" not in response.text


async def test_request_body_limit_rejects_oversized_payload(
    client: AsyncClient,
    assert_error: Callable[[Response, int], dict[str, Any]],
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        content="sensitive-oversized-marker" * 1000,
        headers={"Content-Type": "application/json"},
    )
    assert_error(response, 413)
    assert "sensitive-oversized-marker" not in response.text


def test_json_logs_have_context_and_exclude_arbitrary_secrets() -> None:
    formatter = JsonFormatter("Relay", "test")
    marker = request_id_context.set("safe-request-id")
    try:
        record = logging.LogRecord(
            "relay.http",
            logging.INFO,
            "unused.py",
            1,
            "request.completed",
            ("secret-format-argument",),
            None,
        )
        record.method = "GET"
        record.route = "/api/v1/devices/{device_id}"
        record.status_code = 200
        record.duration_ms = 1.5
        record.password = "hidden-password"
        encoded = formatter.format(record)
        body = json.loads(encoded)
        assert body["service"] == "Relay"
        assert body["environment"] == "test"
        assert body["level"] == "INFO"
        assert body["request_id"] == "safe-request-id"
        assert body["message"] == "request.completed"
        assert datetime.fromisoformat(body["timestamp"]).tzinfo is not None
        assert body["route"] == "/api/v1/devices/{device_id}"
        assert "hidden-password" not in encoded
        assert "secret-format-argument" not in encoded
        for logger_name in ("sqlalchemy.engine", "external.driver", "relay.http"):
            external = logging.LogRecord(
                logger_name,
                logging.ERROR,
                "unused.py",
                1,
                "token=secret-jwt-password",
                (),
                None,
            )
            assert "secret-jwt-password" not in formatter.format(external)
    finally:
        request_id_context.reset(marker)


@pytest.mark.parametrize("message", [{"password": "do-not-log-me"}, ["do-not-log-me"]])
def test_non_string_log_messages_are_safely_suppressed(message: Any) -> None:
    record = logging.LogRecord(
        "relay.adapter", logging.WARNING, "unused.py", 1, message, (), None
    )
    encoded = JsonFormatter("Relay", "test").format(record)
    assert "do-not-log-me" not in encoded
    assert json.loads(encoded)["message"] == "external.log.suppressed"


def test_event_metrics_logs_expose_only_bounded_metadata() -> None:
    record = logging.LogRecord(
        "relay.application.events",
        logging.INFO,
        "unused.py",
        1,
        "events.batch_processed",
        (),
        None,
    )
    record.accepted_count = 2
    record.duplicate_count = 1
    record.rejected_count = 3
    record.payload = {"body": "private-sms-log-marker"}
    encoded = JsonFormatter("Relay", "test").format(record)
    body = json.loads(encoded)
    assert body["message"] == "events.batch_processed"
    assert body["accepted_count"] == 2
    assert body["duplicate_count"] == 1
    assert body["rejected_count"] == 3
    assert "private-sms-log-marker" not in encoded


async def test_http_logs_only_route_templates_and_no_credentials(
    client: AsyncClient,
    app: FastAPI,
    admin: User,
    token: str,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
) -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter("Relay Test", "test"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        response = await client.post(
            "/api/v1/devices", json=device_input, headers=auth_headers
        )
        device_id = response.json()["id"]
        await client.get(
            f"/api/v1/devices/{device_id}?sensitive=private-query-marker",
            headers={**auth_headers, "X-Request-ID": "logged-request"},
        )
        await client.post(
            "/api/v1/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
    finally:
        root.removeHandler(handler)
        handler.close()
    raw = output.getvalue()
    logs = [json.loads(line) for line in raw.splitlines()]
    request_logs = [
        entry for entry in logs if entry.get("request_id") == "logged-request"
    ]
    assert request_logs
    assert any(
        str(entry.get("route", "")).endswith("/devices/{device_id}")
        for entry in request_logs
    )
    for secret in (
        token,
        ADMIN_PASSWORD,
        ADMIN_EMAIL,
        device_id,
        "private-query-marker",
    ):
        assert secret not in raw
    assert request_id_context.get() is None
