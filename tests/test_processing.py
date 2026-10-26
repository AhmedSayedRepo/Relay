import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from relay.application.processing import ProcessingService
from relay.application.processing_errors import ProcessingError
from relay.application.receipts import InstaPayReceiptParser, classify_receipt
from relay.domain.processing import (
    FieldStatus,
    OcrDocument,
    OcrLine,
    OcrLineOrigin,
    OcrToken,
    PreprocessedImage,
    ProcessingFailure,
    ReceiptType,
    ReviewStatus,
)
from relay.infrastructure.image_processing import PillowImagePreprocessor
from relay.infrastructure.processing_models import ProcessingJobRow
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.infrastructure.tesseract_ocr import TesseractOcrProvider
from tests.review_helpers import review_field
from tests.test_binary_objects import (
    device_headers,
    initialize,
    provision,
    request,
)

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic_receipts"


def document(
    lines: list[str], confidence: float = 0.96, languages: tuple[str, ...] = ("eng",)
) -> OcrDocument:
    blocks = tuple(OcrLine(value, confidence) for value in lines)
    return OcrDocument(
        "test-ocr",
        "1.0",
        languages,
        ("ara", "eng"),
        "\n".join(lines),
        blocks,
        confidence,
        4,
    )


def receipt_document(
    reference: str = "ABC-123-456", amount: str = "EGP 1,250.50"
) -> OcrDocument:
    return document(
        [
            "InstaPay",
            "Transfer Successful",
            f"Amount: {amount}",
            "Date: 2026-09-05",
            "Time: 14:35",
            f"Reference Number: {reference}",
            "Sender: Synthetic Sender",
            "Recipient: Synthetic Recipient",
            "Recipient ID: ACCT-***-1234",
        ]
    )


class FakeOcr:
    provider_id = "tesseract"
    provider_version = "test-1"
    available_languages = ("ara", "eng")

    def __init__(self, value: OcrDocument | None = None) -> None:
        self.value = value or receipt_document()

    async def extract(self, image, requested_languages, timeout_seconds):
        assert image.content_type == "image/png"
        assert 0 < timeout_seconds <= 60
        return replace(
            self.value,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            requested_languages=requested_languages,
        )


class TimeoutOcr(FakeOcr):
    async def extract(self, image, requested_languages, timeout_seconds):
        raise ProcessingError(ProcessingFailure.OCR_TIMEOUT, transient=True)


def test_instapay_parser_extracts_decimal_reference_time_and_provenance() -> None:
    parsed = InstaPayReceiptParser().parse(receipt_document())
    assert parsed.receipt_type == ReceiptType.INSTAPAY_TRANSFER_RECEIPT
    assert parsed.parser_version == "v1"
    assert parsed.amount == Decimal("1250.50")
    assert parsed.currency == "EGP"
    assert parsed.reference_original == "ABC-123-456"
    assert parsed.reference_normalized == "ABC123456"
    assert parsed.transaction_occurred_at is not None
    assert parsed.transaction_occurred_at.isoformat() == "2026-09-05T14:35:00"
    assert parsed.timezone_state == "UNKNOWN"
    assert parsed.sender_name == "Synthetic Sender"
    assert parsed.recipient_name == "Synthetic Recipient"
    assert parsed.review_status == ReviewStatus.NEEDS_REVIEW
    fields = {field.field: field for field in parsed.fields}
    assert fields["amount"].status == FieldStatus.CONFIDENT
    assert fields["reference_number"].evidence


def test_parser_is_deterministic_and_does_not_invent_uncertain_values() -> None:
    parser = InstaPayReceiptParser()
    missing = parser.parse(
        document(["InstaPay", "Transfer", "Amount: EGP 12.00"], 0.94)
    )
    assert missing.reference_normalized is None
    assert missing.review_status == ReviewStatus.NEEDS_REVIEW

    malformed = parser.parse(receipt_document(reference="***"))
    assert malformed.reference_normalized is None
    assert malformed.review_status == ReviewStatus.NEEDS_REVIEW

    ambiguous = parser.parse(receipt_document(amount="EGP 1,250.50 or EGP 1,280.50"))
    fields = {field.field: field for field in ambiguous.fields}
    assert fields["amount"].status == FieldStatus.UNCERTAIN
    assert ambiguous.review_status == ReviewStatus.NEEDS_REVIEW

    unknown = parser.parse(document(["Synthetic memo", "nothing to classify"]))
    assert unknown.receipt_type == ReceiptType.UNKNOWN_RECEIPT
    assert unknown.amount is None
    assert unknown.review_status == ReviewStatus.REJECTED
    assert classify_receipt(unknown_document := document(["InstaPay only"])) == (
        ReceiptType.UNKNOWN_RECEIPT,
        ("instapay_brand_marker",),
    )
    assert unknown_document.normalized_text == "InstaPay only"


