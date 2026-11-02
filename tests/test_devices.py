from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response


async def test_create_get_list_and_update_device(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
) -> None:
    created = await client.post(
        "/api/v1/devices", json=device_input, headers=auth_headers
    )
    assert created.status_code == 201, created.text
    device = created.json()
    assert UUID(device["id"])
    for field, expected in device_input.items():
        assert device[field] == expected
    assert device["status"] == "PENDING"
    assert device["last_seen_at"] is None
    assert datetime.fromisoformat(device["created_at"]).tzinfo is not None
    assert datetime.fromisoformat(device["updated_at"]).tzinfo is not None
    path = f"/api/v1/devices/{device['id']}"
    fetched = await client.get(path, headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json() == device
    listing = await client.get("/api/v1/devices", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["items"] == [device]
    assert listing.json()["offset"] == 0
    updated = await client.patch(
        path, json={"name": "Updated name", "status": "ACTIVE"}, headers=auth_headers
    )
    assert updated.status_code == 200
    changed = updated.json()
    assert changed["name"] == "Updated name"
    assert changed["status"] == "ACTIVE"
    assert changed["external_device_id"] == device["external_device_id"]
    assert changed["app_version"] == device["app_version"]
    assert changed["created_at"] == device["created_at"]
    assert datetime.fromisoformat(changed["updated_at"]) >= datetime.fromisoformat(
        device["updated_at"]
    )
    assert (await client.get(path, headers=auth_headers)).json() == changed


async def test_duplicate_external_id_is_conflict_and_does_not_poison_next_request(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
    assert_error: Callable[[Response, int], dict[str, Any]],
) -> None:
    first = await client.post(
        "/api/v1/devices", json=device_input, headers=auth_headers
    )
    assert first.status_code == 201
    duplicate = await client.post(
        "/api/v1/devices", json=device_input, headers=auth_headers
    )
    assert_error(duplicate, 409)
    assert device_input["external_device_id"] not in duplicate.text
    different = {
        **device_input,
        "external_device_id": "independent-device",
        "name": "Independent device",
    }
    assert (
        await client.post("/api/v1/devices", json=different, headers=auth_headers)
    ).status_code == 201
    assert (
        len((await client.get("/api/v1/devices", headers=auth_headers)).json()["items"])
        == 2
    )


async def test_disabled_device_is_manageable_and_can_be_reactivated(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
) -> None:
    created = await client.post(
        "/api/v1/devices",
        json={**device_input, "status": "DISABLED"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    path = f"/api/v1/devices/{created.json()['id']}"
    assert (await client.get(path, headers=auth_headers)).json()["status"] == "DISABLED"
    assert (await client.get("/api/v1/devices", headers=auth_headers)).json()["items"][
        0
    ]["status"] == "DISABLED"
    enabled = await client.patch(path, json={"status": "ACTIVE"}, headers=auth_headers)
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "ACTIVE"


async def test_patch_distinguishes_missing_app_version_from_explicit_null(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
) -> None:
    created = await client.post(
        "/api/v1/devices", json=device_input, headers=auth_headers
    )
    path = f"/api/v1/devices/{created.json()['id']}"
    preserved = await client.patch(path, json={"name": "Renamed"}, headers=auth_headers)
    assert preserved.status_code == 200
    assert preserved.json()["app_version"] == device_input["app_version"]
    cleared = await client.patch(path, json={"app_version": None}, headers=auth_headers)
    assert cleared.status_code == 200
    assert cleared.json()["app_version"] is None
    assert cleared.json()["name"] == "Renamed"
    assert (await client.get(path, headers=auth_headers)).json()["app_version"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("external_device_id", ""),
        ("external_device_id", "x" * 129),
        ("external_device_id", "untrusted\x00device"),
        ("name", ""),
        ("name", "   "),
        ("name", "x" * 121),
        ("name", "untrusted\nname"),
        ("platform", ""),
        ("platform", "ANDROID"),
        ("platform", "x" * 33),
        ("platform", 123),
        ("status", "UNKNOWN"),
        ("status", None),
        ("app_version", ""),
        ("app_version", "x" * 65),
        ("app_version", "untrusted\tversion"),
        ("phone_number", "+123456789"),
        ("id", str(uuid4())),
    ],
)
async def test_invalid_device_inputs_are_rejected_without_persistence(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
    assert_error: Callable[[Response, int], dict[str, Any]],
    field: str,
    value: Any,
) -> None:
    response = await client.post(
        "/api/v1/devices", json={**device_input, field: value}, headers=auth_headers
    )
    assert_error(response, 422)
    assert "untrusted" not in response.text
    assert (await client.get("/api/v1/devices", headers=auth_headers)).json()[
        "items"
    ] == []


@pytest.mark.parametrize("field", ["external_device_id", "name", "platform"])
async def test_required_device_fields(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
    assert_error: Callable[[Response, int], dict[str, Any]],
    field: str,
) -> None:
    del device_input[field]
    assert_error(
        await client.post("/api/v1/devices", json=device_input, headers=auth_headers),
        422,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": None},
        {"status": None},
        {"status": "INVALID"},
        {"name": ""},
        {"external_device_id": "replacement"},
        {"platform": "linux"},
        {"last_seen_at": "2026-01-01T00:00:00Z"},
        {"id": str(uuid4())},
    ],
)
async def test_invalid_patch_does_not_mutate_device(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
    assert_error: Callable[[Response, int], dict[str, Any]],
    payload: dict[str, Any],
) -> None:
    created = await client.post(
        "/api/v1/devices", json=device_input, headers=auth_headers
    )
    before = created.json()
    path = f"/api/v1/devices/{before['id']}"
    assert_error(await client.patch(path, json=payload, headers=auth_headers), 422)
    assert (await client.get(path, headers=auth_headers)).json() == before


async def test_missing_device_and_invalid_uuid(
    client: AsyncClient,
    auth_headers: dict[str, str],
    assert_error: Callable[[Response, int], dict[str, Any]],
) -> None:
    path = f"/api/v1/devices/{uuid4()}"
    assert_error(await client.get(path, headers=auth_headers), 404)
    assert_error(
        await client.patch(path, json={"name": "Missing"}, headers=auth_headers), 404
    )
    assert_error(
        await client.get("/api/v1/devices/not-a-uuid", headers=auth_headers), 422
    )


async def test_device_list_pagination_has_no_duplicates_or_missing_rows(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
) -> None:
    created_ids = []
    for number in range(5):
        response = await client.post(
            "/api/v1/devices",
            json={
                **device_input,
                "external_device_id": f"pagination-{number}",
                "name": f"Device {number}",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        created_ids.append(response.json()["id"])
    ids = []
    for offset in (0, 2, 4):
        page = await client.get(
            "/api/v1/devices",
            params={"limit": 2, "offset": offset},
            headers=auth_headers,
        )
        assert page.status_code == 200
        body = page.json()
        assert body["limit"] == 2 and body["offset"] == offset
        assert len(body["items"]) <= 2
        ids.extend(device["id"] for device in body["items"])
    assert ids == created_ids
    beyond = await client.get("/api/v1/devices?offset=99", headers=auth_headers)
    assert beyond.json()["items"] == []


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc"])
async def test_invalid_pagination(
    client: AsyncClient,
    auth_headers: dict[str, str],
    assert_error: Callable[[Response, int], dict[str, Any]],
    query: str,
) -> None:
    assert_error(
        await client.get(f"/api/v1/devices?{query}", headers=auth_headers), 422
    )


async def test_sql_metacharacters_are_stored_as_data_not_executed(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
) -> None:
    identifier = "device'; DROP TABLE devices; --"
    response = await client.post(
        "/api/v1/devices",
        json={
            **device_input,
            "external_device_id": identifier,
            "name": "Unicode device",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert response.json()["external_device_id"] == identifier
    normal = await client.post(
        "/api/v1/devices", json=device_input, headers=auth_headers
    )
    assert normal.status_code == 201
    assert (
        len((await client.get("/api/v1/devices", headers=auth_headers)).json()["items"])
        == 2
    )


@pytest.mark.asyncio
async def test_device_names_are_unique_among_devices_in_use(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_input: dict[str, str],
) -> None:
    first = await client.post(
        "/api/v1/devices", json=device_input, headers=auth_headers
    )
    assert first.status_code == 201
    same = {
        **device_input,
        "external_device_id": "second-phone",
        "name": "LAB DEVICE",
    }
    assert (
        await client.post("/api/v1/devices", json=same, headers=auth_headers)
    ).status_code == 409
    path = f"/api/v1/devices/{first.json()['id']}"
    assert (
        await client.patch(path, json={"status": "DISABLED"}, headers=auth_headers)
    ).status_code == 200
    second = await client.post(
        "/api/v1/devices",
        json={**same, "name": device_input["name"]},
        headers=auth_headers,
    )
    assert second.status_code == 201
    # The disabled one cannot come back while its name is taken.
    assert (
        await client.patch(path, json={"status": "PENDING"}, headers=auth_headers)
    ).status_code == 409
    assert (
        await client.patch(
            path, json={"status": "PENDING", "name": "Old phone"}, headers=auth_headers
        )
    ).status_code == 200
