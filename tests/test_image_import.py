"""Operator bulk import of receipt images into the normal processing path."""

from uuid import UUID

from relay.application.processing import ProcessingService
from relay.application.receipts import InstaPayReceiptParser
from relay.infrastructure.cli import import_images
from relay.infrastructure.image_processing import PillowImagePreprocessor
from relay.infrastructure.repositories import SqlUnitOfWork
from tests.test_binary_objects import provision
from tests.test_evidence_backend import EvidenceFixture
from tests.test_processing import FIXTURES, FakeOcr


async def test_bulk_import_queues_receipts_and_skips_duplicates(
    client, auth_headers, app
):
    app.state.settings.receipt_evidence_enabled = True
    device, _ = await provision(client, auth_headers, "bulk-import")
    data = (FIXTURES / "clean_instapay.png").read_bytes()
    async with app.state.database.session() as session:
        counts = await import_images(
            SqlUnitOfWork(session),
            app.state.settings,
            UUID(device["id"]),
            [
                ("a.png", data),
                ("copy-of-a.png", data),
                ("broken.png", b"not an image"),
                ("too-big.png", b""),
            ],
        )
    assert counts == {"imported": 1, "duplicate": 1, "rejected": 2}

    async with app.state.database.session() as session:
        processed = await ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
            evidence_extractor=EvidenceFixture(None, "fixture"),
        ).process_pending()
    assert len(processed) == 1
    receipts = (await client.get("/api/v1/receipts", headers=auth_headers)).json()
    assert [item["device_id"] for item in receipts["items"]] == [device["id"]]
    assert receipts["items"][0]["review_status"] == "NEEDS_REVIEW"

    # Re-running the same import is safe.
    async with app.state.database.session() as session:
        again = await import_images(
            SqlUnitOfWork(session),
            app.state.settings,
            UUID(device["id"]),
            [("a", data)],
        )
    assert again == {"imported": 0, "duplicate": 1, "rejected": 0}


async def test_bulk_import_requires_an_enrolled_device(client, auth_headers, app):
    created = await client.post(
        "/api/v1/devices",
        headers=auth_headers,
        json={
            "external_device_id": "never-enrolled",
            "name": "Not enrolled",
            "platform": "android",
            "app_version": "0.6.0",
        },
    )
    async with app.state.database.session() as session:
        try:
            await import_images(
                SqlUnitOfWork(session),
                app.state.settings,
                UUID(created.json()["id"]),
                [],
            )
        except ValueError as exc:
            assert "Enrol" in str(exc)
        else:
            raise AssertionError("import without an installation must fail")


async def test_reopen_rejected_sends_unrecognised_receipts_to_review(
    client, auth_headers, app
):
    from dataclasses import replace

    from relay.domain.processing import ReceiptType
    from relay.infrastructure.cli import reopen_rejected
    from tests.review_helpers import review_critical_fields
    from tests.test_processing import upload_fixture

    class NotRecognised(EvidenceFixture):
        async def extract(self, data, **kwargs):
            evidence = await super().extract(data, **kwargs)
            return replace(
                evidence,
                document=replace(evidence.document, normalized_text="", lines=()),
                candidates=(),
                receipt_type=ReceiptType.UNKNOWN_RECEIPT,
                classification_resolved=False,
                supported_layout=False,
                receipt_possible=False,
                metadata={"recognition_skipped": "visual_registration_not_admitted"},
            )

    app.state.settings.receipt_evidence_enabled = True
    _, bundle = await provision(client, auth_headers, "reopen")
    await upload_fixture(client, bundle, "clean_instapay.png")
    async with app.state.database.session() as session:
        await ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
            evidence_extractor=NotRecognised(),
        ).process_pending()
    receipts = (await client.get("/api/v1/receipts", headers=auth_headers)).json()
    assert receipts["items"][0]["review_status"] == "REJECTED"

    async with app.state.database.session() as session:
        assert await reopen_rejected(SqlUnitOfWork(session), None) == 1
    async with app.state.database.session() as session:
        assert await reopen_rejected(SqlUnitOfWork(session), None) == 0

    item = (await client.get("/api/v1/receipts", headers=auth_headers)).json()["items"][
        0
    ]
    assert item["review_status"] == "NEEDS_REVIEW"
    assert item["receipt_type"] == "INSTAPAY_TRANSFER_RECEIPT"
    detail = (
        await client.get(f"/api/v1/receipts/{item['id']}", headers=auth_headers)
    ).json()
    blocking = detail["resolution_result"]["blocking_fields"]
    assert "classification" not in blocking and "reference_number" in blocking
    final = await review_critical_fields(client, auth_headers, detail)
    assert final["review_status"] == "AUTO_ACCEPTED"


