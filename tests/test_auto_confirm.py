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
    # Trains on the amount the SMS proves; the reference came from the model
    # alone, so it stays out of training.
    labels = labels_from_resolution(resolution)
    assert labels is not None
    assert labels["amount"]["target_value"] == "1250.50"
    assert labels["reference"]["value_loss_mask"] == 0
    other_rule = resolution | {"auto_confirmed": {"rule": "readers"}}
    assert labels_from_resolution(other_rule) is None
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
    listed = await client.get("/api/v1/transactions", headers=auth_headers)
    (item,) = [i for i in listed.json()["items"] if i["id"] == candidate["id"]]
    assert item["status_reason"] == "no_sms"


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
    listed = await client.get("/api/v1/transactions", headers=auth_headers)
    pair = {i["id"]: i["similar_to"] for i in listed.json()["items"]}
    # Each points at the other, so they can be opened side by side.
    assert pair[first["id"]] == second["id"] and pair[second["id"]] == first["id"]


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
    misread = receipt.replace("3405", "3485")
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


def test_sender_and_recipient_names() -> None:
    from relay.infrastructure.text_recheck import parties

    assert parties(
        "SALMA MOHAMED GALAL ©\nsalma@instapay\nإلى انستاباي\n"
        "YOUSSEF Mi*t**** Y***\n01003405232\nPOWERED BY\nOTHER NAME"
    ) == {
        "sender_name": "SALMA MOHAMED GALAL",
        "recipient_name": "YOUSSEF",
        "recipient_phone": "01003405232",
    }
    # Nothing past "powered by", and nothing without a "to" line.
    assert parties("SALMA MOHAMED\nالى المحفظة\nPOWERED BY\nYOUSSEF") == {
        "sender_name": "SALMA MOHAMED"
    }
    assert parties("YOUSSEF\n01003405232") == {}


def test_noisy_receipt_text() -> None:
    from relay.infrastructure.text_recheck import parties, sender

    # "to" lost by OCR: the masked name is still the recipient.
    assert parties("pzqfx@instapay\nب انستاباي\nke YOUSSEF M***** ope 0") == {
        "recipient_name": "YOUSSEF"
    }
    # Stray letters before the address are not part of it.
    assert sender("gin pzqfx@instapay") == "pzqfx@instapay"
    assert sender("salma 1594 @instapay") == "salma1594@instapay"
    failed = signals("فشلت المعاملة\n3,290 EGP\n293686553132\nانستاباي")
    assert failed["failed"]


def _text_result(receipt=True, request=False):
    return {
        "receipt": receipt,
        "payment_request": request,
        "failed": False,
        "found": {"brand": True},
        "values": {"reference_number": "293686553132"},
        "sender": "pzqfx@instapay",
        "parties": {"recipient_name": "YOUSSEF"},
        "text": "YOUSSEF M*****",
    }


async def _machine_rejected(app):
    async with app.state.database.session() as session:
        for row in await session.scalars(select(ReceiptCandidateRow)):
            row.review_status = "REJECTED"
            row.receipt_type = "UNKNOWN_RECEIPT"
            row.reviewed_result = None  # stored as JSON null, as in production
        await session.commit()


async def test_text_pass_reopens_rejected_receipts(
    client, auth_headers, app, monkeypatch
):
    from relay.infrastructure import text_recheck
    from relay.infrastructure.cli import text_pass

    candidate, _ = await run_receipt(client, auth_headers, app, "text-pass")
    await _machine_rejected(app)
    monkeypatch.setattr(text_recheck, "assess", lambda *args: _text_result())
    counts = await text_pass(
        app.state.database, app.state.object_storage, None, ("eng",), 4
    )
    assert counts["read"] == 1 and counts["reopened"] == 1
    detail = await _detail(client, auth_headers, candidate)
    assert detail["review_status"] == "NEEDS_REVIEW"
    # Read once only.
    again = await text_pass(
        app.state.database, app.state.object_storage, None, ("eng",), 4
    )
    assert again["read"] == 0


async def test_text_pass_rejects_payment_requests(
    client, auth_headers, app, monkeypatch
):
    from relay.infrastructure import text_recheck
    from relay.infrastructure.cli import text_pass

    candidate, _ = await run_receipt(client, auth_headers, app, "text-request")
    monkeypatch.setattr(
        text_recheck, "assess", lambda *args: _text_result(receipt=False, request=True)
    )
    counts = await text_pass(
        app.state.database, app.state.object_storage, None, ("eng",), 4
    )
    assert counts["requests"] == 1
    detail = await _detail(client, auth_headers, candidate)
    assert detail["review_status"] == "REJECTED"
    assert detail["resolution_result"]["rejected_reason"] == "payment_request"


