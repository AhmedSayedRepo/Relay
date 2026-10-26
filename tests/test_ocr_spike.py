from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from tools.ocr_spike.benchmark import (
    _needs_english_currency_retry,
    _ocr_languages,
    _promotion_decision,
    _targeted_ocr_languages,
)
from tools.ocr_spike.discovery import (
    DiscoveryBounds,
    discover_text_bands,
    discover_text_bands_with_diagnostics,
    estimate_working_region,
)
from tools.ocr_spike.evidence import (
    CropEvidence,
    EvidenceMerger,
    EvidenceToken,
    extract_candidates,
    has_sufficient_receipt_evidence,
)
from tools.ocr_spike.models import Anchor, Box, SampleManifest


def _font(size: int = 36) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Prefer Arial (Windows), then DejaVu (Linux CI); never the tiny bitmap font.
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _image(
    lines: list[tuple[tuple[int, int], str]], size: tuple[int, int] = (800, 1200)
) -> Image.Image:
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    for position, text in lines:
        draw.text(position, text, font=_font(), fill=15)
    return image


@pytest.mark.parametrize("y", (120, 440, 850))
def test_visual_discovery_finds_amount_without_primary_ocr_regions(y: int) -> None:
    image = _image([((250, y), "790 EGP")])
    bands = discover_text_bands(image, (), DiscoveryBounds())

    assert any(band.box.top <= y + 10 <= band.box.bottom for band in bands)
    assert all(band.provenance.startswith("visual_") for band in bands)


def test_visual_discovery_is_shift_and_scale_tolerant() -> None:
    small = _image([((80, 100), "EGP 790")], (500, 700))
    large = small.resize((1000, 1400), Image.Resampling.BICUBIC)

    small_bands = discover_text_bands(small, (), DiscoveryBounds())
    large_bands = discover_text_bands(large, (), DiscoveryBounds())

    assert small_bands
    assert large_bands
    assert abs((small_bands[0].box.top / 700) - (large_bands[0].box.top / 1400)) < 0.05


def test_discovery_enforces_count_and_pixel_budget() -> None:
    image = _image([((20, y), f"line {y} 12345") for y in range(20, 1100, 45)])
    bounds = DiscoveryBounds(max_bands=5, max_ocr_passes=4, max_total_crop_ratio=0.25)

    bands = discover_text_bands(image, (), bounds)

    assert len(bands) <= 4
    assert sum(band.box.area for band in bands) <= image.width * image.height * 0.25


def test_discovery_retains_selected_and_rejected_region_proposals() -> None:
    image = _image(
        [
            ((250, 130), "790 EGP"),
            *[((40, y), f"row {y} data") for y in range(300, 1000, 70)],
        ]
    )

    result = discover_text_bands_with_diagnostics(
        image, (), DiscoveryBounds(max_ocr_passes=4)
    )

    assert result.selected_bands
    assert len(result.proposals) > len(result.selected_bands)
    assert all(proposal.detector_source for proposal in result.proposals)
    assert all(proposal.grouping_information for proposal in result.proposals)
    assert all(
        proposal.selected or proposal.rejection_reason for proposal in result.proposals
    )
    assert any(proposal.overlap_relationships for proposal in result.proposals)


def test_large_centered_row_is_selected_by_field_independent_salience() -> None:
    image = _image(
        [
            ((270, 120), "123 ABC"),
            ((80, 420), "ordinary detail row"),
            ((90, 620), "another ordinary detail"),
            ((100, 820), "reference style row"),
        ]
    )

    result = discover_text_bands_with_diagnostics(image, (), DiscoveryBounds())

    assert any(band.box.top <= 150 <= band.box.bottom for band in result.selected_bands)
    assert len(result.selected_bands) <= 6


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("790 EGP", ("amount", "currency")),
        ("EGP 790", ("amount", "currency")),
        ("790", ()),
        ("EGP", ()),
        ("128895843616 EGP", ()),
        ("01003405232 EGP", ()),
        ("29 Aug 2026 EGP", ()),
        ("04:30 PM EGP", ()),
    ),
)
def test_amount_currency_candidate_guards(text: str, expected: tuple[str, ...]) -> None:
    tokens = tuple(
        EvidenceToken(word, 0.95, Box(index * 90, 0, 80, 30))
        for index, word in enumerate(text.split())
    )
    evidence = CropEvidence("secondary_psm7", text, 0.95, Box(10, 10, 400, 50), tokens)

    candidates = extract_candidates(evidence)

    assert (
        tuple(
            candidate.field
            for candidate in candidates
            if candidate.field in {"amount", "currency"}
        )
        == expected
    )


