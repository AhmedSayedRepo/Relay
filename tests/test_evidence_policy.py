"""Evidence trust tests; no actual sample values enter product rules."""

import io
from dataclasses import replace

import pytest
from PIL import Image, ImageDraw

from relay.application.evidence_sources import (
    correlate_receipt_sources,
    normalize_field,
    parse_explicit_sms,
)
from relay.domain.evidence import (
    EvidenceKind,
    EvidenceSource,
    FieldCandidate,
    ProductState,
    ResolutionPolicy,
)
from relay.domain.processing import OcrLine
from relay.infrastructure.instapay_adapter import (
    _field_matches,
    field_region,
    select_layout,
)
from relay.infrastructure.receipt_geometry import normalize_receipt, rotated_receipt


def candidate(
    field="reference_number",
    value="123456789012",
    kind=EvidenceKind.RECEIPT,
    strong=False,
    score=0.999,
):
    source = EvidenceSource(
        kind,
        "event",
        "test_adapter",
        "v1",
        "2026-09-01T10:00:00Z",
        kind.value + "hash",
        "test_v1",
    )
    return FieldCandidate(
        field,
        value,
        value,
        value,
        source,
        ("localized_validated_context",),
        True,
        strong,
        score,
    )


def test_s04_high_score_is_not_correctness_and_conflicts_never_choose_winner():
    wrong = candidate(value="403892877777", strong=True)
    correct = candidate(value="408892877777", kind=EvidenceKind.SMS, score=0.4)
    result = ResolutionPolicy().resolve_field("reference_number", (wrong, correct))
    assert not result.resolved and result.reason == "conflicting_evidence"
    assert result.candidates == (wrong, correct)
    assert (
        not ResolutionPolicy()
        .resolve_field(
            "reference_number", (replace(wrong, validated_single_source=False),)
        )
        .resolved
    )


def test_weak_conflicting_crop_is_retained_and_blocks_strong_value():
    strong = candidate(strong=True)
    weak = replace(candidate(value="987654321098", score=0.1), usable=False)
    assert (
        not ResolutionPolicy()
        .resolve_field("reference_number", (strong, weak))
        .resolved
    )


def test_modes_and_forwarded_receipts_are_not_independent():
    first = candidate()
    second = replace(
        first,
        source=replace(
            first.source, source_event_id="another", content_sha256="different-image"
        ),
    )
    assert (
        not ResolutionPolicy()
        .resolve_field("reference_number", (first, second))
        .resolved
    )
    assert (
        ResolutionPolicy()
        .resolve_field("reference_number", (first, candidate(kind=EvidenceKind.SMS)))
        .resolved
    )


def test_trace_and_semantic_context_are_required_even_for_high_score():
    field = replace(candidate(strong=True), structural_evidence=())
    assert not ResolutionPolicy().resolve_field("reference_number", (field,)).resolved
    assert (
        not ResolutionPolicy()
        .resolve_field(
            "reference_number",
            (
                replace(
                    candidate(strong=True),
                    source=replace(field.source, source_event_id=""),
                ),
            ),
        )
        .resolved
    )


def test_only_unresolved_critical_field_needs_review_and_missing_values_not_inferred():
    fields = tuple(
        candidate(name, value, strong=True)
        for name, value in (
            ("reference_number", "123456789012"),
            ("currency", "EGP"),
            ("date", "2026-09-01"),
            ("time", "10:30"),
        )
    )
    tx = ResolutionPolicy().resolve(
        fields, classification_resolved=True, supported_layout=True
    )
    assert tx.state == ProductState.NEEDS_REVIEW and tx.blocking_fields == ("amount",)
    final = ResolutionPolicy().resolve(
        (*fields, candidate("amount", "37.25", strong=True)),
        classification_resolved=True,
        supported_layout=True,
    )
    assert final.state == ProductState.AUTO_FINALIZED
    assert final.as_json()["payment_verification"] == "NOT_PERFORMED"
    assert final.as_json()["authenticity"] == "NOT_ASSESSED"
    assert (
        ResolutionPolicy()
        .resolve(
            fields,
            classification_resolved=False,
            supported_layout=False,
            receipt_possible=False,
        )
        .state
        == ProductState.UNKNOWN
    )
    assert (
        ResolutionPolicy()
        .resolve(
            fields,
            classification_resolved=True,
            supported_layout=True,
            explicit_transfer_failure=True,
        )
        .state
        == ProductState.TRANSFER_FAILED
    )


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("amount", "١٬٢٣٤", None),
        ("amount", "1,234.05", "1234.05"),
        ("currency", "FGP", None),
        ("currency", "ج.م", "EGP"),
        ("currency", "جنيه", None),
        ("currency", "جنيه مصري", "EGP"),
        ("reference_number", "01012345678", None),
        ("reference_number", "١٢٣٤٥٦٧٨٩٠١٢", "123456789012"),
        ("date", "31 Feb 2026", None),
        ("time", "04:30 PM", "16:30"),
    ],
)
def test_explicit_arabic_english_normalization_no_digit_repair(field, value, expected):
    assert normalize_field(field, value) == expected


