"""Receipts from one phone verified against bank SMS from another phone."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from relay.domain.sms_verification import (
    MatchWindow,
    ReceiptFacts,
    SmsDirection,
    SmsFacts,
    VerificationStatus,
    decide,
    parse_bank_sms,
)
from tests.review_helpers import review_critical_fields
from tests.test_events import event, headers, provision
from tests.test_evidence_backend import run_receipt

CAIRO = ZoneInfo("Africa/Cairo")
WINDOW = MatchWindow()
RECEIPT_DEVICE, SMS_DEVICE, OTHER_DEVICE = uuid4(), uuid4(), uuid4()


CIB = (
    "Kindly note your account ********0000 was charged with IPN Outward with "
    "amount EGP {amount} on {day} at {time} with reference {ref} For more "
    "information, please contact 19666."
)
# CIB debit SMS wording from real samples (account, references anonymised)
# paired with their receipts: receipt amount, receipt wall-clock minute.
REAL_PAIRS = [
    (
        CIB.format(amount="650.00", day="25-10-2026", time="23:00", ref="1a2b3c4d"),
        "650.00",
        datetime(2026, 10, 25, 23, 0),
    ),
    (
        CIB.format(amount="1000.00", day="26-10-2026", time="19:11", ref="5e6f7a8b"),
        "1000.00",
        datetime(2026, 10, 26, 19, 11),
    ),
    (
        CIB.format(amount="650.00", day="27-10-2026", time="20:20", ref="12345678"),
        "650.00",
        datetime(2026, 10, 27, 20, 20),
    ),
]


def test_real_cib_debit_sms_are_read_with_their_stated_time():
    for body, amount, minute in REAL_PAIRS:
        parsed = parse_bank_sms(body)
        assert parsed is not None
        assert parsed.amount == Decimal(amount) and parsed.currency == "EGP"
        assert parsed.direction is SmsDirection.OUT
        assert parsed.stated_local == minute


def test_real_cib_credit_sms_is_read():
    parsed = parse_bank_sms(
        "Kindly note your account ********0000 was credited with IPN Inward with "
        "amount EGP 40000.00 on 25-10-2026 at 13:20 from SENDER NAME "
        "with reference 0a1b2c3d For more information, please contact 19666."
    )
    assert parsed is not None
    assert parsed.amount == Decimal("40000.00")
    assert parsed.direction is SmsDirection.IN
    assert parsed.stated_local == datetime(2026, 10, 25, 13, 20)


def test_parser_handles_arabic_and_stays_strict():
    credit = parse_bank_sms(
        "تم إضافة مبلغ ١٬٢٥٠٫٥٠ جنيه إلى حسابك عبر انستاباي "
        "بتاريخ ٢٥/١٠/٢٠٢٦ الساعة ١١:٠٠ م"
    )
    assert credit is not None
    assert (credit.amount, credit.direction) == (Decimal("1250.50"), SmsDirection.IN)
    assert credit.stated_local == datetime(2026, 10, 25, 23, 0)
    debit = parse_bank_sms("تم خصم مبلغ 650.00 جنيه من حسابك تحويل لحظي انستاباي")
    assert debit is not None and debit.direction is SmsDirection.OUT
    assert debit.stated_local is None
    assert parse_bank_sms(
        "You have received EGP 1,250.50 via InstaPay. Your balance is EGP 10,000.00"
    ) == parse_bank_sms("Received EGP 1250.50 via InstaPay")
    # Card purchases, cash withdrawals, non-InstaPay debits, unclear direction,
    # and several amounts are never read.
    assert parse_bank_sms("تم خصم مبلغ 500 جنيه عملية شراء") is None
    assert parse_bank_sms("Your account was debited EGP 500.00") is None
    assert parse_bank_sms("Payment of EGP 500.00 is due") is None
    assert parse_bank_sms("Received EGP 500 and EGP 700") is None
    assert parse_bank_sms("credited and charged EGP 5 via IPN") is None
    assert parse_bank_sms("") is None


def test_real_pairs_verify_and_direction_must_match():
    for body, amount, minute in REAL_PAIRS:
        parsed = parse_bank_sms(body)
        assert parsed is not None
        # The SMS arrived a minute later; the stated time is what is compared.
        sms = SmsFacts(
            uuid4(),
            SMS_DEVICE,
            parsed.amount,
            parsed.currency,
            minute.replace(tzinfo=CAIRO) + timedelta(minutes=1),
            parsed.direction,
            parsed.stated_local,
        )
        sent = _receipt(minute, amount, direction=SmsDirection.OUT)
        assert _decide([sent], [sms])[0].status is VerificationStatus.VERIFIED
        received = _receipt(minute, amount, direction=SmsDirection.IN)
        assert _decide([received], [sms])[0].status is VerificationStatus.WAITING
        # A stated time two minutes off is not the same transfer.
        later = _receipt(minute + timedelta(minutes=2), amount, SmsDirection.OUT)
        assert _decide([later], [sms])[0].sms is None


def _receipt(
    minute: datetime,
    amount="500.00",
    direction=SmsDirection.IN,
    created=None,
    devices=None,
):
    return ReceiptFacts(
        uuid4(),
        Decimal(amount),
        "EGP",
        minute,
        created or datetime.now(UTC),
        frozenset(devices or {SMS_DEVICE}),
        direction,
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
    saved = (await client.get(sources, headers=auth_headers)).json()
    assert saved["sms_device_ids"] == [sms_device["id"]]
    assert saved["direction"] == "IN"
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
