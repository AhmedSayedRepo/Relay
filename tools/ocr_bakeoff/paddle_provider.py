"""Local-only PaddleOCR implementation of Relay's existing OcrProvider port."""

from __future__ import annotations

import asyncio
import importlib
import math
import threading
import time
from collections.abc import Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any

from relay.domain.processing import (
    OcrDocument,
    OcrLine,
    OcrLineOrigin,
    OcrToken,
    PreprocessedImage,
    normalize_ocr_text,
)

DETECTION_MODEL = "PP-OCRv5_server_det"
RECOGNITION_MODEL = "arabic_PP-OCRv5_mobile_rec"


def _box(
    points: list[Any], width: int, height: int
) -> tuple[int, int, int, int] | None:
    if not points:
        return None
    if len(points) == 4 and not isinstance(points[0], list):
        xs, ys = [float(points[0]), float(points[2])], [
            float(points[1]),
            float(points[3]),
        ]
    else:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
    if not all(math.isfinite(value) for value in (*xs, *ys)):
        raise ValueError("Nonfinite Paddle geometry")
    left, top = max(0, round(min(xs))), max(0, round(min(ys)))
    right, bottom = min(width, round(max(xs))), min(height, round(max(ys)))
    return left, top, max(0, right - left), max(0, bottom - top)


def paddle_document_from_response(
    payload: Mapping[str, Any],
    *,
    width: int,
    height: int,
    requested_languages: tuple[str, ...],
    duration_ms: int,
    provider_version: str,
) -> OcrDocument:
    """Map native spans without fabricating word confidence or repairing text."""
    data = payload.get("res", payload)
    texts = list(data.get("rec_texts", []))
    scores = list(data.get("rec_scores", []))
    polygons = list(data.get("rec_polys", []))
    if not len(texts) == len(scores) == len(polygons):
        raise ValueError("Paddle recognition arrays do not align")
    word_texts = data.get("text_word", [])
    word_polys = data.get("text_word_region") or data.get("text_word_boxes", [])
    lines: list[OcrLine] = []
    for index, (text, score, polygon) in enumerate(
        zip(texts, scores, polygons, strict=True)
    ):
        confidence = float(score)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Invalid Paddle confidence")
        tokens: list[OcrToken] = []
        if index < len(word_texts) and index < len(word_polys):
            if len(word_texts[index]) != len(word_polys[index]):
                raise ValueError("Paddle word arrays do not align")
            tokens = [
                OcrToken(str(word), None, _box(points, width, height))
                for word, points in zip(
                    word_texts[index], word_polys[index], strict=True
                )
            ]
        lines.append(
            OcrLine(
                str(text),
                confidence,
                _box(polygon, width, height),
                tuple(tokens),
                OcrLineOrigin.PRIMARY,
                "paddle_native_span_confidence_native_word_geometry",
                source_line_index=index,
            )
        )
    return OcrDocument(
        "paddleocr",
        provider_version,
        requested_languages,
        ("eng", "ara"),
        normalize_ocr_text("\n".join(str(text) for text in texts)),
        tuple(lines),
        sum(float(score) for score in scores) / len(scores) if scores else None,
        duration_ms,
    )


class PaddleOcrProvider:
    """One fixed CPU pipeline; no URLs, OCR APIs, or language switching."""

    def __init__(self, model_root: Path) -> None:
        started = time.perf_counter()
        paddleocr = importlib.import_module("paddleocr")
        self.provider_version = (
            f"paddleocr-{version('paddleocr')};paddle-{version('paddlepaddle')};"
            f"det={DETECTION_MODEL};rec={RECOGNITION_MODEL}"
        )
        self._pipeline = paddleocr.PaddleOCR(
            text_detection_model_name=DETECTION_MODEL,
            text_detection_model_dir=str(model_root / DETECTION_MODEL),
            text_recognition_model_name=RECOGNITION_MODEL,
            text_recognition_model_dir=str(model_root / RECOGNITION_MODEL),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
            cpu_threads=4,
            enable_mkldnn=False,
        )
        self.startup_ms = round((time.perf_counter() - started) * 1000)
        self.last_raw: dict[str, Any] | None = None
        self._lock = threading.Lock()

    @property
    def provider_id(self) -> str:
        return "paddleocr"

    @property
    def available_languages(self) -> tuple[str, ...]:
        return "eng", "ara"

    async def extract(
        self,
        image: PreprocessedImage,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
    ) -> OcrDocument:
        # Native CPU inference is not forcibly cancellable in a Python thread.
        # The isolated benchmark process supplies the outer execution boundary.
        return await asyncio.to_thread(
            self._extract_sync, image, requested_languages, timeout_seconds
        )

    def _extract_sync(
        self,
        image: PreprocessedImage,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
    ) -> OcrDocument:
        cv2 = importlib.import_module("cv2")
        numpy = importlib.import_module("numpy")
        with self._lock:
            self.last_raw = None
            started = time.perf_counter()
            decoded = cv2.imdecode(
                numpy.frombuffer(image.data, dtype=numpy.uint8), cv2.IMREAD_COLOR
            )
            if decoded is None or decoded.shape[:2] != (image.height, image.width):
                raise ValueError("Invalid original image dimensions")
            results = list(
                self._pipeline.predict(
                    decoded, text_rec_score_thresh=0.0, return_word_box=True
                )
            )
            if len(results) != 1:
                raise ValueError("Expected one Paddle result per original image")
            self.last_raw = results[0].json
            elapsed = time.perf_counter() - started
            if elapsed > timeout_seconds:
                raise TimeoutError("Paddle CPU inference exceeded its time budget")
            return paddle_document_from_response(
                self.last_raw,
                width=image.width,
                height=image.height,
                requested_languages=requested_languages,
                duration_ms=round(elapsed * 1000),
                provider_version=self.provider_version,
            )
