from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from relay.application.events import EventService
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.repositories import SqlUnitOfWork


async def provision(
    client: AsyncClient, auth_headers: dict[str, str], external_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    created = await client.post(
        "/api/v1/devices",
        headers=auth_headers,
        json={
            "external_device_id": external_id,
            "name": "SMS test device",
            "platform": "android",
            "app_version": "0.3.0",
        },
    )
    assert created.status_code == 201
    device = created.json()
    challenge = await client.post(
        f"/api/v1/devices/{device['id']}/enrollment-challenges",
        headers=auth_headers,
        json={"replace_existing": False},
    )
    installation_id = str(uuid4())
    enrolled = await client.post(
        "/api/v1/device/enroll",
        json={
            "challenge": challenge.json()["challenge"],
            "installation_id": installation_id,
            "name": "Android agent",
            "platform": "android",
            "app_version": "0.3.0",
        },
    )
    assert enrolled.status_code == 201
    configured = await client.put(
        f"/api/v1/devices/{device['id']}/configuration",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "schema_version": 1,
            "settings": {
                "collectors": {
                    "sms": {
                        "enabled": True,
                        "rules": [
                            {
                                "id": "generic-alert",
                                "enabled": True,
                                "sender": {"mode": "prefix", "value": "ALERT"},
                                "body": {"any_contains": ["notice", "update"]},
                            }
                        ],
                    }
                }
            },
        },
    )
    assert configured.status_code == 200
    return device, enrolled.json()


def headers(bundle: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {bundle['access_token']}"}


def event(
    installation_id: str,
    *,
    event_id: str | None = None,
    body: str = "A generic notice",
    sender: str = "ALERT-SVC",
) -> dict[str, object]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "event_id": event_id or str(uuid4()),
        "installation_id": installation_id,
        "collector": "sms",
        "schema_version": 1,
        "config_version": 2,
        "occurred_at": now,
        "captured_at": now,
        "payload": {"sender": sender, "body": body},
        "metadata": {"rule_id": "generic-alert"},
    }


