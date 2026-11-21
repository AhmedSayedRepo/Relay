"""Cashier shifts: open until closed; closing opens the next one."""

import pytest


@pytest.mark.asyncio
async def test_closing_a_shift_opens_the_next_one(client, auth_headers):
    first = await client.get("/api/v1/shifts", headers=auth_headers)
    assert first.status_code == 200, first.text
    items = first.json()["items"]
    assert [item["number"] for item in items] == [1] and items[0]["open"] is True
    closed = await client.post(
        "/api/v1/shifts/close", headers=auth_headers, json={"note": "evening"}
    )
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["closed"]["number"] == 1 and body["closed"]["note"] == "evening"
    assert body["opened"]["number"] == 2 and body["opened"]["open"] is True
    assert body["closed"]["closed_at"] == body["opened"]["opened_at"]
    after = await client.get("/api/v1/shifts", headers=auth_headers)
    shifts = after.json()["items"]
    assert [(item["number"], item["open"]) for item in shifts] == [
        (1, False),
        (2, True),
    ]
    assert after.json()["open"]["number"] == 2
