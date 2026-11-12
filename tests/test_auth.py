from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response

from relay.application.auth import disable_user
from relay.domain.users import User
from relay.infrastructure.config import Settings
from relay.infrastructure.models import UserRow
from relay.infrastructure.repositories import SqlUnitOfWork

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD


async def test_login_issues_short_lived_access_token(
    client: AsyncClient, admin: User, settings: Settings
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": admin.email.upper(), "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    claims = jwt.decode(
        body["access_token"],
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )
    assert claims["sub"] == str(admin.id)
    assert claims["token_type"] == "access"
    assert claims["exp"] - claims["iat"] == 900
    assert claims["nbf"] <= claims["iat"]
    assert UUID(claims["jti"])
    assert ADMIN_PASSWORD not in response.text


async def test_password_is_argon2id_hash_at_rest(app: FastAPI, admin: User) -> None:
    async with app.state.database.session() as session:
        row = await session.get(UserRow, admin.id)
        assert row is not None
        assert row.password_hash.startswith("$argon2id$")
        assert ADMIN_PASSWORD not in row.password_hash
        assert await app.state.passwords.verify(ADMIN_PASSWORD, row.password_hash)
        assert not await app.state.passwords.verify("wrong-password", row.password_hash)


async def test_bad_password_and_unknown_account_have_same_safe_response(
    client: AsyncClient,
    admin: User,
    assert_error: Callable[[Response, int], dict[str, Any]],
) -> None:
    errors = []
    for email in (admin.email, "missing@example.com"):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "incorrect-secret-value"},
        )
        error = assert_error(response, 401)
        errors.append((error["code"], error["message"]))
        assert email not in response.text
        assert "incorrect-secret-value" not in response.text
    assert errors[0] == errors[1]


async def test_me_returns_current_admin_without_hash_or_token(
    client: AsyncClient, admin: User, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "email", "is_active", "created_at", "role"}
    assert body["id"] == str(admin.id)
    assert body["email"] == ADMIN_EMAIL
    assert body["is_active"] is True
    assert "password" not in response.text
    assert "access_token" not in response.text


@pytest.mark.parametrize(
    "header",
    [None, "Basic dGVzdDp0ZXN0", "Bearer", "Bearer not-a-jwt", "Bearer a.b.c"],
)
async def test_protected_routes_reject_missing_or_invalid_auth(
    client: AsyncClient,
    assert_error: Callable[[Response, int], dict[str, Any]],
    header: str | None,
) -> None:
    headers = {"Authorization": header} if header else {}
    for method, path, body in [
        ("GET", "/api/v1/auth/me", None),
        ("GET", "/api/v1/devices", None),
        ("GET", f"/api/v1/devices/{uuid4()}", None),
        ("POST", "/api/v1/devices", {"name": "unauthorized"}),
        ("PATCH", f"/api/v1/devices/{uuid4()}", {"name": "unauthorized"}),
    ]:
        response = await client.request(method, path, json=body, headers=headers)
        assert_error(response, 401)


async def test_disabled_admin_cannot_login_or_reuse_existing_token(
    client: AsyncClient,
    app: FastAPI,
    admin: User,
    auth_headers: dict[str, str],
    assert_error: Callable[[Response, int], dict[str, Any]],
) -> None:
    assert (
        await client.get("/api/v1/auth/me", headers=auth_headers)
    ).status_code == 200
    async with app.state.database.session() as session:
        await disable_user(SqlUnitOfWork(session), admin.email)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": admin.email, "password": ADMIN_PASSWORD},
    )
    assert_error(login, 401)
    assert_error(await client.get("/api/v1/auth/me", headers=auth_headers), 401)
    assert_error(await client.get("/api/v1/devices", headers=auth_headers), 401)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": "not-an-email", "password": "sensitive-input"},
        {"email": ADMIN_EMAIL, "password": ""},
        {"email": ADMIN_EMAIL, "password": "x" * 1025},
        {"email": ADMIN_EMAIL, "password": "sensitive-input", "is_admin": True},
    ],
)
async def test_login_validation_does_not_echo_sensitive_input(
    client: AsyncClient,
    assert_error: Callable[[Response, int], dict[str, Any]],
    payload: dict[str, Any],
) -> None:
    response = await client.post("/api/v1/auth/login", json=payload)
    assert_error(response, 422)
    assert "sensitive-input" not in response.text
    assert "input" not in response.json()["error"]


async def test_refresh_issues_a_new_token_for_a_signed_in_admin(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post("/api/v1/auth/refresh", headers=auth_headers)
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
