"""Consented capture of operator-reviewed receipts into the training dataset."""

import hashlib
import json
from collections import Counter
from uuid import uuid4

from relay.domain.training import (
    MODEL_FIELDS,
    Split,
    labels_from_resolution,
    split_for_group,
)
from tests.test_evidence_backend import run_receipt
from tests.test_sprint8_review import decide


def _metadata(fields, state="REVIEW_FINALIZED", **extra):
    return {"schema_version": 2, "state": state, "fields": fields, **extra}


def _reviewed(value):
    return {"value": value, "trust": "OPERATOR_CONFIRMED", "state": "RESOLVED"}


CRITICAL = {
    "reference_number": _reviewed("987654321234"),
    "amount": _reviewed("237.50"),
    "currency": _reviewed("EGP"),
    "date": _reviewed("2026-09-05"),
    "time": _reviewed("14:35"),
}


def test_labels_use_only_operator_decisions():
    labels = labels_from_resolution(
        _metadata(
            {
                **CRITICAL,
                "amount": {"reason": "operator_unavailable", "value": None},
                "sender": {"value": "machine guess", "trust": "MACHINE_ONLY"},
            },
            state="NEEDS_REVIEW",
        )
    )
    assert labels is not None
    assert tuple(labels) == MODEL_FIELDS
    assert labels["reference"] == {
        "evidence_state": "PRESENT_VERIFIED",
        "target_value": "987654321234",
        "value_loss_mask": 1,
        "presence_loss_mask": 1,
    }
    # "Unavailable" is ambiguous (cropped vs illegible): excluded from all loss.
    assert labels["amount"]["evidence_state"] == "UNREADABLE"
    assert labels["amount"]["value_loss_mask"] == 0
    assert labels["amount"]["presence_loss_mask"] == 0
    # A machine suggestion never becomes a target.
    assert labels["sender"]["evidence_state"] == "UNREVIEWED"
    assert labels["sender"]["target_value"] is None


def test_incomplete_or_unsupported_receipts_are_not_eligible():
    assert labels_from_resolution(None) is None
    assert labels_from_resolution(_metadata({**CRITICAL, "time": {}})) is None
    assert labels_from_resolution(_metadata(CRITICAL, state="TRANSFER_FAILED")) is None
    assert (
        labels_from_resolution(_metadata(CRITICAL, classification_resolved=False))
        is None
    )
    unavailable = {"reason": "operator_unavailable"}
    assert (
        labels_from_resolution(
            _metadata({name: unavailable for name in CRITICAL}, state="NEEDS_REVIEW")
        )
        is None
    )
    assert labels_from_resolution(_metadata(CRITICAL)) is not None


def test_holdout_split_is_deterministic_and_group_level():
    groups = [str(uuid4()) for _ in range(2000)]
    splits = [split_for_group(group) for group in groups]
    assert splits == [split_for_group(group) for group in groups]
    share = Counter(splits)[Split.HOLDOUT] / len(groups)
    assert 0.16 < share < 0.24


async def _review_all(client, auth_headers, candidate, *, unavailable=()):
    reviewed = candidate
    for field, value in (
        ("reference_number", "987654321234"),
        ("amount", "237.50"),
        ("currency", "EGP"),
        ("date", "2026-09-05"),
        ("time", "14:35"),
    ):
        if field in unavailable:
            reviewed = await decide(
                client,
                auth_headers,
                reviewed,
                field,
                "UNAVAILABLE",
                reason="Cropped in the source image",
            )
        else:
            reviewed = await decide(
                client, auth_headers, reviewed, field, "CONFIRM", value
            )
    return reviewed


