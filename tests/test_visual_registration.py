"""Registration checks use static, non-sensitive UI, not transaction truth."""

import io
from dataclasses import replace

import pytest
from PIL import Image

from relay.infrastructure.visual_registration import (
    geometry_rejection,
    load_profiles,
    register_receipt,
    unique_registration,
)


def static_image(variant="compact_en"):
    from pathlib import Path

    from relay.infrastructure import visual_registration

    path = (
        Path(visual_registration.__file__).with_name("receipt_profiles")
        / f"{variant}.png"
    )
    return Image.open(path).convert("L")


def encoded(image):
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def profile(variant="compact_en"):
    return next(p for p in load_profiles() if p.variant == variant)


def test_known_layout_uses_visual_features_without_transaction_fields():
    result = register_receipt(encoded(static_image()), (profile(),))
    assert result.reason == "admitted"
    assert result.profile.profile == "instapay_compact_bilingual_v1"
    assert result.as_json()["ocr_used_for_admission"] is False
    assert result.working.raster.size == profile().size


def test_wrong_layout_and_insufficient_matches_rejected():
    assert (
        register_receipt(encoded(static_image("expanded_ar")), (profile(),)).profile
        is None
    )
    result = register_receipt(encoded(Image.new("L", (720, 1440), 255)), (profile(),))
    assert result.reason == "insufficient_visual_matches"


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_right_angle_rotation_registers_in_canonical_coordinates(angle):
    result = register_receipt(
        encoded(static_image().rotate(angle, expand=True)), (profile(),)
    )
    assert result.profile is not None
    assert result.working.raster.size == profile().size
    assert len(result.working.polygon(dict(profile().rois)["amount"])) == 4


def test_perspective_photograph_alignment():
    import cv2
    import numpy as np

    original = np.asarray(static_image())
    src = np.float32([[0, 0], [719, 0], [719, 1439], [0, 1439]])
    dst = np.float32([[105, 80], [850, 125], [790, 1550], [80, 1520]])
    matrix = cv2.getPerspectiveTransform(src, dst)
    photo = cv2.warpPerspective(original, matrix, (940, 1650), borderValue=30)
    result = register_receipt(encoded(Image.fromarray(photo)), (profile(),))
    assert result.profile is not None
    for x, y in result.working.polygon(dict(profile().rois)["reference_number"]):
        assert 0 <= x <= 940 and 0 <= y <= 1650


def test_cropped_document_is_not_extrapolated_into_missing_field_pixels():
    result = register_receipt(
        encoded(static_image().crop((0, 450, 720, 1440))), (profile(),)
    )
    assert result.profile is None


@pytest.mark.parametrize(
    "matrix",
    [
        [[0, 0, 0], [0, 0, 0], [0, 0, 1]],
        [[-1, 0, 720], [0, 1, 0], [0, 0, 1]],
        [[1, 0, 0], [0, 1, 0], [0, -0.001, 1]],
    ],
)
def test_bad_homography_geometry_rejected(matrix):
    import numpy as np

    assert geometry_rejection(np.asarray(matrix, dtype=float), (720, 1440), (720, 1440))


def test_ambiguous_layout_is_review_not_best_score():
    first = profile()
    other = replace(first, profile="instapay_expanded_bilingual_v1", variant="other")
    result = register_receipt(encoded(static_image()), (first, other))
    assert result.reason == "ambiguous_layout"
    assert result.profile is None
    assert unique_registration([(first, None), (other, None)]) is None


def test_canonical_rois_are_versioned_and_amount_never_uses_fee_region():
    for item in load_profiles():
        rois = dict(item.rois)
        assert rois["amount"] == rois["currency"]
        assert rois["date"] == rois["time"]
        assert rois["amount"][1] + rois["amount"][3] < rois["handle"][1]
        assert rois["reference_number"][1] > rois["recipient_identifier"][1]
        for x, y, w, h in rois.values():
            assert 0 <= x < x + w <= item.size[0] and 0 <= y < y + h <= item.size[1]


