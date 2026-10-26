import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from relay.application.receipts import InstaPayReceiptParser
from relay.domain.processing import (
    FieldStatus,
    OcrDocument,
    OcrLine,
    OcrLineOrigin,
    OcrToken,
    ReceiptType,
    ReviewStatus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "parser" / "instapay_layout_matrix.json"
STRUCTURAL_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "parser"
    / "instapay_structural_fallback_matrix.json"
)
COMPACT_STRUCTURAL_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "parser"
    / "instapay_compact_structural_matrix.json"
)


def load_cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_structural_cases() -> list[dict[str, Any]]:
    return json.loads(STRUCTURAL_FIXTURE.read_text(encoding="utf-8"))


def load_compact_structural_cases() -> list[dict[str, Any]]:
    return json.loads(COMPACT_STRUCTURAL_FIXTURE.read_text(encoding="utf-8"))


def case_document(case: dict[str, Any]) -> OcrDocument:
    lines = tuple(
        OcrLine(
            line[0],
            line[1],
            tuple(line[2]),
            tuple(
                OcrToken(token[0], token[1], tuple(token[2]))
                for token in (line[3] if len(line) > 3 else ())
            ),
        )
        for line in case["lines"]
    )
    return OcrDocument(
        "fixture-ocr",
        "1.0",
        tuple(case["languages"]),
        ("ara", "eng"),
        "\n".join(line.text for line in lines),
        lines,
        sum(line.confidence or 0 for line in lines) / len(lines),
        1,
    )


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["id"])
def test_instapay_layout_matrix(case: dict[str, Any]) -> None:
    parsed = InstaPayReceiptParser().parse(case_document(case))
    expected = case["expected"]

    assert parsed.review_status == ReviewStatus.NEEDS_REVIEW
    assert parsed.amount == (
        Decimal(expected["amount"]) if expected["amount"] is not None else None
    )
    assert parsed.currency == expected["currency"]
    assert (
        parsed.transaction_occurred_at.isoformat()
        if parsed.transaction_occurred_at
        else None
    ) == expected["occurred_at"]
    assert parsed.reference_normalized == expected["reference"]
    assert parsed.sender_name == expected.get("sender")
    assert parsed.recipient_name == expected.get("recipient")


def test_observed_sequence_preserves_provenance_and_rejects_corruption() -> None:
    cases = {case["id"]: case for case in load_cases()}
    observed = InstaPayReceiptParser().parse(
        case_document(cases["mixed_arabic_english_observed_sequence"])
    )
    fields = {field.field: field for field in observed.fields}

    assert fields["amount"].original_text == "790"
    assert fields["amount"].value == "790.00"
    assert "explicit_currency_marker" in fields["amount"].evidence
    assert fields["reference_number"].original_text == "128895843616"
    assert fields["reference_number"].confidence == pytest.approx(0.97)
    assert fields["reference_number"].status == FieldStatus.CONFIDENT
    assert "transaction_detail_sequence" in fields["reference_number"].evidence
    assert fields["sender_name"].original_text == "pilot.user@instapay"
    assert fields["recipient_name"].status == FieldStatus.MISSING

    corrupted = InstaPayReceiptParser().parse(
        case_document(cases["low_confidence_corrupted_reference"])
    )
    corrupted_fields = {field.field: field for field in corrupted.fields}
    assert corrupted_fields["reference_number"].status == FieldStatus.MISSING
    assert corrupted_fields["transaction_date"].status == FieldStatus.MISSING
    assert corrupted_fields["transaction_time"].status == FieldStatus.MISSING

    low_confidence = InstaPayReceiptParser().parse(
        case_document(cases["low_confidence_numeric_reference"])
    )
    assert low_confidence.reference_normalized is None


@pytest.mark.parametrize(
    "case_id",
    ("arabic_value_first_two_column", "english_value_first_two_column"),
)
def test_two_column_values_require_geometry_provenance(case_id: str) -> None:
    cases = {case["id"]: case for case in load_cases()}
    parsed = InstaPayReceiptParser().parse(case_document(cases[case_id]))
    fields = {field.field: field for field in parsed.fields}

    for field in ("amount", "reference_number", "transaction_date", "transaction_time"):
        assert "geometry_paired_label_value" in fields[field].evidence


def test_phone_amount_and_arbitrary_numbers_are_not_unlabeled_references() -> None:
    cases = {case["id"]: case for case in load_cases()}
    missing = InstaPayReceiptParser().parse(case_document(cases["missing_reference"]))
    guarded = InstaPayReceiptParser().parse(
        case_document(cases["phone_and_reference_present"])
    )

    assert missing.reference_normalized is None
    assert missing.sender_name is None
    assert guarded.amount == Decimal("275.00")
    assert guarded.reference_normalized == "556677889900"
    assert guarded.reference_original != "01003405232"


