"""Static-UI ORB registration; no OCR, transaction values or sample identity.

One fixed matching/geometry policy. A rectangle or highest score is insufficient
to admit a layout. Calibrated ROIs belong to a versioned canonical template.
"""

import importlib
import io
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

from relay.domain.agent import JsonValue
from relay.infrastructure.image_processing import PillowImagePreprocessor
from relay.infrastructure.receipt_geometry import WorkingReceipt

SUPPORTED_PROFILES = ("instapay_compact_bilingual_v1", "instapay_expanded_bilingual_v1")
Box = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class VisualProfile:
    profile: str
    variant: str
    size: tuple[int, int]
    rois: tuple[tuple[str, Box], ...]
    points: tuple[tuple[float, float, int], ...]
    descriptors: tuple[tuple[int, ...], ...]


@dataclass(frozen=True, slots=True)
class RegistrationPolicy:
    minimum_matches: int = 18
    minimum_inliers: int = 12
    minimum_inlier_ratio: float = 0.65
    ratio_test: float = 0.75
    maximum_hamming: int = 64
    minimum_zone_inliers: int = 3
    minimum_x_span: float = 0.25
    minimum_y_span: float = 0.45


FIXED_POLICY = RegistrationPolicy()


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    working: WorkingReceipt | None
    profile: VisualProfile | None
    reason: str
    attempts: tuple[dict[str, JsonValue], ...]

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "method": "orb_mutual_ratio_ransac_v1",
            "reason": self.reason,
            "selected_variant": self.profile.variant if self.profile else None,
            "attempts": list(self.attempts),
            "ocr_used_for_admission": False,
            "rectangle_only_fallback": False,
        }


@lru_cache(maxsize=1)
def load_profiles() -> tuple[VisualProfile, ...]:
    result = []
    directory = Path(__file__).with_name("receipt_profiles")
    for path in sorted(directory.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            value.get("schema_version") != 1
            or value.get("static_pixels_only") is not True
            or value.get("profile") not in SUPPORTED_PROFILES
        ):
            raise ValueError("Invalid visual profile contract")
        size = tuple(value["canonical_size"])
        if len(size) != 2 or not all(100 <= item <= 2400 for item in size):
            raise ValueError("Invalid canonical size")
        rois = []
        for name, box in value["field_rois"].items():
            if len(box) != 4:
                raise ValueError("Invalid canonical ROI")
            x, y, width, height = box
            if not (0 <= x < x + width <= size[0] and 0 <= y < y + height <= size[1]):
                raise ValueError("ROI outside canonical document")
            rois.append((str(name), (int(x), int(y), int(width), int(height))))
        points = tuple((float(p[0]), float(p[1]), int(p[2])) for p in value["points"])
        descriptors = tuple(tuple(int(d) for d in row) for row in value["descriptors"])
        if len(points) != len(descriptors) or any(
            len(row) != 32 for row in descriptors
        ):
            raise ValueError("Invalid ORB descriptors")
        result.append(
            VisualProfile(
                str(value["profile"]),
                str(value["variant"]),
                (size[0], size[1]),
                tuple(rois),
                points,
                descriptors,
            )
        )
    if not result:
        raise ValueError("No calibrated visual profiles installed")
    return tuple(result)


