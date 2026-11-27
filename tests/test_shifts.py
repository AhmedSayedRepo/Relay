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


async def test_the_automatic_close_time_is_a_setting(client, auth_headers):
    saved = await client.put(
        "/api/v1/settings/shift-close",
        json={"time": "02:00", "language": "ar"},
        headers=auth_headers,
    )
    assert saved.status_code == 200
    assert saved.json() == {"time": "02:00", "enabled": True, "language": "ar"}
    paused = await client.put(
        "/api/v1/settings/shift-close",
        json={"time": "02:00", "enabled": False, "language": "ar"},
        headers=auth_headers,
    )
    # Off keeps the time.
    assert paused.json() == {"time": "02:00", "enabled": False, "language": "ar"}
    bad = await client.put(
        "/api/v1/settings/shift-close", json={"time": "25:00"}, headers=auth_headers
    )
    assert bad.status_code == 422
    off = await client.put(
        "/api/v1/settings/shift-close", json={"time": None}, headers=auth_headers
    )
    assert off.json()["time"] is None


async def test_the_shift_closes_by_itself_at_the_set_time(app):
    from datetime import UTC, time, timedelta
    from zoneinfo import ZoneInfo

    from relay.infrastructure.shifts import close_when_due, current

    zone = ZoneInfo(app.state.settings.receipt_timezone)
    async with app.state.database.session() as session:
        opened = await current(session, zone)  # opens at midnight
        midnight = opened.opened_at.astimezone(zone)
        at = time(0, 30)
        early = (midnight + timedelta(minutes=10)).astimezone(UTC)
        assert await close_when_due(session, zone, at, early) is None
        late = (midnight + timedelta(minutes=45)).astimezone(UTC)
        closed, following = await close_when_due(session, zone, at, late)
        assert closed.closed_at.astimezone(zone).strftime("%H:%M") == "00:30"
        assert closed.closed_by is None and "automatically" in closed.note
        assert following.number == closed.number + 1
        # Once closed for today, the next pass leaves the new shift alone.
        assert await close_when_due(session, zone, at, late) is None
