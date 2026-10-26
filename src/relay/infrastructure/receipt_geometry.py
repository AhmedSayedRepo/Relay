"""Bounded CPU screen geometry, with explicit inverse evidence mapping."""

import importlib
import io
from dataclasses import dataclass
from typing import Any

from PIL import Image

from relay.infrastructure.image_processing import PillowImagePreprocessor


@dataclass(frozen=True, slots=True)
class WorkingReceipt:
    raster: Image.Image
    inverse_matrix: Any
    original_size: tuple[int, int]
    transformations: tuple[str, ...]
    original_mapping_available: bool

    def polygon(
        self, box: tuple[int, int, int, int]
    ) -> tuple[tuple[float, float], ...]:
        if not self.original_mapping_available:
            return ()
        np = importlib.import_module("numpy")
        cv2 = importlib.import_module("cv2")
        x, y, width, height = box
        corners = np.array(
            [[[x, y], [x + width, y], [x + width, y + height], [x, y + height]]],
            dtype=np.float32,
        )
        mapped = cv2.perspectiveTransform(corners, self.inverse_matrix)[0]
        return tuple((float(point[0]), float(point[1])) for point in mapped)


def normalize_receipt(data: bytes) -> WorkingReceipt:
    """Fixed geometric detector; a quadrilateral alone never proves a layout."""
    prepared = PillowImagePreprocessor().preprocess(data)
    with Image.open(io.BytesIO(data)) as original:
        original_size = original.size
        exif_orientation = original.getexif().get(274, 1)
    np = importlib.import_module("numpy")
    cv2 = importlib.import_module("cv2")
    cv2.setNumThreads(1)
    raster = Image.open(io.BytesIO(prepared.data)).convert("L")
    array = np.asarray(raster)
    height, width = array.shape
    # Bound geometry detection separately from the original targeted recognition.
    scale = min(1.0, 800.0 / max(width, height))
    small = cv2.resize(array, (round(width * scale), round(height * scale)))
    edges = cv2.Canny(small, 60, 180)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    quads = []
    for contour in contours:
        fraction = cv2.contourArea(contour) / (small.shape[0] * small.shape[1])
        if not 0.30 <= fraction <= 0.94:
            continue
        points = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(points) != 4 or not cv2.isContourConvex(points):
            continue
        points = points.reshape(4, 2).astype(np.float32) / scale
        sums = points.sum(axis=1)
        differences = np.diff(points, axis=1).reshape(-1)
        ordered = np.array(
            [
                points[sums.argmin()],
                points[differences.argmin()],
                points[sums.argmax()],
                points[differences.argmax()],
            ],
            dtype=np.float32,
        )
        if len({tuple(point) for point in ordered}) != 4:
            continue
        quad_width = max(
            np.linalg.norm(ordered[1] - ordered[0]),
            np.linalg.norm(ordered[2] - ordered[3]),
        )
        quad_height = max(
            np.linalg.norm(ordered[3] - ordered[0]),
            np.linalg.norm(ordered[2] - ordered[1]),
        )
        ratio = min(quad_width, quad_height) / max(quad_width, quad_height)
        center = ordered.mean(axis=0)
        if (
            0.35 <= ratio <= 0.85
            and 0.2 * width <= center[0] <= 0.8 * width
            and 0.2 * height <= center[1] <= 0.8 * height
        ):
            quads.append((fraction, ordered, round(quad_width), round(quad_height)))
    matrix = np.eye(3, dtype=np.float64)
    transformations = list(prepared.transformations)
    if quads:
        _, points, out_width, out_height = max(quads, key=lambda item: item[0])
        destination = np.array(
            [
                [0, 0],
                [out_width - 1, 0],
                [out_width - 1, out_height - 1],
                [0, out_height - 1],
            ],
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(points, destination)
        array = cv2.warpPerspective(
            array, matrix, (out_width, out_height), borderMode=cv2.BORDER_REPLICATE
        )
        raster = Image.fromarray(array)
        transformations.append("screen_quadrilateral_perspective")
    # EXIF-rotated originals deliberately suppress an unproven overlay mapping;
    # extraction geometry remains available in the declared working coordinate space.
    original_scale = np.diag([original_size[0] / width, original_size[1] / height, 1.0])
    inverse = original_scale @ np.linalg.inv(matrix)
    return WorkingReceipt(
        raster, inverse, original_size, tuple(transformations), exif_orientation == 1
    )


def rotated_receipt(image: WorkingReceipt, angle: int) -> WorkingReceipt:
    if angle == 0:
        return image
    if angle not in {90, 180, 270}:
        raise ValueError("Unsupported receipt orientation")
    np = importlib.import_module("numpy")
    width, height = image.raster.size
    # PIL positive rotations are counterclockwise, with pixel-boundary mapping.
    matrices = {
        90: [[0, -1, width], [1, 0, 0], [0, 0, 1]],
        180: [[-1, 0, width], [0, -1, height], [0, 0, 1]],
        270: [[0, 1, 0], [-1, 0, height], [0, 0, 1]],
    }
    inverse = image.inverse_matrix @ np.array(matrices[angle], dtype=np.float64)
    return WorkingReceipt(
        image.raster.rotate(angle, expand=True),
        inverse,
        image.original_size,
        (*image.transformations, f"rotation_{angle}"),
        image.original_mapping_available,
    )
