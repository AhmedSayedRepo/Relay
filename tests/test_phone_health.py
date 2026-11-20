"""Why a receipt phone went quiet, from the app's health report."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid3

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from relay.infrastructure.models import DeviceRow
from relay.infrastructure.phone_health import (
    describe,
    gap_reason,
    risks,
    stored,
    summary,
)
from tests.test_agent import agent_headers, provision

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
BEFORE = NOW - timedelta(hours=14)


def test_a_restart_during_the_silence_means_the_phone_was_off() -> None:
    report = {"boot_at": (NOW - timedelta(hours=1)).isoformat()}
    assert gap_reason(report, BEFORE, NOW)["reason"] == "phone_off"


def test_android_closing_the_app_is_named() -> None:
    at = (NOW - timedelta(hours=3)).isoformat()
    killed = {"exits": [{"at": at, "reason": "low_memory"}]}
    stopped = {"exits": [{"at": at, "reason": "force_stopped"}]}
    assert gap_reason(killed, BEFORE, NOW)["detail"] == "low_memory"
    assert gap_reason(stopped, BEFORE, NOW)["reason"] == "force_stopped"
    # An exit before the silence explains nothing.
    old = {
        "exits": [
            {"at": (BEFORE - timedelta(days=1)).isoformat(), "reason": "low_memory"}
        ]
    }
    assert gap_reason(old, BEFORE, NOW)["reason"] == "no_internet"


def test_a_short_pause_is_not_a_silence() -> None:
    assert gap_reason({}, NOW - timedelta(minutes=20), NOW) is None


def test_risks_and_the_last_silence_are_kept() -> None:
    report = {
        "battery_unrestricted": False,
        "folders_ok": False,
        "battery_percent": 9,
        "charging": False,
    }
    assert risks(report) == ["folder_lost", "battery_restricted", "battery_low"]
    assert risks({"sms_allowed": False})[0] == "sms_denied"
    # Unrestricted, but rarely opened: no battery warning.
    assert risks({"battery_unrestricted": True, "standby_bucket": "rare"}) == []
    assert risks({"battery_unrestricted": False, "standby_bucket": "rare"}) == [
        "battery_restricted"
    ]
    first = stored(report | {"boot_at": NOW.isoformat()}, BEFORE, NOW, None)
    # A quick check-in later keeps the explained silence.
    later = stored({}, NOW, NOW + timedelta(minutes=15), first)
    assert later["gap"]["reason"] == "phone_off"
    assert "phone was off" in describe(summary(later))
    assert describe(None).startswith("no health report")
    crashed = summary(
        {"last_crash": {"at": NOW.isoformat(), "error": "NPE | Sync.run:12"}}
    )
    assert crashed is not None and "Sync.run:12" in describe(crashed)


@pytest.mark.asyncio
async def test_heartbeat_stores_the_health_report(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    device, bundle = await provision(client, auth_headers, external_id="health")
    headers = agent_headers(bundle)
    await client.get("/api/v1/device/configuration", headers=headers)
    health = {
        "boot_at": datetime.now(UTC).isoformat(),
        "battery_unrestricted": False,
        "battery_percent": 55,
        "charging": True,
        "folders_ok": True,
        "exits": [{"at": datetime.now(UTC).isoformat(), "reason": "low_memory"}],
        "from_a_newer_app": "ignored",
    }
    response = await client.post(
        "/api/v1/device/heartbeat",
        headers=headers,
        json={"app_version": "0.8.1", "current_config_version": 1, "health": health},
    )
    assert response.status_code == 200, response.text
    async with app.state.database.session() as session:
        row = await session.scalar(
            select(DeviceRow).where(DeviceRow.id == UUID(device["id"]))
        )
        assert row is not None and row.health is not None
        assert row.health["battery_percent"] == 55
        assert "from_a_newer_app" not in row.health
        assert risks(row.health) == ["battery_restricted"]


def test_warning_email_says_why_the_phone_is_silent() -> None:
    from zoneinfo import ZoneInfo

    from relay.infrastructure.alerts import render_email

    html = render_email(
        [
            {
                "kind": "silent_phone",
                "device": "Cashier <1>",
                "since": BEFORE.isoformat(),
                "health": {"gap": None, "risks": ["battery_restricted"]},
            },
            {
                "kind": "sms_without_receipt",
                "amount": "590.00",
                "currency": "EGP",
                "since": NOW.isoformat(),
            },
        ],
        ZoneInfo("Africa/Cairo"),
        NOW,
    )
    assert "Cashier &lt;1&gt;" in html
    assert "battery saving limits the app" in html
    assert "590.00 EGP" in html and "2 warnings" in html


@pytest.mark.asyncio
async def test_the_same_bank_sms_caught_up_again_is_stored_once(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    from relay.infrastructure.event_models import RawEventRow
    from tests.test_events import event, headers
    from tests.test_events import provision as provision_sms

    _, bundle = await provision_sms(client, auth_headers, "sms-twice-phone")
    first = event(bundle["installation_id"])
    # Caught up from the inbox: an id named after the SMS (version 3).
    again = event(bundle["installation_id"], event_id=str(uuid3(NAMESPACE_URL, "sms")))
    # A second live SMS with the same text and time is kept (two payments).
    live = event(bundle["installation_id"])
    again["occurred_at"] = first["occurred_at"]
    statuses = []
    for item in (first, again):
        item["occurred_at"] = first["occurred_at"]
        response = await client.post(
            "/api/v1/device/events/batch",
            headers=headers(bundle),
            json={"events": [item]},
        )
        statuses.append(response.json()["results"][0]["status"])
    assert statuses == ["accepted", "duplicate"]
    live["occurred_at"] = first["occurred_at"]
    response = await client.post(
        "/api/v1/device/events/batch", headers=headers(bundle), json={"events": [live]}
    )
    assert response.json()["results"][0]["status"] == "accepted"
    async with app.state.database.session() as session:
        rows = (
            await session.scalars(
                select(RawEventRow).where(RawEventRow.collector == "sms")
            )
        ).all()
    assert len(rows) == 2