def test_arabic_and_latin_reference_digits_normalize_identically() -> None:
    cases = {case["id"]: case for case in load_cases()}
    ascii_receipt = InstaPayReceiptParser().parse(
        case_document(cases["same_reference_ascii"])
    )
    arabic_receipt = InstaPayReceiptParser().parse(
        case_document(cases["same_reference_arabic_digits"])
    )

    assert ascii_receipt.reference_normalized == "667788990011"
    assert arabic_receipt.reference_normalized == ascii_receipt.reference_normalized
    assert arabic_receipt.reference_original == "٦٦٧٧٨٨٩٩٠٠١١"


@pytest.mark.parametrize("case", load_structural_cases(), ids=lambda case: case["id"])
def test_strict_structural_fallback_matrix(case: dict[str, Any]) -> None:
    parsed = InstaPayReceiptParser().parse(case_document(case))
    expected = case["expected"]

    assert parsed.receipt_type == ReceiptType(expected["receipt_type"])
    assert parsed.review_status == ReviewStatus(expected["review_status"])
    if parsed.receipt_type == ReceiptType.UNKNOWN_RECEIPT:
        assert all(field.status == FieldStatus.MISSING for field in parsed.fields)
        return

    fields = {field.field: field for field in parsed.fields}
    assert (str(parsed.amount) if parsed.amount is not None else None) == expected[
        "amount"
    ]
    assert parsed.currency == expected["currency"]
    assert (
        parsed.transaction_occurred_at.isoformat()
        if parsed.transaction_occurred_at
        else None
    ) == expected["occurred_at"]
    assert parsed.reference_normalized == expected["reference"]
    assert parsed.sender_name == expected["sender"]
    assert parsed.recipient_identifier == expected["recipient_identifier"]
    assert parsed.recipient_name is None
    assert parsed.transfer_status == expected["status"]
    if expected["classification_path"] == "structural":
        assert "strict_structural_receipt_sequence" in parsed.classification_evidence
    else:
        assert parsed.classification_evidence == (
            "instapay_brand_marker",
            "receipt_field_marker",
        )
    assert "transaction_detail_sequence" in fields["reference_number"].evidence
    assert fields["recipient_identifier"].original_text == expected.get(
        "recipient_identifier_original", expected["recipient_identifier"]
    )


@pytest.mark.parametrize(
    "case", load_compact_structural_cases(), ids=lambda case: case["id"]
)
def test_compact_structural_fallback_matrix(case: dict[str, Any]) -> None:
    parsed = InstaPayReceiptParser().parse(case_document(case))
    expected = case["expected"]

    assert parsed.receipt_type == ReceiptType(expected["receipt_type"])
    assert parsed.review_status == ReviewStatus(expected["review_status"])
    if parsed.receipt_type == ReceiptType.UNKNOWN_RECEIPT:
        assert parsed.reference_normalized is None
        assert parsed.transaction_occurred_at is None
        return

    fields = {field.field: field for field in parsed.fields}
    assert "compact_structural_receipt_sequence" in parsed.classification_evidence
    assert parsed.reference_normalized == expected["reference"]
    assert fields["reference_number"].original_text == expected["reference_original"]
    assert fields["reference_number"].confidence == pytest.approx(
        expected["reference_confidence"]
    )
    assert "token_level_ocr_confidence" in fields["reference_number"].evidence
    assert (
        parsed.transaction_occurred_at.isoformat()
        if parsed.transaction_occurred_at
        else None
    ) == expected["occurred_at"]
    assert parsed.sender_name == expected["sender"]
    assert fields["sender_name"].status == FieldStatus.UNCERTAIN
    assert parsed.amount is None
    assert parsed.currency is None
    assert parsed.transfer_status is None


def p04_secondary_document(
    amount_text: str = "790 EGP",
    *,
    amount_confidence: float = 0.85,
    parser_eligible: bool = True,
) -> OcrDocument:
    primary = (
        OcrLine("-—- Pr", 0.155, (578, 344, 125, 20)),
        OcrLine(
            "m.gohari1712@instapay (=)",
            0.681,
            (452, 611, 497, 71),
            (OcrToken("m.gohari1712@instapay", 0.761, (452, 638, 365, 44)),),
        ),
        OcrLine(
            "YOUSSEF M***** Y***44",
            0.574,
            (328, 874, 362, 40),
            (
                OcrToken("YOUSSEF", 0.930, (328, 889, 137, 25)),
                OcrToken("M*****", 0.846, (477, 889, 99, 24)),
            ),
        ),
        OcrLine(
            "128895843616",
            0.967,
            (105, 1139, 230, 24),
            (OcrToken("128895843616", 0.967, (105, 1139, 230, 24)),),
        ),
        OcrLine("29 Aug 2026 04:30 PM", 0.964, (103, 1221, 338, 33)),
        OcrLine("POWERED BY", 0.965, (468, 1522, 122, 13)),
    )
    amount_tokens = tuple(
        OcrToken(token, amount_confidence, (373 + index * 180, 322, 160, 70))
        for index, token in enumerate(amount_text.split())
    )
    secondary = OcrLine(
        amount_text,
        amount_confidence,
        (373, 322, 338, 78),
        amount_tokens,
        origin=OcrLineOrigin.SECONDARY,
        ocr_mode="line_crop_psm7",
        source_region=(357, 314, 370, 94),
        source_line_index=0,
        parser_eligible=parser_eligible,
    )
    return OcrDocument(
        "fixture-ocr",
        "1.0",
        ("eng", "ara"),
        ("ara", "eng"),
        "\n".join(line.text for line in primary),
        primary,
        sum(line.confidence or 0 for line in primary) / len(primary),
        1,
        secondary_lines=(secondary,),
    )