def test_sms_does_not_invent_transaction_fields_from_sender_or_capture_timestamp():
    assert (
        parse_explicit_sms(
            "event", "bank", "Your balance is 790 EGP", "2026-09-01T10:30:00Z", "hash"
        )
        == ()
    )
    assert parse_explicit_sms(
        "event",
        "bank",
        "Reference: 123456789012\nTransfer amount: 37.25\nCurrency: EGP",
        "2026-09-01T10:30:00Z",
        "hash",
    )
    assert not any(
        item.field in {"date", "time"}
        for item in parse_explicit_sms(
            "event", "bank", "Reference: 123456789012", "2026-09-01T10:30:00Z", "hash"
        )
    )


def test_sms_correlation_is_unique_and_not_amount_time_duplicate_identity():
    image = (candidate(), candidate("handle", "example@instapay"))
    group = parse_explicit_sms(
        "sms",
        "bank",
        "Reference: 123456789012\nSender handle: example@instapay\nCurrency: EGP",
        "timestamp",
        "hash1",
    )
    assert correlate_receipt_sources(image, (group,)) == group
    other = tuple(
        replace(item, source=replace(item.source, content_sha256="hash2"))
        for item in group
    )
    assert correlate_receipt_sources(image, (group, other)) == ()
    assert correlate_receipt_sources((candidate("amount", "37.25"),), (group,)) == ()


def test_layout_requires_multiple_structural_anchors_not_brand_name():
    known = tuple(
        OcrLine(text, 0.8, (50, 100, 300, 30))
        for text in ("Transfer amount", "example@instapay", "Reference number", "Date")
    )
    assert select_layout(known) == "instapay_compact_bilingual_v1"
    assert select_layout((OcrLine("InstaPay 790 EGP", 1),)) is None
    assert (
        select_layout(
            tuple(
                OcrLine(text, 0.8)
                for text in (
                    "رسوم الخدمة",
                    "example@instapay",
                    "الرقم المرجعي",
                    "التاريخ",
                )
            )
        )
        == "instapay_expanded_bilingual_v1"
    )


def test_p04_p05_semantic_amount_region_excludes_fee_and_no_phone_reference():
    buffer = io.BytesIO()
    Image.new("RGB", (600, 1000), "white").save(buffer, "PNG")
    working = normalize_receipt(buffer.getvalue())
    region = field_region(
        working, OcrLine("مبلغ التحويل", 0.5, (200, 250, 200, 30)), "amount"
    )
    assert region[1] + region[3] <= 250  # amount above its label, fee below parties
    assert _field_matches("amount", "790 EGP") == ["790"]
    assert _field_matches("currency", "790 EGP") == ["EGP"]
    assert _field_matches("reference_number", "01003405232") == []


def test_geometric_rotation_and_practical_screen_perspective_inverse():
    buffer = io.BytesIO()
    image = Image.new("RGB", (500, 800), "black")
    ImageDraw.Draw(image).polygon(
        [(100, 60), (390, 100), (420, 720), (70, 740)], fill="white"
    )
    image.save(buffer, "PNG")
    working = normalize_receipt(buffer.getvalue())
    assert "screen_quadrilateral_perspective" in working.transformations
    for angle in (0, 90, 180, 270):
        rotated = rotated_receipt(working, angle)
        polygon = rotated.polygon((0, 0, rotated.raster.width, rotated.raster.height))
        assert len(polygon) == 4
        assert all(0 <= x <= 500 and 0 <= y <= 800 for x, y in polygon)
    with pytest.raises(ValueError):
        rotated_receipt(working, 45)
