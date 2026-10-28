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
