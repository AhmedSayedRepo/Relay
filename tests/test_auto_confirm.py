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
    # A blurred screen that still shows an amount or a time: a person judges.
    for screen in ("ex % 1,610 .00 EGP", "noise 12 : 05 PM"):
        assert _receipt_like({"receipt": False, "found": {}, "text": screen})
    # Paper or a poster read as hundreds of random letters: hidden.
    noise = "ex %\n— -_\nبه 061\nim 8) GN Nebel ie\n1 7 0 عليه 1 a " * 20
    assert not _receipt_like({"receipt": False, "found": {}, "text": noise})


def test_untouched_review_photo_with_nothing_readable_is_hidden() -> None:
    from relay.infrastructure.cli import _clear_non_receipt

    blank = {"found": {}, "text": "BURGER TIME", "receipt": False}

    def row(
        read: dict[str, object], amount: str | None = None, source: str = "model"
    ) -> ReceiptCandidateRow:
        field = {"value": amount, "candidates": [{"source": source}]}
        return ReceiptCandidateRow(
            machine_result={VERSION: read},
            resolution_result={"fields": {"amount": field}},
            reviewed_result=None,
        )

    # A burger poster: no receipt words, no amount from either reader.
    assert _clear_non_receipt(row(blank))
    # A handwritten note where only the model guessed 1,200: still hidden.
    assert _clear_non_receipt(row(blank, "1200.00"))
    # The layout reader found an amount: a person should look.
    assert not _clear_non_receipt(row(blank, "1200.00", "prefill"))
    # Receipt words in the text: keep it.
    assert not _clear_non_receipt(row(blank | {"found": {"brand": True}}))
    # Not read yet: keep it.
    assert not _clear_non_receipt(ReceiptCandidateRow(machine_result={}))


def test_big_amount_is_read_from_the_line_above_its_label() -> None:
    from relay.infrastructure.text_recheck import hero_amount

    # The orange "EGP" reads as junk on either side of the number.
    assert hero_amount("550 cap\nالمبلع الإجعالي المحول") == "550.00"
    assert hero_amount("60\nمجع 4,200\nالمبلغ الإجمالي المحول") == "4200.00"
    assert hero_amount("مجع 1,700\nTransfer Amount") == "1700.00"
    assert hero_amount("1,900 EGP\nمبلغ التحويل") == "1900.00"
    # The check-mark icon two lines up is not the amount; one digit is noise.
    assert hero_amount("60\nS\nالمبلغ المحول") is None
    assert hero_amount(
        ")ل< 5\nالمبلغ الإجمالي المحول\n550 cap\nالمبلغ الإجمالي المحول"
    ) == ("550.00")
    # Two different amounts above two labels: no guess.
    assert hero_amount("400\nالمبلغ المحول\n500\nالمبلغ المحول") is None


def test_bank_app_amount_and_reference_are_read() -> None:
    from relay.infrastructure.text_recheck import hero_amount

    # Bank apps print the amount right under the success line.
    assert hero_amount("تم التحويل بنجاح\nمعع 1,500.00\nYOUSSEF") == "1500.00"
    read = signals("تم التحويل بنجاح\nFT26323HQ8RM", "تم التحويل بنجاح\nFT26323HQ8RM")
    assert read["values"]["reference_number"] == "FT26323HQ8RM"


async def test_reconcile_lists_photos_and_sms_side_by_side(client, auth_headers, app):
    from relay.infrastructure.reconcile import reconcile

    candidate, _ = await run_receipt(client, auth_headers, app, "rec-sms")
    bundle = await _link_sms(client, auth_headers, [candidate], "rec-sms-phone")
    await _send_sms(
        client, bundle, "notice: You have received EGP 640.00 via InstaPay", _now()
    )
    await _send_sms(
        client, bundle, "notice: You have received EGP 990.00 via InstaPay", _now()
    )
    await _read_and_confirm(app, {"amount": "640.00", "currency": "EGP"})
    report = "\n".join(await reconcile(app.state.settings, _now().date()))
    key = candidate["id"][:8]
    assert f"{key}" in report and "CONFIRMED" in report
    assert "SMS with no receipt (1)" in report
    assert "990.00  no photo with this amount" in report
    assert "SMS the parser could not read (0)" in report


