from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from openpyxl import load_workbook
from sqlalchemy import func, select, update

from relay.application.processing import ProcessingService
from relay.application.receipts import InstaPayReceiptParser
from relay.infrastructure.excel_export import OpenpyxlWorkbookExporter, spreadsheet_text
from relay.infrastructure.image_processing import PillowImagePreprocessor
from relay.infrastructure.operation_models import OperatorAuditRow
from relay.infrastructure.processing_models import ProcessingJobRow, ReceiptCandidateRow
from relay.infrastructure.repositories import SqlUnitOfWork
from tests.review_helpers import review_critical_fields
from tests.test_binary_objects import provision
from tests.test_processing import FakeOcr, upload_fixture

WINDOW = "date_from=2026-01-01&date_to=2026-12-31"


@pytest.mark.asyncio
async def test_built_operator_shell_has_security_headers(client: AsyncClient) -> None:
    redirect = await client.get("/operator", follow_redirects=False)
    assert redirect.status_code == 307
    response = await client.get("/operator/")
    assert response.status_code == 200
    assert "Relay Operator" in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


async def create_duplicate_group(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    device, bundle = await provision(client, auth_headers, "operator-product")
    await upload_fixture(client, bundle, "duplicate_reference_a.png")
    await upload_fixture(client, bundle, "duplicate_reference_b.png")
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
        f"/api/v1/transactions?{WINDOW}&sort_direction=asc", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    return device, response.json()["items"]


@pytest.mark.asyncio
async def test_operator_dashboard_transactions_duplicates_and_export(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    device, items = await create_duplicate_group(client, auth_headers, app)
    assert len(items) == 2
    assert all(item["duplicate_of_candidate_id"] is None for item in items)
    assert all(item["amount"] is None and item["currency"] is None for item in items)

    unauthorized = await client.get(f"/api/v1/dashboard?{WINDOW}")
    assert unauthorized.status_code == 401
    review_queue = await client.get(
        f"/api/v1/review-queue?{WINDOW}&sort_direction=desc", headers=auth_headers
    )
    assert review_queue.status_code == 200
    assert review_queue.json()["total"] == 2
    processing_queue = await client.get(
        "/api/v1/processing/jobs?status=NEEDS_REVIEW&limit=1", headers=auth_headers
    )
    assert processing_queue.status_code == 200
    assert processing_queue.json()["limit"] == 1
    assert len(processing_queue.json()["items"]) == 1

    dashboard = await client.get(f"/api/v1/dashboard?{WINDOW}", headers=auth_headers)
    assert dashboard.json()["final_transaction_count"] == 0
    assert dashboard.json()["totals_by_currency"] == []
    for item in items:
        candidate = (
            await client.get(f"/api/v1/receipts/{item['id']}", headers=auth_headers)
        ).json()
        await review_critical_fields(client, auth_headers, candidate)
    refreshed = await client.get(
        f"/api/v1/transactions?{WINDOW}&sort_direction=asc", headers=auth_headers
    )
    items = refreshed.json()["items"]
    canonical = next(item for item in items if item["is_canonical"])
    duplicate = next(item for item in items if item["duplicate_of_candidate_id"])
    assert duplicate["duplicate_group_id"] == canonical["id"]
    assert duplicate["image_url"].endswith(f"{duplicate['object_id']}/content")
    page = await client.get(
        f"/api/v1/transactions?{WINDOW}&search=ABC-123&limit=1",
        headers=auth_headers,
    )
    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1
    duplicate_page = await client.get(
        f"/api/v1/transactions?{WINDOW}&duplicate_status=DUPLICATE&currency=egp",
        headers=auth_headers,
    )
    assert duplicate_page.status_code == 200
    assert duplicate_page.json()["total"] == 1

    groups = await client.get(
        f"/api/v1/duplicate-groups?{WINDOW}", headers=auth_headers
    )
    assert groups.status_code == 200, groups.text
    assert groups.json()["total"] == 1
    group_id = groups.json()["items"][0]["group_id"]
    detail = await client.get(
        f"/api/v1/duplicate-groups/{group_id}?limit=1&offset=1",
        headers=auth_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["group_size"] == 2
    assert len(detail.json()["members"]) == 1

    resolution = await client.post(
        f"/api/v1/duplicate-groups/{group_id}/members/{duplicate['id']}/resolution",
        headers=auth_headers,
        json={"decision": "NOT_DUPLICATE", "note": "Different transfer evidence"},
    )
    assert resolution.status_code == 200, resolution.text
    assert resolution.json()["deterministic_link_preserved"] is True
    after = await client.get(
        f"/api/v1/transactions/{duplicate['id']}", headers=auth_headers
    )
    assert after.json()["duplicate_of_candidate_id"] == canonical["id"]
    assert after.json()["duplicate_resolution_history"][-1]["context"]["decision"] == (
        "NOT_DUPLICATE"
    )

    dashboard = await client.get(f"/api/v1/dashboard?{WINDOW}", headers=auth_headers)
    assert dashboard.status_code == 200
    # The duplicate is the same payment again, so it is counted once.
    assert dashboard.json()["final_transaction_count"] == 1
    assert dashboard.json()["duplicate_group_count"] == 1
    assert dashboard.json()["totals_by_currency"] == [
        {"currency": "EGP", "amount": "1250.50", "transaction_count": 1}
    ]
    # Receipts here reached the folder today, so today and the week match.
    assert dashboard.json()["today_totals"] == dashboard.json()["totals_by_currency"]
    assert dashboard.json()["week_totals"] == dashboard.json()["totals_by_currency"]
    assert dashboard.json()["auto_confirmed_count"] == 0
    assert dashboard.json()["manual_confirmed_count"] == 1
    assert isinstance(dashboard.json()["sms_by_status"], dict)
    phones = dashboard.json()["transactions_by_device"]
    assert sum(item["confirmed_count"] for item in phones) == 1
    assert {item["confirmed_amount"] for item in phones} <= {"1250.50", "0.00"}

    devices = await client.get("/api/v1/operations/devices", headers=auth_headers)
    assert devices.status_code == 200
    device_row = next(
        row for row in devices.json()["items"] if row["id"] == device["id"]
    )
    assert device_row["configuration"]["images_enabled"] is True
    assert device_row["configuration"]["image_source_ids"] == ["approved-folder"]
    assert device_row["transaction_count"] == 2

    exported = await client.get(
        f"/api/v1/reports/export.xlsx?{WINDOW}&duplicate_status=DUPLICATE",
        headers=auth_headers,
    )
    assert exported.status_code == 200, exported.text
    assert "attachment; filename=" in exported.headers["Content-Disposition"]
    workbook = load_workbook(BytesIO(exported.content), data_only=False)
    assert workbook.sheetnames == ["Transactions", "Duplicates", "Summary"]
    assert workbook["Transactions"].max_row == 2
    assert workbook["Duplicates"].max_row == 2
    assert workbook["Transactions"]["S1"].value == "Sent to Folder (UTC)"
    assert workbook["Transactions"]["S2"].value is not None

    # Reports count receipts by when the image reached the receipts folder.
    reported = await client.get(
        f"/api/v1/reports/summary?{WINDOW}", headers=auth_headers
    )
    current = await client.get(f"/api/v1/dashboard?{WINDOW}", headers=auth_headers)
    assert reported.json()["final_transaction_count"] > 0
    assert (
        reported.json()["final_transaction_count"]
        == current.json()["final_transaction_count"]
    )
    before = await client.get(
        "/api/v1/reports/summary?date_from=2026-01-01&date_to=2026-01-01",
        headers=auth_headers,
    )
    assert before.json()["final_transaction_count"] == 0
    backwards = await client.get(
        "/api/v1/reports/summary?date_from=2026-01-01&date_to=2026-01-01"
        "&time_from=10:00&time_to=09:00",
        headers=auth_headers,
    )
    assert backwards.status_code == 409
    assert all(
        not (isinstance(cell.value, str) and cell.value.startswith("="))
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )
    async with app.state.database.session() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OperatorAuditRow)
                .where(OperatorAuditRow.action == "EXPORT")
            )
            == 1
        )
        target = await session.get(ReceiptCandidateRow, UUID(duplicate["id"]))
        assert target is not None
        await session.delete(target)
        await session.commit()
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OperatorAuditRow)
                .where(OperatorAuditRow.action == "DUPLICATE_RESOLUTION")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_processing_retry_is_eligible_bounded_and_audited(
    client: AsyncClient, auth_headers: dict[str, str], app: Any
) -> None:
    _, bundle = await provision(client, auth_headers, "operator-retry")
    await upload_fixture(client, bundle, "clean_instapay.png")
    async with app.state.database.session() as session:
        job = await session.scalar(select(ProcessingJobRow))
        assert job is not None
        job_id = job.id
        await session.execute(
            update(ProcessingJobRow)
            .where(ProcessingJobRow.id == job_id)
            .values(
                status="FAILED",
                failure_code="OCR_TIMEOUT",
                attempt_count=3,
                max_attempts=3,
                completed_at=datetime.now(UTC),
            )
        )
        await session.commit()

    retried = await client.post(
        f"/api/v1/processing/jobs/{job_id}/retry", headers=auth_headers
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "PENDING"
    assert retried.json()["max_attempts"] == 4
    second = await client.post(
        f"/api/v1/processing/jobs/{job_id}/retry", headers=auth_headers
    )
    assert second.status_code == 409
    async with app.state.database.session() as session:
        audit = await session.scalar(
            select(OperatorAuditRow).where(
                OperatorAuditRow.action == "PROCESSING_RETRY"
            )
        )
        assert audit is not None
        assert audit.context["previous_failure"] == "OCR_TIMEOUT"


def test_excel_export_sanitizes_formula_injection_and_preserves_types() -> None:
    for prefix in "=+-@":
        assert spreadsheet_text(f"  {prefix}unsafe").startswith("'")
    row: dict[str, Any] = {
        "id": "candidate",
        "transaction_occurred_at": "2026-09-05T14:35:00",
        "amount": "1250.50",
        "currency": "EGP",
        "reference_original": "=1+1",
        "sender_name": "+cmd",
        "recipient_name": "Safe",
        "review_status": "AUTO_ACCEPTED",
        "duplicate_group_id": None,
        "source": "images",
        "device_name": "Lab",
        "receipt_type": "INSTAPAY_TRANSFER_RECEIPT",
        "received_at": "2026-09-05T12:35:00+00:00",
        "processing_status": "SUCCEEDED",
    }
    content = OpenpyxlWorkbookExporter().build(
        [row],
        {"totals_by_currency": [], "date_from": "2026-09-01", "date_to": "2026-09-07"},
        {},
        datetime(2026, 9, 7, tzinfo=UTC),
    )
    workbook = load_workbook(BytesIO(content), data_only=False)
    sheet = workbook["Transactions"]
    assert sheet["C2"].value == 1250.5
    assert sheet["E2"].value == "'=1+1"
    assert sheet["F2"].value == "'+cmd"
