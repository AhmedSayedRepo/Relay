"""Admins grant or deny single pages and actions per user."""

from uuid import UUID

from relay.domain.permissions import allowed, granted
from relay.domain.users import Role

PASSWORD = "a-long-test-password"


def test_role_defaults_and_overrides() -> None:
    assert allowed(Role.REVIEWER, "POST", "/api/v1/shifts/close")
    assert not allowed(Role.VIEWER, "POST", "/api/v1/shifts/close")
    assert allowed(
        Role.VIEWER, "POST", "/api/v1/shifts/close", {"action.close_shift": True}
    )
    assert not allowed(
        Role.REVIEWER, "POST", "/api/v1/receipts/x/reopen", {"action.bring_back": False}
    )
    assert not allowed(Role.REVIEWER, "POST", "/api/v1/receipts/delete")
    assert allowed(
        Role.REVIEWER,
        "POST",
        "/api/v1/receipts/delete",
        {"action.delete_receipts": True},
    )
    assert allowed(Role.REVIEWER, "GET", "/api/v1/bank-sms", {"page.bank_sms": True})
    assert not allowed(Role.REVIEWER, "GET", "/api/v1/events", {"page.bank_sms": True})
    # Unknown keys and non-yes/no values are ignored; admins cannot be limited.
    assert granted(
        Role.VIEWER, {"page.anything": True, "page.review": "yes"}
    ) == granted(Role.VIEWER)
    assert allowed(
        Role.ADMIN, "DELETE", "/api/v1/users/x", {"action.manage_users": False}
    )


async def test_admin_sets_permissions(client, auth_headers, app) -> None:
    created = await client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={"email": "cashier@example.com", "role": "VIEWER", "password": PASSWORD},
    )
    user = created.json()
    assert "page.review" not in user["granted"]
    token = app.state.tokens.issue(UUID(user["id"])).access_token
    as_user = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/bank-sms", headers=as_user)).status_code == 403

    changed = await client.patch(
        f"/api/v1/users/{user['id']}",
        headers=auth_headers,
        json={
            "permissions": {"page.bank_sms": True, "page.rejected": False, "junk": True}
        },
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["permissions"] == {"page.bank_sms": True, "page.rejected": False}
    assert "page.bank_sms" in body["granted"] and "page.rejected" not in body["granted"]
    assert (await client.get("/api/v1/bank-sms", headers=as_user)).status_code == 200
    me = (await client.get("/api/v1/auth/me", headers=as_user)).json()
    assert "page.bank_sms" in me["granted"]

    listed = (
        await client.get("/api/v1/users/permissions", headers=auth_headers)
    ).json()
    assert "action.close_shift" in listed["actions"]
    assert "page.review" in listed["defaults"]["REVIEWER"]
