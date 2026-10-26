"""Public Sprint 8 review/trust behavior without enabling experimental OCR."""

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, null, select, update

from relay.application.processing import ProcessingService
from relay.application.receipts import InstaPayReceiptParser
from relay.infrastructure.config import Settings
from relay.infrastructure.image_processing import PillowImagePreprocessor
from relay.infrastructure.processing_models import (
    ReceiptCandidateRow,
    ReceiptReviewedFieldRow,
)
from relay.infrastructure.repositories import SqlUnitOfWork
from tests.test_evidence_backend import EvidenceFixture, run_receipt
from tests.test_processing import FakeOcr, upload_fixture


async def decide(client, headers, candidate, field, action, value=None, reason=None):
    response = await client.post(
        f"/api/v1/receipts/{candidate['id']}/exceptions",
        headers=headers,
        json={
            "command_id": str(uuid4()),
            "expected_updated_at": candidate["updated_at"],
            "field": field,
            "action": action,
            "value": value,
            "reason": reason,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_machine_values_are_not_authoritative_and_all_fields_reviewed(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(client, auth_headers, app, "sprint8-trust")
    original = candidate["machine_result"]
    assert original["evidence_first"]["state"] == "AUTO_FINALIZED"
    assert candidate["review_status"] == "NEEDS_REVIEW"
    assert candidate["resolution_result"]["state"] == "NEEDS_REVIEW"
    assert candidate["reference_normalized"] is None
    assert candidate["amount"] is None
    assert candidate["currency"] is None
    assert candidate["duplicate_of_candidate_id"] is None
    next_item = await client.get(
        "/api/v1/receipts/next-exception", headers=auth_headers
    )
    assert next_item.json()["id"] == candidate["id"]
    dashboard = await client.get(
        "/api/v1/dashboard?date_from=2026-09-01&date_to=2026-12-31",
        headers=auth_headers,
    )
    assert dashboard.json()["totals_by_currency"] == []
    assert dashboard.json()["final_transaction_count"] == 0

    reviewed = candidate
    for field, value in (
        ("reference_number", "987654321234"),
        ("amount", "237.50"),
        ("currency", "EGP"),
        ("date", "2026-09-05"),
        ("time", "14:35"),
    ):
        reviewed = await decide(client, auth_headers, reviewed, field, "CONFIRM", value)
        assert reviewed["machine_result"] == original
        assert reviewed["resolution_result"]["fields"][field]["trust"] == (
            "OPERATOR_CONFIRMED"
        )
    assert reviewed["authoritative_status"] == "REVIEW_FINALIZED"
    assert reviewed["amount"] == "237.50"
    assert reviewed["currency"] == "EGP"
    assert reviewed["reference_normalized"] == "987654321234"
    assert reviewed["review_status"] == "AUTO_ACCEPTED"  # legacy enum, not auto trust
    detail = await client.get(
        f"/api/v1/receipts/{candidate['id']}", headers=auth_headers
    )
    assert [row["field"] for row in detail.json()["reviewed_fields"]] == [
        "reference_number",
        "amount",
        "currency",
        "date",
        "time",
    ]
    assert detail.json()["machine_result"] == original
    async with app.state.database.session() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ReceiptReviewedFieldRow)
            )
            == 5
        )
    dashboard = await client.get(
        "/api/v1/dashboard?date_from=2026-09-01&date_to=2026-12-31",
        headers=auth_headers,
    )
    assert dashboard.json()["totals_by_currency"] == [
        {"currency": "EGP", "amount": "237.50", "transaction_count": 1}
    ]