def geometry_rejection(
    matrix: Any, size: tuple[int, int], image_size: tuple[int, int]
) -> str | None:
    """Projective and boundary checks apply identically to every image/profile."""
    np = importlib.import_module("numpy")
    cv = importlib.import_module("cv2")
    if matrix is None or matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        return "bad_homography"
    if abs(float(np.linalg.det(matrix))) < 1e-9:
        return "singular_homography"
    width, height = size
    iw, ih = image_size
    corners = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    denominators = np.c_[corners, np.ones(4)] @ matrix[2]
    if not (np.all(denominators > 0) or np.all(denominators < 0)):
        return "projective_pole"
    if float(np.max(np.abs(denominators)) / np.min(np.abs(denominators))) > 3:
        return "extreme_perspective"
    quad = cv.perspectiveTransform(corners[None], matrix)[0]
    if not np.isfinite(quad).all() or not cv.isContourConvex(quad):
        return "non_convex_receipt"
    signed = float(cv.contourArea(quad, oriented=True))
    if signed <= 0:
        return "reflection_or_degenerate_receipt"
    area_fraction = signed / (iw * ih)
    if not 0.08 <= area_fraction <= 1.12:
        return "implausible_document_area"
    # Reject truncation rather than extrapolating fields outside visible pixels.
    if (
        quad[:, 0].min() < -0.02 * iw
        or quad[:, 1].min() < -0.02 * ih
        or quad[:, 0].max() > 1.02 * iw
        or quad[:, 1].max() > 1.02 * ih
    ):
        return "crop_or_occlusion"
    lengths = [float(np.linalg.norm(quad[(i + 1) % 4] - quad[i])) for i in range(4)]
    if min(lengths) < 40:
        return "degenerate_edge"
    if max(lengths[0], lengths[2]) / min(lengths[0], lengths[2]) > 2:
        return "extreme_perspective"
    if max(lengths[1], lengths[3]) / min(lengths[1], lengths[3]) > 2:
        return "extreme_perspective"
    scales = [lengths[i] / (width if i % 2 == 0 else height) for i in range(4)]
    if min(scales) < 0.15 or max(scales) > 4 or max(scales) / min(scales) > 2.5:
        return "implausible_scale_or_aspect"
    # Corners with near-zero angles are unstable even when area is nonzero.
    for i in range(4):
        a = quad[(i - 1) % 4] - quad[i]
        b = quad[(i + 1) % 4] - quad[i]
        cosine = abs(float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))))
        if cosine > 0.92:
            return "extreme_perspective"
    return None


def unique_registration(
    passed: list[tuple[VisualProfile, Any]],
) -> tuple[VisualProfile, Any] | None:
    """No best-score choice, even between differently located language variants."""
    return passed[0] if len(passed) == 1 else None


