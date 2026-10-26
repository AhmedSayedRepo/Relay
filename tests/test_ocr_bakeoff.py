from __future__ import annotations

import io
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from relay.domain.processing import OcrDocument, OcrLine, OcrToken
from tools.ocr_bakeoff.adapters import (
    ProviderHttpError,
    _request_json,
    azure_document_from_response,
    google_document_from_response,
)
from tools.ocr_bakeoff.benchmark import calculate_metrics
from tools.ocr_bakeoff.models import BakeoffSample, ProviderSampleResult
from tools.ocr_bakeoff.policy import evaluate_document


def test_provider_http_failure_exposes_only_safe_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        b'{"error":{"status":"PERMISSION_DENIED",'
        b'"message":"secret access token content",'
        b'"details":[{"reason":"BILLING_DISABLED"}]}}'
    )

    def deny(*args: object, **kwargs: object) -> None:
        raise urllib.error.HTTPError(
            "https://example.invalid", 403, "Forbidden", None, io.BytesIO(body)
        )

    monkeypatch.setattr(urllib.request, "urlopen", deny)
    with pytest.raises(ProviderHttpError) as caught:
        _request_json(urllib.request.Request("https://example.invalid"), 1.0)
    assert str(caught.value) == "Provider HTTP 403: PERMISSION_DENIED BILLING_DISABLED"
    assert "secret" not in str(caught.value)


def test_google_response_maps_text_confidence_tokens_and_geometry() -> None:
    payload = {
        "processorVersion": "pretrained-ocr-v2.1",
        "document": {
            "text": "380 EGP\n",
            "pages": [
                {
                    "lines": [
                        {
                            "layout": {
                                "textAnchor": {"textSegments": [{"endIndex": "7"}]},
                                "confidence": 0.98,
                                "boundingPoly": {
                                    "normalizedVertices": [
                                        {"x": 0.1, "y": 0.2},
                                        {"x": 0.5, "y": 0.2},
                                        {"x": 0.5, "y": 0.3},
                                        {"x": 0.1, "y": 0.3},
                                    ]
                                },
                            }
                        }
                    ],
                    "tokens": [
                        {
                            "layout": {
                                "textAnchor": {
                                    "textSegments": [
                                        {"startIndex": "0", "endIndex": "3"}
                                    ]
                                },
                                "confidence": 0.99,
                                "boundingPoly": {
                                    "normalizedVertices": [
                                        {"x": 0.1, "y": 0.2},
                                        {"x": 0.3, "y": 0.2},
                                        {"x": 0.3, "y": 0.3},
                                        {"x": 0.1, "y": 0.3},
                                    ]
                                },
                            }
                        },
                        {
                            "layout": {
                                "textAnchor": {
                                    "textSegments": [
                                        {"startIndex": "4", "endIndex": "7"}
                                    ]
                                },
                                "confidence": 0.97,
                                "boundingPoly": {
                                    "normalizedVertices": [
                                        {"x": 0.31, "y": 0.2},
                                        {"x": 0.5, "y": 0.2},
                                        {"x": 0.5, "y": 0.3},
                                        {"x": 0.31, "y": 0.3},
                                    ]
                                },
                            }
                        },
                    ],
                }
            ],
        },
    }
    document = google_document_from_response(
        payload,
        width=1_000,
        height=2_000,
        requested_languages=("eng", "ara"),
        duration_ms=120,
    )
    assert document.provider_id == "google_document_ai"
    assert document.lines[0].text == "380 EGP"
    assert document.lines[0].bounding_box == (100, 400, 400, 200)
    assert [token.text for token in document.lines[0].tokens] == ["380", "EGP"]


def test_azure_response_maps_words_and_page_coordinates() -> None:
    payload = {
        "status": "succeeded",
        "analyzeResult": {
            "apiVersion": "2024-11-30",
            "content": "380 EGP",
            "pages": [
                {
                    "width": 100,
                    "height": 200,
                    "lines": [
                        {
                            "content": "380 EGP",
                            "polygon": [10, 20, 50, 20, 50, 30, 10, 30],
                            "spans": [{"offset": 0, "length": 7}],
                        }
                    ],
                    "words": [
                        {
                            "content": "380",
                            "confidence": 0.99,
                            "polygon": [10, 20, 30, 20, 30, 30, 10, 30],
                            "span": {"offset": 0, "length": 3},
                            "spans": [{"offset": 0, "length": 3}],
                        },
                        {
                            "content": "EGP",
                            "confidence": 0.98,
                            "polygon": [31, 20, 50, 20, 50, 30, 31, 30],
                            "spans": [{"offset": 4, "length": 3}],
                        },
                    ],
                }
            ],
        },
    }
    document = azure_document_from_response(
        payload,
        width=1_000,
        height=2_000,
        requested_languages=("eng", "ara"),
        duration_ms=250,
    )
    assert document.provider_id == "azure_document_intelligence"
    assert document.lines[0].bounding_box == (100, 200, 400, 100)
    assert document.lines[0].confidence == 0.985


