"""Fixes from the 2026-11-28 rules review."""

from datetime import timedelta
from uuid import UUID

import pytest

from relay.infrastructure import cli, image_filter
from relay.infrastructure.cli import (
    auto_confirm,
    hide_non_receipts,
    model_suggest,
    run_steps,
    unhide_receipt_like,
)
from relay.infrastructure.processing_models import ReceiptCandidateRow
from tests.test_auto_confirm import (
    FakeReader,
    _link_sms,
    _now,
    _read_and_confirm,
    _text,
)
from tests.test_evidence_backend import run_receipt
from tests.test_sms_verification import _send_sms

SMS = "notice: You have received EGP {} via InstaPay"


async def _edit(app, candidate, change) -> None:
    async with app.state.database.session() as session:
        row = await session.get(ReceiptCandidateRow, UUID(candidate["id"]))
        change(row)
        await session.commit()


async def _row(app, candidate):
    async with app.state.database.session() as session:
        row = await session.get(ReceiptCandidateRow, UUID(candidate["id"]))
        return (
            row.review_status,
            row.hidden_reason,
            dict(row.resolution_result or {}),
        )


def _no_reference(row) -> None:
    resolution = dict(row.resolution_result)
    fields = dict(resolution["fields"])
    fields["reference_number"] = {
        **fields["reference_number"],
        "value": None,
        "candidates": [],
    }
    row.resolution_result = resolution | {"fields": fields}


async def test_two_copies_of_one_receipt_are_never_both_confirmed(
    client, auth_headers, app
):
    from relay.infrastructure.event_models import RawEventRow

    # First a third receipt with the same amount and no reference, then the
    # same receipt (one reference) sent twice. Two bank SMS of that amount.
    other, _ = await run_receipt(client, auth_headers, app, "copies-c")
    first, _ = await run_receipt(client, auth_headers, app, "copies-a")
    second, _ = await run_receipt(client, auth_headers, app, "copies-b")
    bundle = await _link_sms(client, auth_headers, [other, first, second], "copies")
    async with app.state.database.session() as session:
        for minutes, candidate in ((30, other), (20, first), (10, second)):
            row = await session.get(ReceiptCandidateRow, UUID(candidate["id"]))
            event = await session.get(RawEventRow, row.raw_event_id)
            event.occurred_at = _now() - timedelta(minutes=minutes)
        await session.commit()
    for minutes in (1, 2):
        await _send_sms(
            client, bundle, SMS.format("1130.00"), _now() - timedelta(minutes=minutes)
        )
    await model_suggest(
        app.state.database,
        app.state.object_storage,
        FakeReader(
            {"amount": "1130.00", "currency": "EGP", "reference_number": "173791304909"}
        ),
        10,
    )
    await _text(app)
    await _edit(app, other, _no_reference)
    counts = await auto_confirm(
        app.state.database, app.state.object_storage, app.state.settings
    )
    statuses = [(await _row(app, item))[0] for item in (first, second)]
    # Before: each copy compared itself with the third receipt only, and both
    # were confirmed by the two SMS.
    assert statuses.count("AUTO_ACCEPTED") == 1 and counts["sms_day"] <= 2
    assert "REJECTED" in statuses


async def test_one_failing_step_does_not_stop_the_rest() -> None:
    ran = []

    async def broken() -> None:
        raise RuntimeError("down")

    async def confirm() -> None:
        ran.append("auto_confirm")

    failed = await run_steps([("clip_pass", broken), ("auto_confirm", confirm)])
    assert failed == ["clip_pass"] and ran == ["auto_confirm"]


async def test_an_unreadable_receipt_clip_sees_stays_in_review(
    client, auth_headers, app, monkeypatch
):
    candidate, _ = await run_receipt(client, auth_headers, app, "cracked")

    def mark(row) -> None:
        row.machine_result = dict(row.machine_result or {}) | {
            image_filter.KEY: {"verdict": "receipt", "receipt": 0.9}
        }
        row.resolution_result = dict(row.resolution_result or {}) | {
            "retry_read": {"state": "failed", "hidden_as": "not_a_receipt"}
        }

    await _edit(app, candidate, mark)
    monkeypatch.setattr(cli, "_clear_non_receipt", lambda row: True)
    await hide_non_receipts(app.state.database)
    assert (await _row(app, candidate))[1] is None
    # A shop bill is still hidden: CLIP only keeps unread receipts.
    monkeypatch.setattr(cli, "_shop_invoice", lambda row: True)
    await hide_non_receipts(app.state.database)
    assert (await _row(app, candidate))[1] == "shop_invoice"


async def test_a_failed_transfer_is_not_brought_back(
    client, auth_headers, app, monkeypatch
):
    candidate, _ = await run_receipt(client, auth_headers, app, "failed-one")

    def rejected(row) -> None:
        row.review_status = "REJECTED"
        row.resolution_result = dict(row.resolution_result or {}) | {
            "rejected_reason": "not_completed"
        }
        row.machine_result = dict(row.machine_result or {}) | {
            "ppocr_v6": {"text": "x"}
        }

    await _edit(app, candidate, rejected)
    monkeypatch.setattr(cli, "_helper_receipt", lambda helper: True)
    monkeypatch.setattr(cli, "_helper_read", lambda machine: {"text": "x"})
    assert await unhide_receipt_like(app.state.database) == 0
    assert (await _row(app, candidate))[0] == "REJECTED"


async def test_why_a_receipt_waits_is_noted_and_cleared(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "why-wait")
    bundle = await _link_sms(client, auth_headers, [candidate], "why-wait-ph")
    values = {"amount": "777.00", "currency": "EGP", "reference_number": "880492013227"}
    await _read_and_confirm(app, values)
    _, _, resolution = await _row(app, candidate)
    assert resolution["waiting_for"]["reason"] == "no_sms"
    await _send_sms(client, bundle, SMS.format("777.00"), _now())
    await auto_confirm(app.state.database, app.state.object_storage, app.state.settings)
    status, _, resolution = await _row(app, candidate)
    assert status == "AUTO_ACCEPTED" and "waiting_for" not in resolution


@pytest.mark.parametrize("seen", [False, True])
async def test_no_instapay_notifications_alert(client, auth_headers, app, seen):
    from relay.infrastructure.alerts import current_alerts, describe

    candidate, _ = await run_receipt(client, auth_headers, app, f"insta-{seen}")
    bundle = await _link_sms(client, auth_headers, [candidate], f"insta-ph-{seen}")
    morning = _now().replace(hour=10, minute=0, second=0)
    await _send_sms(client, bundle, SMS.format("500.00"), morning)
    if seen:
        from sqlalchemy import select

        from relay.infrastructure.event_models import RawEventRow

        async with app.state.database.session() as session:
            for event in await session.scalars(
                select(RawEventRow).where(RawEventRow.collector == "sms")
            ):
                event.metadata_json = dict(event.metadata_json or {}) | {
                    "instapay_seen": True
                }
            await session.commit()
    async with app.state.database.session() as session:
        alerts = await current_alerts(
            session, app.state.settings, morning.replace(hour=14)
        )
    found = [alert for alert in alerts if alert["kind"] == "no_instapay"]
    if seen:
        assert found == []
    else:
        assert found and found[0]["count"] == 1
        assert "notification access" in describe(found[0], morning.tzinfo)