def test_mixed_language_markers_are_classified_without_ocr_coupling() -> None:
    parsed = InstaPayReceiptParser().parse(
        document(
            [
                "InstaPay تحويل",
                "المبلغ: ١٢٥.٥٠ ج.م",
                "رقم المرجع: AB-١٢٣٤٥٦",
            ],
            languages=("eng", "ara"),
        )
    )
    assert parsed.receipt_type == ReceiptType.INSTAPAY_TRANSFER_RECEIPT
    assert parsed.amount == Decimal("125.50")
    assert parsed.currency == "EGP"
    assert parsed.reference_normalized == "AB123456"


def test_preprocessor_is_bounded_and_does_not_mutate_original() -> None:
    source = (FIXTURES / "clean_instapay.png").read_bytes()
    original = bytes(source)
    result = PillowImagePreprocessor().preprocess(source)
    assert source == original
    assert result.content_type == "image/png"
    assert result.width <= 2400 and result.height <= 2400
    assert result.transformations == (
        "exif_orientation",
        "grayscale",
        "autocontrast_1_percent",
    )
    with pytest.raises(ProcessingError) as captured:
        PillowImagePreprocessor().preprocess(
            (FIXTURES / "corrupt_image.bin").read_bytes()
        )
    assert captured.value.failure == ProcessingFailure.IMAGE_DECODE_FAILED
    with pytest.raises(ProcessingError) as captured:
        PillowImagePreprocessor().preprocess(b"x" * (8 * 1024 * 1024 + 1))
    assert captured.value.failure == ProcessingFailure.RESOURCE_LIMIT


@pytest.mark.asyncio
async def test_secondary_ocr_is_persisted_separately_and_used_by_existing_parser(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    _, bundle = await provision(client, auth_headers, "secondary-ocr")
    await upload_fixture(client, bundle, "clean_instapay.png")
    primary = document(
        [
            "InstaPay",
            "Transfer",
            "-—- Pr",
            "Reference Number: ABC-123-456",
        ]
    )
    secondary = OcrLine(
        "790 EGP",
        0.85,
        (100, 100, 240, 40),
        (
            OcrToken("790", 0.94, (100, 100, 100, 40)),
            OcrToken("EGP", 0.76, (220, 100, 120, 40)),
        ),
        origin=OcrLineOrigin.SECONDARY,
        ocr_mode="line_crop_psm7",
        source_region=(80, 90, 280, 60),
        source_line_index=2,
        parser_eligible=True,
    )
    ocr_document = replace(primary, secondary_lines=(secondary,))

    async with app.state.database.session() as session:
        service = ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(ocr_document),
            InstaPayReceiptParser(),
        )
        assert len(await service.process_pending(limit=10)) == 1

    receipts = await client.get("/api/v1/receipts", headers=auth_headers)
    candidate = receipts.json()["items"][0]
    assert candidate["amount"] is None
    assert candidate["currency"] is None
    machine_fields = {
        item["field"]: item["value"] for item in candidate["machine_result"]["fields"]
    }
    assert machine_fields["amount"] == "790.00"
    assert machine_fields["currency"] == "EGP"
    detail = await client.get(
        f"/api/v1/receipts/{candidate['id']}", headers=auth_headers
    )
    ocr = detail.json()["ocr"]
    assert "790 EGP" not in ocr["normalized_text"]
    stored_secondary = next(
        block for block in ocr["blocks"] if block["origin"] == "SECONDARY"
    )
    assert stored_secondary["text"] == "790 EGP"
    assert stored_secondary["ocr_mode"] == "line_crop_psm7"
    assert stored_secondary["source_region"] == [80, 90, 280, 60]
    assert stored_secondary["source_line_index"] == 2
    assert stored_secondary["parser_eligible"] is True