@pytest.mark.parametrize("amount_text", ("EGP 790", "790 EGP", "٧٩٠ ج.م"))
def test_secondary_amount_evidence_uses_existing_bilingual_parser_rules(
    amount_text: str,
) -> None:
    document = p04_secondary_document(amount_text)

    parsed = InstaPayReceiptParser().parse(document.parser_view())

    assert document.normalized_text.startswith("-—- Pr\n")
    assert parsed.receipt_type == ReceiptType.INSTAPAY_TRANSFER_RECEIPT
    assert parsed.amount == Decimal("790.00")
    assert parsed.currency == "EGP"
    assert parsed.reference_normalized == "128895843616"
    assert parsed.transaction_occurred_at is not None
    assert parsed.transaction_occurred_at.isoformat() == "2026-08-29T16:30:00"
    assert parsed.transfer_status is None
    assert parsed.review_status == ReviewStatus.NEEDS_REVIEW


@pytest.mark.parametrize("amount_text", ("790", "EGP"))
def test_ineligible_secondary_value_does_not_reach_parser(amount_text: str) -> None:
    document = p04_secondary_document(amount_text, parser_eligible=False)

    parsed = InstaPayReceiptParser().parse(document.parser_view())

    assert parsed.amount is None
    assert parsed.currency is None


def test_conflicting_primary_and_secondary_amounts_remain_missing() -> None:
    secondary_document = p04_secondary_document()
    primary = (
        OcrLine("InstaPay Transfer", 0.96, (80, 40, 300, 30)),
        OcrLine("EGP 800", 0.96, (80, 120, 160, 30)),
    )
    secondary = secondary_document.secondary_lines[0]
    document = OcrDocument(
        "fixture-ocr",
        "1.0",
        ("eng",),
        ("ara", "eng"),
        "\n".join(line.text for line in primary),
        primary,
        0.96,
        1,
        secondary_lines=(
            OcrLine(
                secondary.text,
                secondary.confidence,
                secondary.bounding_box,
                secondary.tokens,
                origin=secondary.origin,
                ocr_mode=secondary.ocr_mode,
                source_region=secondary.source_region,
                source_line_index=1,
                parser_eligible=True,
            ),
        ),
    )

    parsed = InstaPayReceiptParser().parse(document.parser_view())

    assert {line.text for line in document.evidence_lines} >= {"EGP 800", "790 EGP"}
    assert parsed.receipt_type == ReceiptType.INSTAPAY_TRANSFER_RECEIPT
    assert parsed.amount is None
    assert parsed.currency is None
    assert parsed.review_status == ReviewStatus.NEEDS_REVIEW


def test_low_confidence_secondary_amount_is_uncertain() -> None:
    document = p04_secondary_document(amount_confidence=0.70)

    parsed = InstaPayReceiptParser().parse(document.parser_view())
    fields = {field.field: field for field in parsed.fields}

    assert parsed.amount == Decimal("790.00")
    assert fields["amount"].status == FieldStatus.UNCERTAIN
    assert fields["currency"].status == FieldStatus.UNCERTAIN


def test_secondary_amount_does_not_classify_generic_numeric_screenshot() -> None:
    amount = p04_secondary_document().secondary_lines[0]
    primary = (
        OcrLine("Monthly report", 0.96, (80, 40, 250, 30)),
        OcrLine("128895843616", 0.97, (80, 120, 220, 30)),
        OcrLine("29 Aug 2026 04:30 PM", 0.96, (80, 180, 340, 30)),
    )
    document = OcrDocument(
        "fixture-ocr",
        "1.0",
        ("eng",),
        ("ara", "eng"),
        "\n".join(line.text for line in primary),
        primary,
        0.96,
        1,
        secondary_lines=(
            OcrLine(
                amount.text,
                amount.confidence,
                amount.bounding_box,
                amount.tokens,
                origin=amount.origin,
                ocr_mode=amount.ocr_mode,
                source_region=amount.source_region,
                source_line_index=0,
                parser_eligible=True,
            ),
        ),
    )

    parsed = InstaPayReceiptParser().parse(document.parser_view())

    assert parsed.receipt_type == ReceiptType.UNKNOWN_RECEIPT
    assert parsed.amount is None
    assert parsed.currency is None
    assert parsed.review_status == ReviewStatus.REJECTED
