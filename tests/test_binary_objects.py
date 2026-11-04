import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from relay.application.binaries import BinaryService
from relay.infrastructure.binary_models import BinaryObjectRow, UploadSessionRow
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.repositories import SqlUnitOfWork


def png(width: int = 2, height: int = 3, suffix: bytes = b"") -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + suffix
    )


async def provision(
    client: AsyncClient, auth_headers: dict[str, str], external_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    created = await client.post(
        "/api/v1/devices",
        headers=auth_headers,
        json={
            "external_device_id": external_id,
            "name": f"Image test device {external_id}",
            "platform": "android",
            "app_version": "0.4.0",
        },
    )
    assert created.status_code == 201
    device = created.json()
    challenge = await client.post(
        f"/api/v1/devices/{device['id']}/enrollment-challenges",
        headers=auth_headers,
        json={"replace_existing": False},
    )
    enrolled = await client.post(
        "/api/v1/device/enroll",
        json={
            "challenge": challenge.json()["challenge"],
            "installation_id": str(uuid4()),
            "name": "Android image agent",
            "platform": "android",
            "app_version": "0.4.0",
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
                    "images": {
                        "enabled": True,
                        "sources": [{"id": "approved-folder", "type": "saf_tree"}],
                        "rules": [
                            {
                                "id": "new-png",
                                "enabled": True,
                                "source_id": "approved-folder",
                                "mime_types": ["image/png"],
                                "filename": {"mode": "suffix", "value": ".png"},
                                "max_bytes": 1024 * 1024,
                            }
                        ],
                    }
                }
            },
        },
    )
    assert configured.status_code == 200, configured.text
    return device, enrolled.json()