@pytest.mark.ocr_runtime
@pytest.mark.asyncio
async def test_real_tesseract_smoke_on_synthetic_fixture() -> None:
    executable = os.environ.get("RELAY_TEST_TESSERACT")
    tessdata = os.environ.get("RELAY_TEST_TESSDATA")
    if not executable or not tessdata:
        pytest.skip("Explicit local Tesseract paths are required")
    provider = TesseractOcrProvider(Path(executable), Path(tessdata))
    assert {"eng", "ara"} <= set(provider.available_languages)
    image = PillowImagePreprocessor().preprocess(
        (FIXTURES / "clean_instapay.png").read_bytes()
    )
    result = await provider.extract(image, ("eng",), 20)
    folded = result.normalized_text.casefold()
    assert "instapay" in folded
    assert "1,250.50" in result.normalized_text
    assert "abc-123-456" in folded
    assert result.provider_id == "tesseract"
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_tesseract_adapter_enforces_language_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = object.__new__(TesseractOcrProvider)
    provider.executable = tmp_path / "fixed-tesseract"
    provider.tessdata_dir = tmp_path
    provider._available_languages = ("eng",)
    provider._provider_version = "test"
    image = PreprocessedImage(b"bounded", "image/png", 1, 1, ())

    with pytest.raises(ProcessingError) as unavailable:
        await provider.extract(image, ("ara",), 1)
    assert unavailable.value.failure == ProcessingFailure.OCR_LANGUAGE_UNAVAILABLE

    captured: dict[str, Any] = {}

    class TimedProcess:
        def poll(self):
            return None

        def wait(self, timeout):
            return 1

        def kill(self):
            captured["killed"] = True

    def popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return TimedProcess()

    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(ProcessingError) as timeout:
        await provider.extract(image, ("eng",), 1)
    assert timeout.value.failure == ProcessingFailure.OCR_TIMEOUT
    assert timeout.value.transient is True
    assert captured["killed"] is True
    assert captured["kwargs"]["shell"] is False
    assert captured["args"][0] == str(provider.executable)
    assert captured["args"][-2:] == ["-c", "tessedit_create_tsv=1"]


async def upload_fixture(
    client: AsyncClient,
    bundle: dict[str, Any],
    filename: str,
) -> UUID:
    data = (FIXTURES / filename).read_bytes()
    started = await initialize(
        client,
        bundle,
        request(bundle, data, width=1100, height=720, display_name=filename),
    )
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    uploaded = await client.put(
        f"/api/v1/device/objects/uploads/{session_id}/chunks?offset=0",
        headers={
            **device_headers(bundle),
            "Content-Type": "application/octet-stream",
        },
        content=data,
    )
    assert uploaded.status_code == 200, uploaded.text
    finalized = await client.post(
        f"/api/v1/device/objects/uploads/{session_id}/finalize",
        headers=device_headers(bundle),
    )
    assert finalized.status_code == 200, finalized.text
    return UUID(finalized.json()["object"]["id"])


@pytest.mark.asyncio
async def test_async_job_admin_review_audit_and_authorization(
    client: AsyncClient, auth_headers: dict[str, str], app: Any, caplog
) -> None:
    _, bundle = await provision(client, auth_headers, "processing-review")
    object_id = await upload_fixture(client, bundle, "clean_instapay.png")

    jobs = await client.get("/api/v1/processing/jobs", headers=auth_headers)
    assert jobs.status_code == 200
    assert jobs.headers["Cache-Control"] == "no-store"
    assert jobs.json()["items"][0]["status"] == "PENDING"
    assert (await client.get("/api/v1/processing/jobs")).status_code == 401

    enqueued = await client.post(
        f"/api/v1/objects/{object_id}/process", headers=auth_headers, json={}
    )
    assert enqueued.status_code == 200
    assert enqueued.json()["id"] == jobs.json()["items"][0]["id"]

    async with app.state.database.session() as session:
        caplog.set_level("INFO")
        service = ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
        )
        assert len(await service.process_pending(limit=10)) == 1
        assert "InstaPay" not in caplog.text
        assert "ABC-123-456" not in caplog.text

    receipts = await client.get("/api/v1/receipts", headers=auth_headers)
    candidate = receipts.json()["items"][0]
    assert candidate["review_status"] == "NEEDS_REVIEW"
    detail = await client.get(
        f"/api/v1/receipts/{candidate['id']}", headers=auth_headers
    )
    before_machine = detail.json()["machine_result"]
    assert "InstaPay" in detail.json()["ocr"]["normalized_text"]
    assert detail.headers["Cache-Control"] == "no-store"

    unauthorized = await client.post(
        f"/api/v1/receipts/{candidate['id']}/review",
        json={"action": "REJECT"},
    )
    assert unauthorized.status_code == 401
    old_review = await client.post(
        f"/api/v1/receipts/{candidate['id']}/review",
        headers=auth_headers,
        json={"action": "CORRECT", "corrections": {"amount": "1251.25"}},
    )
    assert old_review.status_code == 409
    corrected = await review_field(
        client, auth_headers, candidate, "amount", "CORRECT", "1251.25"
    )
    assert corrected["amount"] == "1251.25"
    reviewed = await client.get(
        f"/api/v1/receipts/{candidate['id']}", headers=auth_headers
    )
    assert reviewed.json()["machine_result"] == before_machine
    assert reviewed.json()["reviewed_result"]["amount"] == "1251.25"
    assert reviewed.json()["audit"][0]["action"] == "CORRECT"
    rejected = await client.post(
        f"/api/v1/receipts/{candidate['id']}/review",
        headers=auth_headers,
        json={"action": "REJECT"},
    )
    assert rejected.json()["review_status"] == "REJECTED"
    after_reject = await client.get(
        f"/api/v1/receipts/{candidate['id']}", headers=auth_headers
    )
    assert after_reject.json()["machine_result"] == before_machine
    assert [item["action"] for item in after_reject.json()["audit"]] == [
        "CORRECT",
        "REJECT",
    ]