def test_common_policy_keeps_conflicting_amounts_unselected() -> None:
    lines = (
        OcrLine(
            "380 EGP",
            0.99,
            (100, 100, 200, 50),
            (
                OcrToken("380", 0.99, (100, 100, 90, 50)),
                OcrToken("EGP", 0.99, (200, 100, 90, 50)),
            ),
        ),
        OcrLine(
            "490 EGP",
            0.99,
            (100, 160, 200, 50),
            (
                OcrToken("490", 0.99, (100, 160, 90, 50)),
                OcrToken("EGP", 0.99, (200, 160, 90, 50)),
            ),
        ),
        OcrLine("sender@instapay", 0.99, (100, 400, 300, 50)),
        OcrLine("Reference 762881236203", 0.99, (100, 900, 400, 50)),
        OcrLine("29 Aug 2026 12:32 PM", 0.99, (100, 960, 400, 50)),
    )
    document = OcrDocument(
        "fixture",
        "v1",
        ("eng", "ara"),
        ("eng", "ara"),
        "\n".join(line.text for line in lines),
        lines,
        0.99,
        1,
    )
    selected, conflicts, promotion = evaluate_document(
        document, image_width=1_000, image_height=1_200
    )
    assert "amount" not in selected
    assert "amount" in conflicts
    assert promotion["transaction_promoted"] is False


def test_common_policy_does_not_infer_egp_from_ecp() -> None:
    lines = (
        OcrLine(
            "380 ECP",
            0.99,
            (100, 100, 200, 50),
            (
                OcrToken("380", 0.99, (100, 100, 90, 50)),
                OcrToken("ECP", 0.99, (200, 100, 90, 50)),
            ),
        ),
        OcrLine("sender@instapay", 0.99, (100, 400, 300, 50)),
        OcrLine("Reference 762881236203", 0.99, (100, 900, 400, 50)),
    )
    document = OcrDocument(
        "fixture",
        "v1",
        ("eng", "ara"),
        ("eng", "ara"),
        "\n".join(line.text for line in lines),
        lines,
        0.99,
        1,
    )
    selected, _, promotion = evaluate_document(
        document, image_width=1_000, image_height=1_200
    )
    assert "amount" not in selected
    assert "currency" not in selected
    assert promotion["transaction_promoted"] is False


def test_metrics_exclude_uncertain_and_unavailable_ground_truth() -> None:
    sample = BakeoffSample(
        "S",
        Path("unused"),
        True,
        "english",
        {"amount": "380", "currency": "EGP", "reference": "123456789012"},
        frozenset({"reference"}),
        None,
        frozenset({"positive"}),
        False,
    )
    result = ProviderSampleResult(
        "S",
        "hash",
        "tesseract",
        "v1",
        100,
        "380 EGP",
        {
            "amount": {"normalized": "380", "confidence": 0.99},
            "currency": {"normalized": "EGP", "confidence": 0.99},
        },
        [],
        True,
        True,
        "ok",
        1,
        2,
        3,
        3,
    )
    metrics = calculate_metrics([sample], [result])
    assert metrics["reference"] == {"exact": 0, "scorable": 0, "accuracy": None}
    assert metrics["amount_currency_pair"]["accuracy"] == 1.0
    assert metrics["raw_ocr_coverage"]["currency"]["accuracy"] == 1.0
    assert metrics["p95_latency_ms"] == 100.0
    result.raw_text = "380 ECP"
    strict_raw_metrics = calculate_metrics([sample], [result])
    assert strict_raw_metrics["raw_ocr_coverage"]["currency"]["accuracy"] == 0.0
    result.raw_text = "380 FGP\n0.38 EGP\n380.38 EGP"
    fee_raw_metrics = calculate_metrics([sample], [result])
    assert fee_raw_metrics["raw_ocr_coverage"]["amount"]["accuracy"] == 1.0
    assert fee_raw_metrics["raw_ocr_coverage"]["currency"]["accuracy"] == 0.0
    assert (
        fee_raw_metrics["raw_ocr_coverage"]["amount_currency_pair"]["accuracy"] == 0.0
    )
    result.raw_text = "380.38 EGP\nReference 123380456789"
    substring_raw_metrics = calculate_metrics([sample], [result])
    assert substring_raw_metrics["raw_ocr_coverage"]["amount"]["accuracy"] == 0.0
