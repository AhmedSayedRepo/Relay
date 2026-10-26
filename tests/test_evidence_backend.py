"""Evidence product persistence, exception commands and trusted duplicates."""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from relay.application.evidence_ports import ReceiptEvidence
from relay.application.processing import ProcessingService
from relay.application.receipts import InstaPayReceiptParser
from relay.domain.evidence import (
    CRITICAL_FIELDS,
    EvidenceKind,
    EvidenceSource,
    FieldCandidate,
)
from relay.domain.processing import ReceiptType
from relay.infrastructure.image_processing import PillowImagePreprocessor
from relay.infrastructure.processing_models import (
    ReceiptEvidenceRow,
    ReceiptExceptionCommandRow,
    ReceiptReviewAuditRow,
)
from relay.infrastructure.repositories import SqlUnitOfWork
from tests.review_helpers import review_field
from tests.test_binary_objects import provision
from tests.test_processing import FakeOcr, receipt_document, upload_fixture


class EvidenceFixture:
    def __init__(self, unresolved=None, adapter="fixture"):
        self.unresolved = unresolved
        self.adapter = adapter

    async def extract(
        self,
        data,
        *,
        source_event_id,
        timestamp,
        requested_languages,
        timeout_seconds,
        structured_sources=(),
    ):
        assert data
        source = EvidenceSource(
            EvidenceKind.RECEIPT,
            source_event_id,
            self.adapter,
            "v1",
            timestamp,
            "b" * 64,
            "fixture_layout",
        )
        values = {
            "reference_number": "987654321234",
            "amount": "237.50",
            "currency": "EGP",
            "date": "2026-09-05",
            "time": "14:35",
        }
        candidates = tuple(
            FieldCandidate(
                name,
                value,
                value,
                value,
                source,
                ("validated_semantic_region",),
                True,
                name != self.unresolved,
                0.99,
                (1, 1, 20, 10),
            )
            for name, value in values.items()
        )
        doc = replace(
            receipt_document(),
            provider_id="tesseract",
            requested_languages=requested_languages,
        )
        return ReceiptEvidence(
            doc,
            candidates,
            ReceiptType.INSTAPAY_TRANSFER_RECEIPT,
            True,
            True,
            True,
            False,
            {
                "adapter": self.adapter,
                "adapter_version": "v1",
                "layout_profile": "fixture_layout",
            },
        )


async def run_receipt(
    client,
    auth_headers,
    app,
    key,
    unresolved=None,
    filename="clean_instapay.png",
    adapter="fixture",
):
    app.state.settings.receipt_evidence_enabled = True
    _, bundle = await provision(client, auth_headers, key)
    await upload_fixture(client, bundle, filename)
    async with app.state.database.session() as session:
        service = ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
            evidence_extractor=EvidenceFixture(unresolved, adapter),
        )
        assert len(await service.process_pending()) == 1
    response = await client.get("/api/v1/receipts", headers=auth_headers)
    return response.json()["items"][0], bundle


async def test_evidence_suggestion_stays_untrusted_and_persists_trace(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(client, auth_headers, app, "evidence-auto")
    assert candidate["review_status"] == "NEEDS_REVIEW"
    evidence = candidate["machine_result"]["evidence_first"]
    assert evidence["state"] == "AUTO_FINALIZED"
    assert evidence["blocking_fields"] == []
    assert candidate["reference_normalized"] is None
    assert candidate["resolution_result"]["state"] == "NEEDS_REVIEW"
    assert candidate["resolution_result"]["blocking_fields"] == list(CRITICAL_FIELDS)
    assert evidence["payment_verification"] == "NOT_PERFORMED"
    for _ in range(2):
        enqueued = await client.post(
            f"/api/v1/objects/{candidate['object_id']}/process",
            headers=auth_headers,
            json={
                "processor_version": "receipt_evidence:v1",
                "parser_type": "receipt_adapter",
                "parser_version": "v1",
            },
        )
        assert enqueued.status_code == 200, enqueued.text
        assert enqueued.json()["id"] == candidate["job_id"]
    async with app.state.database.session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ReceiptEvidenceRow))
            == 1
        )


