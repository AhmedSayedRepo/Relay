"""InstaPay app notifications as a second source next to the bank SMS: one
record per transfer when both arrive."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.test_events import event, headers, provision


def _at(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def _send(client, bundle, *, sender, body, rule, moment):
    item = event(bundle["installation_id"], sender=sender, body=body)
    item["metadata"] = {"rule_id": rule}
    item["occurred_at"] = item["captured_at"] = _at(moment)
    response = await client.post(
        "/api/v1/device/events/batch", headers=headers(bundle), json={"events": [item]}
    )
    assert response.status_code == 200, response.text
    return response.json()["results"][0]["status"]


async def _bodies(client, auth_headers, device):
    listed = await client.get(
        f"/api/v1/events?device_id={device['id']}&collector=sms", headers=auth_headers
    )
    return sorted(item["payload"]["body"] for item in listed.json()["items"])


NOTICE = "You have received 820.00 EGP from fonpn@instapay"
SMS = "notice: IPN transfer received with amount of EGP 820.00 on 3761 at 10:42 AM"


@pytest.mark.asyncio
async def test_notification_alone_is_kept_and_a_later_sms_replaces_it(
    client, auth_headers
):
    device, bundle = await provision(client, auth_headers, "instapay-phone")
    now = datetime.now(UTC)
    first = await _send(
        client, bundle, sender="InstaPay", body=NOTICE, rule="instapay-app", moment=now
    )
    assert first == "accepted"
    assert await _bodies(client, auth_headers, device) == [NOTICE]
    # The bank SMS for the same transfer arrives two minutes later: one record.
    later = await _send(
        client,
        bundle,
        sender="ALERT-QNB",
        body=SMS,
        rule="generic-alert",
        moment=now + timedelta(minutes=2),
    )
    assert later == "accepted"
    assert await _bodies(client, auth_headers, device) == [SMS]


@pytest.mark.asyncio
async def test_notification_after_its_sms_is_not_stored_twice(client, auth_headers):
    device, bundle = await provision(client, auth_headers, "instapay-phone-2")
    now = datetime.now(UTC)
    await _send(
        client, bundle, sender="ALERT-QNB", body=SMS, rule="generic-alert", moment=now
    )
    status = await _send(
        client,
        bundle,
        sender="InstaPay",
        body=NOTICE,
        rule="instapay-app",
        moment=now + timedelta(minutes=1),
    )
    assert status == "duplicate"
    assert await _bodies(client, auth_headers, device) == [SMS]
    # Another sender cannot use the InstaPay rule.
    other = event(bundle["installation_id"], sender="Fake", body=NOTICE)
    other["metadata"] = {"rule_id": "instapay-app"}
    response = await client.post(
        "/api/v1/device/events/batch", headers=headers(bundle), json={"events": [other]}
    )
    assert response.json()["results"][0]["status"] == "rejected"


@pytest.mark.asyncio
async def test_notification_fourteen_minutes_after_its_sms_is_the_same_transfer(
    client, auth_headers
):
    device, bundle = await provision(client, auth_headers, "instapay-phone-3")
    now = datetime.now(UTC)
    await _send(
        client, bundle, sender="ALERT-QNB", body=SMS, rule="generic-alert", moment=now
    )
    status = await _send(
        client,
        bundle,
        sender="InstaPay",
        body=NOTICE,
        rule="instapay-app",
        moment=now + timedelta(minutes=14),
    )
    assert status == "duplicate"
    assert await _bodies(client, auth_headers, device) == [SMS]


def test_the_source_says_who_showed_the_transfer() -> None:
    from relay.application.sms_verification import sms_source

    assert sms_source("CIB", False) == "bank"
    assert sms_source("CIB", True) == "both"
    assert sms_source("InstaPay", False) == "instapay"