class LocalRecognitionFixture:
    provider_id = "tesseract"
    provider_version = "fixture-unchanged-modes"
    available_languages = ("eng", "ara")

    def __init__(self, conflict=False):
        self.calls = []
        self.conflict = conflict

    def _run_tesseract(self, path, output, languages, psm, deadline):
        from relay.domain.processing import OcrLine, OcrToken

        # Real crop dimensions are checked at the recognizer boundary. No
        # transaction fixture, fee, phone or reference supplies the crop location.
        with Image.open(path) as crop:
            assert crop.height < 160
        self.calls.append(psm)
        values = [
            "128895843616",
            "sample@instapay",
            "790 EGP",
            "29 Aug 2026 04:30 PM",
            "01003405232",
            "Example recipient",
            "",
        ]
        value = values[min((len(self.calls) - 1) // 2, 6)]
        if self.conflict and len(self.calls) == 2:
            value = "408892877777"
        return (
            (
                OcrLine(
                    value,
                    0.999,
                    (2, 2, 100, 20),
                    tokens=tuple(
                        OcrToken(word, 0.999, (2, 2, 40, 20)) for word in value.split()
                    ),
                ),
            )
            if value
            else ()
        )


async def test_p04_p05_amount_roi_and_reference_not_phone_through_adapter():
    from relay.application.evidence_product import resolve_evidence
    from relay.infrastructure.instapay_adapter import InstaPayReceiptAdapter

    provider = LocalRecognitionFixture()
    evidence = await InstaPayReceiptAdapter(provider).extract(
        encoded(static_image()),
        source_event_id="random-event",
        timestamp="provenance-time",
        requested_languages=("eng", "ara"),
        timeout_seconds=20,
    )
    parsed = resolve_evidence(evidence)
    assert str(parsed.amount) == "790" and parsed.currency == "EGP"
    assert parsed.reference_normalized == "128895843616"
    assert parsed.reference_normalized != parsed.recipient_identifier
    assert parsed.evidence_result["state"] == "AUTO_FINALIZED"
    assert parsed.evidence_result["payment_verification"] == "NOT_PERFORMED"
    assert provider.calls == [7, 6] * 6 + [6]


async def test_s04_wrong_reference_conflict_survives_canonical_crop_modes():
    from relay.application.evidence_product import resolve_evidence
    from relay.infrastructure.instapay_adapter import InstaPayReceiptAdapter

    provider = LocalRecognitionFixture(conflict=True)
    evidence = await InstaPayReceiptAdapter(provider).extract(
        encoded(static_image()),
        source_event_id="event",
        timestamp="provenance",
        requested_languages=("eng",),
        timeout_seconds=20,
    )
    parsed = resolve_evidence(evidence)
    assert parsed.reference_normalized is None
    assert parsed.evidence_result["state"] == "NEEDS_REVIEW"
    assert parsed.evidence_result["blocking_fields"] == ["reference_number"]
    assert len(parsed.evidence_result["fields"]["reference_number"]["candidates"]) == 2


async def test_unknown_negative_never_calls_recognizer_or_forges_transcript():
    from relay.application.evidence_product import resolve_evidence
    from relay.infrastructure.instapay_adapter import InstaPayReceiptAdapter

    provider = LocalRecognitionFixture()
    evidence = await InstaPayReceiptAdapter(provider).extract(
        encoded(Image.new("L", (720, 1440), 255)),
        source_event_id="event",
        timestamp="provenance",
        requested_languages=("eng",),
        timeout_seconds=20,
    )
    assert not provider.calls and not evidence.document.normalized_text
    assert (
        evidence.metadata["recognition_skipped"] == "visual_registration_not_admitted"
    )
    parsed = resolve_evidence(evidence)
    assert parsed.reference_normalized is None and parsed.amount is None
    assert parsed.evidence_result["state"] == "REJECTED_OR_UNKNOWN_RECEIPT"