@pytest.mark.asyncio
async def test_transient_ocr_failure_has_bounded_retry_history(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    _, bundle = await provision(client, auth_headers, "processing-retry")
    await upload_fixture(client, bundle, "clean_instapay.png")

    async with app.state.database.session() as session:
        service = ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            TimeoutOcr(),
            InstaPayReceiptParser(),
        )
        for attempt in range(3):
            assert await service.process_one() is not None
            if attempt < 2:
                await session.execute(
                    update(ProcessingJobRow).values(
                        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)
                    )
                )
                await session.commit()

    response = await client.get("/api/v1/processing/jobs", headers=auth_headers)
    job = response.json()["items"][0]
    assert job["status"] == "FAILED"
    assert job["attempt_count"] == 3
    assert job["failure_code"] == "OCR_TIMEOUT"
    detail = await client.get(
        f"/api/v1/processing/jobs/{job['id']}", headers=auth_headers
    )
    assert [attempt["failure_code"] for attempt in detail.json()["attempts"]] == [
        "OCR_TIMEOUT",
        "OCR_TIMEOUT",
        "OCR_TIMEOUT",
    ]


@pytest.mark.asyncio
async def test_reclaimed_job_discards_stale_worker_result(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    _, bundle = await provision(client, auth_headers, "processing-stale-lease")
    await upload_fixture(client, bundle, "clean_instapay.png")
    now = datetime.now(UTC)
    async with app.state.database.session() as session:
        uow = SqlUnitOfWork(session)
        first = await uow.processing.claim_next(now, now - timedelta(minutes=5))
        assert first is not None
        first_job, first_attempt = first
        await uow.commit()
        await session.execute(
            update(ProcessingJobRow)
            .where(ProcessingJobRow.id == first_job.id)
            .values(updated_at=now - timedelta(minutes=10))
        )
        await session.commit()

        second = await uow.processing.claim_next(now, now - timedelta(minutes=5))
        assert second is not None
        second_job, second_attempt = second
        await uow.commit()
        assert second_job.attempt_count == 2

        assert not await uow.processing.save_failure(
            first_job,
            first_attempt,
            ProcessingFailure.INTERNAL_ERROR,
            transient=False,
            now=now,
        )
        await uow.rollback()
        current = await uow.processing.job(first_job.id)
        assert current is not None
        assert current.status.value == "PROCESSING"
        assert current.attempt_count == 2

        assert await uow.processing.save_failure(
            second_job,
            second_attempt,
            ProcessingFailure.OCR_TIMEOUT,
            transient=False,
            now=now,
        )
        await uow.commit()
        current = await uow.processing.job(first_job.id)
        assert current is not None
        assert current.status.value == "FAILED"
        assert current.failure_code == ProcessingFailure.OCR_TIMEOUT


@pytest.mark.asyncio
async def test_different_images_same_reference_preserve_duplicate_provenance(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    first_device, bundle = await provision(client, auth_headers, "processing-dupe")
    first = await upload_fixture(client, bundle, "duplicate_reference_a.png")
    second = await upload_fixture(client, bundle, "duplicate_reference_b.png")
    assert first != second
    async with app.state.database.session() as session:
        service = ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
        )
        assert len(await service.process_pending(limit=10)) == 2

    response = await client.get(
        f"/api/v1/receipts?device_id={first_device['id']}", headers=auth_headers
    )
    items = response.json()["items"]
    assert len(items) == 2
    assert all(item["duplicate_of_candidate_id"] is None for item in items)
    canonical = await review_field(
        client, auth_headers, items[0], "reference_number", "CONFIRM", "ABC123456"
    )
    duplicate = await review_field(
        client, auth_headers, items[1], "reference_number", "CONFIRM", "ABC123456"
    )
    assert duplicate["duplicate_of_candidate_id"] == canonical["id"]
    assert duplicate["duplicate_reason"] == "REFERENCE_NUMBER"
    assert duplicate["object_id"] != canonical["object_id"]
    assert duplicate["review_status"] == "NEEDS_REVIEW"

    async with app.state.database.session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessingJobRow))
            == 2
        )

    second_device, second_bundle = await provision(
        client, auth_headers, "processing-dupe-isolated"
    )
    await upload_fixture(client, second_bundle, "duplicate_reference_a.png")
    async with app.state.database.session() as session:
        service = ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
        )
        assert len(await service.process_pending(limit=10)) == 1
    isolated = await client.get(
        f"/api/v1/receipts?device_id={second_device['id']}", headers=auth_headers
    )
    assert len(isolated.json()["items"]) == 1
    assert isolated.json()["items"][0]["duplicate_of_candidate_id"] is None