def test_common_egp_ocr_confusion_is_normalized_with_explicit_diagnostic() -> None:
    evidence = CropEvidence(
        "visual_band_psm7",
        "380 Ecp",
        0.91,
        Box(20, 20, 240, 60),
        (
            EvidenceToken("380", 0.94, Box(40, 30, 80, 35)),
            EvidenceToken("Ecp", 0.41, Box(130, 30, 70, 35)),
        ),
        structural_evidence=("upper_receipt_region",),
    )

    candidates = extract_candidates(evidence)

    assert {candidate.field for candidate in candidates} == {"amount", "currency"}
    currency = next(
        candidate for candidate in candidates if candidate.field == "currency"
    )
    assert currency.normalized == "EGP"
    assert "ocr_currency_confusion_normalized" in (
        currency.supporting_structural_evidence
    )


def test_low_confidence_currency_confusion_without_position_guard_is_rejected() -> None:
    evidence = CropEvidence(
        "visual_band_psm7",
        "380 Ecp",
        0.65,
        Box(20, 600, 240, 60),
        (
            EvidenceToken("380", 0.94, Box(40, 610, 80, 35)),
            EvidenceToken("Ecp", 0.41, Box(130, 610, 70, 35)),
        ),
    )

    candidates = extract_candidates(evidence)

    assert not {"amount", "currency"}.intersection(
        candidate.field for candidate in candidates
    )


def test_negative_pack_manifest_schema_defaults_to_english_without_targets() -> None:
    manifest = SampleManifest.from_json(
        {
            "sample_id": "synthetic-negative",
            "filename": "generic-screen.png",
            "description": "Synthetic generic screen",
            "expected_receipt": False,
            "intended_false_positive_challenge": "Currency and identifier text",
        }
    )

    assert manifest.source_filename == "generic-screen.png"
    assert manifest.language == "en"
    assert manifest.expected_receipt is False
    assert manifest.targets == ()
    assert manifest.description == "Synthetic generic screen"


def test_manifest_language_bounds_ocr_languages() -> None:
    assert _ocr_languages("en") == ("eng",)
    assert _ocr_languages("ar") == ("ara", "eng")
    assert _ocr_languages("mixed") == ("eng", "ara")


def test_receipt_identity_anchor_enables_mixed_targeted_ocr() -> None:
    anchors = (Anchor("handle", Box(100, 500, 300, 30), 0.95, "user@instapay"),)

    assert _targeted_ocr_languages("en", anchors) == ("eng", "ara")
    assert _targeted_ocr_languages("en", ()) == ("eng",)


def test_missing_currency_in_upper_numeric_band_requests_english_retry() -> None:
    missing_currency = CropEvidence(
        "visual_band_psm7",
        "مقع 380",
        0.82,
        Box(100, 100, 300, 90),
        (EvidenceToken("380", 0.90, Box(180, 120, 80, 50)),),
        structural_evidence=("above_handle_anchor", "upper_receipt_region"),
    )
    recognized = CropEvidence(
        "visual_band_psm7",
        "380 EGP",
        0.90,
        Box(100, 100, 300, 90),
        (
            EvidenceToken("380", 0.91, Box(150, 120, 80, 50)),
            EvidenceToken("EGP", 0.91, Box(240, 120, 70, 50)),
        ),
        structural_evidence=("above_handle_anchor", "upper_receipt_region"),
    )

    assert _needs_english_currency_retry([missing_currency]) is True
    assert _needs_english_currency_retry([recognized]) is False