@pytest.mark.parametrize(
    "skip_marker,supported", [(True, False), (False, False), (True, True)]
)
async def test_visual_non_admission_without_ocr_is_not_ocr_failure(
    client, auth_headers, app, skip_marker, supported
):
    class NoRecognition(EvidenceFixture):
        async def extract(self, data, **kwargs):
            evidence = await super().extract(data, **kwargs)
            return replace(
                evidence,
                document=replace(evidence.document, normalized_text="", lines=()),
                candidates=(),
                receipt_type=ReceiptType.UNKNOWN_RECEIPT,
                classification_resolved=supported,
                supported_layout=supported,
                receipt_possible=False,
                metadata={
                    "recognition_skipped": (
                        "visual_registration_not_admitted" if skip_marker else None
                    )
                },
            )

    app.state.settings.receipt_evidence_enabled = True
    _, bundle = await provision(client, auth_headers, "visual-skip")
    await upload_fixture(client, bundle, "clean_instapay.png")
    async with app.state.database.session() as session:
        service = ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
            evidence_extractor=NoRecognition(),
        )
        assert len(await service.process_pending()) == 1
    jobs = (await client.get("/api/v1/processing/jobs", headers=auth_headers)).json()[
        "items"
    ]
    receipts = (await client.get("/api/v1/receipts", headers=auth_headers)).json()[
        "items"
    ]
    if skip_marker and not supported:
        assert len(receipts) == 1 and receipts[0]["reference_normalized"] is None
        assert (
            receipts[0]["machine_result"]["evidence_first"]["state"]
            == "REJECTED_OR_UNKNOWN_RECEIPT"
        )
        assert jobs[0]["failure_code"] == "UNKNOWN_RECEIPT"
        assert jobs[0]["status"] == "SKIPPED"
    else:
        assert receipts == [] and jobs[0]["failure_code"] == "OCR_EMPTY"


async def test_one_exception_idempotent_confirm_and_legacy_bypass_blocked(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(
        client, auth_headers, app, "evidence-one", "amount"
    )
    original_machine = candidate["machine_result"]
    assert original_machine["evidence_first"]["blocking_fields"] == ["amount"]
    uri = f"/api/v1/receipts/{candidate['id']}"
    assert (
        await client.post(
            uri + "/review", headers=auth_headers, json={"action": "ACCEPT"}
        )
    ).status_code == 409
    body = {
        "command_id": str(uuid4()),
        "expected_updated_at": candidate["updated_at"],
        "field": "amount",
        "action": "CONFIRM",
        "value": "237.50",
    }
    first = await client.post(uri + "/exceptions", headers=auth_headers, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["review_status"] == "NEEDS_REVIEW"
    assert (
        "amount"
        not in first.json()["reviewed_result"]["evidence_first"]["blocking_fields"]
    )
    assert first.json()["machine_result"] == original_machine
    job = await client.get(
        f"/api/v1/processing/jobs/{candidate['job_id']}", headers=auth_headers
    )
    assert job.json()["status"] == "NEEDS_REVIEW"
    replay = await client.post(uri + "/exceptions", headers=auth_headers, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert (
        await client.post(
            uri + "/exceptions", headers=auth_headers, json={**body, "value": "999"}
        )
    ).status_code == 409
    async with app.state.database.session() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ReceiptExceptionCommandRow)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(ReceiptReviewAuditRow)
            )
            == 1
        )


async def test_evidence_retention_children_removed(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "evidence-retention")
    async with app.state.database.session() as session:
        uow = SqlUnitOfWork(session)
        await uow.processing.purge_expired(
            datetime.fromisoformat(candidate["expires_at"]) + timedelta(seconds=1)
        )
        await uow.commit()
        assert (
            await session.scalar(select(func.count()).select_from(ReceiptEvidenceRow))
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(ReceiptExceptionCommandRow)
            )
            == 0
        )


