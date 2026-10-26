"""Bounded deterministic image preprocessing; originals remain immutable."""

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from relay.application.processing_errors import ProcessingError
from relay.domain.processing import PreprocessedImage, ProcessingFailure

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_DIMENSION = 12_000
MAX_SOURCE_PIXELS = 20_000_000
MAX_DECODED_BYTES = 80 * 1024 * 1024
MAX_OUTPUT_DIMENSION = 2_400
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class PillowImagePreprocessor:
    def preprocess(self, data: bytes) -> PreprocessedImage:
        if not data or len(data) > MAX_SOURCE_BYTES:
            raise ProcessingError(ProcessingFailure.RESOURCE_LIMIT)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS
                with Image.open(io.BytesIO(data)) as probe:
                    if probe.format not in ALLOWED_FORMATS:
                        raise ProcessingError(ProcessingFailure.UNSUPPORTED_IMAGE)
                    self._check_dimensions(probe.width, probe.height)
                    probe.verify()
                with Image.open(io.BytesIO(data)) as source:
                    self._check_dimensions(source.width, source.height)
                    ImageOps.exif_transpose(source, in_place=True)
                    oriented = source
                    transformations = ["exif_orientation"]
                    if (
                        oriented.width > MAX_OUTPUT_DIMENSION
                        or oriented.height > MAX_OUTPUT_DIMENSION
                    ):
                        oriented.thumbnail(
                            (MAX_OUTPUT_DIMENSION, MAX_OUTPUT_DIMENSION),
                            Image.Resampling.LANCZOS,
                        )
                        transformations.append("bounded_resize")
                    grayscale = ImageOps.grayscale(oriented)
                    normalized = ImageOps.autocontrast(grayscale, cutoff=1)
                    transformations.extend(("grayscale", "autocontrast_1_percent"))
                    output = io.BytesIO()
                    normalized.save(
                        output, format="PNG", optimize=False, compress_level=6
                    )
                    value = output.getvalue()
                    if len(value) > MAX_OUTPUT_BYTES:
                        raise ProcessingError(ProcessingFailure.RESOURCE_LIMIT)
                    return PreprocessedImage(
                        value,
                        "image/png",
                        normalized.width,
                        normalized.height,
                        tuple(transformations),
                    )
        except ProcessingError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ProcessingError(ProcessingFailure.RESOURCE_LIMIT) from None
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
            raise ProcessingError(ProcessingFailure.IMAGE_DECODE_FAILED) from None

    @staticmethod
    def _check_dimensions(width: int, height: int) -> None:
        if (
            not 1 <= width <= MAX_SOURCE_DIMENSION
            or not 1 <= height <= MAX_SOURCE_DIMENSION
            or width * height > MAX_SOURCE_PIXELS
            or width * height * 4 > MAX_DECODED_BYTES
        ):
            raise ProcessingError(ProcessingFailure.RESOURCE_LIMIT)