def test_a_lone_fee_is_not_taken_as_the_transfer() -> None:
    from relay.infrastructure.cli import _text_amount

    # A photo where only the fee line was readable: "0.72 EGP" is the fee of
    # a 720 transfer, so the text reader offers no amount at all.
    fee_only = {VERSION: {"text": "رسوم 0.72 EGP", "values": {}}}
    assert _text_amount(fee_only) is None
    assert _text_amount({VERSION: {"text": "4.25 EGP", "values": {}}}) is None
    assert _text_amount({VERSION: {"text": "805.00 EGP", "values": {}}}) == "805.00"


async def _owner(client, auth_headers):
    saved = await client.put(
        "/api/v1/settings/recipient",
        headers=auth_headers,
        json={"name": "YOUSSEF", "phone": "01003405232"},
    )
    assert saved.status_code == 200, saved.text


async def _model_then_text(app, text, *values):
    await model_suggest(
        app.state.database, app.state.object_storage, FakeReader(*values), 10
    )
    await _text(app, text)
    return await auto_confirm(
        app.state.database, app.state.object_storage, app.state.settings
    )


async def test_unreadable_recipient_is_confirmed_by_the_bank_sms(
    client, auth_headers, app
):
    await _owner(client, auth_headers)
    candidate, _ = await run_receipt(client, auth_headers, app, "day-unclear")
    bundle = await _link_sms(client, auth_headers, [candidate], "day-unclear-phone")
    body = "notice: You have received EGP 1,070.00 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    # A photo of a screen: no name and no phone readable.
    counts = await _model_then_text(
        app, "تم التحويل بنجاح", {"amount": "1070.00", "currency": "EGP"}
    )
    assert counts["sms_day"] == 1
    resolution = (await _detail(client, auth_headers, candidate))["resolution_result"]
    assert resolution["auto_confirmed"]["recipient"] == "unclear"
    assert "recipient_mismatch" not in resolution


async def test_unreadable_recipient_waits_when_the_amount_is_not_alone(
    client, auth_headers, app
):
    await _owner(client, auth_headers)
    first, _ = await run_receipt(client, auth_headers, app, "day-unc-a")
    second, _ = await run_receipt(client, auth_headers, app, "day-unc-b")
    bundle = await _link_sms(client, auth_headers, [first, second], "day-unc-phone")
    body = "notice: You have received EGP 720.00 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    # Two receipts of 720 today with different references, one SMS: with no
    # readable recipient either could be the one the SMS proves.
    counts = await _model_then_text(
        app,
        "تم التحويل بنجاح",
        {"amount": "720.00", "currency": "EGP", "reference_number": "111111111111"},
        {"amount": "720.00", "currency": "EGP", "reference_number": "333333333333"},
    )
    assert counts["sms_day"] == 0


async def test_someone_elses_phone_still_waits(client, auth_headers, app):
    await _owner(client, auth_headers)
    candidate, _ = await run_receipt(client, auth_headers, app, "day-other")
    bundle = await _link_sms(client, auth_headers, [candidate], "day-other-phone")
    body = "notice: You have received EGP 845.00 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    counts = await _model_then_text(
        app, "إلى Mohamed A 01021204916", {"amount": "845.00", "currency": "EGP"}
    )
    assert counts["sms_day"] == 0


async def test_lost_separator_is_confirmed_by_the_exact_sms(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "day-point")
    bundle = await _link_sms(client, auth_headers, [candidate], "day-point-phone")
    body = "notice: You have received EGP 425.00 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    # Both readers saw "425" as 4.25.
    counts = await _read_and_confirm(app, {"amount": "4.25", "currency": "EGP"})
    assert counts["sms_day"] == 1
    resolution = (await _detail(client, auth_headers, candidate))["resolution_result"]
    assert resolution["fields"]["amount"]["value"] == "425.00"
    assert resolution["auto_confirmed"]["amount_read_as"] == "4.25"


async def test_whole_small_amount_is_not_scaled(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "day-small")
    bundle = await _link_sms(client, auth_headers, [candidate], "day-small-phone")
    body = "notice: You have received EGP 500.00 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    counts = await _read_and_confirm(app, {"amount": "5.00", "currency": "EGP"})
    assert counts["sms_day"] == 0


