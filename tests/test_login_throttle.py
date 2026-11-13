"""Sign-in slows down password guessing."""

from typing import Any

from relay.presentation.throttle import PER_EMAIL_AND_CLIENT, LoginThrottle

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD


async def test_repeated_wrong_passwords_are_refused(client: Any, admin: Any) -> None:
    url = "/api/v1/auth/login"
    wrong = {"email": ADMIN_EMAIL, "password": "not-the-password-1"}
    for _ in range(PER_EMAIL_AND_CLIENT):
        assert (await client.post(url, json=wrong)).status_code == 401
    blocked = await client.post(
        url, json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    # Someone else, from another address, is not locked out with them.
    other = await client.post(
        url,
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert other.status_code == 200


def test_success_clears_the_count() -> None:
    throttle = LoginThrottle()
    for _ in range(PER_EMAIL_AND_CLIENT - 1):
        throttle.failed("a@example.com", "198.51.100.1")
    throttle.succeeded("a@example.com", "198.51.100.1")
    throttle.failed("a@example.com", "198.51.100.1")
    assert throttle.retry_after("a@example.com", "198.51.100.1") == 0