async def test_correction_unavailable_idempotence_and_stale_revision(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(client, auth_headers, app, "sprint8-correction")
    original = candidate["machine_result"]
    uri = f"/api/v1/receipts/{candidate['id']}/exceptions"
    command = {
        "command_id": str(uuid4()),
        "expected_updated_at": candidate["updated_at"],
        "field": "reference_number",
        "action": "CORRECT",
        "value": "987654321235",
        "reason": "OCR digit misread",
    }
    corrected = await client.post(uri, headers=auth_headers, json=command)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["reference_normalized"] == "987654321235"
    assert corrected.json()["machine_result"] == original
    replay = await client.post(uri, headers=auth_headers, json=command)
    assert replay.status_code == 200 and replay.json() == corrected.json()
    stale = await client.post(
        uri,
        headers=auth_headers,
        json={
            **command,
            "command_id": str(uuid4()),
            "field": "amount",
            "value": "8.00",
        },
    )
    assert stale.status_code == 409
    unavailable = await decide(
        client,
        auth_headers,
        corrected.json(),
        "amount",
        "UNAVAILABLE",
        reason="Image is cropped",
    )
    assert unavailable["resolution_result"]["state"] == "NEEDS_REVIEW"
    assert "amount" in unavailable["resolution_result"]["blocking_fields"]
    assert unavailable["amount"] is None
    detail = await client.get(
        f"/api/v1/receipts/{candidate['id']}", headers=auth_headers
    )
    assert [row["action"] for row in detail.json()["reviewed_fields"]] == [
        "CORRECT",
        "UNAVAILABLE",
    ]
    assert detail.json()["reviewed_fields"][0]["reason"] == "OCR digit misread"
    metrics = await client.get("/api/v1/receipts/review-metrics", headers=auth_headers)
    assert metrics.status_code == 200
    assert metrics.json()["by_field"]["reference_number"]["corrections"] == 1
    assert metrics.json()["by_field"]["amount"]["unavailable"] == 1


async def test_reviewed_reference_only_creates_duplicate_link(
    client, auth_headers, app
):
    first, bundle = await run_receipt(client, auth_headers, app, "sprint8-duplicates")
    await upload_fixture(client, bundle, "duplicate_reference_b.png")
    async with app.state.database.session() as session:
        service = ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
            evidence_extractor=EvidenceFixture(),
        )
        assert len(await service.process_pending()) == 1
    rows = (await client.get("/api/v1/receipts", headers=auth_headers)).json()["items"]
    second = next(row for row in rows if row["id"] != first["id"])
    assert first["reference_normalized"] is None
    assert second["reference_normalized"] is None
    assert second["duplicate_of_candidate_id"] is None
    first = await decide(
        client, auth_headers, first, "reference_number", "CONFIRM", "987654321234"
    )
    assert first["duplicate_of_candidate_id"] is None
    second = await decide(
        client, auth_headers, second, "reference_number", "CONFIRM", "987654321234"
    )
    assert second["duplicate_of_candidate_id"] == first["id"]
    assert second["duplicate_reason"] == "REFERENCE_NUMBER"
    metrics = await client.get("/api/v1/receipts/review-metrics", headers=auth_headers)
    assert metrics.json()["duplicate_links_after_review"] == 1


async def test_rejection_and_reserved_auto_trust_flag(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "sprint8-reject")
    rejected = await decide(client, auth_headers, candidate, None, "REJECT")
    assert rejected["authoritative_status"] == "REJECTED_OR_UNKNOWN_RECEIPT"
    assert rejected["reference_normalized"] is None
    assert rejected["machine_result"] == candidate["machine_result"]
    assert app.state.settings.extraction_auto_trust_enabled is False
    settings_data = app.state.settings.model_dump()
    settings_data["extraction_auto_trust_enabled"] = True
    try:
        Settings(**settings_data)
    except ValueError as exc:
        assert "Automatic extraction trust" in str(exc)
    else:
        raise AssertionError("The reserved auto-trust flag was accepted")


async def test_historical_machine_evidence_remains_readable_but_not_authoritative(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(client, auth_headers, app, "sprint8-historical")
    machine = candidate["machine_result"]
    async with app.state.database.session() as session:
        await session.execute(
            update(ReceiptCandidateRow)
            .where(ReceiptCandidateRow.id == UUID(candidate["id"]))
            .values(
                resolution_result=null(),
                review_status="AUTO_ACCEPTED",
                amount=Decimal("237.50"),
                currency="EGP",
                reference_original="987654321234",
                reference_normalized="987654321234",
            )
        )
        await session.commit()
    detail = await client.get(
        f"/api/v1/receipts/{candidate['id']}", headers=auth_headers
    )
    assert detail.json()["machine_result"] == machine
    assert detail.json()["resolution_result"]["schema_version"] == 2
    assert detail.json()["reference_normalized"] is None
    assert detail.json()["amount"] is None
    tx = await client.get(
        f"/api/v1/transactions/{candidate['id']}", headers=auth_headers
    )
    assert tx.json()["authoritative_status"] == "UNVERIFIED_HISTORICAL"
    assert tx.json()["amount"] is None
    dashboard = await client.get(
        "/api/v1/dashboard?date_from=2026-09-01&date_to=2026-12-31",
        headers=auth_headers,
    )
    assert dashboard.json()["final_transaction_count"] == 0
    assert dashboard.json()["totals_by_currency"] == []
