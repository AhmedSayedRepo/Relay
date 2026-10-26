from __future__ import annotations

from typing import Any

import pytest

from tools.ocr_bakeoff.models import ProviderSampleResult
from tools.ocr_bakeoff.paddle_benchmark import _network_denied, decide, evaluate_trust
from tools.ocr_bakeoff.paddle_provider import paddle_document_from_response


def _map(payload: dict[str, Any]) -> Any:
    return paddle_document_from_response(
        payload,
        width=100,
        height=200,
        requested_languages=("eng", "ara"),
        duration_ms=20,
        provider_version="fixture-model-version",
    )


def test_native_mixed_text_geometry_and_confidence_are_preserved() -> None:
    document = _map(
        {
            "res": {
                "rec_texts": ["تم التحويل", "790 EGP", "اسم sender@instapay"],
                "rec_scores": [0.9, 0.99, 0.7],
                "rec_polys": [[[10, 20], [90, 20], [90, 30], [10, 30]]] * 3,
                "text_word": [["تم", "التحويل"], ["790", "EGP"], []],
                "text_word_boxes": [
                    [[10, 20, 35, 30], [40, 20, 90, 30]],
                    [[10, 20, 35, 30], [40, 20, 90, 30]],
                    [],
                ],
            }
        }
    )
    assert document.provider_id == "paddleocr"
    assert document.provider_version == "fixture-model-version"
    assert document.lines[2].text == "اسم sender@instapay"
    assert document.lines[1].confidence == 0.99
    assert document.lines[1].bounding_box == (10, 20, 80, 10)
    assert document.lines[1].tokens[1].bounding_box == (40, 20, 50, 10)
    assert document.lines[1].tokens[1].confidence is None


def test_low_confidence_text_is_retained_without_currency_repair() -> None:
    document = _map(
        {
            "rec_texts": ["380 FGP"],
            "rec_scores": [0.1],
            "rec_polys": [[]],
        }
    )
    assert document.normalized_text == "380 FGP"
    assert document.lines[0].confidence == 0.1
    assert document.lines[0].bounding_box is None
    assert document.lines[0].tokens == ()


def test_malformed_arrays_and_scores_are_rejected() -> None:
    with pytest.raises(ValueError, match="align"):
        _map({"rec_texts": ["380"], "rec_scores": [], "rec_polys": []})
    with pytest.raises(ValueError, match="confidence"):
        _map({"rec_texts": ["380"], "rec_scores": [1.1], "rec_polys": [[]]})


def test_empty_detection_does_not_create_evidence() -> None:
    document = _map({})
    assert document.normalized_text == ""
    assert document.lines == ()
    assert document.mean_confidence is None


@pytest.mark.parametrize(
    ("reference", "pair", "negative", "expected"),
    [
        (1.0, 1.0, 0, ("INTEGRATE", True)),
        (16 / 17, 10 / 11, 0, ("REVIEW_FIRST", False)),
        (0.94999, 1.0, 0, ("REVIEW_FIRST", False)),
        (0.89999, 1.0, 0, ("REJECT_PROVIDER", False)),
        (1.0, 1.0, 1, ("REJECT_PROVIDER", False)),
    ],
)
def test_decision_uses_unrounded_ratios_and_negative_safety(
    reference: float, pair: float, negative: int, expected: tuple[str, bool]
) -> None:
    assert (
        decide(
            {
                "reference": {"accuracy": reference},
                "amount_currency_pair": {"accuracy": pair},
                "negative_false_transaction_promotions": negative,
            }
        )
        == expected
    )


def test_missing_evidence_is_review_only_and_not_duplicate_authority() -> None:
    document = _map({})
    result = ProviderSampleResult(
        "fixture", "hash", "paddleocr", "fixture-model", 20, "", {}, [],
        False, False, "No evidence", 0, 0, 0, 0,
    )
    trust = evaluate_trust(document, result)
    assert trust["review_status"] == "NEEDS_REVIEW"
    assert trust["transaction_promoted"] is False
    assert trust["duplicate_link_eligible"] is False
    assert trust["fields"]["reference"]["reasons"] == ["missing_or_unselected"]


def test_outbound_connection_guard_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="Network disabled"):
        _network_denied()


def test_invalid_date_and_source_mismatch_are_not_trusted() -> None:
    document = _map(
        {
            "rec_texts": ["39 Aug 2026 12:32 PM"],
            "rec_scores": [0.999],
            "rec_polys": [[[10, 20], [90, 20], [90, 30], [10, 30]]],
        }
    )
    result = ProviderSampleResult(
        "fixture", "hash", "paddleocr", "fixture-model", 20, "", {
            "date": {
                "normalized": "39 Aug 2026",
                "original_ocr_text": "different source text",
                "confidence": 0.999,
                "source_pass": "paddleocr_line_0",
                "crop_geometry": [10, 20, 80, 10],
                "supporting_structural_evidence": ["rigid_datetime_span"],
            }
        }, [], True, True, "fixture", 1, 0, 1, 1,
    )
    trust = evaluate_trust(document, result)
    assert "invalid_field_structure" in trust["fields"]["date"]["reasons"]
    assert "source_text_mismatch" in trust["fields"]["date"]["reasons"]
    assert trust["transaction_promoted"] is False