def device_headers(bundle: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {bundle['access_token']}"}


def request(
    bundle: dict[str, Any], data: bytes, **changes: object
) -> dict[str, object]:
    stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    value: dict[str, object] = {
        "upload_id": str(uuid4()),
        "event_id": str(uuid4()),
        "installation_id": bundle["installation_id"],
        "content_hash": hashlib.sha256(data).hexdigest(),
        "content_type": "image/png",
        "byte_size": len(data),
        "width": 2,
        "height": 3,
        "source_type": "saf_tree",
        "source_id": "approved-folder",
        "display_name": "candidate.png",
        "config_version": 2,
        "rule_id": "new-png",
        "occurred_at": stamp,
        "captured_at": stamp,
        "media_at": stamp,
    }
    value.update(changes)
    return value


async def initialize(
    client: AsyncClient, bundle: dict[str, Any], value: dict[str, object]
) -> Any:
    return await client.post(
        "/api/v1/device/objects/uploads",
        headers=device_headers(bundle),
        json=value,
    )


@pytest.mark.asyncio
async def test_resumable_upload_links_event_and_admin_can_download_and_delete(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    device, bundle = await provision(client, auth_headers, "binary-happy")
    data = png(suffix=b"private-image-marker")
    started = await initialize(client, bundle, request(bundle, data))
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    assert started.json()["status"] == "upload_required"
    split = 11
    first = await client.put(
        f"/api/v1/device/objects/uploads/{session_id}/chunks?offset=0",
        headers={**device_headers(bundle), "Content-Type": "application/octet-stream"},
        content=data[:split],
    )
    assert first.json()["uploaded_bytes"] == split
    duplicate = await client.put(
        f"/api/v1/device/objects/uploads/{session_id}/chunks?offset=0",
        headers={**device_headers(bundle), "Content-Type": "application/octet-stream"},
        content=data[:split],
    )
    assert duplicate.json()["status"] == "duplicate"
    second = await client.put(
        f"/api/v1/device/objects/uploads/{session_id}/chunks?offset={split}",
        headers={**device_headers(bundle), "Content-Type": "application/octet-stream"},
        content=data[split:],
    )
    assert second.json()["uploaded_bytes"] == len(data)
    finalized = await client.post(
        f"/api/v1/device/objects/uploads/{session_id}/finalize",
        headers=device_headers(bundle),
    )
    assert finalized.status_code == 200, finalized.text
    object_id = finalized.json()["object"]["id"]
    assert finalized.json()["event_status"] == "accepted"
    retry = await client.post(
        f"/api/v1/device/objects/uploads/{session_id}/finalize",
        headers=device_headers(bundle),
    )
    assert retry.json()["object"]["id"] == object_id
    assert retry.json()["event_status"] == "duplicate"

    events = await client.get(
        f"/api/v1/events?device_id={device['id']}&collector=images",
        headers=auth_headers,
    )
    item = events.json()["items"][0]
    assert item["object_id"] == object_id
    assert item["payload"]["object_id"] == object_id
    assert "private-image-marker" not in events.text
    metadata = await client.get(f"/api/v1/objects/{object_id}", headers=auth_headers)
    assert metadata.status_code == 200
    assert "storage_key" not in metadata.text
    content = await client.get(
        f"/api/v1/objects/{object_id}/content", headers=auth_headers
    )
    assert content.content == data
    assert content.headers["Cache-Control"] == "no-store"
    assert content.headers["Content-Type"].startswith("image/png")
    assert "attachment" in content.headers["Content-Disposition"]
    assert (await client.get(f"/api/v1/objects/{object_id}/content")).status_code == 401
    deleted = await client.delete(f"/api/v1/objects/{object_id}", headers=auth_headers)
    assert deleted.json() == {"deleted": True}
    async with app.state.database.session() as session:
        assert await session.scalar(select(func.count()).select_from(RawEventRow)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(BinaryObjectRow)) == 0
        )


@pytest.mark.asyncio
async def test_conflicting_event_finalization_does_not_leave_orphan_storage(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    _, bundle = await provision(client, auth_headers, "binary-conflict-cleanup")
    event_id = str(uuid4())

    async def complete(data: bytes) -> Any:
        started = await initialize(
            client, bundle, request(bundle, data, event_id=event_id)
        )
        session_id = started.json()["session_id"]
        uploaded = await client.put(
            f"/api/v1/device/objects/uploads/{session_id}/chunks?offset=0",
            headers={
                **device_headers(bundle),
                "Content-Type": "application/octet-stream",
            },
            content=data,
        )
        assert uploaded.status_code == 200
        return await client.post(
            f"/api/v1/device/objects/uploads/{session_id}/finalize",
            headers=device_headers(bundle),
        )

    assert (await complete(png(suffix=b"first"))).status_code == 200
    assert (await complete(png(suffix=b"second"))).status_code == 409
    assert len(list((app.state.object_storage.root / "objects").rglob("*.blob"))) == 1
    async with app.state.database.session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(BinaryObjectRow)) == 1
        )


