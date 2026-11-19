from uuid import UUID

from sqlalchemy import select

from relay.infrastructure import ppocr_reader
from relay.infrastructure.processing_models import ReceiptCandidateRow
from relay.infrastructure.reader_eval import grade
from tests.test_evidence_backend import run_receipt


def box(left: float, top: float, width: float = 50, height: float = 20):
    return [
        [left, top],
        [left + width, top],
        [left + width, top + height],
        [left, top + height],
    ]


def test_text_boxes_become_lines_top_to_bottom() -> None:
    boxes = [box(200, 100), box(10, 102), box(10, 160), box(10, 40)]
    texts = ["EGP", "550", "المبلغ المحول", "تم التحويل بنجاح"]
    assert ppocr_reader.lines(boxes, texts) == [
        "تم التحويل بنجاح",
        "550 EGP",
        "المبلغ المحول",
    ]


def test_its_lines_feed_the_same_rules() -> None:
    text = "تم التحويل بنجاح\n1,115 EGP\nمبلغ التحويل\nإلى YOUSSEF 01003405232"
    grades = grade(text, {"amount": "1115.00"}, ("YOUSSEF", "01003405232"), single=True)
    assert grades["amount"] == "ok" and grades["recipient"] == "ok"


async def test_each_receipt_is_read_once(client, auth_headers, app, monkeypatch):
    candidate, _ = await run_receipt(client, auth_headers, app, "ppocr-once")
    monkeypatch.setattr(ppocr_reader, "read_ppocr", lambda data: "550 EGP")
    database, storage = app.state.database, app.state.object_storage
    assert await ppocr_reader.ppocr_pass(database, storage, 5) >= 1
    assert await ppocr_reader.ppocr_pass(database, storage, 5) == 0
    async with database.session() as session:
        machine = await session.scalar(
            select(ReceiptCandidateRow.machine_result).where(
                ReceiptCandidateRow.id == UUID(candidate["id"])
            )
        )
    assert machine[ppocr_reader.VERSION]["text"] == "550 EGP"
