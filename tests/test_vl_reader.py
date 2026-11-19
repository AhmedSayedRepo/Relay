import io
from uuid import UUID

from PIL import Image
from sqlalchemy import select

from relay.infrastructure import vl_reader
from relay.infrastructure.processing_models import ReceiptCandidateRow
from relay.infrastructure.reader_eval import grade
from tests.test_evidence_backend import run_receipt


def test_image_is_shrunk_and_sent_with_the_ocr_prompt() -> None:
    out = io.BytesIO()
    Image.new("RGB", (900, 1600), "white").save(out, "PNG")
    small = vl_reader.prepare(out.getvalue())
    assert max(Image.open(io.BytesIO(small)).size) == vl_reader.LONGEST_SIDE
    content = vl_reader.request_body(small)["messages"][0]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert content[1] == {"type": "text", "text": "OCR:"}


async def test_each_receipt_is_read_once_and_stored(
    client, auth_headers, app, monkeypatch
):
    candidate, _ = await run_receipt(client, auth_headers, app, "vl-once")
    calls: list[str] = []

    def fake(image: bytes, url: str, timeout: float = 900.0) -> str:
        calls.append(url)
        return "تم التحويل بنجاح\n550 EGP\nالمبلغ المحول"

    monkeypatch.setattr(vl_reader, "read_vl", fake)
    database, storage = app.state.database, app.state.object_storage
    assert await vl_reader.vl_pass(database, storage, "http://ocr:8080", 5) >= 1
    assert await vl_reader.vl_pass(database, storage, "http://ocr:8080", 5) == 0
    async with database.session() as session:
        machine = await session.scalar(
            select(ReceiptCandidateRow.machine_result).where(
                ReceiptCandidateRow.id == UUID(candidate["id"])
            )
        )
    assert machine[vl_reader.VERSION]["text"].startswith("تم التحويل")


async def test_server_down_stores_nothing(client, auth_headers, app, monkeypatch):
    await run_receipt(client, auth_headers, app, "vl-down")

    def down(image: bytes, url: str, timeout: float = 900.0) -> str:
        raise ConnectionRefusedError

    monkeypatch.setattr(vl_reader, "read_vl", down)
    database, storage = app.state.database, app.state.object_storage
    try:
        await vl_reader.vl_pass(database, storage, "http://ocr:8080", 5)
    except OSError:
        pass
    else:
        raise AssertionError("expected the loop to retry later")


def test_model_text_is_graded_as_one_reading() -> None:
    text = "تم التحويل بنجاح\n1,115 EGP\nمبلغ التحويل\nإلى YOUSSEF 01003405232"
    grades = grade(text, {"amount": "1115.00"}, ("YOUSSEF", "01003405232"), single=True)
    assert grades["amount"] == "ok" and grades["recipient"] == "ok"


def test_model_reads_everything_only_at_night() -> None:
    from datetime import UTC, datetime

    # Cairo is UTC+2: 23:30 UTC is 01:30 there (night), 07:00 UTC is 09:00.
    assert vl_reader.night(datetime(2026, 11, 18, 23, 30, tzinfo=UTC))
    assert not vl_reader.night(datetime(2026, 11, 19, 7, 0, tzinfo=UTC))


async def test_daytime_reads_only_receipts_waiting_in_review(
    client, auth_headers, app, monkeypatch
):
    candidate, _ = await run_receipt(client, auth_headers, app, "vl-day")
    database, storage = app.state.database, app.state.object_storage
    async with database.session() as session:
        row = await session.scalar(
            select(ReceiptCandidateRow).where(
                ReceiptCandidateRow.id == UUID(candidate["id"])
            )
        )
        assert row is not None
        row.review_status = "AUTO_ACCEPTED"
        await session.commit()
    monkeypatch.setattr(vl_reader, "read_vl", lambda image, url, timeout=0: "x")
    assert await vl_reader.vl_pass(database, storage, "u", 5, review_only=True) == 0
    assert await vl_reader.vl_pass(database, storage, "u", 5) == 1
