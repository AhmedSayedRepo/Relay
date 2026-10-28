"""Receipts from one phone verified against bank SMS from another phone."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from relay.domain.sms_verification import (
    MatchWindow,
    ReceiptFacts,
    SmsFacts,
    VerificationStatus,
    decide,
    parse_credit_sms,
)
from tests.review_helpers import review_critical_fields
from tests.test_events import event, headers, provision
from tests.test_evidence_backend import run_receipt

CAIRO = ZoneInfo("Africa/Cairo")
WINDOW = MatchWindow()
RECEIPT_DEVICE, SMS_DEVICE, OTHER_DEVICE = uuid4(), uuid4(), uuid4()


def test_credit_sms_parser_is_strict():
    assert parse_credit_sms(
        "You have received EGP 1,250.50 via InstaPay. Your balance is EGP 10,000.00"
    ) == (Decimal("1250.50"), "EGP")
    assert parse_credit_sms("تم استلام مبلغ ١٢٥٠٫٥٠ جنيه عبر InstaPay") == (
        Decimal("1250.50"),
        "EGP",
    )
    assert parse_credit_sms("Your account was debited EGP 500.00") is None
    assert parse_credit_sms("Payment of EGP 500.00 is due") is None  # no credit word
    assert parse_credit_sms("Received EGP 500 and EGP 700") is None  # two amounts
    assert parse_credit_sms("") is None


def _receipt(minute: datetime, amount="500.00", created=None, devices=None):
    return ReceiptFacts(
        uuid4(),
        Decimal(amount),
        "EGP",
        minute,
        created or datetime.now(UTC),
        frozenset(devices or {SMS_DEVICE}),
    )


def _sms(at: datetime, amount="500.00", device=SMS_DEVICE):
    return SmsFacts(uuid4(), device, Decimal(amount), "EGP", at)


def _decide(receipts, messages, **kwargs):
    return decide(
        receipts,
        messages,
        zone=CAIRO,
        window=WINDOW,
        now=kwargs.pop("now", datetime.now(UTC)),
        **kwargs,
    )


def test_amount_and_time_window_decide_one_to_one():
    minute = datetime(2026, 9, 5, 14, 35)  # Cairo summer time, UTC+3
    utc = datetime(2026, 9, 5, 11, 35, tzinfo=UTC)
    receipt = _receipt(minute)
    real = _sms(utc + timedelta(seconds=50))
    [verified] = _decide([receipt], [real, _sms(utc, amount="499.00")])
    assert verified.status is VerificationStatus.VERIFIED and verified.sms == real

    late = _sms(utc + timedelta(minutes=12))
    [waiting] = _decide([receipt], [late])
    assert waiting.status is VerificationStatus.WAITING
    old = _receipt(minute, created=datetime.now(UTC) - timedelta(hours=25))
    assert _decide([old], [late])[0].status is VerificationStatus.UNMATCHED

    # One real SMS cannot vouch for two receipts with the same amount and minute.
    twins = [_receipt(minute), _receipt(minute)]
    decisions = _decide(twins, [real])
    assert {item.status for item in decisions} == {VerificationStatus.AMBIGUOUS}

    # An SMS already used elsewhere, or from an unlinked phone, is ignored.
    assert _decide([receipt], [real], taken=frozenset({real.event_id}))[0].sms is None
    stranger = _sms(utc, device=OTHER_DEVICE)
    assert _decide([receipt], [stranger])[0].status is VerificationStatus.WAITING


def _local_minute():
    local = datetime.now(CAIRO).replace(second=0, microsecond=0) - timedelta(hours=1)
    return local, local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


async def _send_sms(client, bundle, body, occurred):
    item = event(bundle["installation_id"], body=body)
    item["occurred_at"] = occurred.astimezone(UTC).isoformat().replace("+00:00", "Z")
    response = await client.post(
        "/api/v1/device/events/batch", headers=headers(bundle), json={"events": [item]}
    )
    assert response.json()["results"][0]["status"] == "accepted", response.text


async def test_cross_device_verification_flow(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "sms-verify-receipt")
    receipt_device = candidate["device_id"]
    sms_device, bundle = await provision(client, auth_headers, "sms-verify-phone")
    url = f"/api/v1/receipts/{candidate['id']}/sms-verification"

    assert (await client.get(url, headers=auth_headers)).json()["status"] == (
        "NOT_SET_UP"
    )
    sources = f"/api/v1/devices/{receipt_device}/sms-sources"
    own = await client.put(
        sources, headers=auth_headers, json={"sms_device_ids": [receipt_device]}
    )
    assert own.status_code == 409
    missing = await client.put(
        sources, headers=auth_headers, json={"sms_device_ids": [str(uuid4())]}
    )
    assert missing.status_code == 404
    linked = await client.put(
        sources, headers=auth_headers, json={"sms_device_ids": [sms_device["id"]]}
    )
    assert linked.status_code == 200
    assert (await client.get(sources, headers=auth_headers)).json()[
        "sms_device_ids"
    ] == [sms_device["id"]]
    assert (await client.get(url, headers=auth_headers)).json()["status"] == (
        "NOT_READY"
    )

    local, day, minute = _local_minute()
    await review_critical_fields(
        client,
        auth_headers,
        candidate,
        {"amount": "1250.50", "currency": "EGP", "date": day, "time": minute},
    )
    assert (await client.get(url, headers=auth_headers)).json()["status"] == (
        "NOT_CHECKED"
    )
    run = await client.post("/api/v1/verification/sms/run", headers=auth_headers)
    assert run.json() == {"verified": 0, "ambiguous": 0, "waiting": 1, "unmatched": 0}

    body = "notice: You have received EGP 1,250.50 via InstaPay"
    await _send_sms(client, bundle, body, local + timedelta(minutes=1))
    await _send_sms(client, bundle, body, local + timedelta(minutes=40))
    run = await client.post("/api/v1/verification/sms/run", headers=auth_headers)
    assert run.json()["verified"] == 1
    detail = (await client.get(url, headers=auth_headers)).json()
    assert detail["status"] == "VERIFIED" and detail["method"] == "AUTO"
    assert detail["sms"]["amount"] == "1250.50" and detail["options"] == []
    listing = await client.get("/api/v1/transactions", headers=auth_headers)
    rows = [row for row in listing.json()["items"] if row["id"] == candidate["id"]]
    assert rows and rows[0]["sms_verification"] == "VERIFIED"

    cleared = (await client.delete(url, headers=auth_headers)).json()
    assert cleared["status"] == "NOT_CHECKED"
    options = cleared["options"]
    assert [option["in_window"] for option in options] == [True, False]
    bogus = await client.put(
        url, headers=auth_headers, json={"sms_event_id": str(uuid4())}
    )
    assert bogus.status_code == 409
    chosen = await client.put(
        url, headers=auth_headers, json={"sms_event_id": options[1]["event_id"]}
    )
    assert chosen.status_code == 200
    assert chosen.json()["method"] == "MANUAL"
    assert chosen.json()["sms"]["event_id"] == options[1]["event_id"]
    # An operator decision survives later automatic runs.
    await client.post("/api/v1/verification/sms/run", headers=auth_headers)
    kept = (await client.get(url, headers=auth_headers)).json()
    assert kept["method"] == "MANUAL" and kept["status"] == "VERIFIED"

    unknown = await client.get(
        f"/api/v1/receipts/{uuid4()}/sms-verification", headers=auth_headers
    )
    assert unknown.status_code == 404
