"""User management and role-based access."""

from uuid import UUID, uuid4

PASSWORD = "a-long-test-password"


async def _add(client, auth_headers, email, role):
    response = await client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={"email": email, "role": role, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _headers(app, user_id):
    token = app.state.tokens.issue(UUID(user_id)).access_token
    return {"Authorization": f"Bearer {token}"}


async def test_admin_manages_users(client, auth_headers, admin):
    reviewer = await _add(client, auth_headers, "Reviewer@Example.com", "REVIEWER")
    assert reviewer["email"] == "reviewer@example.com"
    assert reviewer["role"] == "REVIEWER"
    listed = (await client.get("/api/v1/users", headers=auth_headers)).json()["items"]
    assert {item["role"] for item in listed} == {"ADMIN", "REVIEWER"}

    changed = await client.patch(
        f"/api/v1/users/{reviewer['id']}",
        headers=auth_headers,
        json={"role": "VIEWER", "is_active": False},
    )
    assert changed.json()["role"] == "VIEWER" and changed.json()["is_active"] is False

    removed = await client.delete(
        f"/api/v1/users/{reviewer['id']}", headers=auth_headers
    )
    assert removed.json() == {"removed": True}
    listed = (await client.get("/api/v1/users", headers=auth_headers)).json()["items"]
    assert [item["email"] for item in listed] == [admin.email]
    # The email can be used again.
    await _add(client, auth_headers, "reviewer@example.com", "REVIEWER")


async def test_last_admin_and_self_are_protected(client, auth_headers, admin):
    me = f"/api/v1/users/{admin.id}"
    demote = await client.patch(me, headers=auth_headers, json={"role": "VIEWER"})
    assert demote.status_code == 409
    delete = await client.delete(me, headers=auth_headers)
    assert delete.status_code == 409
    missing = await client.delete(f"/api/v1/users/{uuid4()}", headers=auth_headers)
    assert missing.status_code == 404


async def test_roles_limit_actions(client, auth_headers, app):
    viewer = await _add(client, auth_headers, "viewer@example.com", "VIEWER")
    reviewer = await _add(client, auth_headers, "reviewer@example.com", "REVIEWER")
    as_viewer, as_reviewer = _headers(app, viewer["id"]), _headers(app, reviewer["id"])

    for headers in (as_viewer, as_reviewer):
        assert (await client.get("/api/v1/dashboard", headers=headers)).status_code == 200
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert (await client.get("/api/v1/users", headers=headers)).status_code == 403
        created = await client.post(
            "/api/v1/devices",
            headers=headers,
            json={
                "external_device_id": "x-1",
                "name": "Phone",
                "platform": "android",
                "app_version": "0.1.0",
            },
        )
        assert created.status_code == 403

    review = f"/api/v1/receipts/{uuid4()}/exceptions"
    body = {
        "command_id": str(uuid4()),
        "expected_updated_at": "2026-01-01T00:00:00+00:00",
        "field": "amount",
        "action": "CONFIRM",
        "value": "1.00",
    }
    viewer_review = await client.post(review, headers=as_viewer, json=body)
    assert viewer_review.status_code == 403
    # A reviewer passes the role check (the receipt itself does not exist).
    reviewer_review = await client.post(review, headers=as_reviewer, json=body)
    assert reviewer_review.status_code == 404