async def test_prefill_suggestions_are_confirmable_but_not_trusted(
    client, auth_headers, app
):
    import hashlib

    from relay.infrastructure.cli import prefill_suggestions
    from tests.review_helpers import review_field
    from tests.test_processing import upload_fixture

    app.state.settings.receipt_evidence_enabled = True
    _, bundle = await provision(client, auth_headers, "prefill")
    await upload_fixture(client, bundle, "clean_instapay.png")
    async with app.state.database.session() as session:
        await ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
            evidence_extractor=EvidenceFixture(None, "fixture"),
        ).process_pending()
    digest = hashlib.sha256((FIXTURES / "clean_instapay.png").read_bytes()).hexdigest()
    document = {
        "source": "test-prefill",
        "receipts": {digest: {"amount": "777", "date": "not-a-date"}},
    }
    async with app.state.database.session() as session:
        counts = await prefill_suggestions(SqlUnitOfWork(session), document)
    assert counts == {"receipts": 1, "fields": 1, "skipped": 1}
    item = (await client.get("/api/v1/receipts", headers=auth_headers)).json()["items"][
        0
    ]
    assert item["review_status"] == "NEEDS_REVIEW"
    detail = (
        await client.get(f"/api/v1/receipts/{item['id']}", headers=auth_headers)
    ).json()
    amount = detail["resolution_result"]["fields"]["amount"]
    assert amount["value"] == "777.00" and amount["trust"] == "MACHINE_ONLY"
    assert [c["normalized_value"] for c in amount["candidates"]] == ["777.00"]
    if "amount" in detail["resolution_result"]["blocking_fields"]:
        confirmed = await review_field(
            client, auth_headers, detail, "amount", "CONFIRM", "777.00"
        )
        field = confirmed["resolution_result"]["fields"]["amount"]
        assert field["trust"] == "OPERATOR_CONFIRMED"


async def test_review_queue_category_filter_and_counts(client, auth_headers, app):
    app.state.settings.receipt_evidence_enabled = True
    _, bundle = await provision(client, auth_headers, "categories")
    from tests.test_processing import upload_fixture

    await upload_fixture(client, bundle, "clean_instapay.png")
    async with app.state.database.session() as session:
        await ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
            evidence_extractor=EvidenceFixture(None, "fixture"),
        ).process_pending()
    counts = (
        await client.get("/api/v1/review-queue/categories", headers=auth_headers)
    ).json()
    assert set(counts) == {"ready", "missing", "duplicate", "in_progress", "done"}
    assert sum(counts.values()) == 1
    category = next(name for name, value in counts.items() if value)
    page = (
        await client.get(
            f"/api/v1/review-queue?category={category}", headers=auth_headers
        )
    ).json()
    assert page["total"] == 1 and page["items"][0]["review_category"] == category


async def test_confirm_suggestions_decides_every_field_as_the_operator(
    client, auth_headers, app, admin
):
    import hashlib

    from relay.infrastructure.cli import confirm_suggestions, prefill_suggestions
    from tests.test_processing import upload_fixture

    app.state.settings.receipt_evidence_enabled = True
    _, bundle = await provision(client, auth_headers, "bulk")
    await upload_fixture(client, bundle, "clean_instapay.png")
    async with app.state.database.session() as session:
        await ProcessingService(
            SqlUnitOfWork(session),
            app.state.object_storage,
            PillowImagePreprocessor(),
            FakeOcr(),
            InstaPayReceiptParser(),
            evidence_extractor=EvidenceFixture(None, "fixture"),
        ).process_pending()
    digest = hashlib.sha256((FIXTURES / "clean_instapay.png").read_bytes()).hexdigest()
    document = {"receipts": {digest: {"amount": "777", "currency": "EGP"}}}
    async with app.state.database.session() as session:
        await prefill_suggestions(SqlUnitOfWork(session), document)
    counts = await confirm_suggestions(
        app.state.database, app.state.object_storage, admin.id, document
    )
    assert counts["receipts"] == 1 and counts["failed"] == 0
    item = (await client.get("/api/v1/receipts", headers=auth_headers)).json()["items"][
        0
    ]
    detail = (
        await client.get(f"/api/v1/receipts/{item['id']}", headers=auth_headers)
    ).json()
    fields = detail["resolution_result"]["fields"]
    if "amount" in fields:
        assert fields["amount"]["value"] == "777.00"
    remaining = [
        name
        for name in detail["resolution_result"]["blocking_fields"]
        if fields[name].get("reason") != "operator_unavailable"
    ]
    assert remaining == []
