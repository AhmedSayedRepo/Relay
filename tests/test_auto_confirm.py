"""Automatic confirmation: an incoming bank SMS with the same amount on the
same day backs up a receipt; everything else waits for review."""

from datetime import datetime, timedelta
from itertools import cycle
from zoneinfo import ZoneInfo

from sqlalchemy import select

from relay.domain.training import labels_from_resolution
from relay.infrastructure.cli import auto_confirm, model_suggest
from relay.infrastructure.processing_models import ReceiptCandidateRow
from relay.infrastructure.text_recheck import VERSION, recipient_matches, signals
from tests.test_events import provision
from tests.test_evidence_backend import run_receipt
from tests.test_sms_verification import _send_sms

CAIRO = ZoneInfo("Africa/Cairo")


class FakeReader:
    version = "test-reader"

    def __init__(self, *values):
        self.values = cycle(values)

    def read(self, image: bytes) -> dict[str, str]:
        return dict(next(self.values))


async def _text(app, text="", sender=None, instapay=True):
    """What the plain-text pass would store, without running Tesseract."""
    async with app.state.database.session() as session:
        for row in await session.scalars(select(ReceiptCandidateRow)):
            if VERSION not in row.machine_result:
                row.machine_result = row.machine_result | {
                    VERSION: {
                        "receipt": True,
                        "payment_request": False,
                        "found": {"brand": instapay},
                        "sender": sender,
                        "text": text,
                    }
                }
        await session.commit()


async def _read_and_confirm(app, *values):
    await model_suggest(
        app.state.database, app.state.object_storage, FakeReader(*values), 10
    )
    await _text(app)
    return await auto_confirm(
        app.state.database, app.state.object_storage, app.state.settings
    )


async def _detail(client, auth_headers, candidate):
    response = await client.get(
        f"/api/v1/receipts/{candidate['id']}", headers=auth_headers
    )
    return response.json()


async def _link_sms(client, auth_headers, candidates, name):
    sms_device, bundle = await provision(client, auth_headers, name)
    for candidate in candidates:
        linked = await client.put(
            f"/api/v1/devices/{candidate['device_id']}/sms-sources",
            headers=auth_headers,
            json={"sms_device_ids": [sms_device["id"]]},
        )
        assert linked.status_code == 200
    return bundle


def _now():
    return datetime.now(CAIRO).replace(microsecond=0)


async def test_same_day_bank_sms_confirms_on_amount(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "day-sms")
    bundle = await _link_sms(client, auth_headers, [candidate], "day-sms-phone")
    body = "notice: You have received EGP 1,250.50 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    # No date or time read: the same day is enough.
    counts = await _read_and_confirm(
        app,
        {"amount": "1250.50", "currency": "EGP", "reference_number": "222222222222"},
    )
    assert counts["sms_day"] == 1
    detail = await _detail(client, auth_headers, candidate)
    resolution = detail["resolution_result"]
    assert resolution["auto_confirmed"]["rule"] == "sms_day"
    assert resolution["sms_verified"]
    assert resolution["blocking_fields"] == []
    assert resolution["fields"]["amount"]["value"] == "1250.50"
    assert detail["review_status"] != "NEEDS_REVIEW"
    assert labels_from_resolution(resolution) is None
    status = await client.get(
        f"/api/v1/receipts/{candidate['id']}/sms-verification", headers=auth_headers
    )
    assert status.json()["status"] == "VERIFIED"


async def test_sms_from_another_day_waits(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "day-old-sms")
    bundle = await _link_sms(client, auth_headers, [candidate], "day-old-phone")
    body = "notice: You have received EGP 455.00 via InstaPay"
    await _send_sms(client, bundle, body, _now() - timedelta(days=1, minutes=5))
    counts = await _read_and_confirm(app, {"amount": "455.00", "currency": "EGP"})
    assert counts["sms_day"] == 0 and counts["waiting"] == 1
    detail = await _detail(client, auth_headers, candidate)
    assert detail["review_status"] == "NEEDS_REVIEW"


async def test_without_sms_waits(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "day-no-sms")
    counts = await _read_and_confirm(app, {"amount": "237.50", "currency": "EGP"})
    assert counts["sms_day"] == 0 and counts["waiting"] == 1
    detail = await _detail(client, auth_headers, candidate)
    assert "auto_confirmed" not in detail["resolution_result"]


async def test_outgoing_sms_does_not_confirm(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "day-out-sms")
    bundle = await _link_sms(client, auth_headers, [candidate], "day-out-phone")
    body = "notice: EGP 612.00 debited from your account via InstaPay"
    await _send_sms(client, bundle, body, _now())
    counts = await _read_and_confirm(app, {"amount": "612.00", "currency": "EGP"})
    assert counts["sms_day"] == 0


