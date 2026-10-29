import re
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient, Response

from relay.application.account_recovery import (
    CODE_ALPHABET,
    AccountRecoveryService,
    new_recovery_code,
    normalize_code,
)
from relay.domain.users import User
from relay.infrastructure.config import Settings
from relay.infrastructure.repositories import SqlUnitOfWork

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD

NEW_PASSWORD = "A-new-and-long-enough-password!7"
Check = Callable[[Response, int], dict[str, Any]]


async def login(client: AsyncClient, password: str) -> int:
    response = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": password}
    )
    return response.status_code


async def issue_codes(app: FastAPI, admin: User) -> list[str]:
    async with app.state.database.session() as session:
        service = AccountRecoveryService(SqlUnitOfWork(session), app.state.passwords)
        return await service.generate_codes(admin.id)


def test_recovery_codes_are_readable_and_random() -> None:
    codes = {new_recovery_code() for _ in range(50)}
    assert len(codes) == 50
    for code in codes:
        assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code)
        assert set(code.replace("-", "")) <= set(CODE_ALPHABET)
    assert normalize_code(" abcd-efgh jkmn ") == "ABCDEFGHJKMN"


async def test_recovery_code_reveals_username_and_resets_once(
    app: FastAPI, client: AsyncClient, admin: User, assert_error: Check
) -> None:
    codes = await issue_codes(app, admin)
    response = await client.post(
        "/api/v1/auth/recovery-code",
        json={"code": codes[0].lower(), "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"email": ADMIN_EMAIL}
    assert await login(client, ADMIN_PASSWORD) == 401
    assert await login(client, NEW_PASSWORD) == 200
    again = await client.post("/api/v1/auth/recovery-code", json={"code": codes[0]})
    assert_error(again, 401)
    # Username-only lookup consumes a code but keeps the password.
    lookup = await client.post("/api/v1/auth/recovery-code", json={"code": codes[1]})
    assert lookup.json() == {"email": ADMIN_EMAIL}
    assert await login(client, NEW_PASSWORD) == 200


async def test_wrong_recovery_code_and_short_password_are_rejected(
    app: FastAPI, client: AsyncClient, admin: User, assert_error: Check
) -> None:
    codes = await issue_codes(app, admin)
    assert_error(
        await client.post(
            "/api/v1/auth/recovery-code", json={"code": "AAAA-BBBB-CCCC"}
        ),
        401,
    )
    short = await client.post(
        "/api/v1/auth/recovery-code",
        json={"code": codes[0], "new_password": "short"},
    )
    assert short.status_code == 422
    # The rejected request did not use up the code.
    assert (
        await client.post("/api/v1/auth/recovery-code", json={"code": codes[0]})
    ).status_code == 200


async def test_admin_regenerates_codes_with_password(
    app: FastAPI,
    client: AsyncClient,
    admin: User,
    auth_headers: dict[str, str],
    assert_error: Check,
) -> None:
    old = await issue_codes(app, admin)
    status = await client.get("/api/v1/auth/recovery-codes", headers=auth_headers)
    assert status.json() == {"remaining": 10}
    assert_error(
        await client.post(
            "/api/v1/auth/recovery-codes",
            headers=auth_headers,
            json={"password": "not-the-password"},
        ),
        401,
    )
    response = await client.post(
        "/api/v1/auth/recovery-codes",
        headers=auth_headers,
        json={"password": ADMIN_PASSWORD},
    )
    assert response.status_code == 201
    fresh = response.json()["codes"]
    assert len(fresh) == 10 and not set(fresh) & set(old)
    assert_error(
        await client.post("/api/v1/auth/recovery-code", json={"code": old[0]}), 401
    )
    assert (
        await client.post("/api/v1/auth/recovery-code", json={"code": fresh[0]})
    ).status_code == 200
    remaining = await client.get("/api/v1/auth/recovery-codes", headers=auth_headers)
    assert remaining.json() == {"remaining": 9}


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