async def test_alerts_for_a_silent_phone_and_an_sms_without_receipt(
    client, auth_headers, app
):
    from relay.infrastructure.alerts import current_alerts

    candidate, _ = await run_receipt(client, auth_headers, app, "alert-phone")
    bundle = await _link_sms(client, auth_headers, [candidate], "alert-sms-phone")
    # Yesterday at 10 AM, so "two hours later" is still the same day.
    sent_at = (_now() - timedelta(days=1)).replace(hour=10, minute=0, second=0)
    await _send_sms(
        client, bundle, "notice: You have received EGP 2,140.00 via InstaPay", sent_at
    )
    settings = app.state.settings
    async with app.state.database.session() as session:
        # Two hours later: the SMS has no receipt.
        later = await current_alerts(session, settings, sent_at + timedelta(hours=2))
        assert any(
            a["kind"] == "sms_without_receipt" and a["amount"] == "2140.00"
            for a in later
        )
        # The next morning the list starts empty again.
        morning = (sent_at + timedelta(days=1)).replace(hour=8)
        assert not any(
            a["kind"] == "sms_without_receipt"
            for a in await current_alerts(session, settings, morning)
        )
        # Tomorrow at 1 PM Cairo: the receipt phone has been silent for hours.
        noon = (_now() + timedelta(days=1)).replace(hour=13, minute=0)
        silent = await current_alerts(session, settings, noon)
        assert any(a["kind"] == "silent_phone" for a in silent)
        # At 3 AM nobody works: no silent-phone warning.
        night = (_now() + timedelta(days=1)).replace(hour=3, minute=0)
        assert not any(
            a["kind"] == "silent_phone"
            for a in await current_alerts(session, settings, night)
        )
    response = await client.get("/api/v1/alerts", headers=auth_headers)
    assert response.status_code == 200 and "alerts" in response.json()


async def _ppocr(app, text):
    """What the classic PaddleOCR reader would store."""
    from relay.infrastructure.ppocr_reader import VERSION as PPOCR

    async with app.state.database.session() as session:
        for row in await session.scalars(select(ReceiptCandidateRow)):
            row.machine_result = row.machine_result | {PPOCR: {"text": text}}
        await session.commit()


async def test_paddleocr_helps_when_tesseract_reads_nothing(client, auth_headers, app):
    await _owner(client, auth_headers)
    candidate, _ = await run_receipt(client, auth_headers, app, "day-helper")
    bundle = await _link_sms(client, auth_headers, [candidate], "day-helper-phone")
    # Total 590.59 includes the 0.59 fee: the transfer is 590.00.
    body = "notice: You have received EGP 590.00 via InstaPay"
    await _send_sms(client, bundle, body, _now())
    # A photo of a phone: Tesseract reads neither amount nor recipient.
    await model_suggest(
        app.state.database, app.state.object_storage, FakeReader({}), 10
    )
    await _text(app, "متابعة المعاملة")
    await _ppocr(
        app,
        "إلى YOUSSEF M***** Y****** M******\n01003405232\nالمبلغ الإجمالي 590.59 EGP",
    )
    counts = await auto_confirm(
        app.state.database, app.state.object_storage, app.state.settings
    )
    assert counts["sms_day"] == 1
    resolution = (await _detail(client, auth_headers, candidate))["resolution_result"]
    assert resolution["fields"]["amount"]["value"] == "590.00"
    # PaddleOCR read the owner's name and phone: not an unclear recipient.
    assert "recipient" not in resolution["auto_confirmed"]
    assert "recipient_mismatch" not in resolution


async def test_paddleocr_fills_empty_review_fields(client, auth_headers, app):
    from relay.infrastructure.cli import helper_suggest

    candidate, _ = await run_receipt(client, auth_headers, app, "day-helper-fill")
    await _ppocr(app, "المرجع 885379009975\n590.59 EGP")
    before = (await _detail(client, auth_headers, candidate))["resolution_result"]
    empty = [
        name
        for name in ("amount", "reference_number")
        if before["fields"][name].get("value") is None
    ]
    await helper_suggest(app.state.database)
    fields = (await _detail(client, auth_headers, candidate))["resolution_result"][
        "fields"
    ]
    expected = {"amount": "590.59", "reference_number": "885379009975"}
    for name in empty:
        assert fields[name]["value"] == expected[name]
        assert fields[name]["candidates"][0]["adapter"] == "ppocr_helper_v1"