async def test_capture_requires_consent_and_review_then_exports(
    client, auth_headers, app, tmp_path
):
    candidate, _ = await run_receipt(client, auth_headers, app, "training-capture")
    device_id = candidate["device_id"]

    consent = await client.get(
        f"/api/v1/devices/{device_id}/training-consent", headers=auth_headers
    )
    assert consent.status_code == 200 and consent.json()["enabled"] is False
    capture = await client.post("/api/v1/training/capture", headers=auth_headers)
    assert capture.json() == {"captured": 0}  # no consent yet

    enabled = await client.put(
        f"/api/v1/devices/{device_id}/training-consent",
        headers=auth_headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True
    capture = await client.post("/api/v1/training/capture", headers=auth_headers)
    assert capture.json() == {"captured": 0}  # not reviewed yet

    await _review_all(client, auth_headers, candidate, unavailable=("amount",))
    capture = await client.post("/api/v1/training/capture", headers=auth_headers)
    assert capture.json() == {"captured": 1}
    again = await client.post("/api/v1/training/capture", headers=auth_headers)
    assert again.json() == {"captured": 0}  # idempotent

    response = await client.get("/api/v1/training/summary", headers=auth_headers)
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["train_examples"] + summary["holdout_examples"] == 1
    assert summary["transaction_groups"] == 1
    assert summary["consenting_devices"] == 1

    async with app.state.database.session() as session:
        from relay.application.training import TrainingDatasetService
        from relay.infrastructure.repositories import SqlUnitOfWork

        service = TrainingDatasetService(
            SqlUnitOfWork(session), app.state.object_storage, app.state.dataset_store
        )
        train_only = await service.export(tmp_path / "train", include_holdout=False)
        everything = await service.export(tmp_path / "all", include_holdout=True)

    assert sum(everything.values()) == 1
    split = next(name for name, count in everything.items() if count)
    assert train_only[Split.HOLDOUT.value] == 0
    assert not (tmp_path / "train" / "holdout-manifest.jsonl").exists()
    manifest = (
        tmp_path
        / "all"
        / ("train-manifest.jsonl" if split == "TRAIN" else "holdout-manifest.jsonl")
    )
    (record,) = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert record["source_kind"] == "relay_reviewed_real"
    assert record["transaction_group_id"] == candidate["id"]
    assert list(record["fields"]) == list(MODEL_FIELDS)
    assert record["fields"]["reference"]["target_value"] == "987654321234"
    assert record["fields"]["amount"]["evidence_state"] == "UNREADABLE"
    image = (tmp_path / "all" / record["image_path"]).read_bytes()
    assert hashlib.sha256(image).hexdigest() == record["image_sha256"]


async def test_withdrawn_consent_stops_capture_and_erasure_removes_copies(
    client, auth_headers, app
):
    candidate, _ = await run_receipt(client, auth_headers, app, "training-erase")
    device_id = candidate["device_id"]
    uri = f"/api/v1/devices/{device_id}/training-consent"
    await client.put(uri, headers=auth_headers, json={"enabled": True})
    await _review_all(client, auth_headers, candidate)
    await client.put(uri, headers=auth_headers, json={"enabled": False})
    capture = await client.post("/api/v1/training/capture", headers=auth_headers)
    assert capture.json() == {"captured": 0}

    await client.put(uri, headers=auth_headers, json={"enabled": True})
    capture = await client.post("/api/v1/training/capture", headers=auth_headers)
    assert capture.json() == {"captured": 1}
    images = list((app.state.dataset_store.root / "images").iterdir())
    assert len(images) == 1

    erased = await client.delete(
        f"/api/v1/devices/{device_id}/training-examples", headers=auth_headers
    )
    assert erased.json() == {"deleted": 1}
    assert list((app.state.dataset_store.root / "images").iterdir()) == []
    summary = (
        await client.get("/api/v1/training/summary", headers=auth_headers)
    ).json()
    assert summary["train_examples"] + summary["holdout_examples"] == 0


async def test_training_routes_require_admin_and_known_device(
    client, auth_headers, assert_error
):
    missing = uuid4()
    assert_error(await client.get(f"/api/v1/devices/{missing}/training-consent"), 401)
    assert_error(
        await client.get(
            f"/api/v1/devices/{missing}/training-consent", headers=auth_headers
        ),
        404,
    )
    assert_error(
        await client.put(
            f"/api/v1/devices/{missing}/training-consent",
            headers=auth_headers,
            json={"enabled": True, "unexpected": 1},
        ),
        422,
    )
