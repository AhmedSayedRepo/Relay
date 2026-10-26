"""Benchmark-only Google Document AI and Azure Document Intelligence adapters."""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from relay.domain.processing import (
    OcrDocument,
    OcrLine,
    OcrLineOrigin,
    OcrToken,
    PreprocessedImage,
    normalize_ocr_text,
)


class ProviderUnavailable(RuntimeError):
    """The benchmark provider cannot run with the current environment."""


class ProviderHttpError(RuntimeError):
    """A provider HTTP failure with credential-safe machine-readable details."""

    def __init__(self, code: int, status: str, reason: str) -> None:
        self.code = code
        self.status = status
        self.reason = reason
        super().__init__(f"Provider HTTP {code}: {status} {reason}".strip())


def _safe_error_code(value: object) -> str:
    return re.sub(r"[^A-Z0-9_]", "", str(value).upper())[:80]


def _request_json(
    request: urllib.request.Request,
    timeout_seconds: float,
) -> tuple[int, Mapping[str, str], dict[str, Any]]:
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
            return response.status, dict(response.headers.items()), json.loads(payload)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        error = body.get("error") or {} if isinstance(body, dict) else {}
        details = error.get("details") or [] if isinstance(error, dict) else []
        reason = next(
            (
                _safe_error_code(item.get("reason"))
                for item in details
                if item.get("reason")
            ),
            "",
        )
        raise ProviderHttpError(
            exc.code, _safe_error_code(error.get("status")), reason
        ) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Provider request failed: {exc.reason}") from None


def _text_range(anchor: Mapping[str, Any] | None) -> tuple[int, int]:
    if not anchor:
        return 0, 0
    segments = anchor.get("textSegments", [])
    if not segments:
        return 0, 0
    start = min(int(segment.get("startIndex", 0)) for segment in segments)
    end = max(int(segment.get("endIndex", start)) for segment in segments)
    return start, end


def _anchored_text(text: str, anchor: Mapping[str, Any] | None) -> str:
    start, end = _text_range(anchor)
    return text[start:end].strip()