@pytest.mark.asyncio
async def test_upload_identity_offsets_hash_mime_and_cross_device_are_enforced(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _, first = await provision(client, auth_headers, "binary-isolation-a")
    _, second = await provision(client, auth_headers, "binary-isolation-b")
    data = png()
    value = request(first, data)
    started = await initialize(client, first, value)
    session_id = started.json()["session_id"]
    changed = await initialize(client, first, {**value, "width": 4})
    assert changed.status_code == 409
    cross = await client.put(
        f"/api/v1/device/objects/uploads/{session_id}/chunks?offset=0",
        headers={**device_headers(second), "Content-Type": "application/octet-stream"},
        content=data,
    )
    assert cross.status_code == 404
    missing = await client.put(
        f"/api/v1/device/objects/uploads/{session_id}/chunks?offset=1",
        headers={**device_headers(first), "Content-Type": "application/octet-stream"},
        content=data[:-1],
    )
    assert missing.status_code == 409
    tampered = bytearray(data)
    tampered[8] ^= 1
    accepted = await client.put(
        f"/api/v1/device/objects/uploads/{session_id}/chunks?offset=0",
        headers={**device_headers(first), "Content-Type": "application/octet-stream"},
        content=bytes(tampered),
    )
    assert accepted.status_code == 200
    mismatch = await client.post(
        f"/api/v1/device/objects/uploads/{session_id}/finalize",
        headers=device_headers(first),
    )
    assert mismatch.status_code == 422

    spoofed = request(first, data, content_type="image/jpeg")
    assert (await initialize(client, first, spoofed)).status_code == 422
    unsupported = request(first, data, content_type="image/gif")
    assert (await initialize(client, first, unsupported)).status_code == 422
    malformed_data = b"\x89PNG\r\n\x1a\n" + b"not-an-ihdr"
    malformed = request(first, malformed_data)
    malformed_started = await initialize(client, first, malformed)
    malformed_session = malformed_started.json()["session_id"]
    assert (
        await client.put(
            f"/api/v1/device/objects/uploads/{malformed_session}/chunks?offset=0",
            headers={
                **device_headers(first),
                "Content-Type": "application/octet-stream",
            },
            content=malformed_data,
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/device/objects/uploads/{malformed_session}/finalize",
            headers=device_headers(first),
        )
    ).status_code == 422
    wrong_installation = request(first, data, installation_id=second["installation_id"])
    assert (await initialize(client, first, wrong_installation)).status_code == 422


@pytest.mark.asyncio
async def test_duplicate_content_is_installation_scoped_and_needs_no_second_upload(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _, bundle = await provision(client, auth_headers, "binary-dedupe")
    data = png()
    first_request = request(bundle, data)
    first = await initialize(client, bundle, first_request)
    session_id = first.json()["session_id"]
    await client.put(
        f"/api/v1/device/objects/uploads/{session_id}/chunks?offset=0",
        headers={**device_headers(bundle), "Content-Type": "application/octet-stream"},
        content=data,
    )
    finalized = await client.post(
        f"/api/v1/device/objects/uploads/{session_id}/finalize",
        headers=device_headers(bundle),
    )
    object_id = finalized.json()["object"]["id"]
    duplicate = await initialize(client, bundle, request(bundle, data))
    assert duplicate.json()["status"] == "finalized"
    assert duplicate.json()["object_id"] == object_id
    assert duplicate.json()["event_status"] == "accepted"


@pytest.mark.asyncio
async def test_session_capacity_chunk_bound_and_abandoned_cleanup(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    _, bundle = await provision(client, auth_headers, "binary-bounds")
    app.state.settings.image_max_active_uploads = 1
    data = png()
    started = await initialize(client, bundle, request(bundle, data))
    assert started.status_code == 200
    assert (
        await initialize(client, bundle, request(bundle, data + b"2"))
    ).status_code == 409
    oversized = await client.put(
        f"/api/v1/device/objects/uploads/{started.json()['session_id']}/chunks?offset=0",
        headers={**device_headers(bundle), "Content-Type": "application/octet-stream"},
        content=b"x" * (256 * 1024 + 1),
    )
    assert oversized.status_code == 413
    async with app.state.database.session() as session:
        row = await session.get(UploadSessionRow, UUID(started.json()["session_id"]))
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        result = await BinaryService(
            SqlUnitOfWork(session), app.state.object_storage
        ).purge_expired()
        assert result.sessions == 1
    staging = list((app.state.object_storage.root / "staging").glob("*"))
    assert staging == []


@pytest.mark.asyncio
async def test_image_configuration_requires_safe_declarative_sources_and_rules(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/devices",
        headers=auth_headers,
        json={
            "external_device_id": "binary-unsafe-config",
            "name": "Unsafe",
            "platform": "android",
            "app_version": "0.4.0",
        },
    )
    settings = {
        "collectors": {
            "images": {
                "enabled": True,
                "sources": [{"id": "all-files", "type": "filesystem_crawl"}],
                "rules": [
                    {
                        "id": "ocr-payment",
                        "enabled": True,
                        "source_id": "all-files",
                        "regex": ".*",
                    }
                ],
            }
        }
    }
    response = await client.put(
        f"/api/v1/devices/{created.json()['id']}/configuration",
        headers=auth_headers,
        json={"expected_version": 1, "schema_version": 1, "settings": settings},
    )
    assert response.status_code == 422
