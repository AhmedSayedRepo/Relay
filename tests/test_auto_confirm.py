"""Automatic confirmation of model readings backed by an independent check."""

from datetime import timedelta

from relay.domain.training import labels_from_resolution
from relay.infrastructure.cli import auto_confirm, model_suggest
from tests.test_events import provision
from tests.test_evidence_backend import run_receipt
from tests.test_sms_verification import _local_minute, _send_sms

FIXTURE = {
    "reference_number": "987654321234",
    "amount": "237.50",
    "currency": "EGP",
    "date": "2026-09-05",
    "time": "14:35:00",
}


class FakeReader:
    version = "test-reader"

    def __init__(self, values):
        self.values = values

    def read(self, image: bytes) -> dict[str, str]:
        return dict(self.values)


async def _read_and_confirm(app, values):
    await model_suggest(
        app.state.database, app.state.object_storage, FakeReader(values), 10
    )
    return await auto_confirm(
        app.state.database, app.state.object_storage, app.state.settings
    )


async def _detail(client, auth_headers, candidate):
    response = await client.get(
        f"/api/v1/receipts/{candidate['id']}", headers=auth_headers
    )
    return response.json()


async def test_two_readers_agreeing_confirms_and_skips_training(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(client, auth_headers, app, "auto-readers")
    counts = await _read_and_confirm(app, FIXTURE)
    assert counts["readers"] == 1 and counts["waiting"] == 0
    detail = await _detail(client, auth_headers, candidate)
    resolution = detail["resolution_result"]
    assert resolution["auto_confirmed"]["rule"] == "readers"
    assert resolution["blocking_fields"] == []
    for name, value in FIXTURE.items():
        assert resolution["fields"][name]["value"] == value
        assert resolution["fields"][name]["trust"] == "OPERATOR_CONFIRMED"
    assert detail["review_status"] != "NEEDS_REVIEW"
    assert labels_from_resolution(resolution) is None


async def test_disagreement_without_sms_waits_for_review(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "auto-wait")
    counts = await _read_and_confirm(
        app, FIXTURE | {"reference_number": "111111111111"}
    )
    assert counts["readers"] == 0 and counts["sms"] == 0 and counts["waiting"] == 1
    detail = await _detail(client, auth_headers, candidate)
    assert detail["review_status"] == "NEEDS_REVIEW"
    assert "auto_confirmed" not in detail["resolution_result"]
    again = await auto_confirm(
        app.state.database, app.state.object_storage, app.state.settings
    )
    assert again["waiting"] == 1


async def test_matching_bank_sms_confirms(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "auto-sms")
    sms_device, bundle = await provision(client, auth_headers, "auto-sms-phone")
    linked = await client.put(
        f"/api/v1/devices/{candidate['device_id']}/sms-sources",
        headers=auth_headers,
        json={"sms_device_ids": [sms_device["id"]]},
    )
    assert linked.status_code == 200
    local, day, minute = _local_minute()
    body = "notice: You have received EGP 1,250.50 via InstaPay"
    await _send_sms(client, bundle, body, local + timedelta(minutes=1))
    reading = {
        "reference_number": "222222222222",  # the text reader saw another one
        "amount": "1250.50",
        "currency": "EGP",
        "date": day,
        "time": f"{minute}:00",
    }
    counts = await _read_and_confirm(app, reading)
    assert counts["sms"] == 1
    resolution = (await _detail(client, auth_headers, candidate))["resolution_result"]
    assert resolution["auto_confirmed"]["rule"] == "sms"
    assert resolution["fields"]["amount"]["value"] == "1250.50"
    assert resolution["blocking_fields"] == []


async def _link_sms(client, auth_headers, candidate, name):
    sms_device, bundle = await provision(client, auth_headers, name)
    linked = await client.put(
        f"/api/v1/devices/{candidate['device_id']}/sms-sources",
        headers=auth_headers,
        json={"sms_device_ids": [sms_device["id"]]},
    )
    assert linked.status_code == 200
    return bundle


async def test_bank_sms_makes_reference_optional(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "auto-sms-noref")
    bundle = await _link_sms(client, auth_headers, candidate, "auto-noref-phone")
    local, day, minute = _local_minute()
    body = "notice: You have received EGP 780.00 via InstaPay"
    await _send_sms(client, bundle, body, local + timedelta(minutes=1))
    # The model could not read a reference at all.
    reading = {
        "amount": "780.00",
        "currency": "EGP",
        "date": day,
        "time": f"{minute}:00",
    }
    counts = await _read_and_confirm(app, reading)
    assert counts["sms"] == 1
    detail = await _detail(client, auth_headers, candidate)
    resolution = detail["resolution_result"]
    assert resolution["sms_verified"]
    assert resolution["blocking_fields"] == []
    assert resolution["fields"]["reference_number"]["trust"] == "MACHINE_ONLY"
    assert resolution["fields"]["date"]["value"] == day
    assert detail["review_status"] != "NEEDS_REVIEW"
    assert labels_from_resolution(resolution) is None


async def test_without_sms_reference_is_still_required(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "auto-noref-nosms")
    reading = {"amount": "237.50", "currency": "EGP", "date": "2026-09-05"}
    counts = await _read_and_confirm(app, reading)
    assert counts["waiting"] == 1 and counts["sms"] == 0 and counts["readers"] == 0
    detail = await _detail(client, auth_headers, candidate)
    assert detail["review_status"] == "NEEDS_REVIEW"
    assert "reference_number" in detail["resolution_result"]["blocking_fields"]
