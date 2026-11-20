"""Rejected page: bring a receipt back to Review, the warning before a reject,
and the same-day warning for a rejected receipt whose bank SMS is unused."""

from uuid import UUID

from sqlalchemy import select

from relay.infrastructure.cli import model_suggest
from relay.infrastructure.processing_models import (
    ReceiptCandidateRow,
    ReceiptReviewAuditRow,
)
from relay.infrastructure.text_recheck import bank_app_amount
from tests.test_auto_confirm import FakeReader, _link_sms, _now, _text
from tests.test_evidence_backend import run_receipt
from tests.test_sms_verification import _send_sms


async def _receipt_with_unused_sms(client, auth_headers, app, key):
    candidate, _ = await run_receipt(client, auth_headers, app, key)
    bundle = await _link_sms(client, auth_headers, [candidate], key + "-sms")
    await _send_sms(
        client, bundle, "notice: You have received EGP 1,096.00 via InstaPay", _now()
    )
    await model_suggest(
        app.state.database,
        app.state.object_storage,
        FakeReader({"amount": "1096.00", "currency": "EGP"}),
        10,
    )
    await _text(app)
    return candidate


async def _reject(app, candidate_id):
    async with app.state.database.session() as session:
        row = await session.get(ReceiptCandidateRow, UUID(candidate_id))
        resolution = dict(row.resolution_result)
        resolution["state"] = "REJECTED_OR_UNKNOWN_RECEIPT"
        row.resolution_result = resolution
        row.review_status = "REJECTED"
        row.reviewed_result = {"review_status": "REJECTED"}
        await session.commit()


async def test_a_rejected_receipt_is_listed_and_brought_back(client, auth_headers, app):
    candidate = await _receipt_with_unused_sms(client, auth_headers, app, "aside")
    # Before a reject, the reviewer is told the bank SMS is still unused.
    warned = await client.get(
        f"/api/v1/receipts/{candidate['id']}/unused-sms", headers=auth_headers
    )
    assert warned.status_code == 200, warned.text
    assert [item["amount"] for item in warned.json()["items"]] == ["1096.00"]
    await _reject(app, candidate["id"])
    listed = await client.get(
        "/api/v1/transactions?review_status=SET_ASIDE", headers=auth_headers
    )
    items = {item["id"]: item for item in listed.json()["items"]}
    assert items[candidate["id"]]["status_reason"] == "rejected_by_reviewer"
    assert items[candidate["id"]]["suggested_amount"] == "1096.00"
    # The same evening, the warnings list it.
    from relay.infrastructure.alerts import current_alerts, render_email
    from tests.test_auto_confirm import CAIRO

    async with app.state.database.session() as session:
        alerts = await current_alerts(session, app.state.settings)
    rejected = [a for a in alerts if a["kind"] == "rejected_with_sms"]
    assert [a["id"] for a in rejected] == [candidate["id"]]
    assert "bank SMS is unused" in render_email(rejected, CAIRO, _now())
    # Bring it back: in Review again, recorded in the audit.
    back = await client.post(
        f"/api/v1/receipts/{candidate['id']}/reopen", headers=auth_headers
    )
    assert back.status_code == 204, back.text
    async with app.state.database.session() as session:
        row = await session.get(ReceiptCandidateRow, UUID(candidate["id"]))
        assert row.review_status == "NEEDS_REVIEW" and row.reviewed_result is None
        assert row.resolution_result["kept_by"]["user"]
        audit = (
            await session.scalars(
                select(ReceiptReviewAuditRow).where(
                    ReceiptReviewAuditRow.candidate_id == row.id
                )
            )
        ).all()
        assert [entry.action for entry in audit] == ["REOPEN"]
    again = await client.post(
        f"/api/v1/receipts/{candidate['id']}/reopen", headers=auth_headers
    )
    assert again.status_code == 409


def test_bank_app_transfer_amount_without_currency() -> None:
    posted = (
        "1.,096 action AccountDel Transaction has been posted to benefici "
        "20 Nov 2026 03:19 PM nour3zzam@instapay"
    )
    assert bank_app_amount(posted) == "1096.00"
    # No transfer words, or two amounts: nothing is guessed.
    assert bank_app_amount("1,096 01003405232") is None
    assert bank_app_amount("Transaction 1,096 and 2,500") is None
