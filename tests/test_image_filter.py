from uuid import UUID

from relay.infrastructure import cli, image_filter
from relay.infrastructure.cli import hide_non_receipts, rehide_failed, rescue_hidden
from relay.infrastructure.processing_models import ReceiptCandidateRow
from tests.test_evidence_backend import run_receipt
from tests.test_retry_before_hide import _hidden_receipt, _row


def test_verdict_thresholds() -> None:
    assert image_filter.verdict(0.6) == "receipt"
    assert image_filter.verdict(0.59) == "unsure"
    assert image_filter.verdict(0.16) == "unsure"
    assert image_filter.verdict(0.15) == "not_receipt"
    assert image_filter.clip_verdict({}) == "unsure"


async def _set(app, candidate, **values) -> None:
    async with app.state.database.session() as session:
        row = await session.get(ReceiptCandidateRow, UUID(candidate["id"]))
        for name, value in values.items():
            if name == "verdict":
                row.machine_result = dict(row.machine_result or {}) | {
                    image_filter.KEY: {"verdict": value, "receipt": 0.0}
                }
            else:
                setattr(row, name, value)
        await session.commit()


async def test_clip_pass_scores_each_photo_once(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "clip-once")
    database, storage = app.state.database, app.state.object_storage
    calls = []

    def judge(picture):
        calls.append(picture.size)
        return {"label": "receipt", "receipt": 0.9, "scores": {}}

    assert await image_filter.clip_pass(database, storage, 10, judge) == 1
    assert await image_filter.clip_pass(database, storage, 10, judge) == 0
    assert len(calls) == 1
    row = await _row(app, candidate)
    assert row.machine_result[image_filter.KEY]["verdict"] == "receipt"


async def test_a_bad_image_is_unsure(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "clip-bad")

    def broken(picture):
        raise ValueError("cannot read")

    database, storage = app.state.database, app.state.object_storage
    assert await image_filter.clip_pass(database, storage, 10, broken) == 1
    stored = (await _row(app, candidate)).machine_result[image_filter.KEY]
    assert stored["verdict"] == "unsure" and stored["error"] == "ValueError"


async def test_no_model_changes_nothing(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "clip-none")

    def missing(picture):
        raise image_filter.ModelMissing("none")

    database, storage = app.state.database, app.state.object_storage
    assert await image_filter.clip_pass(database, storage, 10, missing) == 0
    assert image_filter.KEY not in (await _row(app, candidate)).machine_result


async def test_not_receipt_is_hidden_even_with_numbers(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "clip-hide")
    await _set(app, candidate, verdict="not_receipt")
    await hide_non_receipts(app.state.database)
    assert (await _row(app, candidate)).hidden_reason == "not_a_receipt"


async def test_a_receipt_verdict_is_never_hidden(
    client, auth_headers, app, monkeypatch
):
    candidate, _ = await run_receipt(client, auth_headers, app, "clip-keep")
    await _set(app, candidate, verdict="receipt")
    # The text rules would hide it (it reads like a shop bill).
    monkeypatch.setattr(cli, "_shop_invoice", lambda row: True)
    monkeypatch.setattr(cli, "_clear_non_receipt", lambda row: True)
    await hide_non_receipts(app.state.database)
    assert (await _row(app, candidate)).hidden_reason is None


async def test_a_person_s_photo_is_never_hidden_by_clip(client, auth_headers, app):
    candidate, _ = await run_receipt(client, auth_headers, app, "clip-person")
    await _set(app, candidate, verdict="not_receipt", reviewed_result={"note": "x"})
    await hide_non_receipts(app.state.database)
    assert (await _row(app, candidate)).hidden_reason is None


async def test_rescue_skips_not_receipt(client, auth_headers, app):
    candidate = await _hidden_receipt(
        client, auth_headers, app, "clip-rescue", "3750.00"
    )
    await _set(app, candidate, verdict="not_receipt")
    assert await rescue_hidden(app.state.database, app.state.settings) == 0
    assert (await _row(app, candidate)).hidden_reason == "not_a_receipt"


async def test_only_not_receipt_failures_are_hidden_again(client, auth_headers, app):
    junk = await _hidden_receipt(client, auth_headers, app, "clip-junk", "960.00")
    real = await _hidden_receipt(client, auth_headers, app, "clip-real", "961.00")
    assert await rescue_hidden(app.state.database, app.state.settings) == 2
    for candidate, seen in ((junk, "not_receipt"), (real, "receipt")):
        async with app.state.database.session() as session:
            row = await session.get(ReceiptCandidateRow, UUID(candidate["id"]))
            resolution = dict(row.resolution_result)
            resolution["retry_read"] = dict(resolution["retry_read"]) | {
                "state": "failed"
            }
            row.resolution_result = resolution
            await session.commit()
        await _set(app, candidate, verdict=seen)
    assert await rehide_failed(app.state.database) == 1
    assert (await _row(app, junk)).hidden_reason == "not_a_receipt"
    assert (await _row(app, real)).hidden_reason is None
