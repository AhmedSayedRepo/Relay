"""Deleting devices, and one SMS phone serving two receipt phones."""

from tests.test_events import provision
from tests.test_evidence_backend import run_receipt


async def _create(client, auth_headers, name):
    response = await client.post(
        "/api/v1/devices",
        headers=auth_headers,
        json={
            "external_device_id": name,
            "name": name,
            "platform": "android",
            "app_version": "0.1.0",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _disable(client, auth_headers, device_id):
    response = await client.patch(
        f"/api/v1/devices/{device_id}",
        headers=auth_headers,
        json={"status": "DISABLED"},
    )
    assert response.status_code == 200, response.text


async def test_unlinked_device_is_deleted_without_disabling(client, auth_headers):
    device = await _create(client, auth_headers, "spare-phone")
    assert device["status"] == "PENDING"
    url = f"/api/v1/devices/{device['id']}"
    removed = await client.delete(url, headers=auth_headers)
    assert removed.json() == {"removed": True}
    assert (await client.get(url, headers=auth_headers)).status_code == 404
    # The same phone id and name can be added again.
    await _create(client, auth_headers, "spare-phone")


async def test_linked_device_must_be_disabled_first(client, auth_headers):
    device, _ = await provision(client, auth_headers, external_id="linked-phone")
    url = f"/api/v1/devices/{device['id']}"
    assert (await client.delete(url, headers=auth_headers)).status_code == 409
    await _disable(client, auth_headers, device["id"])
    removed = await client.delete(url, headers=auth_headers)
    assert removed.status_code == 200, removed.text


async def test_device_with_receipts_is_hidden_and_keeps_evidence(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(client, auth_headers, app, "delete-collector")
    device_id = candidate["device_id"]
    await _disable(client, auth_headers, device_id)
    removed = await client.delete(f"/api/v1/devices/{device_id}", headers=auth_headers)
    assert removed.json() == {"removed": False}
    listed = (
        await client.get("/api/v1/operations/devices?limit=100", headers=auth_headers)
    ).json()["items"]
    assert device_id not in {item["id"] for item in listed}
    receipt = await client.get(
        f"/api/v1/receipts/{candidate['id']}", headers=auth_headers
    )
    assert receipt.status_code == 200


async def test_one_sms_phone_serves_two_receipt_phones(client, auth_headers, app):
    first, _ = await run_receipt(client, auth_headers, app, "two-phones-a")
    second, _ = await run_receipt(client, auth_headers, app, "two-phones-b")
    sms_device, _ = await provision(client, auth_headers, "two-phones-sms")
    for receipt in (first, second):
        linked = await client.put(
            f"/api/v1/devices/{receipt['device_id']}/sms-sources",
            headers=auth_headers,
            json={"sms_device_ids": [sms_device["id"]]},
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["sms_device_ids"] == [sms_device["id"]]