def test_recipient_check_cases() -> None:
    from relay.infrastructure.text_recheck import recipient_check

    owner = ("YOUSSEF", "01003405232")
    # Phone read, name lost by OCR: still the owner.
    assert recipient_check("To Instapay\n01003405232", *owner, True) == "match"
    # Name read, phone too faint to read: the owner.
    assert recipient_check("YOUSSEF M***** Y***", *owner, True) == "match"
    # "Y0USSEF" misread with a zero.
    assert recipient_check("Y0USSEF M*****", *owner, True) == "match"
    # Another number printed: someone else.
    assert recipient_check("YOUSSEF A\n01021204916", *owner, True) == "different"
    # Nothing readable about the recipient.
    assert recipient_check("nouranabokora01@instapay", *owner, True) == "unclear"


async def test_quiet_receipts_phone_still_listed_on_dashboard(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(client, auth_headers, app, "quiet-phone")
    # A day long ago: the phone sent nothing then, but stays in the list.
    old = await client.get(
        "/api/v1/dashboard?date_from=2026-01-01&date_to=2026-01-01",
        headers=auth_headers,
    )
    assert old.status_code == 200, old.text
    (phone,) = [
        item
        for item in old.json()["transactions_by_device"]
        if item["device_id"] == candidate["device_id"]
    ]
    assert phone["count"] == 0 and phone["confirmed_amount"] == "0.00"


async def test_text_amount_corrects_a_dropped_digit(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "dropped-digit")
    bundle = await _link_sms(client, auth_headers, [candidate], "dropped-phone")
    body = "notice: You have received EGP 1,140.00 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    # The model reads "1,140" as 114; the text reader saw 1,140.
    await model_suggest(
        app.state.database,
        app.state.object_storage,
        FakeReader({"amount": "114.00", "currency": "EGP"}),
        10,
    )
    await _text(app, "تم التحويل بنجاح\n1,140 EGP")
    counts = await auto_confirm(
        app.state.database, app.state.object_storage, app.state.settings
    )
    assert counts["sms_day"] == 1
    detail = await _detail(client, auth_headers, candidate)
    assert detail["resolution_result"]["fields"]["amount"]["value"] == "1140.00"


def test_amount_from_fee_and_total() -> None:
    from relay.infrastructure.text_recheck import (
        amounts,
        transfer_from_fee,
        transfer_from_total,
    )

    # Photos of a phone often cut off the big amount; fee + total remain.
    assert transfer_from_fee(amounts("1.08 EGP\n1,081.08 EGP")) == "1080.00"
    assert transfer_from_fee(amounts("1.82 EGP\n1,816.82 EGP")) == "1815.00"
    # "0:62" is how OCR read the fee's decimal point.
    assert transfer_from_fee(amounts("0:62 EGP\n620.62 EGP")) == "620.00"
    # Numbers that do not follow the 0.1% fee rule are not a pair.
    assert transfer_from_fee(amounts("5 EGP\n100 EGP")) is None
    # Fee lost: a labelled total alone still gives the amount.
    assert transfer_from_total("المبلغ الإجمالي 620.62 EGP") == "620.00"
    assert transfer_from_total("price 1500.50 EGP") is None


def test_only_clearly_non_receipt_photos_are_hidden() -> None:
    from relay.infrastructure.cli import _receipt_like

    # A poor photo of a phone screen: not a full receipt, but it shows a
    # reference, so it goes to Review instead of being hidden.
    assert _receipt_like({"receipt": False, "found": {"reference": True}})
    assert _receipt_like({"receipt": False, "found": {"brand": True}})
    # A photo of food: nothing receipt-like at all.
    assert not _receipt_like({"receipt": False, "found": {"date": True}})
    # A blurred screen still full of text is for a person to judge.
    screen = "تحويل ناجح " * 10 + "0100340 5232 YOUSSEF"
    assert _receipt_like({"receipt": False, "found": {}, "text": screen})
    assert not _receipt_like({"receipt": False, "found": {}, "text": "ok 12"})
