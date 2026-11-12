import re
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient, Response

from relay.application.account_recovery import (
    CODE_ALPHABET,
    new_recovery_code,
    normalize_code,
)
from relay.domain.users import User
from relay.infrastructure.config import Settings

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD

NEW_PASSWORD = "A-new-and-long-enough-password!7"
Check = Callable[[Response, int], dict[str, Any]]


async def login(client: AsyncClient, password: str) -> int:
    response = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": password}
    )
    return response.status_code


def test_recovery_codes_are_readable_and_random() -> None:
    codes = {new_recovery_code() for _ in range(50)}
    assert len(codes) == 50
    for code in codes:
        assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code)
        assert set(code.replace("-", "")) <= set(CODE_ALPHABET)
    assert normalize_code(" abcd-efgh jkmn ") == "ABCDEFGHJKMN"


async def test_admin_changes_password(
    client: AsyncClient, admin: User, auth_headers: dict[str, str], assert_error: Check
) -> None:
    assert_error(
        await client.post(
            "/api/v1/auth/password",
            headers=auth_headers,
            json={"current_password": "wrong", "new_password": NEW_PASSWORD},
        ),
        401,
    )
    response = await client.post(
        "/api/v1/auth/password",
        headers=auth_headers,
        json={"current_password": ADMIN_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 204
    assert await login(client, NEW_PASSWORD) == 200


async def test_recovery_code_endpoints_are_gone(client: AsyncClient) -> None:
    gone = await client.post("/api/v1/auth/recovery-code", json={"code": "x" * 12})
    assert gone.status_code in {404, 405}


async def test_session_cookie_survives_reload_and_ends_with_new_password(
    client: AsyncClient, admin: User, auth_headers: dict[str, str]
) -> None:
    signed_in = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert signed_in.status_code == 200
    cookie = signed_in.cookies.get("relay_session")
    assert cookie
    session = {"Cookie": f"relay_session={cookie}"}
    client.cookies.clear()
    resumed = await client.post("/api/v1/auth/session", headers=session)
    assert resumed.status_code == 200 and resumed.json()["access_token"]
    client.cookies.clear()
    assert (await client.post("/api/v1/auth/session")).status_code == 401
    changed = await client.post(
        "/api/v1/auth/password",
        headers=auth_headers,
        json={"current_password": ADMIN_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 204
    client.cookies.clear()
    stale = await client.post("/api/v1/auth/session", headers=session)
    assert stale.status_code == 401
    out = await client.post("/api/v1/auth/logout")
    assert out.status_code == 204


async def test_email_reset_is_off_without_smtp(
    client: AsyncClient, admin: User
) -> None:
    options = await client.get("/api/v1/auth/recovery-options")
    assert options.json() == {"email_reset": False}
    response = await client.post(
        "/api/v1/auth/password-reset/request", json={"email": ADMIN_EMAIL}
    )
    assert response.status_code == 202


async def test_email_reset_link_flow(
    app: FastAPI, client: AsyncClient, admin: User, assert_error: Check
) -> None:
    settings: Settings = app.state.settings
    sent: list[Any] = []

    class FakeMailer:
        async def send(self, email: Any) -> None:
            sent.append(email)

    app.state.settings = settings.model_copy(
        update={
            "smtp_host": "smtp.example.com",
            "smtp_from": "relay@example.com",
            "operator_public_url": "https://relay.example.com",
        }
    )
    app.state.mailer = FakeMailer()
    try:
        options = await client.get("/api/v1/auth/recovery-options")
        assert options.json() == {"email_reset": True}
        unknown = await client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "nobody@example.com"},
        )
        assert unknown.status_code == 202 and sent == []
        known = await client.post(
            "/api/v1/auth/password-reset/request", json={"email": ADMIN_EMAIL}
        )
        assert known.json() == unknown.json()
        assert len(sent) == 1 and sent[0].to == ADMIN_EMAIL
        match = re.search(
            r"https://relay\.example\.com/#/reset-password\?token=(\S+)", sent[0].body
        )
        assert match
        token = match.group(1)
        confirm = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": token, "new_password": NEW_PASSWORD},
        )
        assert confirm.status_code == 204
        assert await login(client, NEW_PASSWORD) == 200
        reused = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": token, "new_password": NEW_PASSWORD + "x"},
        )
        assert_error(reused, 401)
        for _ in range(3):
            await client.post(
                "/api/v1/auth/password-reset/request", json={"email": ADMIN_EMAIL}
            )
        # One email earlier plus two more reach the hourly limit of three.
        assert len(sent) == 3
    finally:
        app.state.settings = settings
        app.state.mailer = None