def test_a_shop_bill_is_not_a_transfer() -> None:
    from relay.infrastructure.text_recheck import shop_invoice

    bill = (
        "لحوم أبو عفصة\nرقم الفاتورة 160 التاريخ 2026/11/19\nالوردية 1,564\n"
        "Total Item Name Price Qty\n480.00 برجر 480.00 1\nالاجمالى 1,977.35\n"
        "خدمة توصيل 20.00\nالصافى 1,997.35\nالمدفوع نقدا 1,997.35\nاسم الطيار"
    )
    assert shop_invoice(bill)
    # Real PaddleOCR readings of bills that were missed: misread "Qtv",
    # "lem Name", a photo of the till screen.
    assert shop_invoice("35 2026/11/19 1,564 11:49AM Total Item Name Price Qtv 240.00")
    assert shop_invoice("11126 202511/19 lem Name Price Qty 830.00 0.33 194.90")
    assert shop_invoice("11418 Total Item 530.00 530.00 20.00 550.00 550.00")
    # A transfer receipt that mentions an amount and a cashier is kept.
    assert not shop_invoice("تم التحويل بنجاح\nInstaPay\n500.00 EGP\ncashier")
    assert not shop_invoice("متابعة المعاملة\nالمبلغ الإجمالي 590.59 EGP")


def test_a_price_tag_shows_no_transfer() -> None:
    from relay.infrastructure.text_recheck import transfer_words

    assert not transfer_words(
        "كندوز ممتاز\nالاجمالي 318.00\n0.600\n2013-08-16\nلحوم ابو عفصة"
    )
    assert transfer_words("متابعة المعاملة\nhagarsalah2222@instapay\n590.59 EGP")
    assert transfer_words("تم التحويل بنجاح\nFT26323HQ8RM")


async def test_bank_sms_decides_when_the_readers_disagree(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "day-disagree")
    bundle = await _link_sms(client, auth_headers, [candidate], "day-disagree-phone")
    await _send_sms(
        client, bundle, "notice: You have received EGP 56.00 via InstaPay", _now()
    )
    await model_suggest(
        app.state.database, app.state.object_storage, FakeReader({}), 10
    )
    # Tesseract reads 56.00, PaddleOCR 590.00 (total minus fee): the only
    # SMS that day says 56.00, so Tesseract's reading is the one proved.
    await _text(app, "تم التحويل بنجاح\n56.00 EGP")
    await _ppocr(app, "تم التحويل بنجاح\nالرسوم 0.59 EGP\nالمبلغ الإجمالي 590.59 EGP")
    counts = await auto_confirm(
        app.state.database, app.state.object_storage, app.state.settings
    )
    assert counts["sms_day"] == 1
    resolution = (await _detail(client, auth_headers, candidate))["resolution_result"]
    assert resolution["fields"]["amount"]["value"] == "56.00"


async def test_paddleocr_brings_back_a_rejected_screen_photo(client, auth_headers, app):
    from relay.infrastructure.cli import helper_reopen, hide_non_receipts

    candidate, _ = await run_receipt(client, auth_headers, app, "day-reopen")
    await _machine_rejected(app)
    # Tesseract could not read the photo of a phone; PaddleOCR could.
    await _text(app, "")
    async with app.state.database.session() as session:
        for row in await session.scalars(select(ReceiptCandidateRow)):
            row.hidden_reason = "not_a_receipt"
        await session.commit()
    await _ppocr(
        app,
        "تم التحويل بنجاح\n1,325 EGP\nالرسوم 1.33 EGP\nالمبلغ الإجمالي 1,326.33 EGP\n"
        "الرقم المرجعي 735522508457",
    )
    assert await helper_reopen(app.state.database) == 1
    await hide_non_receipts(app.state.database)
    detail = await _detail(client, auth_headers, candidate)
    assert detail["review_status"] == "NEEDS_REVIEW"
    fields = detail["resolution_result"]["fields"]
    assert fields["amount"]["value"] == "1325.00"
    assert fields["reference_number"]["value"] == "735522508457"
    # Still visible after the hiding pass.
    async with app.state.database.session() as session:
        row = await session.scalar(select(ReceiptCandidateRow))
        assert row is not None and row.hidden_reason is None


async def test_a_receipt_a_person_rejected_stays_rejected(client, auth_headers, app):
    from relay.infrastructure.cli import helper_reopen

    await run_receipt(client, auth_headers, app, "day-kept-rejected")
    async with app.state.database.session() as session:
        for row in await session.scalars(select(ReceiptCandidateRow)):
            row.review_status = "REJECTED"
            row.reviewed_result = {"decision": "REJECT"}
        await session.commit()
    await _ppocr(app, "تم التحويل بنجاح\n500.00 EGP\nالرقم المرجعي 111122223333")
    assert await helper_reopen(app.state.database) == 0