async def test_untrusted_reference_not_canonical_and_stale_invalid_corrections(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(
        client, auth_headers, app, "evidence-ref", "reference_number"
    )
    assert candidate["reference_normalized"] is None
    assert candidate["duplicate_of_candidate_id"] is None
    uri = f"/api/v1/receipts/{candidate['id']}/exceptions"
    body = {
        "command_id": str(uuid4()),
        "expected_updated_at": candidate["updated_at"],
        "field": "reference_number",
        "action": "CORRECT",
        "value": "bad",
    }
    assert (await client.post(uri, headers=auth_headers, json=body)).status_code == 409
    body["value"] = "987654321234"
    corrected = await client.post(uri, headers=auth_headers, json=body)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["reference_normalized"] == "987654321234"
    assert (
        await client.post(
            uri, headers=auth_headers, json={**body, "command_id": str(uuid4())}
        )
    ).status_code == 409


async def test_trusted_duplicate_automated_same_device(client, auth_headers, app):
    first, bundle = await run_receipt(client, auth_headers, app, "evidence-duplicates")
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
    receipts = (await client.get("/api/v1/receipts", headers=auth_headers)).json()[
        "items"
    ]
    assert len(receipts) == 2
    duplicate = next(item for item in receipts if item["id"] != first["id"])
    assert duplicate["duplicate_of_candidate_id"] is None
    first = await review_field(
        client, auth_headers, first, "reference_number", "CONFIRM", "987654321234"
    )
    duplicate = await review_field(
        client, auth_headers, duplicate, "reference_number", "CONFIRM", "987654321234"
    )
    assert duplicate["duplicate_of_candidate_id"] == first["id"]
    assert duplicate["duplicate_reason"] == "REFERENCE_NUMBER"
    assert duplicate["review_status"] == "NEEDS_REVIEW"


async def test_resolved_field_cannot_be_reconfirmed_and_reject_audit(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(
        client, auth_headers, app, "evidence-reject", "amount"
    )
    uri = f"/api/v1/receipts/{candidate['id']}/exceptions"
    body = {
        "command_id": str(uuid4()),
        "expected_updated_at": candidate["updated_at"],
        "field": "currency",
        "action": "CONFIRM",
        "value": "EGP",
    }
    confirmed = await client.post(uri, headers=auth_headers, json=body)
    assert confirmed.status_code == 200, confirmed.text
    assert (
        await client.post(
            uri,
            headers=auth_headers,
            json={
                **body,
                "command_id": str(uuid4()),
                "expected_updated_at": confirmed.json()["updated_at"],
            },
        )
    ).status_code == 409
    rejected = await client.post(
        uri,
        headers=auth_headers,
        json={
            **body,
            "command_id": str(uuid4()),
            "expected_updated_at": confirmed.json()["updated_at"],
            "field": None,
            "action": "REJECT",
            "value": None,
        },
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["review_status"] == "REJECTED"
    assert (
        rejected.json()["reviewed_result"]["evidence_first"]["state"]
        == "REJECTED_OR_UNKNOWN_RECEIPT"
    )


async def test_reject_preserves_trusted_canonical_and_duplicate_identity(
    client, auth_headers, app
):
    canonical, bundle = await run_receipt(
        client, auth_headers, app, "reject-identities", "amount"
    )
    await upload_fixture(client, bundle, "duplicate_reference_b.png")
    async with app.state.database.session() as session:
        service = ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
            evidence_extractor=EvidenceFixture("amount"),
        )
        assert len(await service.process_pending()) == 1
    receipts = (await client.get("/api/v1/receipts", headers=auth_headers)).json()[
        "items"
    ]
    duplicate = next(item for item in receipts if item["id"] != canonical["id"])
    assert duplicate["duplicate_of_candidate_id"] is None
    canonical = await review_field(
        client, auth_headers, canonical, "reference_number", "CONFIRM", "987654321234"
    )
    duplicate = await review_field(
        client, auth_headers, duplicate, "reference_number", "CONFIRM", "987654321234"
    )
    assert duplicate["duplicate_of_candidate_id"] == canonical["id"]
    rejected_canonical = await client.post(
        f"/api/v1/receipts/{canonical['id']}/exceptions",
        headers=auth_headers,
        json={
            "command_id": str(uuid4()),
            "expected_updated_at": canonical["updated_at"],
            "action": "REJECT",
        },
    )
    assert rejected_canonical.status_code == 200, rejected_canonical.text
    assert (
        rejected_canonical.json()["reference_normalized"]
        == canonical["reference_normalized"]
    )
    assert rejected_canonical.json()["duplicate_of_candidate_id"] is None
    rejected_duplicate = await client.post(
        f"/api/v1/receipts/{duplicate['id']}/review",
        headers=auth_headers,
        json={"action": "REJECT"},
    )
    assert rejected_duplicate.status_code == 200, rejected_duplicate.text
    assert (
        rejected_duplicate.json()["reference_normalized"]
        == duplicate["reference_normalized"]
    )
    assert rejected_duplicate.json()["duplicate_of_candidate_id"] == canonical["id"]
    assert rejected_duplicate.json()["duplicate_reason"] == "REFERENCE_NUMBER"
    for result in (rejected_canonical.json(), rejected_duplicate.json()):
        assert result["review_status"] == "REJECTED"
        assert (
            result["reviewed_result"]["evidence_first"]["state"]
            == "REJECTED_OR_UNKNOWN_RECEIPT"
        )
        assert (
            result["machine_result"]["evidence_first"]["trusted_reference"]
            == "987654321234"
        )


async def test_reject_untrusted_reference_never_promotes_canonical(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(
        client, auth_headers, app, "reject-untrusted", "reference_number"
    )
    response = await client.post(
        f"/api/v1/receipts/{candidate['id']}/exceptions",
        headers=auth_headers,
        json={
            "command_id": str(uuid4()),
            "expected_updated_at": candidate["updated_at"],
            "action": "REJECT",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["reference_normalized"] is None
    assert response.json()["duplicate_of_candidate_id"] is None
    assert (
        response.json()["reviewed_result"]["evidence_first"]["trusted_reference"]
        is None
    )


async def test_instapay_operator_reference_validates_adapter_format(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(
        client,
        auth_headers,
        app,
        "operator-ref-format",
        "reference_number",
        adapter="instapay_receipt",
    )
    uri = f"/api/v1/receipts/{candidate['id']}/exceptions"
    body = {
        "expected_updated_at": candidate["updated_at"],
        "field": "reference_number",
        "action": "CORRECT",
    }
    for invalid in ("01012345678", "20260929", "ABC123456", "012345678901"):
        response = await client.post(
            uri,
            headers=auth_headers,
            json={**body, "command_id": str(uuid4()), "value": invalid},
        )
        assert response.status_code == 409, response.text
    corrected = await client.post(
        uri,
        headers=auth_headers,
        json={**body, "command_id": str(uuid4()), "value": "987654321234"},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["reference_normalized"] == "987654321234"


async def test_invalid_native_document_never_reaches_legacy_parser(
    client, auth_headers, app
):
    _, bundle = await provision(client, auth_headers, "native-document-bounds")
    await upload_fixture(client, bundle, "clean_instapay.png")

    class GuardParser(InstaPayReceiptParser):
        called = False

        def parse(self, document):
            self.called = True
            raise AssertionError("Invalid native document reached parser")

    from relay.domain.processing import OcrLine

    parser = GuardParser()
    bad_document = replace(receipt_document(), lines=(OcrLine("x" * 513, 0.99),))
    async with app.state.database.session() as session:
        service = ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(bad_document),
            parser,
        )
        assert len(await service.process_pending()) == 1
    assert parser.called is False
    jobs = (await client.get("/api/v1/processing/jobs", headers=auth_headers)).json()[
        "items"
    ]
    assert jobs[0]["failure_code"] == "VALIDATION_FAILED"