@pytest.mark.asyncio
async def test_valid_event_batch_admin_visibility_and_no_store(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    device, bundle = await provision(client, auth_headers, "events-valid")
    first = event(bundle["installation_id"])
    accepted = await client.post(
        "/api/v1/device/events/batch",
        headers=headers(bundle),
        json={"events": [first]},
    )
    assert accepted.status_code == 200
    assert accepted.json()["results"] == [
        {"event_id": first["event_id"], "status": "accepted", "reason": None}
    ]
    listing = await client.get(
        f"/api/v1/events?device_id={device['id']}&collector=sms&limit=1",
        headers=auth_headers,
    )
    assert listing.status_code == 200
    assert listing.headers["Cache-Control"] == "no-store"
    item = listing.json()["items"][0]
    assert item["payload"] == first["payload"]
    assert item["config_version"] == 2
    detail = await client.get(f"/api/v1/events/{item['id']}", headers=auth_headers)
    assert detail.status_code == 200 and detail.json() == item


@pytest.mark.asyncio
async def test_duplicate_batches_conflicts_and_independent_mixed_results(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _, bundle = await provision(client, auth_headers, "events-mixed")
    identifier = str(uuid4())
    original = event(bundle["installation_id"], event_id=identifier)
    malformed = {**event(bundle["installation_id"]), "schema_version": 99}
    first = await client.post(
        "/api/v1/device/events/batch",
        headers=headers(bundle),
        json={"events": [original, original, "not-an-event", malformed]},
    )
    assert [item["status"] for item in first.json()["results"]] == [
        "accepted",
        "duplicate",
        "rejected",
        "rejected",
    ]
    assert first.json()["results"][2] == {
        "event_id": "invalid",
        "status": "rejected",
        "reason": "invalid_envelope",
    }
    assert first.json()["results"][3]["reason"] == "unsupported_schema"
    duplicate = await client.post(
        "/api/v1/device/events/batch",
        headers=headers(bundle),
        json={"events": [original]},
    )
    assert duplicate.json()["results"][0]["status"] == "duplicate"
    changed = event(
        bundle["installation_id"], event_id=identifier, body="Changed notice"
    )
    conflict = await client.post(
        "/api/v1/device/events/batch",
        headers=headers(bundle),
        json={"events": [changed]},
    )
    assert conflict.json()["results"][0] == {
        "event_id": identifier,
        "status": "rejected",
        "reason": "event_id_conflict",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"collector": "images"}, "unsupported_collector"),
        ({"schema_version": 2}, "unsupported_schema"),
        ({"occurred_at": "not-a-time"}, "invalid_timestamp"),
        ({"payload": {"sender": "ALERT", "body": "x" * 4097}}, "invalid_payload"),
        (
            {"payload": {"sender": "OTHER", "body": "A generic notice"}},
            "collector_not_enabled_or_unmatched",
        ),
    ],
)
async def test_terminal_item_validation(
    client: AsyncClient,
    auth_headers: dict[str, str],
    change: dict[str, object],
    reason: str,
) -> None:
    _, bundle = await provision(client, auth_headers, f"invalid-{uuid4()}")
    submitted = {**event(bundle["installation_id"]), **change}
    response = await client.post(
        "/api/v1/device/events/batch",
        headers=headers(bundle),
        json={"events": [submitted]},
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["reason"] == reason


@pytest.mark.asyncio
async def test_authentication_revocation_and_installation_ownership(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    first_device, first = await provision(client, auth_headers, "events-auth-a")
    _, second = await provision(client, auth_headers, "events-auth-b")
    wrong = event(second["installation_id"])
    assert (
        await client.post(
            "/api/v1/device/events/batch",
            headers=headers(first),
            json={"events": [wrong]},
        )
    ).json()["results"][0]["reason"] == "wrong_installation"
    assert (
        await client.post(
            "/api/v1/device/events/batch",
            headers=auth_headers,
            json={"events": [event(first["installation_id"])]},
        )
    ).status_code == 401
    assert (
        await client.get("/api/v1/events", headers=headers(first))
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/device/events/batch",
            headers={"Authorization": "Bearer invalid"},
            json={"events": [event(first["installation_id"])]},
        )
    ).status_code == 401
    disabled = await client.patch(
        f"/api/v1/devices/{first_device['id']}",
        headers=auth_headers,
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200
    assert (
        await client.post(
            "/api/v1/device/events/batch",
            headers=headers(first),
            json={"events": [event(first["installation_id"])]},
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_same_event_id_is_scoped_to_installation(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _, first = await provision(client, auth_headers, "events-scope-a")
    _, second = await provision(client, auth_headers, "events-scope-b")
    identifier = str(uuid4())
    outcomes = []
    for bundle in (first, second):
        response = await client.post(
            "/api/v1/device/events/batch",
            headers=headers(bundle),
            json={"events": [event(bundle["installation_id"], event_id=identifier)]},
        )
        outcomes.append(response.json()["results"][0]["status"])
    assert outcomes == ["accepted", "accepted"]


@pytest.mark.asyncio
async def test_retention_and_administrator_device_deletion_do_not_echo_payload(
    client: AsyncClient, auth_headers: dict[str, str], app: Any, capsys
) -> None:
    device, bundle = await provision(client, auth_headers, "events-retention")
    secret_body = "private-sms-body-retention-marker notice"
    for body in (secret_body, "current notice"):
        response = await client.post(
            "/api/v1/device/events/batch",
            headers=headers(bundle),
            json={"events": [event(bundle["installation_id"], body=body)]},
        )
        assert response.json()["results"][0]["status"] == "accepted"
    async with app.state.database.session() as session:
        oldest = await session.scalar(
            select(RawEventRow).order_by(RawEventRow.received_at)
        )
        assert oldest is not None
        oldest.delete_after = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        assert await EventService(SqlUnitOfWork(session)).purge_expired() == 1
        assert await session.scalar(select(func.count()).select_from(RawEventRow)) == 1
    deleted = await client.delete(
        f"/api/v1/devices/{device['id']}/events", headers=auth_headers
    )
    assert deleted.json() == {"deleted": 1}
    captured = capsys.readouterr()
    assert secret_body not in captured.out + captured.err


@pytest.mark.asyncio
async def test_sms_configuration_schema_rejects_unsafe_rules(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/devices",
        headers=auth_headers,
        json={
            "external_device_id": "unsafe-rules",
            "name": "Rules",
            "platform": "android",
            "app_version": "0.3.0",
        },
    )
    unsafe = {
        "collectors": {
            "sms": {
                "enabled": True,
                "rules": [
                    {
                        "id": "bad",
                        "enabled": True,
                        "sender": {"mode": "regex", "value": "(a+)+$"},
                    }
                ],
            }
        }
    }
    response = await client.put(
        f"/api/v1/devices/{created.json()['id']}/configuration",
        headers=auth_headers,
        json={"expected_version": 1, "schema_version": 1, "settings": unsafe},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_admin_event_filters_are_bounded_and_require_ordered_aware_times(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    device, bundle = await provision(client, auth_headers, "events-filters")
    submitted = event(bundle["installation_id"])
    accepted = await client.post(
        "/api/v1/device/events/batch",
        headers=headers(bundle),
        json={"events": [submitted]},
    )
    assert accepted.json()["results"][0]["status"] == "accepted"

    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    filtered = await client.get(
        "/api/v1/events",
        headers=auth_headers,
        params={"device_id": device["id"], "occurred_from": future},
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []

    naive = await client.get(
        "/api/v1/events?occurred_from=2024-01-01T00:00:00",
        headers=auth_headers,
    )
    assert naive.status_code == 422
    reversed_range = await client.get(
        "/api/v1/events"
        "?occurred_from=2024-01-02T00:00:00Z"
        "&occurred_to=2024-01-01T00:00:00Z",
        headers=auth_headers,
    )
    assert reversed_range.status_code == 422
    assert (
        await client.get("/api/v1/events?limit=101", headers=auth_headers)
    ).status_code == 422