def test_a_send_screen_with_an_error_is_not_a_transfer() -> None:
    from relay.infrastructure.text_recheck import not_completed

    error = (
        "إرسال نقود\nfonpn@instapay CHECKING\nإرسال النقود إلى المفضلين\n"
        "رقم الهاتف أو عنوان الدفع اللحظي غير صحيح ، يرجى المحاولة مرة أخرى "
        "بعد التحقق من بيانات المستفيد\nEGP 1,300\nأضف سبب التحويل\nالتالي"
    )
    assert not_completed(error)
    assert not_completed("إرسال النقود إلى\n01003405232\nEGP 820\nالتالي")
    assert not not_completed("تم التحويل بنجاح\n530 EGP\nمبلغ التحويل")
    assert not not_completed("Your transaction was successful\n195 EGP")


async def test_review_rejects_a_failed_send_screen(client, auth_headers, app):
    from relay.infrastructure.cli import hide_non_receipts

    candidate, _ = await run_receipt(client, auth_headers, app, "day-failed-send")
    async with app.state.database.session() as session:
        for row in await session.scalars(select(ReceiptCandidateRow)):
            row.review_status = "NEEDS_REVIEW"
            row.reviewed_result = None
        await session.commit()
    await _text(app, "")
    await _ppocr(
        app,
        "إرسال نقود\nرقم الهاتف أو عنوان الدفع اللحظي غير صحيح ، يرجى المحاولة "
        "مرة أخرى\nEGP 1,300\nالتالي",
    )
    await hide_non_receipts(app.state.database)
    detail = await _detail(client, auth_headers, candidate)
    assert detail["review_status"] == "REJECTED"
    assert detail["resolution_result"]["rejected_reason"] == "not_completed"


async def test_dashboard_counts_unchecked_receipts_and_unused_sms(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(client, auth_headers, app, "dash-counts")
    bundle = await _link_sms(client, auth_headers, [candidate], "dash-counts-phone")
    await _send_sms(
        client, bundle, "notice: You have received EGP 777.00 via InstaPay", _now()
    )
    async with app.state.database.session() as session:
        for row in await session.scalars(select(ReceiptCandidateRow)):
            row.review_status = "NEEDS_REVIEW"
        await session.commit()
    today = _now().date().isoformat()
    response = await client.get(
        "/api/v1/dashboard",
        headers=auth_headers,
        params={"date_from": today, "date_to": today},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    # The receipt waits in Review with no SMS check yet: it counts as waiting.
    sms = data["sms_by_status"]
    assert sum(sms.values()) >= 1 and sms.get("VERIFIED", 0) == 0
    # The 777 SMS has no receipt.
    assert data["bank_sms_unused_count"] == 1
    assert data["bank_sms_unused_totals"][0]["amount"] == "777.00"


async def test_paddleocr_values_lead_in_review(client, auth_headers, app):
    from relay.infrastructure.cli import helper_suggest

    candidate, _ = await run_receipt(client, auth_headers, app, "day-primary")
    # The trained model read 114.00; PaddleOCR reads 1,140.
    await model_suggest(
        app.state.database,
        app.state.object_storage,
        FakeReader({"amount": "114.00", "currency": "EGP"}),
        10,
    )
    await _ppocr(app, "تم التحويل بنجاح\n1,140 EGP\nالمرجع 885379009975")
    assert await helper_suggest(app.state.database) == 1
    fields = (await _detail(client, auth_headers, candidate))["resolution_result"][
        "fields"
    ]
    assert fields["amount"]["value"] == "1140.00"
    assert fields["reference_number"]["value"] == "885379009975"
    # Once per reading.
    assert await helper_suggest(app.state.database) == 0


def test_paddleocr_amount_comes_from_its_latin_reader_first() -> None:
    from relay.infrastructure.cli import _paddle_amount

    latin = "577 EGP\nahmedhassan0902@instapay\n01003405232\n820492013227"
    # The Arabic recognizer dropped a digit under the success line.
    both = latin + "\nتم التحويل بنجاح\n57 EGP\nمبلغ التحويل"
    assert _paddle_amount(both, latin) == "577.00"
    # Older readings without the Latin lines still give an amount.
    assert _paddle_amount("تم التحويل بنجاح\n577 EGP\nمبلغ التحويل", None) == "577.00"