def test_extracted_negative_fields_do_not_imply_transaction_promotion() -> None:
    evidence = (
        CropEvidence(
            "visual_band_psm7",
            "380 EGP",
            0.93,
            Box(20, 20, 220, 50),
            (
                EvidenceToken("380", 0.94, Box(40, 30, 70, 30)),
                EvidenceToken("EGP", 0.92, Box(120, 30, 60, 30)),
            ),
            structural_evidence=("upper_receipt_region",),
        ),
        CropEvidence(
            "primary_psm6",
            "Reference 762881236203",
            0.96,
            Box(20, 100, 350, 40),
            (EvidenceToken("762881236203", 0.96, Box(150, 105, 180, 30)),),
        ),
        CropEvidence(
            "primary_psm6",
            "29 Aug 2026 12:32 PM",
            0.96,
            Box(20, 150, 350, 40),
            (),
        ),
    )
    candidates = tuple(
        candidate for item in evidence for candidate in extract_candidates(item)
    )
    merged = EvidenceMerger().merge(candidates)

    decision = _promotion_decision(
        merged, "\n".join(item.original_text for item in evidence)
    )

    assert set(decision["selected_candidate_fields"]) >= {
        "amount",
        "currency",
        "reference",
        "date",
        "time",
    }
    assert decision["receipt_classified"] is False
    assert decision["transaction_candidate_promoted"] is False
    assert decision["promoted_fields"] == []


def test_reference_date_handle_and_phone_are_preserved_with_provenance() -> None:
    evidence = CropEvidence(
        "secondary_psm7",
        "pilot.user@instapay 128895843616 29 Aug 2026 04:30 PM 01003405232",
        0.95,
        Box(0, 0, 700, 40),
        (
            EvidenceToken("pilot.user@instapay", 0.95, Box(0, 0, 160, 30)),
            EvidenceToken("128895843616", 0.96, Box(170, 0, 130, 30)),
            EvidenceToken("29", 0.95, Box(310, 0, 30, 30)),
            EvidenceToken("Aug", 0.95, Box(345, 0, 45, 30)),
            EvidenceToken("2026", 0.95, Box(395, 0, 55, 30)),
            EvidenceToken("04:30", 0.95, Box(455, 0, 65, 30)),
            EvidenceToken("PM", 0.95, Box(525, 0, 30, 30)),
            EvidenceToken("01003405232", 0.95, Box(560, 0, 120, 30)),
        ),
    )

    candidates = extract_candidates(evidence)

    values = {(candidate.field, candidate.normalized) for candidate in candidates}
    assert ("reference", "128895843616") in values
    assert ("date", "29 Aug 2026") in values
    assert ("time", "04:30 PM") in values
    assert ("handle", "pilot.user@instapay") in values
    assert ("recipient_identifier", "01003405232") in values
    assert all(candidate.source_pass == "secondary_psm7" for candidate in candidates)


def test_conflicts_are_preserved_and_not_silently_selected() -> None:
    merger = EvidenceMerger()
    first = CropEvidence(
        "primary_psm6",
        "790 EGP",
        0.95,
        Box(0, 0, 200, 40),
        (
            EvidenceToken("790", 0.95, Box(0, 0, 80, 30)),
            EvidenceToken("EGP", 0.95, Box(90, 0, 70, 30)),
        ),
        structural_evidence=("upper_receipt_region",),
    )
    second = CropEvidence(
        "secondary_psm7",
        "700 EGP",
        0.96,
        Box(0, 0, 200, 40),
        (
            EvidenceToken("700", 0.96, Box(0, 0, 80, 30)),
            EvidenceToken("EGP", 0.96, Box(90, 0, 70, 30)),
        ),
        structural_evidence=("upper_receipt_region",),
    )

    merged = merger.merge((*extract_candidates(first), *extract_candidates(second)))

    assert merged["amount"].selected is None
    assert merged["amount"].conflict is True
    assert {candidate.normalized for candidate in merged["amount"].candidates} == {
        "790",
        "700",
    }


