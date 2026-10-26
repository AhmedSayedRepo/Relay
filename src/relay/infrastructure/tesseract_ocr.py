"""Local Tesseract adapter with fixed arguments, bounded files, and timeouts."""

import asyncio
import csv
import io
import os
import re
import subprocess
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from relay.application.processing_errors import ProcessingError
from relay.domain.processing import (
    MAX_OCR_BLOCKS,
    MAX_OCR_TEXT_BYTES,
    OcrDocument,
    OcrLine,
    OcrLineOrigin,
    OcrToken,
    PreprocessedImage,
    ProcessingFailure,
    normalize_ocr_text,
)

MAX_TSV_BYTES = 2 * 1024 * 1024
SECONDARY_MAX_REGIONS = 4
SECONDARY_MAX_CROP_WIDTH = 1_600
SECONDARY_MAX_CROP_HEIGHT = 256
SECONDARY_MAX_CROP_PIXELS = 512_000
SECONDARY_LOW_CONFIDENCE_THRESHOLD = 0.50
SECONDARY_TOKEN_MIN_CONFIDENCE = 0.70
_SECONDARY_DARK_PIXEL_THRESHOLD = 200
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_BIDI_CONTROLS = (
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
_SECONDARY_CURRENCY = r"(?:EGP|USD|EUR|ج\.?\s*م\.?|جنيه(?:\s+مصري)?)"
_SECONDARY_NUMBER = r"[0-9٠-٩۰-۹][0-9٠-٩۰-۹,]*(?:\.[0-9٠-٩۰-۹]{1,2})?"
_SECONDARY_AMOUNT_PAIR = re.compile(
    rf"\s*(?:(?P<currency_before>{_SECONDARY_CURRENCY})\s*)?"
    rf"(?P<number>{_SECONDARY_NUMBER})"
    rf"(?:\s*(?P<currency_after>{_SECONDARY_CURRENCY}))?\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _SecondaryRegion:
    bounding_box: tuple[int, int, int, int]
    source_line_index: int


class TesseractOcrProvider:
    provider_id = "tesseract"

    def __init__(self, executable: Path, tessdata_dir: Path) -> None:
        if not executable.is_absolute() or not tessdata_dir.is_absolute():
            raise RuntimeError(
                "OCR executable and language directory must be absolute."
            )
        self.executable = executable.expanduser().resolve()
        self.tessdata_dir = tessdata_dir.expanduser().resolve()
        if not self.executable.is_absolute() or not self.executable.is_file():
            raise RuntimeError("Configured OCR executable is unavailable.")
        if not self.tessdata_dir.is_dir():
            raise RuntimeError("Configured OCR language directory is unavailable.")
        self._available_languages = tuple(
            sorted(path.stem for path in self.tessdata_dir.glob("*.traineddata"))
        )
        self._provider_version = self._read_version()

    @property
    def provider_version(self) -> str:
        return self._provider_version

    @property
    def available_languages(self) -> tuple[str, ...]:
        return self._available_languages

    async def extract(
        self,
        image: PreprocessedImage,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
    ) -> OcrDocument:
        return await asyncio.to_thread(
            self._extract, image, requested_languages, timeout_seconds
        )

    def _read_version(self) -> str:
        try:
            completed = subprocess.run(
                [str(self.executable), "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError(
                "Configured OCR executable could not be verified."
            ) from None
        first = completed.stdout[:512].decode("utf-8", "replace").splitlines()
        if (
            completed.returncode != 0
            or not first
            or not first[0].lower().startswith("tesseract")
        ):
            raise RuntimeError("Configured OCR executable could not be verified.")
        return first[0].strip().removeprefix("tesseract ").removeprefix("v")[:64]

    def _extract(
        self,
        image: PreprocessedImage,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
    ) -> OcrDocument:
        if (
            not requested_languages
            or len(requested_languages) > 4
            or any(
                len(language) != 3
                or not language.isascii()
                or not language.isalpha()
                or language not in self._available_languages
                for language in requested_languages
            )
        ):
            raise ProcessingError(ProcessingFailure.OCR_LANGUAGE_UNAVAILABLE)
        started = time.monotonic()
        deadline = started + timeout_seconds
        with tempfile.TemporaryDirectory(prefix="relay-ocr-") as directory:
            work = Path(directory)
            source = work / "input.png"
            output_base = work / "result"
            source.write_bytes(image.data)
            lines = self._run_tesseract(
                source,
                output_base,
                requested_languages,
                6,
                deadline,
            )
            secondary_lines = self._secondary_extract(
                image,
                lines,
                work,
                requested_languages,
                deadline,
            )
        text = normalize_ocr_text("\n".join(line.text for line in lines))
        if not text or len(text.encode("utf-8")) > MAX_OCR_TEXT_BYTES:
            raise ProcessingError(ProcessingFailure.OCR_EMPTY)
        confidences = [line.confidence for line in lines if line.confidence is not None]
        mean = sum(confidences) / len(confidences) if confidences else None
        return OcrDocument(
            self.provider_id,
            self.provider_version,
            requested_languages,
            self.available_languages,
            text,
            lines,
            mean,
            round((time.monotonic() - started) * 1000),
            secondary_lines=secondary_lines,
        )

    def _run_tesseract(
        self,
        source: Path,
        output_base: Path,
        requested_languages: tuple[str, ...],
        psm: int,
        deadline: float,
    ) -> tuple[OcrLine, ...]:
        args = [
            str(self.executable),
            str(source),
            str(output_base),
            "--tessdata-dir",
            str(self.tessdata_dir),
            "-l",
            "+".join(requested_languages),
            "--psm",
            str(psm),
            "-c",
            "tessedit_create_tsv=1",
        ]
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        environment.update(
            {
                "TESSDATA_PREFIX": str(self.tessdata_dir),
                "OMP_THREAD_LIMIT": "1",
            }
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=output_base.parent,
                env=environment,
                shell=False,
                creationflags=creationflags,
            )
            while (return_code := process.poll()) is None:
                tsv_path = output_base.with_suffix(".tsv")
                if tsv_path.is_file() and tsv_path.stat().st_size > MAX_TSV_BYTES:
                    process.kill()
                    process.wait(timeout=5)
                    raise ProcessingError(ProcessingFailure.RESOURCE_LIMIT)
                if time.monotonic() >= deadline:
                    process.kill()
                    process.wait(timeout=5)
                    raise ProcessingError(
                        ProcessingFailure.OCR_TIMEOUT, transient=True
                    ) from None
                time.sleep(0.05)
        except ProcessingError:
            raise
        except OSError:
            raise ProcessingError(
                ProcessingFailure.OCR_FAILED, transient=True
            ) from None
        tsv_path = output_base.with_suffix(".tsv")
        if (
            return_code != 0
            or not tsv_path.is_file()
            or tsv_path.stat().st_size > MAX_TSV_BYTES
        ):
            raise ProcessingError(ProcessingFailure.OCR_FAILED)
        return self._parse_tsv(tsv_path)

    def _secondary_extract(
        self,
        image: PreprocessedImage,
        primary_lines: tuple[OcrLine, ...],
        work: Path,
        requested_languages: tuple[str, ...],
        deadline: float,
    ) -> tuple[OcrLine, ...]:
        try:
            regions = self._secondary_regions(image, primary_lines)
            with Image.open(io.BytesIO(image.data)) as opened:
                raster = opened.convert("L")
        except (OSError, UnidentifiedImageError):
            return ()

        secondary: list[OcrLine] = []
        eligible_signatures: set[tuple[str, str]] = set()
        for index, region in enumerate(regions):
            if time.monotonic() >= deadline:
                break
            left, top, width, height = region.bounding_box
            crop_path = work / f"secondary-{index}.png"
            raster.crop((left, top, left + width, top + height)).save(
                crop_path, format="PNG", optimize=False, compress_level=6
            )
            try:
                crop_lines = self._run_tesseract(
                    crop_path,
                    work / f"secondary-result-{index}",
                    requested_languages,
                    7,
                    deadline,
                )
            except ProcessingError:
                continue
            for crop_line in crop_lines:
                translated = self._translate_secondary_line(crop_line, region)
                signature = self._secondary_amount_signature(translated)
                eligible = False
                if (
                    len(crop_lines) == 1
                    and signature is not None
                    and signature not in eligible_signatures
                ):
                    eligible = True
                    eligible_signatures.add(signature)
                secondary.append(replace(translated, parser_eligible=eligible))
        return tuple(secondary)

    @staticmethod
    def _secondary_line_is_parser_eligible(line: OcrLine) -> bool:
        return TesseractOcrProvider._secondary_amount_signature(line) is not None

    @staticmethod
    def _secondary_amount_signature(line: OcrLine) -> tuple[str, str] | None:
        if not line.tokens or any(
            (token.confidence or 0.0) < SECONDARY_TOKEN_MIN_CONFIDENCE
            for token in line.tokens
        ):
            return None
        comparable = (
            str(line.text)
            .translate(_ARABIC_DIGITS)
            .translate({ord(character): " " for character in _BIDI_CONTROLS})
        )
        match = _SECONDARY_AMOUNT_PAIR.fullmatch(comparable)
        if match is None:
            return None
        currency_before = match.group("currency_before")
        currency_after = match.group("currency_after")
        if (currency_before is None) == (currency_after is None):
            return None
        number = match.group("number")
        digits = re.sub(r"[^0-9]", "", number)
        if not 1 <= len(digits) <= 9:
            return None
        currency = re.sub(
            r"[.\s]", "", currency_before or currency_after, flags=re.IGNORECASE
        ).casefold()
        return number.replace(",", ""), currency

    @staticmethod
    def _translate_secondary_line(line: OcrLine, region: _SecondaryRegion) -> OcrLine:
        left, top, _, _ = region.bounding_box

        def translated_box(
            box: tuple[int, int, int, int] | None,
        ) -> tuple[int, int, int, int] | None:
            if box is None:
                return None
            return left + box[0], top + box[1], box[2], box[3]

        return OcrLine(
            line.text,
            line.confidence,
            translated_box(line.bounding_box),
            tuple(
                OcrToken(
                    token.text, token.confidence, translated_box(token.bounding_box)
                )
                for token in line.tokens
            ),
            origin=OcrLineOrigin.SECONDARY,
            ocr_mode="line_crop_psm7",
            source_region=region.bounding_box,
            source_line_index=region.source_line_index,
            parser_eligible=False,
        )

    @staticmethod
    def _secondary_regions(
        image: PreprocessedImage, primary_lines: tuple[OcrLine, ...]
    ) -> tuple[_SecondaryRegion, ...]:
        try:
            with Image.open(io.BytesIO(image.data)) as opened:
                raster = opened.convert("L")
        except (OSError, UnidentifiedImageError):
            return ()
        regions: list[_SecondaryRegion] = []
        for index, line in enumerate(primary_lines):
            if len(regions) >= SECONDARY_MAX_REGIONS:
                break
            if line.bounding_box is None or (
                line.confidence is not None
                and line.confidence >= SECONDARY_LOW_CONFIDENCE_THRESHOLD
            ):
                continue
            bounding_box = TesseractOcrProvider._expand_fragmented_band(
                raster, line.bounding_box
            )
            if bounding_box is None:
                continue
            if any(
                TesseractOcrProvider._overlap_ratio(bounding_box, existing.bounding_box)
                >= 0.80
                for existing in regions
            ):
                continue
            regions.append(_SecondaryRegion(bounding_box, index))
        return tuple(regions)

    @staticmethod
    def _expand_fragmented_band(
        raster: Image.Image, bounding_box: tuple[int, int, int, int]
    ) -> tuple[int, int, int, int] | None:
        left, top, width, height = bounding_box
        if (
            width <= 0
            or height <= 0
            or width > SECONDARY_MAX_CROP_WIDTH
            or height > SECONDARY_MAX_CROP_HEIGHT
        ):
            return None
        right, bottom = left + width, top + height
        if left < 0 or top < 0 or right > raster.width or bottom > raster.height:
            return None
        horizontal_margin = min(320, max(64, width * 2))
        vertical_margin = min(128, max(32, height * 2))
        search_left = max(0, left - horizontal_margin)
        search_right = min(raster.width, right + horizontal_margin)
        search_top = max(0, top - vertical_margin)
        search_bottom = min(raster.height, bottom + vertical_margin)
        if (search_right - search_left) * (
            search_bottom - search_top
        ) > SECONDARY_MAX_CROP_PIXELS * 2:
            return None
        search = raster.crop((search_left, search_top, search_right, search_bottom))
        pixels = search.tobytes()
        minimum_dark_pixels = max(4, search.width // 200)
        active_rows = [
            row
            for row in range(search.height)
            if sum(
                pixels[(row * search.width) + column] < _SECONDARY_DARK_PIXEL_THRESHOLD
                for column in range(search.width)
            )
            >= minimum_dark_pixels
        ]
        if not active_rows:
            return None
        groups: list[tuple[int, int]] = []
        start = previous = active_rows[0]
        for row in active_rows[1:]:
            if row - previous > 3:
                groups.append((start, previous))
                start = row
            previous = row
        groups.append((start, previous))
        target_top = top - search_top
        target_bottom = bottom - search_top
        overlaps = [
            group
            for group in groups
            if group[0] <= target_bottom + 2 and group[1] >= target_top - 2
        ]
        if not overlaps:
            return None
        band_top, band_bottom = max(overlaps, key=lambda group: group[1] - group[0])
        dark_columns = [
            column
            for column in range(search.width)
            if any(
                pixels[(row * search.width) + column] < _SECONDARY_DARK_PIXEL_THRESHOLD
                for row in range(band_top, band_bottom + 1)
            )
        ]
        if not dark_columns:
            return None
        band_height = band_bottom - band_top + 1
        margin_x = min(64, max(16, band_height // 2))
        margin_top = max(2, band_height // 40)
        margin_bottom = min(64, max(12, round(band_height * 0.6)))
        crop_left = max(0, search_left + dark_columns[0] - margin_x)
        crop_right = min(raster.width, search_left + dark_columns[-1] + 1 + margin_x)
        crop_top = max(0, search_top + band_top - margin_top)
        crop_bottom = min(raster.height, search_top + band_bottom + 1 + margin_bottom)
        crop_width, crop_height = crop_right - crop_left, crop_bottom - crop_top
        if (
            crop_width < 48
            or crop_height < 12
            or crop_width > SECONDARY_MAX_CROP_WIDTH
            or crop_height > SECONDARY_MAX_CROP_HEIGHT
            or crop_width * crop_height > SECONDARY_MAX_CROP_PIXELS
        ):
            return None
        return crop_left, crop_top, crop_width, crop_height

    @staticmethod
    def _overlap_ratio(
        first: tuple[int, int, int, int], second: tuple[int, int, int, int]
    ) -> float:
        left = max(first[0], second[0])
        top = max(first[1], second[1])
        right = min(first[0] + first[2], second[0] + second[2])
        bottom = min(first[1] + first[3], second[1] + second[3])
        if right <= left or bottom <= top:
            return 0.0
        intersection = (right - left) * (bottom - top)
        return intersection / min(first[2] * first[3], second[2] * second[3])

    @staticmethod
    def _parse_tsv(path: Path) -> tuple[OcrLine, ...]:
        grouped: dict[
            tuple[int, int, int, int],
            list[OcrToken],
        ] = defaultdict(list)
        with path.open("r", encoding="utf-8", errors="replace", newline="") as source:
            reader = csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
            for index, row in enumerate(reader):
                if index >= MAX_OCR_BLOCKS:
                    raise ProcessingError(ProcessingFailure.RESOURCE_LIMIT)
                text = " ".join((row.get("text") or "").split())[:512]
                if not text:
                    continue
                try:
                    confidence = float(row.get("conf", "-1")) / 100
                    left, top = int(row["left"]), int(row["top"])
                    width, height = int(row["width"]), int(row["height"])
                    key = (
                        int(row["page_num"]),
                        int(row["block_num"]),
                        int(row["par_num"]),
                        int(row["line_num"]),
                    )
                except (KeyError, TypeError, ValueError):
                    raise ProcessingError(ProcessingFailure.OCR_FAILED) from None
                if confidence < 0 or min(left, top, width, height) < 0:
                    continue
                grouped[key].append(
                    OcrToken(
                        text,
                        min(confidence, 1.0),
                        (left, top, width, height),
                    )
                )
        result: list[OcrLine] = []
        for words in grouped.values():
            retained_words: list[OcrToken] = []
            retained_characters = 0
            for word in words:
                required = len(word.text) + (1 if retained_words else 0)
                if retained_characters + required > 512:
                    break
                retained_words.append(word)
                retained_characters += required
            text = " ".join(word.text for word in retained_words)
            confidence = sum(word.confidence or 0.0 for word in retained_words) / len(
                retained_words
            )
            boxes = [
                word.bounding_box
                for word in retained_words
                if word.bounding_box is not None
            ]
            left = min(box[0] for box in boxes)
            top = min(box[1] for box in boxes)
            right = max(box[0] + box[2] for box in boxes)
            bottom = max(box[1] + box[3] for box in boxes)
            result.append(
                OcrLine(
                    text,
                    confidence,
                    (left, top, right - left, bottom - top),
                    tuple(retained_words),
                )
            )
        return tuple(result)


def create_tesseract_provider(
    executable: Path, tessdata_dir: Path | None
) -> TesseractOcrProvider:
    candidate = executable.expanduser()
    if (
        not candidate.is_absolute()
        or tessdata_dir is None
        or not tessdata_dir.is_absolute()
    ):
        raise RuntimeError("Absolute OCR executable and language paths are required.")
    return TesseractOcrProvider(candidate, tessdata_dir)