def _google_box(
    polygon: Mapping[str, Any] | None,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    if not polygon:
        return None
    normalized = polygon.get("normalizedVertices") or []
    if normalized:
        xs = [float(point.get("x", 0.0)) * width for point in normalized]
        ys = [float(point.get("y", 0.0)) * height for point in normalized]
    else:
        vertices = polygon.get("vertices") or []
        if not vertices:
            return None
        xs = [float(point.get("x", 0.0)) for point in vertices]
        ys = [float(point.get("y", 0.0)) for point in vertices]
    left, top = max(0, round(min(xs))), max(0, round(min(ys)))
    right, bottom = min(width, round(max(xs))), min(height, round(max(ys)))
    return left, top, max(0, right - left), max(0, bottom - top)


def google_document_from_response(
    payload: Mapping[str, Any],
    *,
    width: int,
    height: int,
    requested_languages: tuple[str, ...],
    duration_ms: int,
) -> OcrDocument:
    document = payload.get("document") or {}
    text = str(document.get("text", ""))
    result_lines: list[OcrLine] = []
    confidences: list[float] = []
    for page in document.get("pages", []):
        page_tokens = list(page.get("tokens", []))
        line_items = page.get("lines") or page.get("paragraphs") or []
        for line_index, item in enumerate(line_items):
            layout = item.get("layout") or {}
            line_text = _anchored_text(text, layout.get("textAnchor"))
            if not line_text:
                continue
            line_start, line_end = _text_range(layout.get("textAnchor"))
            tokens: list[OcrToken] = []
            for token_item in page_tokens:
                token_layout = token_item.get("layout") or {}
                token_start, token_end = _text_range(token_layout.get("textAnchor"))
                if token_end <= line_start or token_start >= line_end:
                    continue
                confidence = token_layout.get("confidence")
                if confidence is not None:
                    confidences.append(float(confidence))
                tokens.append(
                    OcrToken(
                        _anchored_text(text, token_layout.get("textAnchor")),
                        float(confidence) if confidence is not None else None,
                        _google_box(token_layout.get("boundingPoly"), width, height),
                    )
                )
            line_confidence = layout.get("confidence")
            if line_confidence is not None:
                confidences.append(float(line_confidence))
            result_lines.append(
                OcrLine(
                    line_text,
                    float(line_confidence) if line_confidence is not None else None,
                    _google_box(layout.get("boundingPoly"), width, height),
                    tuple(tokens),
                    OcrLineOrigin.PRIMARY,
                    "google_document_ai_enterprise_ocr",
                    source_line_index=line_index,
                )
            )
    if not result_lines and text.strip():
        result_lines.append(OcrLine(text.strip(), None, (0, 0, width, height)))
    mean = sum(confidences) / len(confidences) if confidences else None
    revisions = document.get("revisions") or []
    processor_version = next(
        (
            str(revision["processorVersion"])
            for revision in reversed(revisions)
            if revision.get("processorVersion")
        ),
        str(payload.get("processorVersion", "enterprise-document-ocr")),
    )
    return OcrDocument(
        "google_document_ai",
        processor_version,
        requested_languages,
        ("eng", "ara"),
        normalize_ocr_text(text),
        tuple(result_lines),
        mean,
        duration_ms,
    )


def _google_access_token() -> str:
    explicit = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    if explicit:
        return explicit
    try:
        google_auth = importlib.import_module("google.auth")
        request_type = importlib.import_module("google.auth.transport.requests").Request
    except ImportError as exc:
        raise ProviderUnavailable(
            "Google credentials are absent and google-auth is not installed."
        ) from exc
    credentials, _ = google_auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(request_type())
    if not credentials.token:
        raise ProviderUnavailable(
            "Google Application Default Credentials returned no token."
        )
    return str(credentials.token)


@dataclass(frozen=True, slots=True)
class GoogleDocumentAiProvider:
    processor_name: str

    @property
    def provider_id(self) -> str:
        return "google_document_ai"

    @property
    def provider_version(self) -> str:
        return "enterprise-document-ocr"

    @property
    def available_languages(self) -> tuple[str, ...]:
        return "eng", "ara"

    async def extract(
        self,
        image: PreprocessedImage,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
    ) -> OcrDocument:
        return await asyncio.to_thread(
            self._extract_sync, image, requested_languages, timeout_seconds
        )

    def _extract_sync(
        self,
        image: PreprocessedImage,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
    ) -> OcrDocument:
        parts = self.processor_name.split("/")
        if (
            len(parts) != 6
            or parts[0] != "projects"
            or parts[2] != "locations"
            or parts[4] != "processors"
            or not all(parts[index] for index in (1, 3, 5))
        ):
            raise ProviderUnavailable(
                "GOOGLE_DOCUMENT_AI_PROCESSOR_NAME must be a full processor "
                "resource name."
            )
        token = _google_access_token()
        location = parts[3]
        host = (
            "documentai.googleapis.com"
            if location == "global"
            else f"{location}-documentai.googleapis.com"
        )
        url = f"https://{host}/v1/{self.processor_name}:process"
        body = json.dumps(
            {
                "rawDocument": {
                    "content": base64.b64encode(image.data).decode("ascii"),
                    "mimeType": image.content_type,
                }
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        _, _, payload = _request_json(request, timeout_seconds)
        duration_ms = round((time.monotonic() - started) * 1_000)
        return google_document_from_response(
            payload,
            width=image.width,
            height=image.height,
            requested_languages=requested_languages,
            duration_ms=duration_ms,
        )


def _azure_box(
    polygon: list[float] | None,
    *,
    page_width: float,
    page_height: float,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    if not polygon or len(polygon) < 8:
        return None
    xs = polygon[0::2]
    ys = polygon[1::2]
    scale_x = width / page_width if page_width else 1.0
    scale_y = height / page_height if page_height else 1.0
    left, top = round(min(xs) * scale_x), round(min(ys) * scale_y)
    right, bottom = round(max(xs) * scale_x), round(max(ys) * scale_y)
    return left, top, max(0, right - left), max(0, bottom - top)


def _span(value: Mapping[str, Any]) -> tuple[int, int]:
    spans = value.get("spans") or []
    if not spans:
        return 0, 0
    start = min(int(item.get("offset", 0)) for item in spans)
    end = max(int(item.get("offset", 0)) + int(item.get("length", 0)) for item in spans)
    return start, end


def azure_document_from_response(
    payload: Mapping[str, Any],
    *,
    width: int,
    height: int,
    requested_languages: tuple[str, ...],
    duration_ms: int,
) -> OcrDocument:
    analyze = payload.get("analyzeResult") or {}
    content = str(analyze.get("content", ""))
    lines: list[OcrLine] = []
    confidences: list[float] = []
    for page in analyze.get("pages", []):
        page_width = float(page.get("width", width))
        page_height = float(page.get("height", height))
        words = list(page.get("words", []))
        for line_index, line in enumerate(page.get("lines", [])):
            line_start, line_end = _span(line)
            tokens: list[OcrToken] = []
            for word in words:
                word_start, word_end = _span(word)
                if word_end <= line_start or word_start >= line_end:
                    continue
                confidence = word.get("confidence")
                if confidence is not None:
                    confidences.append(float(confidence))
                tokens.append(
                    OcrToken(
                        str(word.get("content", "")),
                        float(confidence) if confidence is not None else None,
                        _azure_box(
                            word.get("polygon"),
                            page_width=page_width,
                            page_height=page_height,
                            width=width,
                            height=height,
                        ),
                    )
                )
            line_confidence = (
                sum(
                    float(token.confidence)
                    for token in tokens
                    if token.confidence is not None
                )
                / sum(token.confidence is not None for token in tokens)
                if any(token.confidence is not None for token in tokens)
                else None
            )
            lines.append(
                OcrLine(
                    str(line.get("content", "")),
                    line_confidence,
                    _azure_box(
                        line.get("polygon"),
                        page_width=page_width,
                        page_height=page_height,
                        width=width,
                        height=height,
                    ),
                    tuple(tokens),
                    OcrLineOrigin.PRIMARY,
                    "azure_document_intelligence_layout",
                    source_line_index=line_index,
                )
            )
    mean = sum(confidences) / len(confidences) if confidences else None
    return OcrDocument(
        "azure_document_intelligence",
        str(analyze.get("apiVersion", "2024-11-30/prebuilt-layout")),
        requested_languages,
        ("eng", "ara"),
        normalize_ocr_text(content),
        tuple(lines),
        mean,
        duration_ms,
    )


@dataclass(frozen=True, slots=True)
class AzureDocumentIntelligenceProvider:
    endpoint: str
    api_key: str
    api_version: str = "2024-11-30"
    model_id: str = "prebuilt-layout"

    @property
    def provider_id(self) -> str:
        return "azure_document_intelligence"

    @property
    def provider_version(self) -> str:
        return f"{self.api_version}/{self.model_id}"

    @property
    def available_languages(self) -> tuple[str, ...]:
        return "eng", "ara"

    async def extract(
        self,
        image: PreprocessedImage,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
    ) -> OcrDocument:
        return await asyncio.to_thread(
            self._extract_sync, image, requested_languages, timeout_seconds
        )

    def _extract_sync(
        self,
        image: PreprocessedImage,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
    ) -> OcrDocument:
        url = (
            f"{self.endpoint.rstrip('/')}/documentintelligence/documentModels/"
            f"{self.model_id}:analyze?_overload=analyzeDocument&api-version="
            f"{self.api_version}"
        )
        body = json.dumps(
            {"base64Source": base64.b64encode(image.data).decode("ascii")}
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Ocp-Apim-Subscription-Key": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        _, headers, _ = _request_json(request, timeout_seconds)
        operation = next(
            (
                value
                for key, value in headers.items()
                if key.lower() == "operation-location"
            ),
            None,
        )
        if not operation:
            raise RuntimeError("Azure response omitted Operation-Location.")
        deadline = started + timeout_seconds
        payload: dict[str, Any] = {}
        while time.monotonic() < deadline:
            remaining = max(1.0, deadline - time.monotonic())
            poll = urllib.request.Request(
                operation,
                headers={"Ocp-Apim-Subscription-Key": self.api_key},
                method="GET",
            )
            _, _, payload = _request_json(poll, remaining)
            status = str(payload.get("status", "")).lower()
            if status == "succeeded":
                break
            if status == "failed":
                raise RuntimeError("Azure analysis failed.")
            time.sleep(min(0.5, remaining))
        else:
            raise TimeoutError("Azure analysis did not complete before the deadline.")
        duration_ms = round((time.monotonic() - started) * 1_000)
        return azure_document_from_response(
            payload,
            width=image.width,
            height=image.height,
            requested_languages=requested_languages,
            duration_ms=duration_ms,
        )