def test_low_confidence_reference_is_not_promoted() -> None:
    evidence = CropEvidence(
        "secondary_psm7",
        "128895843616",
        0.80,
        Box(0, 0, 200, 40),
        (EvidenceToken("128895843616", 0.84, Box(0, 0, 180, 30)),),
    )

    assert not any(
        candidate.field == "reference" for candidate in extract_candidates(evidence)
    )


def test_multiple_references_remain_a_conflict() -> None:
    merger = EvidenceMerger()
    evidences = [
        CropEvidence(
            "secondary_psm7",
            value,
            0.96,
            Box(0, index * 50, 200, 40),
            (EvidenceToken(value, 0.96, Box(0, index * 50, 180, 30)),),
        )
        for index, value in enumerate(("128895843616", "930342632189"))
    ]
    merged = merger.merge(
        tuple(c for evidence in evidences for c in extract_candidates(evidence))
    )

    assert merged["reference"].selected is None
    assert merged["reference"].conflict is True


def test_box_overlap_supports_manual_region_scoring() -> None:
    target = Box(100, 100, 200, 60)
    candidate = Box(90, 90, 220, 80)

    assert candidate.coverage_of(target) == pytest.approx(1.0)


def test_fee_or_total_currency_pair_is_preserved_but_not_promoted() -> None:
    evidence = CropEvidence(
        "visual_band_psm7",
        "806.81 EGP",
        0.96,
        Box(100, 800, 240, 40),
        (
            EvidenceToken("806.81", 0.96, Box(100, 800, 100, 30)),
            EvidenceToken("EGP", 0.96, Box(210, 800, 60, 30)),
        ),
        structural_evidence=(),
    )

    merged = EvidenceMerger().merge(extract_candidates(evidence))

    assert merged["amount"].selected is None
    assert merged["currency"].selected is None
    assert merged["amount"].candidates[0].normalized == "806.81"


def test_optional_anchor_band_is_selected_but_not_required() -> None:
    image = _image([((80, 100), "790 EGP"), ((70, 500), "128895843616")])
    anchor = Anchor("reference", Box(70, 500, 250, 45), 0.96, "128895843616")

    anchored = discover_text_bands(image, (anchor,), DiscoveryBounds())
    independent = discover_text_bands(image, (), DiscoveryBounds())

    assert any(band.provenance == "primary_anchor_band" for band in anchored)
    assert any(band.provenance.startswith("visual_") for band in independent)


@pytest.mark.parametrize(
    "text",
    (
        "تم التحويل بنجاح",
        "Transfer Successful",
        "تم التحويل 790 EGP",
    ),
)
def test_visual_discovery_handles_language_and_mixed_content(text: str) -> None:
    image = _image([((120, 260), text)])

    bands = discover_text_bands(image, (), DiscoveryBounds())

    assert bands


def test_visual_discovery_tolerates_crop_and_jpeg_compression() -> None:
    source = _image(
        [((120, 220), "790 EGP"), ((100, 520), "128895843616")],
        (900, 1000),
    )
    cropped = source.crop((60, 120, 850, 850))
    encoded = io.BytesIO()
    cropped.save(encoded, format="JPEG", quality=25)
    compressed = Image.open(io.BytesIO(encoded.getvalue())).convert("L")

    bands = discover_text_bands(compressed, (), DiscoveryBounds())

    assert bands
    assert len(bands) <= DiscoveryBounds().max_ocr_passes


