from decimal import Decimal
from uuid import UUID

from PIL import Image

from relay.infrastructure import ppocr_reader
from relay.infrastructure.cli import hide_non_receipts, rescue_hidden
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.processing_models import ReceiptCandidateRow
from tests.test_auto_confirm import _link_sms, _now
from tests.test_evidence_backend import run_receipt
from tests.test_sms_verification import _send_sms

RECEIPT = (
    "تم التحويل بنجاح\n3,750 EGP\nالمبلغ المحول\n"
    "إلى YOUSSEF M***** 01003405232\nالمرجع 123456789012"
)
OWNER = ("YOUSSEF", "01003405232")
WAITING = [Decimal("3750.00")]


def _reading(text: str) -> dict[str, str]:
    return {"text": text, "latin": text}


def test_a_sideways_receipt_is_read_turned() -> None:
    picture = Image.new("RGB", (300, 100), "white")  # lying on its side

    def read(variant):
        return _reading(RECEIPT if variant.height > variant.width else "")

    rule, reading, amount, tried = ppocr_reader.try_variants(
        picture, WAITING, OWNER, read
    )
    assert rule == "rotate_90" and amount == "3750.00" and tried == ["rotate_90"]
    assert reading["text"] == RECEIPT


def test_a_blurry_receipt_is_read_cropped_and_enlarged() -> None:
    picture = Image.new("RGB", (400, 600), "white")

    def read(variant):
        # Too small to read until it is enlarged.
        return _reading(RECEIPT if max(variant.size) >= 1100 else "")

    rule, _, amount, tried = ppocr_reader.try_variants(picture, WAITING, OWNER, read)
    assert rule == "crop_2x" and amount == "3750.00"
    assert tried == ["rotate_90", "rotate_180", "rotate_270", "crop_2x"]


def test_only_the_waiting_amount_sent_to_the_owner_counts() -> None:
    picture = Image.new("RGB", (400, 600), "white")
    other = RECEIPT.replace("01003405232", "01112223334").replace("YOUSSEF", "AHMED")
    rule, _, _, tried = ppocr_reader.try_variants(
        picture, WAITING, OWNER, lambda variant: _reading(other)
    )
    assert rule is None and len(tried) == 6
    rule, _, _, _ = ppocr_reader.try_variants(
        picture, [Decimal("960.00")], OWNER, lambda variant: _reading(RECEIPT)
    )
    assert rule is None


async def _hidden_receipt(client, auth_headers, app, key, amount=None):
    candidate, _ = await run_receipt(client, auth_headers, app, key)
    bundle = await _link_sms(client, auth_headers, [candidate], key + "-sms")
    async with app.state.database.session() as session:
        row = await session.get(ReceiptCandidateRow, UUID(candidate["id"]))
        event = await session.get(RawEventRow, row.raw_event_id)
        event.occurred_at = _now()
        row.hidden_reason = "not_a_receipt"
        await session.commit()
    if amount is not None:
        await _send_sms(
            client,
            bundle,
            f"notice: You have received EGP {amount} via InstaPay",
            _now(),
        )
    return candidate


async def _row(app, candidate) -> ReceiptCandidateRow:
    async with app.state.database.session() as session:
        return await session.get(ReceiptCandidateRow, UUID(candidate["id"]))


async def test_a_hidden_photo_is_read_again_when_a_bank_sms_waits(
    client, auth_headers, app, monkeypatch
):
    candidate = await _hidden_receipt(client, auth_headers, app, "retry-ok", "3750.00")
    database, storage = app.state.database, app.state.object_storage
    assert await rescue_hidden(database, app.state.settings) == 1
    row = await _row(app, candidate)
    assert row.hidden_reason is None and row.review_status == "NEEDS_REVIEW"
    assert row.resolution_result["retry_read"]["state"] == "pending"
    assert row.resolution_result["retry_read"]["amounts"] == ["3750.00"]

    monkeypatch.setattr(ppocr_reader, "_read_picture", lambda p: _reading(RECEIPT))
    assert await ppocr_reader.retry_pass(database, storage) == 1
    row = await _row(app, candidate)
    retry = row.resolution_result["retry_read"]
    assert retry["state"] == "read" and retry["rule"] == "rotate_90"
    assert retry["amount"] == "3750.00"
    # Its reading is now the PaddleOCR one; its values apply again.
    assert row.machine_result[ppocr_reader.VERSION]["retry"] == "rotate_90"
    assert "primary_reader" not in row.resolution_result
    await hide_non_receipts(database)
    assert (await _row(app, candidate)).hidden_reason is None


async def test_a_real_non_receipt_with_no_sms_stays_hidden(client, auth_headers, app):
    candidate = await _hidden_receipt(client, auth_headers, app, "retry-none")
    assert await rescue_hidden(app.state.database, app.state.settings) == 0
    row = await _row(app, candidate)
    assert row.hidden_reason == "not_a_receipt"
    assert "retry_read" not in row.resolution_result


async def test_when_every_retry_fails_it_waits_in_review(
    client, auth_headers, app, monkeypatch
):
    candidate = await _hidden_receipt(client, auth_headers, app, "retry-fail", "510.00")
    database, storage = app.state.database, app.state.object_storage
    # The one-off command for a given day does the same.
    day = _now().astimezone(ppocr_reader_zone(app)).date()
    assert await rescue_hidden(database, app.state.settings, day) == 1
    monkeypatch.setattr(
        ppocr_reader, "_read_picture", lambda p: _reading("a cat on a sofa")
    )
    assert await ppocr_reader.retry_pass(database, storage) == 1
    row = await _row(app, candidate)
    retry = row.resolution_result["retry_read"]
    assert retry["state"] == "failed" and len(retry["tried"]) == 6
    await hide_non_receipts(database)
    row = await _row(app, candidate)
    assert row.hidden_reason is None and row.review_status == "NEEDS_REVIEW"
    listed = await client.get("/api/v1/transactions", headers=auth_headers)
    reasons = {i["id"]: i["status_reason"] for i in listed.json()["items"]}
    assert reasons[candidate["id"]] == "unreadable_sms_waiting"


def ppocr_reader_zone(app):
    from zoneinfo import ZoneInfo

    return ZoneInfo(app.state.settings.receipt_timezone)