async def test_each_sms_confirms_one_receipt(client, auth_headers, app):
    first, _ = await run_receipt(client, auth_headers, app, "day-two-a")
    second, _ = await run_receipt(client, auth_headers, app, "day-two-b")
    bundle = await _link_sms(client, auth_headers, [first, second], "day-two-phone")
    body = "notice: You have received EGP 300.00 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    # Same amount twice today, but two different references: both can count,
    # each needing its own SMS.
    counts = await _read_and_confirm(
        app,
        {"amount": "300.00", "currency": "EGP", "reference_number": "111111111111"},
        {"amount": "300.00", "currency": "EGP", "reference_number": "333333333333"},
    )
    assert counts["sms_day"] == 1 and counts["waiting"] == 1
    await _send_sms(client, bundle, body, _now())
    again = await auto_confirm(
        app.state.database, app.state.object_storage, app.state.settings
    )
    assert again["sms_day"] == 1


async def test_repeat_without_references_waits(client, auth_headers, app):
    first, _ = await run_receipt(client, auth_headers, app, "day-rep-a")
    second, _ = await run_receipt(client, auth_headers, app, "day-rep-b")
    bundle = await _link_sms(client, auth_headers, [first, second], "day-rep-phone")
    body = "notice: You have received EGP 410.00 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    await _send_sms(client, bundle, body, _now())
    # Same amount, no reference and no readable sender: could be one transfer.
    counts = await _read_and_confirm(app, {"amount": "410.00", "currency": "EGP"})
    assert counts["sms_day"] == 0 and counts["waiting"] == 2


async def test_recipient_must_be_set_owner(client, auth_headers, app):
    saved = await client.put(
        "/api/v1/settings/recipient",
        headers=auth_headers,
        json={"name": "YOUSSEF", "phone": "01003405232"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"name": "YOUSSEF", "phone": "01003405232"}
    candidate, _ = await run_receipt(client, auth_headers, app, "day-owner")
    bundle = await _link_sms(client, auth_headers, [candidate], "day-owner-phone")
    body = "notice: You have received EGP 520.00 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    await model_suggest(
        app.state.database,
        app.state.object_storage,
        FakeReader({"amount": "520.00", "currency": "EGP"}),
        10,
    )
    await _text(app, "Mohamed A 01021204916")
    counts = await auto_confirm(
        app.state.database, app.state.object_storage, app.state.settings
    )
    assert counts["sms_day"] == 0
    detail = await _detail(client, auth_headers, candidate)
    assert detail["resolution_result"]["recipient_mismatch"]
    off = await client.put(
        "/api/v1/settings/recipient", headers=auth_headers, json={"name": ""}
    )
    assert off.json() == {"name": "", "phone": None}


def test_recipient_and_payment_request_reading() -> None:
    receipt = "تم التحويل بنجاح\nYOUSSEF M***** Y****** M******\n01003405232"
    assert recipient_matches(receipt, "Youssef", "01003405232", instapay=True)
    # One misread digit is tolerated; another number is not.
    misread = receipt.replace("0100", "0700")
    assert recipient_matches(misread, "YOUSSEF", "01003405232", instapay=True)
    assert not recipient_matches(receipt, "YOUSSEF", "01021204916", instapay=True)
    assert not recipient_matches("Mohamed A 01003405232", "YOUSSEF", None, False)
    # Bank receipts may not show the phone: the name is enough there.
    assert recipient_matches("YOUSSEF MOHAMED", "YOUSSEF", "01003405232", False)

    request = signals(
        "طلب دفع\n50 EGP\nمبلغ التحويل\nطلب دفع من YOUSSEF\n"
        "ibrahim.afsa@instapay\nتحصيل"
    )
    assert request["payment_request"] and not request["receipt"]
    paid = signals(
        "تم التحويل بنجاح\n535 EGP\nمن loba.loba@instapay\n"
        "الرقم المرجعي 734109652963\n14 Nov 2026 08:38 PM",
        "الرقم المرجعي 734109652963\n14 Nov 2026 08:38 PM\n535 EGP",
    )
    assert paid["receipt"] and not paid["payment_request"]
    assert paid["sender"] == "loba.loba@instapay"
    assert paid["values"] == {
        "reference_number": "734109652963",
        "date": "2026-11-14",
        "time": "20:38:00",
        "amount": "535.00",
        "currency": "EGP",
    }
