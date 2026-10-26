"""Template-tolerant visual text-band discovery for the offline OCR spike."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from PIL import Image, ImageChops, ImageFilter, ImageOps

from tools.ocr_spike.models import (
    Anchor,
    Box,
    CandidateBand,
    DiscoveryResult,
    RegionProposal,
    WorkingRegion,
)


@dataclass(frozen=True, slots=True)
class DiscoveryBounds:
    max_bands: int = 10
    max_ocr_passes: int = 8
    min_width_ratio: float = 0.04
    max_width_ratio: float = 0.96
    min_height_ratio: float = 0.006
    max_height_ratio: float = 0.12
    min_density: float = 0.010
    max_density: float = 0.95
    max_total_crop_ratio: float = 0.50
    timeout_seconds: float = 20.0


def _otsu_threshold(image: Image.Image) -> int:
    histogram = image.histogram()
    total = sum(histogram)
    weighted_total = sum(index * count for index, count in enumerate(histogram))
    foreground = 0
    weighted_foreground = 0
    best_variance = -1.0
    best = 180
    for threshold, count in enumerate(histogram):
        foreground += count
        if foreground == 0:
            continue
        background = total - foreground
        if background == 0:
            break
        weighted_foreground += threshold * count
        foreground_mean = weighted_foreground / foreground
        background_mean = (weighted_total - weighted_foreground) / background
        variance = foreground * background * (foreground_mean - background_mean) ** 2
        if variance > best_variance:
            best_variance = variance
            best = threshold
    return min(225, max(80, best))


def _groups(values: list[int], max_gap: int) -> list[tuple[int, int]]:
    if not values:
        return []
    grouped: list[tuple[int, int]] = []
    start = previous = values[0]
    for value in values[1:]:
        if value - previous > max_gap:
            grouped.append((start, previous))
            start = value
        previous = value
    grouped.append((start, previous))
    return grouped


def _anchor_support(
    box: Box, anchors: tuple[Anchor, ...], image_height: int
) -> tuple[str, ...]:
    supported: list[str] = []
    vertical_limit = max(24, round(image_height * 0.16))
    for anchor in anchors:
        vertical_distance = min(
            abs(box.top - anchor.box.bottom),
            abs(anchor.box.top - box.bottom),
            abs(
                (box.top + box.height // 2) - (anchor.box.top + anchor.box.height // 2)
            ),
        )
        if vertical_distance <= vertical_limit:
            supported.append(anchor.kind)
    return tuple(sorted(set(supported)))


def estimate_working_region(
    image: Image.Image, anchors: tuple[Anchor, ...] = ()
) -> WorkingRegion:
    """Estimate a display/document x-range without requiring a phone template.

    Low-resolution vertical boundary contrast supplies the independent estimate.
    OCR anchors may validate it, but low-confidence estimates are reported and
    deliberately not applied.
    """

    grayscale = ImageOps.grayscale(image)
    width, height = grayscale.size
    sample_width = min(128, width)
    sample_height = min(160, height)
    sample = grayscale.resize(
        (sample_width, sample_height), Image.Resampling.BILINEAR
    ).filter(ImageFilter.GaussianBlur(radius=1.2))
    pixels = sample.load()
    assert pixels is not None

    top = round(sample_height * 0.06)
    bottom = max(top + 1, round(sample_height * 0.94))
    column_means = [
        sum(cast(int, pixels[column, row]) for row in range(top, bottom))
        / (bottom - top)
        for column in range(sample_width)
    ]
    gradients = [0.0] + [
        abs(column_means[index] - column_means[index - 1])
        for index in range(1, sample_width)
    ]
    smoothed = [
        sum(gradients[max(0, index - 2) : min(sample_width, index + 3)])
        / len(gradients[max(0, index - 2) : min(sample_width, index + 3)])
        for index in range(sample_width)
    ]
    left_limit = max(2, round(sample_width * 0.43))
    right_start = min(sample_width - 2, round(sample_width * 0.57))
    left_index = max(range(1, left_limit), key=smoothed.__getitem__)
    right_index = max(range(right_start, sample_width - 1), key=smoothed.__getitem__)

    inside = column_means[left_index:right_index]
    outside = column_means[:left_index] + column_means[right_index:]
    interior_advantage = (
        (sum(inside) / len(inside)) - (sum(outside) / len(outside))
        if inside and outside
        else 0.0
    )
    boundary_strength = (smoothed[left_index] + smoothed[right_index]) / 2
    width_ratio = (right_index - left_index) / sample_width
    confidence = min(
        1.0,
        max(0.0, boundary_strength / 24.0) + max(0.0, interior_advantage) / 90.0,
    )
    scaled_left = round(left_index * width / sample_width)
    scaled_right = round(right_index * width / sample_width)
    provenance = "visual_vertical_boundaries"

    credible_anchors = [
        anchor
        for anchor in anchors
        if anchor.confidence >= 0.45 and anchor.box.width > 0 and anchor.box.height > 0
    ]
    if len(credible_anchors) >= 2:
        anchor_left = min(anchor.box.left for anchor in credible_anchors)
        anchor_right = max(anchor.box.right for anchor in credible_anchors)
        margin = round(width * 0.08)
        anchor_left = max(0, anchor_left - margin)
        anchor_right = min(width, anchor_right + margin)
        if anchor_left < scaled_right and anchor_right > scaled_left:
            scaled_left = min(scaled_left, anchor_left)
            scaled_right = max(scaled_right, anchor_right)
            confidence = min(1.0, confidence + 0.18)
            provenance = "visual_vertical_boundaries+anchor_validation"

    applied = confidence >= 0.45 and 0.45 <= width_ratio <= 0.96
    if not applied:
        return WorkingRegion(Box(0, 0, width, height), confidence, provenance, False)

    margin = round(width * 0.015)
    left = max(0, scaled_left - margin)
    right = min(width, scaled_right + margin)
    if right - left < width * 0.45:
        return WorkingRegion(Box(0, 0, width, height), confidence, provenance, False)
    return WorkingRegion(
        Box(left, 0, right - left, height), confidence, provenance, True
    )


def _projection_candidates(
    signal: Image.Image,
    working: Box,
    anchors: tuple[Anchor, ...],
    bounds: DiscoveryBounds,
    provenance: str,
    row_fraction: float,
    column_fraction: float,
) -> list[CandidateBand]:
    width, height = signal.size
    working_crop = signal.crop(
        (working.left, working.top, working.right, working.bottom)
    )
    row_projection = list(
        working_crop.resize((1, working.height), Image.Resampling.BOX).tobytes()
    )
    row_baseline = sorted(row_projection)[round((len(row_projection) - 1) * 0.55)]
    row_threshold = max(round(255 * row_fraction), row_baseline + 3)
    active_rows = [
        working.top + index
        for index, value in enumerate(row_projection)
        if value >= row_threshold
    ]
    row_groups = _groups(active_rows, max(2, round(height * 0.0035)))
    min_height = max(6, round(height * bounds.min_height_ratio))
    max_height = max(min_height, round(height * bounds.max_height_ratio))
    min_width = max(24, round(width * bounds.min_width_ratio))
    max_width = max(min_width, round(width * bounds.max_width_ratio))
    margin_y = max(3, round(height * 0.004))
    margin_x = max(8, round(width * 0.012))
    candidates: list[CandidateBand] = []

    for row_start, row_end in row_groups:
        core_height = row_end - row_start + 1
        if core_height > max_height:
            continue
        strip = signal.crop((working.left, row_start, working.right, row_end + 1))
        column_projection = strip.resize(
            (working.width, 1), Image.Resampling.BOX
        ).tobytes()
        active_columns = [
            working.left + index
            for index, value in enumerate(column_projection)
            if value / 255 >= column_fraction
        ]
        for column_start, column_end in _groups(
            active_columns, max(10, round(width * 0.035))
        ):
            left = max(working.left, column_start - margin_x)
            right = min(working.right, column_end + 1 + margin_x)
            top = max(working.top, row_start - margin_y)
            bottom = min(working.bottom, row_end + 1 + margin_y)
            box = Box(left, top, right - left, bottom - top)
            if not (
                min_width <= box.width <= max_width
                and min_height <= box.height <= max_height
            ):
                continue
            core = signal.crop((column_start, row_start, column_end + 1, row_end + 1))
            density = (
                cast(
                    int,
                    core.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0)),
                )
                / 255
            )
            if not bounds.min_density <= density <= bounds.max_density:
                continue
            support = _anchor_support(box, anchors, height)
            aspect = box.width / max(1, box.height)
            line_likeness = min(1.0, aspect / 7.0)
            density_quality = max(0.0, 1.0 - abs(density - 0.30) / 0.48)
            score = min(
                1.0,
                density_quality * 0.50
                + line_likeness * 0.32
                + min(0.18, len(support) * 0.045),
            )
            candidates.append(CandidateBand(box, score, density, provenance, support))
    return candidates


def _legacy_global_candidates(
    grayscale: Image.Image,
    working: Box,
    anchors: tuple[Anchor, ...],
    bounds: DiscoveryBounds,
) -> list[CandidateBand]:
    smoothed = ImageOps.autocontrast(grayscale, cutoff=1).filter(
        ImageFilter.MaxFilter(3)
    )
    threshold = max(180, _otsu_threshold(smoothed))
    width, height = smoothed.size
    pixels = smoothed.load()
    assert pixels is not None
    minimum_row_dark = max(3, round(working.width * 0.0025))
    active_rows = [
        row
        for row in range(working.top, working.bottom)
        if sum(
            1
            for column in range(working.left, working.right)
            if cast(int, pixels[column, row]) < threshold
        )
        >= minimum_row_dark
    ]
    row_groups = _groups(active_rows, max(2, round(height * 0.0025)))
    min_height = max(6, round(height * bounds.min_height_ratio))
    max_height = max(min_height, round(height * bounds.max_height_ratio))
    min_width = max(24, round(width * bounds.min_width_ratio))
    max_width = max(min_width, round(width * bounds.max_width_ratio))
    margin_y = max(3, round(height * 0.004))
    margin_x = max(8, round(width * 0.012))
    candidates: list[CandidateBand] = []
    for row_start, row_end in row_groups:
        dark_columns = [
            column
            for column in range(working.left, working.right)
            if any(
                cast(int, pixels[column, row]) < threshold
                for row in range(row_start, row_end + 1)
            )
        ]
        for column_start, column_end in _groups(
            dark_columns, max(8, round(width * 0.025))
        ):
            left = max(working.left, column_start - margin_x)
            right = min(working.right, column_end + 1 + margin_x)
            top = max(working.top, row_start - margin_y)
            bottom = min(working.bottom, row_end + 1 + margin_y)
            box = Box(left, top, right - left, bottom - top)
            if not (
                min_width <= box.width <= max_width
                and min_height <= box.height <= max_height
            ):
                continue
            dark_count = sum(
                1
                for row in range(row_start, row_end + 1)
                for column in range(column_start, column_end + 1)
                if cast(int, pixels[column, row]) < threshold
            )
            core_area = max(
                1,
                (column_end - column_start + 1) * (row_end - row_start + 1),
            )
            density = dark_count / core_area
            if not bounds.min_density <= density <= bounds.max_density:
                continue
            support = _anchor_support(box, anchors, height)
            score = min(
                1.0,
                density * 2.2 + (box.width / width) * 0.45 + len(support) * 0.04,
            )
            candidates.append(
                CandidateBand(
                    box,
                    score,
                    density,
                    "legacy_global_projection",
                    support,
                )
            )
    return candidates


def _tighten_vertical_to_peak(signal: Image.Image, box: Box, image_height: int) -> Box:
    row_values = list(
        signal.crop((box.left, box.top, box.right, box.bottom))
        .resize((1, box.height), Image.Resampling.BOX)
        .tobytes()
    )
    if not row_values:
        return box
    threshold = max(18, round(max(row_values) * 0.30))
    active = [index for index, value in enumerate(row_values) if value >= threshold]
    groups = _groups(active, max(2, round(box.height * 0.025)))
    if not groups:
        return box
    start, end = max(
        groups,
        key=lambda group: (
            group[1] - group[0] + 1,
            sum(row_values[group[0] : group[1] + 1]),
        ),
    )
    if end - start + 1 < box.height * 0.28:
        return box
    margin = max(round(image_height * 0.006), round((end - start + 1) * 0.28))
    top = max(0, box.top + start - margin)
    bottom = min(image_height, box.top + end + 1 + margin)
    return Box(box.left, top, box.width, bottom - top)


def _merge_line_candidates(
    candidates: list[CandidateBand], image_width: int, image_height: int
) -> list[CandidateBand]:
    """Merge fragments on a common baseline before ranking OCR crops."""

    ordered = sorted(candidates, key=lambda item: (item.box.top, item.box.left))
    merged: list[CandidateBand] = []
    for candidate in ordered:
        match_index: int | None = None
        candidate_center = candidate.box.top + candidate.box.height / 2
        for index, existing in enumerate(merged):
            existing_center = existing.box.top + existing.box.height / 2
            center_gap = abs(candidate_center - existing_center)
            height_limit = max(candidate.box.height, existing.box.height) * 0.58
            vertical_overlap = max(
                0,
                min(candidate.box.bottom, existing.box.bottom)
                - max(candidate.box.top, existing.box.top),
            )
            horizontal_gap = max(
                0,
                max(candidate.box.left, existing.box.left)
                - min(candidate.box.right, existing.box.right),
            )
            if (
                vertical_overlap > 0 or center_gap <= height_limit
            ) and horizontal_gap <= max(18, round(image_width * 0.20)):
                match_index = index
                break
        if match_index is None:
            merged.append(candidate)
            continue
        existing = merged[match_index]
        if candidate.box.overlap_ratio(existing.box) >= 0.75:
            # Once fragments have been combined into a row, a nested signal
            # from another detector must not collapse that row back to one
            # word. Keep the containing geometry while merging provenance.
            preferred = max((existing, candidate), key=lambda item: item.box.area)
            provenance = "+".join(
                sorted(
                    set(
                        existing.provenance.split("+") + candidate.provenance.split("+")
                    )
                )
            )
            merged[match_index] = CandidateBand(
                preferred.box,
                min(1.0, max(existing.score, candidate.score) + 0.035),
                max(existing.density, candidate.density),
                provenance,
                tuple(sorted(set(existing.anchor_support + candidate.anchor_support))),
            )
            continue
        left = min(existing.box.left, candidate.box.left)
        top = min(existing.box.top, candidate.box.top)
        right = max(existing.box.right, candidate.box.right)
        bottom = max(existing.box.bottom, candidate.box.bottom)
        box = Box(left, top, right - left, bottom - top)
        if box.height > image_height * 0.12:
            merged.append(candidate)
            continue
        provenance = "+".join(
            sorted(
                set(existing.provenance.split("+") + candidate.provenance.split("+"))
            )
        )
        merged[match_index] = CandidateBand(
            box,
            min(1.0, max(existing.score, candidate.score) + 0.035),
            max(existing.density, candidate.density),
            provenance,
            tuple(sorted(set(existing.anchor_support + candidate.anchor_support))),
        )
    return merged


def _signal_score(signal: Image.Image, box: Box) -> float:
    if box.width <= 0 or box.height <= 0:
        return 0.0
    value = cast(
        int,
        signal.crop((box.left, box.top, box.right, box.bottom))
        .resize((1, 1), Image.Resampling.BOX)
        .getpixel((0, 0)),
    )
    return value / 255


def _ranking_score(candidate: CandidateBand, working: Box, image_height: int) -> float:
    """Rank field-independent row salience without inspecting OCR values."""

    width_ratio = candidate.box.width / max(1, working.width)
    height_ratio = candidate.box.height / max(1, image_height)
    aspect = candidate.box.width / max(1, candidate.box.height)
    candidate_center = candidate.box.left + candidate.box.width / 2
    working_center = working.left + working.width / 2
    centeredness = max(
        0.0,
        1.0 - abs(candidate_center - working_center) / max(1, working.width / 2),
    )
    row_salience = min(1.0, height_ratio / 0.055)
    line_coherence = min(1.0, aspect / 4.0)
    if aspect > 6:
        line_coherence *= max(0.0, 1.0 - (aspect - 6) / 8)
    density_quality = max(0.0, 1.0 - abs(candidate.density - 0.34) / 0.50)
    detector_agreement = min(1.0, len(candidate.provenance.split("+")) / 3)
    context_bonus = 0.0
    if "context_expansion" in candidate.provenance:
        context_bonus = 0.14 if 0.40 <= width_ratio <= 0.70 else 0.04
    excessive_width_penalty = max(0.0, (width_ratio - 0.78) / 0.22) * 0.28
    excessive_aspect_penalty = max(0.0, (aspect - 6) / 8) * 0.18
    return max(
        0.0,
        min(
            1.0,
            candidate.score * 0.30
            + row_salience * 0.24
            + centeredness * 0.18
            + line_coherence * 0.10
            + density_quality * 0.10
            + detector_agreement * 0.08
            + context_bonus
            - excessive_width_penalty
            - excessive_aspect_penalty,
        ),
    )


def discover_text_bands_with_diagnostics(
    image: Image.Image,
    anchors: tuple[Anchor, ...],
    bounds: DiscoveryBounds,
    working_region: WorkingRegion | None = None,
) -> DiscoveryResult:
    """Find bounded text rows and retain every pre-selection proposal."""

    grayscale = ImageOps.grayscale(image)
    width, height = grayscale.size
    region = working_region or estimate_working_region(grayscale, anchors)
    working = region.box

    radius = max(7, min(31, round(min(width, height) * 0.018)))
    local_background = grayscale.filter(ImageFilter.BoxBlur(radius=radius))
    local_darkness = ImageChops.subtract(local_background, grayscale)
    local_darkness = ImageOps.autocontrast(local_darkness, cutoff=2)
    histogram = local_darkness.histogram()
    total = sum(histogram)
    accumulated = 0
    darkness_threshold = 18
    for value, count in enumerate(histogram):
        accumulated += count
        if accumulated >= total * 0.89:
            darkness_threshold = max(12, min(64, value))
            break
    adaptive_signal = local_darkness.point(
        lambda value: 255 if value >= darkness_threshold else 0
    )

    blurred = grayscale.filter(ImageFilter.GaussianBlur(radius=1.8))
    high_frequency = ImageChops.difference(grayscale, blurred)
    edge_histogram = high_frequency.histogram()
    edge_total = sum(edge_histogram)
    edge_accumulated = 0
    edge_threshold = 6
    for value, count in enumerate(edge_histogram):
        edge_accumulated += count
        if edge_accumulated >= edge_total * 0.91:
            edge_threshold = max(5, min(34, value))
            break
    edge_signal = high_frequency.point(
        lambda value: 255 if value >= edge_threshold else 0
    )

    raw_candidates = _projection_candidates(
        adaptive_signal,
        working,
        anchors,
        bounds,
        "adaptive_local_darkness",
        row_fraction=0.006,
        column_fraction=0.045,
    )
    raw_candidates.extend(
        _projection_candidates(
            edge_signal,
            working,
            anchors,
            bounds,
            "local_gradient_density",
            row_fraction=0.014,
            column_fraction=0.08,
        )
    )

    # Preserve the Iteration 1 darkness projection as a supplemental signal.
    # Local methods do not depend on it, but its precise clean-screen bands are
    # valuable when local normalization produces a larger nested row.
    globally_smoothed = ImageOps.autocontrast(grayscale, cutoff=1).filter(
        ImageFilter.MaxFilter(3)
    )
    global_threshold = max(180, _otsu_threshold(globally_smoothed))
    global_signal = globally_smoothed.point(
        lambda value: 255 if value < global_threshold else 0
    )
    global_candidates = _projection_candidates(
        global_signal,
        working,
        anchors,
        bounds,
        "global_darkness_supplement",
        row_fraction=0.003,
        column_fraction=0.025,
    )
    legacy_candidates = _legacy_global_candidates(
        grayscale,
        working,
        anchors,
        bounds,
    )
    raw_candidates.extend(global_candidates)
    raw_candidates.extend(legacy_candidates)

    proposals: list[RegionProposal] = []

    def record(
        candidate: CandidateBand,
        stage: str,
        rejection_reason: str | None = None,
    ) -> RegionProposal:
        proposal = RegionProposal(
            proposal_id=f"proposal-{len(proposals) + 1:04d}",
            box=candidate.box,
            detector_source=candidate.provenance,
            local_darkness_score=_signal_score(adaptive_signal, candidate.box),
            gradient_score=_signal_score(edge_signal, candidate.box),
            text_density_score=candidate.density,
            grouping_information={
                "stage": stage,
                "width_ratio": candidate.box.width / max(1, width),
                "height_ratio": candidate.box.height / max(1, height),
                "aspect_ratio": candidate.box.width / max(1, candidate.box.height),
            },
            anchor_influence=candidate.anchor_support,
            ranking_score=_ranking_score(candidate, working, height),
            rejection_reason=rejection_reason,
        )
        proposals.append(proposal)
        return proposal

    for candidate in raw_candidates:
        record(candidate, "detector_output", "merged")

    candidates = _merge_line_candidates(raw_candidates, width, height)
    working_center = working.left + working.width / 2
    salient_unmerged = [
        CandidateBand(
            candidate.box,
            candidate.score,
            candidate.density,
            f"{candidate.provenance}+unmerged_salient",
            candidate.anchor_support,
        )
        for candidate in raw_candidates
        if 0.14 <= candidate.box.width / max(1, working.width) <= 0.62
        and 0.022 <= candidate.box.height / max(1, height) <= 0.11
        and 1.5 <= candidate.box.width / max(1, candidate.box.height) <= 8.0
        and abs((candidate.box.left + candidate.box.width / 2) - working_center)
        <= working.width * 0.28
    ]
    candidates.extend(salient_unmerged)

    contextual: list[CandidateBand] = []
    max_width = round(width * bounds.max_width_ratio)
    max_height = round(height * bounds.max_height_ratio)
    for candidate in candidates:
        box = candidate.box
        if "global_darkness_supplement" in candidate.provenance:
            box = _tighten_vertical_to_peak(global_signal, box, height)
        aspect = box.width / max(1, box.height)
        if (
            candidate.density >= 0.16
            and 1.3 <= aspect <= 6.5
            and box.width <= width * 0.45
        ):
            wider_context = min(
                round(width * 0.22),
                max(round(box.height * 2.0), round(width * 0.04)),
            )
            narrow_context = max(
                round(width * 0.012),
                round(box.height * 0.25),
            )
            left_space = box.left - working.left
            right_space = working.right - box.right
            if left_space >= right_space:
                left_context, right_context = wider_context, narrow_context
            else:
                left_context, right_context = narrow_context, wider_context
            vertical = max(3, round(box.height * 0.12))
            left = max(working.left, box.left - left_context)
            right = min(working.right, box.right + right_context)
            top = max(working.top, box.top - vertical)
            bottom = min(working.bottom, box.bottom + vertical)
            expanded = Box(left, top, right - left, bottom - top)
            if expanded.width <= max_width and expanded.height <= max_height:
                contextual.append(
                    CandidateBand(
                        expanded,
                        min(1.0, candidate.score + 0.05),
                        candidate.density,
                        "visual_context_expansion",
                        candidate.anchor_support,
                    )
                )
    candidates.extend(contextual)

    # Preserve compact global-signal context crops as alternatives to merged
    # rows. This keeps useful whitespace for PSM 7 without overwriting or
    # deleting the merged local evidence.
    global_contextual: list[CandidateBand] = []
    for candidate in legacy_candidates:
        box = candidate.box
        aspect = box.width / max(1, box.height)
        if (
            candidate.density >= 0.25
            and 1.5 <= aspect <= 5.0
            and box.width <= width * 0.45
        ):
            horizontal = min(
                round(width * 0.22),
                max(round(box.height * 2.0), round(width * 0.04)),
            )
            vertical = max(3, round(box.height * 0.16))
            left = max(working.left, box.left - horizontal)
            right = min(working.right, box.right + horizontal)
            top = max(working.top, box.top - vertical)
            bottom = min(working.bottom, box.bottom + vertical)
            expanded = Box(left, top, right - left, bottom - top)
            if expanded.width <= max_width and expanded.height <= max_height:
                global_contextual.append(
                    CandidateBand(
                        expanded,
                        min(1.0, candidate.score + 0.08),
                        candidate.density,
                        "global_context_expansion",
                        candidate.anchor_support,
                    )
                )
    candidates.extend(global_contextual)

    anchor_priority = {
        "reference": 0,
        "date": 1,
        "handle": 2,
        "brand": 3,
        "participant": 4,
        "footer": 5,
    }
    min_width = max(24, round(width * bounds.min_width_ratio))
    min_height = max(6, round(height * bounds.min_height_ratio))
    for anchor in anchors:
        margin_x = max(8, round(width * 0.012))
        margin_y = max(3, round(anchor.box.height * 0.45))
        left = max(working.left, anchor.box.left - margin_x)
        right = min(working.right, anchor.box.right + margin_x)
        top = max(working.top, anchor.box.top - margin_y)
        bottom = min(working.bottom, anchor.box.bottom + margin_y)
        box = Box(left, top, right - left, bottom - top)
        if box.width >= min_width and box.height >= min_height:
            candidates.append(
                CandidateBand(
                    box,
                    0.95 - anchor_priority.get(anchor.kind, 9) * 0.01,
                    0.0,
                    "primary_anchor_band",
                    (anchor.kind,),
                )
            )

    final_proposals = {
        index: record(
            candidate,
            (
                "anchor_band"
                if candidate.provenance == "primary_anchor_band"
                else (
                    "context_expansion"
                    if "context_expansion" in candidate.provenance
                    else "merged_row"
                )
            ),
        )
        for index, candidate in enumerate(candidates)
    }
    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (
            -final_proposals[item[0]].ranking_score,
            -item[1].box.height,
            item[1].box.top,
            item[1].box.left,
        ),
    )
    selected: list[CandidateBand] = []
    selected_indices: set[int] = set()
    pixel_budget = round(width * height * bounds.max_total_crop_ratio)
    used_pixels = 0
    selected_anchor_bands = 0
    adaptive_pass_limit = 7 if region.applied else 6
    selection_limit = min(
        bounds.max_bands,
        bounds.max_ocr_passes,
        adaptive_pass_limit,
    )

    def redundant(candidate: CandidateBand) -> bool:
        return any(
            candidate.box.overlap_ratio(item.box) >= 0.58
            or (
                abs(
                    (candidate.box.top + candidate.box.height / 2)
                    - (item.box.top + item.box.height / 2)
                )
                <= max(candidate.box.height, item.box.height) * 0.42
                and candidate.box.intersection_area(item.box) > 0
            )
            for item in selected
        )

    def try_select(index: int, *, allow_redundant: bool = False) -> bool:
        nonlocal used_pixels, selected_anchor_bands
        candidate = candidates[index]
        proposal = final_proposals[index]
        if index in selected_indices:
            return True
        if len(selected) >= selection_limit:
            proposal.rejection_reason = "crop-count limit"
            return False
        if not allow_redundant and redundant(candidate):
            proposal.rejection_reason = "nested suppression"
            return False
        if used_pixels + candidate.box.area > pixel_budget:
            proposal.rejection_reason = "pixel-budget limit"
            return False
        selected.append(candidate)
        selected_indices.add(index)
        used_pixels += candidate.box.area
        proposal.selected = True
        proposal.rejection_reason = None
        if candidate.provenance == "primary_anchor_band":
            selected_anchor_bands += 1
        return True

    upper_content = [
        (index, candidate)
        for index, candidate in ranked
        if candidate.provenance != "primary_anchor_band"
        and height * 0.04
        <= candidate.box.top + candidate.box.height / 2
        <= height * 0.48
        and 0.14 <= candidate.box.width / max(1, working.width) <= 0.72
        and 0.018 <= candidate.box.height / max(1, height) <= 0.13
        and 1.3 <= candidate.box.width / max(1, candidate.box.height) <= 10.0
    ]
    for index, _candidate in upper_content:
        if len(selected) >= min(2, selection_limit):
            break
        try_select(index)

    detail_anchors = [
        (index, candidate)
        for index, candidate in ranked
        if candidate.provenance == "primary_anchor_band"
        and any(kind in {"reference", "date"} for kind in candidate.anchor_support)
    ]
    for index, _candidate in detail_anchors:
        if len(selected) >= selection_limit:
            break
        try_select(index, allow_redundant=True)

    for index, candidate in ranked:
        if len(selected) >= selection_limit:
            break
        if candidate.provenance == "primary_anchor_band" and selected_anchor_bands >= 3:
            final_proposals[index].rejection_reason = "low rank"
            continue
        try_select(index)

    for _index, proposal in final_proposals.items():
        if proposal.selected or proposal.rejection_reason is not None:
            continue
        proposal.rejection_reason = (
            "crop-count limit" if len(selected) >= selection_limit else "low rank"
        )

    for proposal in proposals:
        related = [
            other.proposal_id
            for other in proposals
            if other is not proposal
            and proposal.box.intersection_area(other.box) > 0
            and proposal.box.overlap_ratio(other.box) >= 0.15
        ]
        proposal.overlap_relationships = tuple(related)

    selected.sort(key=lambda item: (item.box.top, item.box.left))
    return DiscoveryResult(tuple(selected), tuple(proposals))


def discover_text_bands(
    image: Image.Image,
    anchors: tuple[Anchor, ...],
    bounds: DiscoveryBounds,
    working_region: WorkingRegion | None = None,
) -> tuple[CandidateBand, ...]:
    """Compatibility wrapper returning only selected OCR bands."""

    return discover_text_bands_with_diagnostics(
        image, anchors, bounds, working_region
    ).selected_bands