def test_generic_numeric_screenshot_is_not_sufficient_receipt_evidence() -> None:
    evidence = CropEvidence(
        "primary_psm6",
        "790 EGP 128895843616 29 Aug 2026 04:30 PM",
        0.96,
        Box(0, 0, 700, 40),
        (
            EvidenceToken("790", 0.96, Box(0, 0, 60, 30)),
            EvidenceToken("EGP", 0.96, Box(70, 0, 60, 30)),
            EvidenceToken("128895843616", 0.96, Box(140, 0, 170, 30)),
            EvidenceToken("29", 0.96, Box(320, 0, 30, 30)),
            EvidenceToken("Aug", 0.96, Box(355, 0, 45, 30)),
            EvidenceToken("2026", 0.96, Box(405, 0, 55, 30)),
            EvidenceToken("04:30", 0.96, Box(465, 0, 65, 30)),
            EvidenceToken("PM", 0.96, Box(535, 0, 35, 30)),
        ),
        structural_evidence=("upper_receipt_region",),
    )
    candidates = extract_candidates(evidence)

    assert has_sufficient_receipt_evidence(candidates, brand_visible=False) is False
    assert has_sufficient_receipt_evidence(candidates, brand_visible=True) is False


def test_working_region_estimates_bright_phone_display_without_anchors() -> None:
    image = Image.new("L", (800, 1200), 20)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((130, 40, 680, 1160), radius=45, fill=210)
    draw.text((280, 260), "790 EGP", font=_font(44), fill=25)

    region = estimate_working_region(image)

    assert region.applied is True
    assert region.provenance == "visual_vertical_boundaries"
    assert region.box.left <= 150
    assert region.box.right >= 660


def test_working_region_suppresses_external_edges_before_discovery() -> None:
    image = Image.new("L", (900, 1200), 15)
    draw = ImageDraw.Draw(image)
    draw.rectangle((150, 20, 760, 1180), fill=215)
    for x in (20, 45, 810, 850):
        draw.line((x, 0, x + 35, 1199), fill=245, width=8)
    draw.text((300, 250), "520 EGP", font=_font(44), fill=20)

    region = estimate_working_region(image)
    bands = discover_text_bands(image, (), DiscoveryBounds(), region)

    assert region.applied is True
    assert any(band.box.top <= 285 <= band.box.bottom for band in bands)
    assert all(
        band.box.left >= region.box.left and band.box.right <= region.box.right
        for band in bands
    )


def test_local_darkness_discovery_handles_uneven_illumination() -> None:
    image = Image.new("L", (900, 1000))
    pixels = image.load()
    assert pixels is not None
    for y in range(image.height):
        for x in range(image.width):
            pixels[x, y] = (
                65 + round(150 * x / image.width) + round(20 * y / image.height)
            )
    draw = ImageDraw.Draw(image)
    draw.text((280, 300), "1,145 EGP", font=_font(48), fill=38)

    bands = discover_text_bands(image, (), DiscoveryBounds())

    assert any(band.box.top <= 340 <= band.box.bottom for band in bands)
    assert any(
        "adaptive_local_darkness" in band.provenance
        or band.provenance == "visual_context_expansion"
        for band in bands
    )


def test_same_baseline_fragments_are_grouped_into_one_candidate_row() -> None:
    image = _image([((120, 300), "790"), ((330, 300), "EGP")])

    bands = discover_text_bands(image, (), DiscoveryBounds())

    matching = [band for band in bands if band.box.top <= 330 <= band.box.bottom]
    assert matching
    assert any(band.box.left <= 130 and band.box.right >= 350 for band in matching)
    assert len(matching) == 1


def test_anchor_validation_is_optional_and_does_not_create_working_region() -> None:
    image = Image.new("L", (700, 900), 20)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 20, 610, 880), fill=220)
    draw.text((230, 250), "790 EGP", font=_font(42), fill=25)
    anchor = Anchor("handle", Box(220, 500, 260, 40), 0.96, "test@instapay")

    visual_only = estimate_working_region(image)
    anchored = estimate_working_region(image, (anchor,))

    assert visual_only.applied is True
    assert anchored.applied is True
    assert visual_only.provenance == "visual_vertical_boundaries"