def register_receipt(
    data: bytes,
    profiles: tuple[VisualProfile, ...] | None = None,
    policy: RegistrationPolicy = FIXED_POLICY,
) -> RegistrationResult:
    np = importlib.import_module("numpy")
    cv = importlib.import_module("cv2")
    cv.setNumThreads(1)
    cv.setRNGSeed(1729)
    prepared = PillowImagePreprocessor().preprocess(data)
    with Image.open(io.BytesIO(data)) as original:
        original_size = original.size
        mapping_available = original.getexif().get(274, 1) == 1
    raster = Image.open(io.BytesIO(prepared.data)).convert("L")
    array = np.asarray(raster)
    detector = cv.ORB_create(nfeatures=5000)
    keys, descriptors = detector.detectAndCompute(array, None)
    matcher = cv.BFMatcher(cv.NORM_HAMMING)
    attempts: list[dict[str, JsonValue]] = []
    passed: list[tuple[VisualProfile, Any]] = []
    for profile in profiles if profiles is not None else load_profiles():
        diagnostic: dict[str, JsonValue] = {
            "profile": profile.profile,
            "variant": profile.variant,
            "good_matches": 0,
            "inliers": 0,
            "inlier_ratio": 0.0,
            "passed": False,
            "reason": "insufficient_visual_matches",
        }
        attempts.append(diagnostic)
        if descriptors is None or len(descriptors) < 2 or len(profile.descriptors) < 2:
            continue
        template = np.asarray(profile.descriptors, dtype=np.uint8)
        forward = matcher.knnMatch(template, descriptors, k=2)
        reverse = matcher.match(descriptors, template)
        mutual = {match.queryIdx: match.trainIdx for match in reverse}
        good = [
            pair[0]
            for pair in forward
            if len(pair) == 2
            and pair[0].distance < policy.ratio_test * pair[1].distance
            and pair[0].distance <= policy.maximum_hamming
            and mutual.get(pair[0].trainIdx) == pair[0].queryIdx
        ]
        diagnostic["good_matches"] = len(good)
        if len(good) < policy.minimum_matches:
            continue
        src = np.float32([profile.points[m.queryIdx][:2] for m in good])
        dst = np.float32([keys[m.trainIdx].pt for m in good])
        matrix, mask = cv.findHomography(
            src, dst, cv.RANSAC, 5.0, maxIters=2000, confidence=0.995
        )
        if matrix is None or mask is None:
            diagnostic["reason"] = "homography_failure"
            continue
        flags = mask.reshape(-1).astype(bool)
        count = int(flags.sum())
        ratio = count / len(good)
        diagnostic.update(inliers=count, inlier_ratio=round(ratio, 6))
        if count < policy.minimum_inliers or ratio < policy.minimum_inlier_ratio:
            diagnostic["reason"] = "homography_failure"
            continue
        inlier_points = src[flags]
        zones = {
            zone: sum(
                bool(flag) and profile.points[m.queryIdx][2] == zone
                for m, flag in zip(good, flags, strict=True)
            )
            for zone in (1, 2, 3)
        }
        diagnostic["zone_inliers"] = {
            str(zone): number for zone, number in zones.items()
        }
        if (
            any(number < policy.minimum_zone_inliers for number in zones.values())
            or float(np.ptp(inlier_points[:, 0])) / profile.size[0]
            < policy.minimum_x_span
            or float(np.ptp(inlier_points[:, 1])) / profile.size[1]
            < policy.minimum_y_span
        ):
            diagnostic["reason"] = "insufficient_spatial_support"
            continue
        rejected = geometry_rejection(matrix, profile.size, raster.size)
        if rejected:
            diagnostic["reason"] = "geometric_rejection"
            diagnostic["detail"] = rejected
            continue
        inverse = np.linalg.inv(matrix)
        projected = cv.perspectiveTransform(src[None], matrix)[0]
        residual = float(
            np.median(np.linalg.norm(projected[flags] - dst[flags], axis=1))
        )
        diagnostic["median_reprojection_error_px"] = round(residual, 4)
        diagnostic["canonical_to_input"] = [[float(v) for v in row] for row in matrix]
        # Ensure every mapped ROI is supported by actual pixels, never border fill.
        visibility = cv.warpPerspective(
            np.ones_like(array, dtype=np.uint8),
            inverse,
            profile.size,
            flags=cv.INTER_NEAREST,
            borderValue=0,
        )
        if any(
            float(visibility[y : y + h, x : x + w].mean()) < 0.99
            for _, (x, y, w, h) in profile.rois
        ):
            diagnostic["reason"] = "geometric_rejection"
            diagnostic["detail"] = "roi_outside_visible_pixels"
            continue
        diagnostic.update(passed=True, reason="admitted")
        passed.append((profile, matrix))
    selected = unique_registration(passed)
    if selected is None:
        if len(passed) > 1:
            reason = "ambiguous_layout"
        else:
            reasons = {attempt["reason"] for attempt in attempts}
            reason = next(
                (
                    r
                    for r in (
                        "geometric_rejection",
                        "homography_failure",
                        "insufficient_spatial_support",
                    )
                    if r in reasons
                ),
                "insufficient_visual_matches",
            )
        return RegistrationResult(None, None, reason, tuple(attempts))
    profile, matrix = selected
    canonical = cv.warpPerspective(
        array, np.linalg.inv(matrix), profile.size, borderValue=255
    )
    original_scale = np.diag(
        [original_size[0] / raster.width, original_size[1] / raster.height, 1.0]
    )
    working = WorkingReceipt(
        Image.fromarray(canonical),
        original_scale @ matrix,
        original_size,
        (*prepared.transformations, "visual_homography_canonical_v1"),
        mapping_available,
    )
    return RegistrationResult(working, profile, "admitted", tuple(attempts))
