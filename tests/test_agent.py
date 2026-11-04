import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from relay.domain.agent import validate_settings
from relay.domain.errors import InvalidDevice
from relay.infrastructure.agent_models import ChallengeRow, CredentialRow


async def create_device(
    client: AsyncClient,
    auth_headers: dict[str, str],
    *,
    external_id: str = "agent-lab-001",
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/devices",
        headers=auth_headers,
        json={
            "external_device_id": external_id,
            "name": (
                "Administrator name"
                if external_id == "agent-lab-001"
                else f"Administrator {external_id}"
            ),
            "platform": "android",
            "app_version": "0.1.0",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def challenge(
    client: AsyncClient,
    auth_headers: dict[str, str],
    device_id: str,
    *,
    replace_existing: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/devices/{device_id}/enrollment-challenges",
        headers=auth_headers,
        json={"replace_existing": replace_existing},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def enroll(
    client: AsyncClient,
    enrollment_challenge: str,
    *,
    installation_id: UUID | None = None,
    name: str = "Android supplied name",
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/device/enroll",
        json={
            "challenge": enrollment_challenge,
            "installation_id": str(installation_id or uuid4()),
            "name": name,
            "platform": "android",
            "app_version": "0.2.0",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def agent_headers(bundle: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {bundle['access_token']}"}


async def provision(
    client: AsyncClient, auth_headers: dict[str, str], *, external_id: str = "agent-1"
) -> tuple[dict[str, Any], dict[str, Any]]:
    device = await create_device(client, auth_headers, external_id=external_id)
    value = await challenge(client, auth_headers, device["id"])
    return device, await enroll(client, value["challenge"])


@pytest.mark.asyncio
async def test_enrollment_preserves_admin_identity_and_uses_distinct_token_domain(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    device = await create_device(client, auth_headers)
    initial = await client.get(
        f"/api/v1/devices/{device['id']}/configuration", headers=auth_headers
    )
    assert initial.status_code == 200
    assert initial.json() == {"schema_version": 1, "version": 1, "settings": {}}

    value = await challenge(client, auth_headers, device["id"])
    bundle = await enroll(client, value["challenge"])
    assert bundle["device_id"] == device["id"]
    assert bundle["device_name"] == "Administrator name"
    assert bundle["token_type"] == "bearer"
    assert bundle["expires_in"] == 900
    assert bundle["refresh_token"].count(".") == 1

    state = await client.get(
        f"/api/v1/devices/{device['id']}/state", headers=auth_headers
    )
    assert state.status_code == 200
    assert state.json()["name"] == "Administrator name"
    assert state.json()["status"] == "ACTIVE"
    assert state.json()["installation_id"] == bundle["installation_id"]

    assert (
        await client.get(
            f"/api/v1/devices/{device['id']}/state", headers=agent_headers(bundle)
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/device/heartbeat",
            headers=auth_headers,
            json={"app_version": "0.2.0", "current_config_version": 0},
        )
    ).status_code == 401
    claims = jwt.decode(bundle["access_token"], options={"verify_signature": False})
    assert claims["aud"] == "relay-device"
    assert claims["token_type"] == "device_access"
    assert set(("device_id", "credential_id", "sub")) <= claims.keys()


@pytest.mark.asyncio
async def test_challenge_is_single_use_and_failed_attempt_limit_is_persistent(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    device = await create_device(client, auth_headers, external_id="attempts")
    value = await challenge(client, auth_headers, device["id"])
    challenge_id, _ = value["challenge"].split(".", 1)
    wrong = f"{challenge_id}.{secrets.token_urlsafe(32)}"
    for _ in range(5):
        response = await client.post(
            "/api/v1/device/enroll",
            json={
                "challenge": wrong,
                "installation_id": str(uuid4()),
                "name": "Agent",
                "platform": "android",
                "app_version": "0.2.0",
            },
        )
        assert response.status_code == 401
    assert (
        await client.post(
            "/api/v1/device/enroll",
            json={
                "challenge": value["challenge"],
                "installation_id": str(uuid4()),
                "name": "Agent",
                "platform": "android",
                "app_version": "0.2.0",
            },
        )
    ).status_code == 401
    async with app.state.database.session() as session:
        row = await session.get(ChallengeRow, UUID(challenge_id))
        assert row is not None and row.attempts == 5
        assert row.secret_hash != value["challenge"].split(".", 1)[1]


@pytest.mark.asyncio
async def test_expired_invalid_and_replayed_challenges_are_rejected(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    expired_device = await create_device(client, auth_headers, external_id="expired")
    expired = await challenge(client, auth_headers, expired_device["id"])
    expired_id, _ = expired["challenge"].split(".", 1)
    async with app.state.database.session() as session:
        row = await session.get(ChallengeRow, UUID(expired_id))
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    expired_response = await client.post(
        "/api/v1/device/enroll",
        json={
            "challenge": expired["challenge"],
            "installation_id": str(uuid4()),
            "name": "Agent",
            "platform": "android",
            "app_version": "0.2.0",
        },
    )
    assert expired_response.status_code == 401

    replay_device = await create_device(client, auth_headers, external_id="replayed")
    one_time = await challenge(client, auth_headers, replay_device["id"])
    first = await enroll(client, one_time["challenge"])
    replay_response = await client.post(
        "/api/v1/device/enroll",
        json={
            "challenge": one_time["challenge"],
            "installation_id": str(uuid4()),
            "name": "Agent",
            "platform": "android",
            "app_version": "0.2.0",
        },
    )
    assert first["device_id"] == replay_device["id"]
    assert replay_response.status_code == 401
    malformed = await client.post(
        "/api/v1/device/enroll",
        json={
            "challenge": "not-a-challenge",
            "installation_id": str(uuid4()),
            "name": "Agent",
            "platform": "android",
            "app_version": "0.2.0",
        },
    )
    assert malformed.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotation_is_idempotent_and_replay_revokes_family(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _, bundle = await provision(client, auth_headers, external_id="rotation")
    request_id = str(uuid4())
    body = {
        "refresh_token": bundle["refresh_token"],
        "installation_id": bundle["installation_id"],
        "request_id": request_id,
    }
    first = await client.post("/api/v1/device/renew", json=body)
    assert first.status_code == 200, first.text
    retry = await client.post("/api/v1/device/renew", json=body)
    assert retry.status_code == 200, retry.text
    assert retry.json()["refresh_token"] == first.json()["refresh_token"]
    assert retry.json()["refresh_expires_at"] == first.json()["refresh_expires_at"]

    old_access = await client.get(
        "/api/v1/device/configuration", headers=agent_headers(bundle)
    )
    assert old_access.status_code == 401
    assert (
        await client.get(
            "/api/v1/device/configuration", headers=agent_headers(first.json())
        )
    ).status_code == 200

    replay = await client.post(
        "/api/v1/device/renew", json={**body, "request_id": str(uuid4())}
    )
    assert replay.status_code == 401
    assert (
        await client.get(
            "/api/v1/device/configuration", headers=agent_headers(first.json())
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_configuration_delivery_ack_and_version_rules(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    device, bundle = await provision(client, auth_headers, external_id="config")
    headers = agent_headers(bundle)
    premature = await client.post(
        "/api/v1/device/configuration/ack", headers=headers, json={"version": 1}
    )
    assert premature.status_code == 409

    delivered = await client.get(
        "/api/v1/device/configuration?known_version=1", headers=headers
    )
    assert delivered.status_code == 200
    assert delivered.json()["version"] == 1
    assert (
        await client.post(
            "/api/v1/device/configuration/ack", headers=headers, json={"version": 1}
        )
    ).status_code == 200
    assert (
        await client.get(
            "/api/v1/device/configuration?known_version=1", headers=headers
        )
    ).status_code == 304

    update = await client.put(
        f"/api/v1/devices/{device['id']}/configuration",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "schema_version": 1,
            "settings": {"sync_interval_seconds": 1800, "feature": "inert"},
        },
    )
    assert update.status_code == 200 and update.json()["version"] == 2
    assert (
        await client.put(
            f"/api/v1/devices/{device['id']}/configuration",
            headers=auth_headers,
            json={"expected_version": 1, "schema_version": 1, "settings": {}},
        )
    ).status_code == 409

    # A claimed local version never authorizes an acknowledgement by itself.
    claimed = await client.get(
        "/api/v1/device/configuration?known_version=2", headers=headers
    )
    assert claimed.status_code == 200 and claimed.json() == update.json()
    assert (
        await client.post(
            "/api/v1/device/configuration/ack", headers=headers, json={"version": 2}
        )
    ).status_code == 200
    assert (
        await client.post(
            "/api/v1/device/configuration/ack", headers=headers, json={"version": 1}
        )
    ).status_code == 409
    assert (
        await client.get(
            "/api/v1/device/configuration?known_version=3", headers=headers
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_configuration_and_credentials_are_isolated_between_devices(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    first_device, first = await provision(
        client, auth_headers, external_id="isolated-a"
    )
    _, second = await provision(client, auth_headers, external_id="isolated-b")
    updated = await client.put(
        f"/api/v1/devices/{first_device['id']}/configuration",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "schema_version": 1,
            "settings": {"owner": "first"},
        },
    )
    assert updated.status_code == 200
    first_config = await client.get(
        "/api/v1/device/configuration", headers=agent_headers(first)
    )
    second_config = await client.get(
        "/api/v1/device/configuration", headers=agent_headers(second)
    )
    assert first_config.json()["version"] == 2
    assert first_config.json()["settings"] == {"owner": "first"}
    assert second_config.json() == {"schema_version": 1, "version": 1, "settings": {}}
    cross_ack = await client.post(
        "/api/v1/device/configuration/ack",
        headers=agent_headers(second),
        json={"version": 2},
    )
    assert cross_ack.status_code == 409


@pytest.mark.asyncio
async def test_invalid_malformed_and_expired_device_credentials_are_rejected(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    _, bundle = await provision(client, auth_headers, external_id="credential-errors")
    malformed = await client.get(
        "/api/v1/device/configuration",
        headers={"Authorization": "Bearer definitely-not-a-jwt"},
    )
    assert malformed.status_code == 401
    refresh_id, _ = bundle["refresh_token"].split(".", 1)
    invalid_refresh = await client.post(
        "/api/v1/device/renew",
        json={
            "refresh_token": f"{refresh_id}.{secrets.token_urlsafe(32)}",
            "installation_id": bundle["installation_id"],
            "request_id": str(uuid4()),
        },
    )
    assert invalid_refresh.status_code == 401
    async with app.state.database.session() as session:
        credential = await session.get(CredentialRow, UUID(refresh_id))
        assert credential is not None
        credential.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert (
        await client.get("/api/v1/device/configuration", headers=agent_headers(bundle))
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/device/renew",
            json={
                "refresh_token": bundle["refresh_token"],
                "installation_id": bundle["installation_id"],
                "request_id": str(uuid4()),
            },
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_heartbeat_is_minimal_validated_and_contact_is_coalesced(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    device, bundle = await provision(client, auth_headers, external_id="heartbeat")
    headers = agent_headers(bundle)
    await client.get("/api/v1/device/configuration", headers=headers)
    first = await client.post(
        "/api/v1/device/heartbeat",
        headers=headers,
        json={"app_version": "0.2.0", "current_config_version": 1},
    )
    second = await client.post(
        "/api/v1/device/heartbeat",
        headers=headers,
        json={"app_version": "0.2.0", "current_config_version": 1},
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["last_seen_at"] == second.json()["last_seen_at"]
    assert set(first.json()) == {
        "status",
        "last_seen_at",
        "configuration_version",
        "acknowledged_version",
    }
    assert (
        await client.post(
            "/api/v1/device/heartbeat",
            headers=headers,
            json={
                "app_version": "0.2.0",
                "current_config_version": 2,
                "location": "forbidden",
            },
        )
    ).status_code == 422
    assert (
        await client.get(f"/api/v1/devices/{device['id']}/state", headers=auth_headers)
    ).json()["app_version"] == "0.2.0"


@pytest.mark.asyncio
async def test_replacement_requires_explicit_admin_intent_and_revokes_old_agent(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    device, old = await provision(client, auth_headers, external_id="replace")
    denied = await client.post(
        f"/api/v1/devices/{device['id']}/enrollment-challenges",
        headers=auth_headers,
        json={"replace_existing": False},
    )
    assert denied.status_code == 409
    replacement = await challenge(
        client, auth_headers, device["id"], replace_existing=True
    )
    new = await enroll(client, replacement["challenge"])
    assert new["installation_id"] != old["installation_id"]
    assert (
        await client.get("/api/v1/device/configuration", headers=agent_headers(old))
    ).status_code == 401
    assert (
        await client.get("/api/v1/device/configuration", headers=agent_headers(new))
    ).status_code == 200


@pytest.mark.asyncio
async def test_disable_and_reenable_never_revives_old_credentials(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    device, old = await provision(client, auth_headers, external_id="disabled")
    disabled = await client.patch(
        f"/api/v1/devices/{device['id']}",
        headers=auth_headers,
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200
    assert (
        await client.get("/api/v1/device/configuration", headers=agent_headers(old))
    ).status_code == 401
    assert (
        await client.post(
            f"/api/v1/devices/{device['id']}/enrollment-challenges",
            headers=auth_headers,
            json={"replace_existing": False},
        )
    ).status_code == 409
    assert (
        await client.patch(
            f"/api/v1/devices/{device['id']}",
            headers=auth_headers,
            json={"status": "ACTIVE"},
        )
    ).status_code == 200
    assert (
        await client.get("/api/v1/device/configuration", headers=agent_headers(old))
    ).status_code == 401
    fresh_challenge = await challenge(client, auth_headers, device["id"])
    fresh = await enroll(client, fresh_challenge["challenge"])
    assert fresh["installation_id"] != old["installation_id"]


@pytest.mark.asyncio
async def test_admin_revocation_blocks_access_and_refresh(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    device, bundle = await provision(client, auth_headers, external_id="revoked")
    response = await client.post(
        f"/api/v1/devices/{device['id']}/revoke", headers=auth_headers
    )
    assert response.status_code == 200 and response.json() == {"status": "revoked"}
    assert (
        await client.get("/api/v1/device/configuration", headers=agent_headers(bundle))
    ).status_code == 401
    renew = await client.post(
        "/api/v1/device/renew",
        json={
            "refresh_token": bundle["refresh_token"],
            "installation_id": bundle["installation_id"],
            "request_id": str(uuid4()),
        },
    )
    assert renew.status_code == 401
    state = await client.get(
        f"/api/v1/devices/{device['id']}/state", headers=auth_headers
    )
    assert state.json()["status"] == "PENDING"
    assert state.json()["installation_id"] is None


@pytest.mark.asyncio
async def test_secrets_are_hashed_at_rest_and_never_returned_by_state(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    device = await create_device(client, auth_headers, external_id="at-rest")
    value = await challenge(client, auth_headers, device["id"])
    bundle = await enroll(client, value["challenge"])
    refresh_id, refresh_secret = bundle["refresh_token"].split(".", 1)
    async with app.state.database.session() as session:
        credential = await session.scalar(
            select(CredentialRow).where(CredentialRow.id == UUID(refresh_id))
        )
        assert credential is not None
        assert len(credential.secret_hash) == 64
        assert credential.secret_hash != refresh_secret
    state = await client.get(
        f"/api/v1/devices/{device['id']}/state", headers=auth_headers
    )
    assert "token" not in state.text.lower()
    assert refresh_secret not in state.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config_document",
    [
        {"sync_interval_seconds": 899},
        {"sync_interval_seconds": 86401},
        {"sync_interval_seconds": True},
        {
            "collectors": {
                "sms": {
                    "enabled": True,
                    "rules": [
                        {
                            "id": "match-all",
                            "enabled": True,
                            "body": {"all_contains": []},
                        }
                    ],
                }
            }
        },
        {"x": "z" * 8200},
        {"x": {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}}},
    ],
)
async def test_configuration_bounds_reject_unsafe_documents(
    client: AsyncClient,
    auth_headers: dict[str, str],
    config_document: dict[str, Any],
) -> None:
    device = await create_device(client, auth_headers, external_id=str(uuid4()))
    response = await client.put(
        f"/api/v1/devices/{device['id']}/configuration",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "schema_version": 1,
            "settings": config_document,
        },
    )
    assert response.status_code in {400, 422}


def test_non_finite_configuration_number_is_rejected() -> None:
    with pytest.raises(InvalidDevice):
        validate_settings({"unsafe": float("inf")})


@pytest.mark.asyncio
async def test_same_phone_relinks_after_disable_and_with_replacement(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    device, old = await provision(client, auth_headers, external_id="relink")
    phone = UUID(old["installation_id"])
    for status in ("DISABLED", "ACTIVE"):
        response = await client.patch(
            f"/api/v1/devices/{device['id']}",
            headers=auth_headers,
            json={"status": status},
        )
        assert response.status_code == 200
    again = await enroll(
        client,
        (await challenge(client, auth_headers, device["id"]))["challenge"],
        installation_id=phone,
    )
    assert again["installation_id"] == old["installation_id"]
    assert (
        await client.get("/api/v1/device/configuration", headers=agent_headers(old))
    ).status_code == 401
    assert (
        await client.get("/api/v1/device/configuration", headers=agent_headers(again))
    ).status_code == 200
    replaced = await enroll(
        client,
        (await challenge(client, auth_headers, device["id"], replace_existing=True))[
            "challenge"
        ],
        installation_id=phone,
    )
    assert (
        await client.get(
            "/api/v1/device/configuration", headers=agent_headers(replaced)
        )
    ).status_code == 200
    other = await create_device(client, auth_headers, external_id="relink-other")
    response = await client.post(
        "/api/v1/device/enroll",
        json={
            "challenge": (await challenge(client, auth_headers, other["id"]))[
                "challenge"
            ],
            "installation_id": str(phone),
            "name": "Android supplied name",
            "platform": "android",
            "app_version": "0.2.0",
        },
    )
    assert response.status_code == 409
