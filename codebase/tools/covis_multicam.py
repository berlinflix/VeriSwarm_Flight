"""Experimental dynamic 2--5 camera co-visibility dashboard.

This runner extends the reviewed two-camera visualizer without changing its
accepted command or evidence contract.  Each camera is captured once, YOLO is
invoked at most once per camera/frame set, and every unordered camera pair is
then assessed in its own Camera-B coordinate system.

The dashboard keeps small source previews across the top and gives the larger
centre area to all pairwise projected-overlap panels.  A pair without valid
homography-projected intersection is explicitly shown as ``ABSTAIN / NO
INTERSECTION``; geometry is never invented.

Typical Windows use from ``codebase``::

    python -m tools.covis_multicam \
      --camera cam1 0 dshow \
      --camera cam2 http://192.168.1.21:4747/video ffmpeg \
      --camera cam3 http://192.168.1.22:4747/video ffmpeg \
      --camera cam4 http://192.168.1.23:4747/video ffmpeg \
      --weights yolov8n.pt --expected-model-sha256 f59b3d... \
      --run-id MULTICAM-P2-01 --record-video

Keys: ``s`` saves the displayed dashboard and ``q`` exits and releases every
source.  This tool is unarmed and is not part of the frozen two-camera internal
qualifier unless Suyash explicitly accepts a new plan.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.covis_multicam_bridge import MultiCameraBridgeError, MultiCameraDashboardServer
from tools.covis_multicam_rescue_adapter import (
    MultiCameraAdapterError,
    MultiCameraObservationAdapter,
)

from tools.covis_live import (
    CAPTURE_BACKENDS,
    RUN_ID_RE,
    Assessment,
    CaptureWorker,
    CompositeVideoRecorder,
    DetectorObservation,
    FramePacket,
    LiveDemoError,
    YoloClaimProvider,
    _atomic_json,
    _draw_detections,
    _overlap_panel,
    _probe_release,
    _sha256,
    _shutdown_workers,
    _source_value,
    _utc_now,
    assess_pair,
)

SCHEMA = "veriswarm.covis_multicam.v3"
CAMERA_NAME_RE = RUN_ID_RE
MIN_CAMERAS = 2
MAX_CAMERAS = 5
DEFAULT_APPEARANCE_THRESHOLD = 0.60
DEFAULT_PERSON_COLOUR_THRESHOLD = 0.60
WINDOW_TITLE = "VeriSwarm multi-camera | s save | q quit"


@dataclass(frozen=True)
class CameraSpec:
    name: str
    source: int | str
    backend: str


@dataclass(frozen=True)
class PairResult:
    camera_a: str
    camera_b: str
    assessment: Assessment
    overlap_available: bool
    displayed_decision: str
    displayed_reason: str
    appearance_assumption: "AppearanceAssumption | None" = None


@dataclass(frozen=True)
class AppearanceAssumption:
    """Appearance evidence that cannot connect views without shared geometry."""

    class_id: int
    class_name: str
    score: float
    colour_similarity: float
    shape_similarity: float
    camera_a_detection_index: int
    camera_b_detection_index: int
    assumed_same_object: bool
    geometry_supported: bool
    colour_gate_passed: bool
    colour_region: str
    identity_proven: bool = False


def parse_camera_specs(values: Sequence[Sequence[str]]) -> tuple[CameraSpec, ...]:
    """Validate repeated ``--camera NAME SOURCE BACKEND`` values."""
    if not MIN_CAMERAS <= len(values) <= MAX_CAMERAS:
        raise ValueError(
            f"camera count must be between {MIN_CAMERAS} and {MAX_CAMERAS}"
        )
    specs: list[CameraSpec] = []
    names: set[str] = set()
    sources: set[str] = set()
    for value in values:
        if len(value) != 3:
            raise ValueError("each camera requires NAME SOURCE BACKEND")
        name, source_text, backend = value
        if not CAMERA_NAME_RE.fullmatch(name):
            raise ValueError(
                f"invalid camera name {name!r}; use letters, digits, dot, dash or underscore"
            )
        if name in names:
            raise ValueError(f"duplicate camera name: {name}")
        if backend not in CAPTURE_BACKENDS:
            raise ValueError(f"unsupported backend {backend!r} for {name}")
        source = _source_value(source_text)
        source_key = f"{type(source).__name__}:{source}"
        if source_key in sources:
            raise ValueError(f"duplicate camera source: {source_text}")
        names.add(name)
        sources.add(source_key)
        specs.append(CameraSpec(name, source, backend))
    return tuple(specs)


def camera_pairs(specs: Sequence[CameraSpec]) -> tuple[tuple[str, str], ...]:
    return tuple((a.name, b.name) for a, b in combinations(specs, 2))


def wait_new_frames(
    workers: Mapping[str, CaptureWorker],
    after: Mapping[str, int],
    timeout: float,
) -> dict[str, FramePacket]:
    """Return one fresh latest-frame packet from every worker."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        packets: dict[str, FramePacket] = {}
        for name, worker in workers.items():
            if worker.error:
                raise LiveDemoError(f"{name}: {worker.error}")
            packet = worker.latest()
            if packet is None or packet.sequence <= after.get(name, 0):
                break
            packets[name] = packet
        if len(packets) == len(workers):
            return packets
        time.sleep(0.005)
    missing = [
        name
        for name, worker in workers.items()
        if worker.latest() is None
        or worker.latest().sequence <= after.get(name, 0)  # type: ignore[union-attr]
    ]
    raise LiveDemoError(
        "timed out waiting for fresh frames from: " + ", ".join(missing)
    )


def wait_available_frames(
    workers: Mapping[str, CaptureWorker],
    after: Mapping[str, int],
    timeout: float,
) -> dict[str, FramePacket]:
    """Return fresh frames from every currently healthy source.

    A failed camera is excluded rather than terminating the remaining visualizer.
    This is used only by the multi-camera presentation path; pair assessment still
    requires two measured frames and never invents an unavailable view.
    """
    deadline = time.monotonic() + timeout
    newest: dict[str, FramePacket] = {}
    while time.monotonic() < deadline:
        healthy = [name for name, worker in workers.items() if not worker.error]
        newest = {
            name: packet
            for name in healthy
            if (packet := workers[name].latest()) is not None
            and packet.sequence > after.get(name, 0)
        }
        if newest and len(newest) == len(healthy):
            return newest
        time.sleep(0.005)
    return newest


def observe_once_per_camera(
    packets: Mapping[str, FramePacket],
    provider: YoloClaimProvider | None,
) -> dict[str, DetectorObservation | Exception] | None:
    """Run the detector no more than once per camera for one frame set."""
    if provider is None:
        return None
    observations: dict[str, DetectorObservation | Exception] = {}
    for name, packet in packets.items():
        try:
            observations[name] = provider(packet.frame)
        except Exception as exc:  # pair assessments convert this to ABSTAIN
            observations[name] = exc
    return observations


def _cached_pair_provider(
    first: DetectorObservation | Exception,
    second: DetectorObservation | Exception,
):
    values = iter((first, second))

    def provider(_frame: Any) -> DetectorObservation:
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value

    return provider


def _detection_crop(frame: Any, detection: Any) -> Any | None:
    """Return one bounded YOLO crop from normalized detection coordinates."""
    height, width = frame.shape[:2]
    x0 = max(0, min(width, int(round((detection.x - detection.w / 2) * width))))
    x1 = max(0, min(width, int(round((detection.x + detection.w / 2) * width))))
    y0 = max(0, min(height, int(round((detection.y - detection.h / 2) * height))))
    y1 = max(0, min(height, int(round((detection.y + detection.h / 2) * height))))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return frame[y0:y1, x0:x1]


def _colour_region(crop: Any, class_name: str) -> tuple[Any, str]:
    """Use clothing-heavy torso pixels for people and the full crop otherwise."""
    if class_name.casefold() != "person":
        return crop, "full_detection"
    height, width = crop.shape[:2]
    x0, x1 = round(width * 0.20), round(width * 0.80)
    y0, y1 = round(height * 0.20), round(height * 0.75)
    torso = crop[y0:y1, x0:x1]
    if torso.shape[0] < 2 or torso.shape[1] < 2:
        return crop, "full_detection_fallback"
    return torso, "person_torso"


def _appearance_similarity(
    frame_a: Any,
    detection_a: Any,
    frame_b: Any,
    detection_b: Any,
    class_name: str,
    cv2_module: Any,
) -> tuple[float, float, float, str] | None:
    """Compare same-class crops by HSV colour distribution and box shape."""
    crop_a = _detection_crop(frame_a, detection_a)
    crop_b = _detection_crop(frame_b, detection_b)
    if crop_a is None or crop_b is None:
        return None

    colour_regions = []
    region_names = []
    for crop in (crop_a, crop_b):
        region, region_name = _colour_region(crop, class_name)
        colour_regions.append(region)
        region_names.append(region_name)
    histograms = []
    for crop in colour_regions:
        hsv = cv2_module.cvtColor(crop, cv2_module.COLOR_BGR2HSV)
        histogram = cv2_module.calcHist(
            [hsv], [0, 1], None, [24, 16], [0, 180, 0, 256]
        )
        cv2_module.normalize(histogram, histogram)
        histograms.append(histogram)
    distance = float(
        cv2_module.compareHist(
            histograms[0], histograms[1], cv2_module.HISTCMP_BHATTACHARYYA
        )
    )
    colour_similarity = max(0.0, min(1.0, 1.0 - distance))
    aspect_a = detection_a.w / max(detection_a.h, 1e-9)
    aspect_b = detection_b.w / max(detection_b.h, 1e-9)
    shape_similarity = math.exp(-abs(math.log(max(aspect_a, 1e-9) / max(aspect_b, 1e-9))))
    score = 0.8 * colour_similarity + 0.2 * shape_similarity
    region_name = (
        region_names[0]
        if region_names[0] == region_names[1]
        else "+".join(region_names)
    )
    return score, colour_similarity, shape_similarity, region_name


def _appearance_match_allowed(
    *,
    class_name: str,
    score: float,
    colour_similarity: float,
    appearance_threshold: float,
    person_colour_threshold: float,
    geometry_supported: bool,
) -> tuple[bool, bool]:
    """Require geometry for all classes and a separate clothing-colour gate for people."""
    colour_gate_passed = (
        class_name.casefold() != "person"
        or colour_similarity >= person_colour_threshold
    )
    return (
        geometry_supported and score >= appearance_threshold and colour_gate_passed,
        colour_gate_passed,
    )


def _best_appearance_assumption(
    frame_a: Any,
    frame_b: Any,
    assessment: Any,
    cv2_module: Any,
    threshold: float,
    person_colour_threshold: float,
    geometry_supported: bool,
) -> AppearanceAssumption | None:
    """Select same-class appearance evidence without bypassing shared geometry."""
    names = dict(assessment.class_names)
    best: AppearanceAssumption | None = None
    for index_a, detection_a in enumerate(assessment.detections_a):
        for index_b, detection_b in enumerate(assessment.detections_b):
            if detection_a.cls != detection_b.cls:
                continue
            class_name = names.get(detection_a.cls, f"class_{detection_a.cls}")
            similarities = _appearance_similarity(
                frame_a,
                detection_a,
                frame_b,
                detection_b,
                class_name,
                cv2_module,
            )
            if similarities is None:
                continue
            score, colour_similarity, shape_similarity, colour_region = similarities
            assumed_same_object, colour_gate_passed = _appearance_match_allowed(
                class_name=class_name,
                score=score,
                colour_similarity=colour_similarity,
                appearance_threshold=threshold,
                person_colour_threshold=person_colour_threshold,
                geometry_supported=geometry_supported,
            )
            candidate = AppearanceAssumption(
                class_id=detection_a.cls,
                class_name=class_name,
                score=score,
                colour_similarity=colour_similarity,
                shape_similarity=shape_similarity,
                camera_a_detection_index=index_a,
                camera_b_detection_index=index_b,
                assumed_same_object=assumed_same_object,
                geometry_supported=geometry_supported,
                colour_gate_passed=colour_gate_passed,
                colour_region=colour_region,
            )
            if best is None or candidate.score > best.score:
                best = candidate
    return best


def assess_all_pairs(
    specs: Sequence[CameraSpec],
    packets: Mapping[str, FramePacket],
    observations: Mapping[str, DetectorObservation | Exception] | None,
    *,
    cv2_module: Any,
    now_monotonic_ns: int | None = None,
    max_receive_skew_ms: float = 150.0,
    max_age_ms: float = 1000.0,
    min_focus: float = 20.0,
    min_luma: float = 5.0,
    max_luma: float = 250.0,
    m_min: int = 15,
    min_intersection_pixels: float = 1.0,
    appearance_threshold: float = DEFAULT_APPEARANCE_THRESHOLD,
    person_colour_threshold: float = DEFAULT_PERSON_COLOUR_THRESHOLD,
    alignment_provider: Any = None,
) -> tuple[PairResult, ...]:
    """Assess every unordered pair using cached per-camera observations."""
    if not 0.0 <= appearance_threshold <= 1.0:
        raise ValueError("appearance_threshold must be in [0, 1]")
    if not 0.0 <= person_colour_threshold <= 1.0:
        raise ValueError("person_colour_threshold must be in [0, 1]")
    results: list[PairResult] = []
    kwargs = {
        "cv2_module": cv2_module,
        "now_monotonic_ns": now_monotonic_ns,
        "max_receive_skew_ms": max_receive_skew_ms,
        "max_age_ms": max_age_ms,
        "min_focus": min_focus,
        "min_luma": min_luma,
        "max_luma": max_luma,
        "m_min": m_min,
    }
    if alignment_provider is not None:
        kwargs["alignment_provider"] = alignment_provider
    by_name = {spec.name: spec for spec in specs}
    for name_a, name_b in camera_pairs(specs):
        if name_a not in by_name or name_b not in by_name:
            raise AssertionError("camera pair references an unknown source")
        claim_provider = None
        if observations is not None:
            claim_provider = _cached_pair_provider(
                observations[name_a], observations[name_b]
            )
        assessment = assess_pair(
            packets[name_a],
            packets[name_b],
            claim_provider=claim_provider,
            **kwargs,
        )
        spatial = assessment.spatial
        intersection_area = (
            None if spatial is None else spatial.view_intersection_area_px
        )
        overlap_available = bool(
            spatial is not None
            and spatial.view_overlap_iou is not None
            and math.isfinite(spatial.view_overlap_iou)
            and intersection_area is not None
            and math.isfinite(intersection_area)
            and intersection_area >= min_intersection_pixels
            and spatial.view_intersection_polygon
        )
        if overlap_available:
            displayed_decision = assessment.decision
            displayed_reason = assessment.reason
        else:
            displayed_decision = "ABSTAIN"
            displayed_reason = (
                assessment.reason
                if assessment.decision == "ABSTAIN"
                else "no_projected_view_intersection"
            )
            assessment = replace(
                assessment,
                decision=displayed_decision,
                reason=displayed_reason,
            )
        appearance_assumption = None
        if observations is not None:
            appearance_assumption = _best_appearance_assumption(
                packets[name_a].frame,
                packets[name_b].frame,
                assessment,
                cv2_module,
                appearance_threshold,
                person_colour_threshold,
                overlap_available,
            )
        results.append(
            PairResult(
                name_a,
                name_b,
                assessment,
                overlap_available,
                displayed_decision,
                displayed_reason,
                appearance_assumption,
            )
        )
    return tuple(results)


def _fit_image(frame: Any, width: int, height: int, cv2_module: Any) -> Any:
    import numpy as np

    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    if width <= 0 or height <= 0:
        return canvas
    scale = min(width / frame.shape[1], height / frame.shape[0])
    resized_width = max(1, round(frame.shape[1] * scale))
    resized_height = max(1, round(frame.shape[0] * scale))
    resized = cv2_module.resize(frame, (resized_width, resized_height))
    x = (width - resized_width) // 2
    y = (height - resized_height) // 2
    canvas[y : y + resized_height, x : x + resized_width] = resized
    return canvas


def fit_visible_window(
    render_width: int,
    render_height: int,
    screen_width: int,
    screen_height: int,
    *,
    horizontal_margin: int = 40,
    vertical_margin: int = 110,
) -> tuple[int, int]:
    """Fit a rendered dashboard inside the usable screen without cropping it."""
    available_width = max(1, screen_width - horizontal_margin)
    available_height = max(1, screen_height - vertical_margin)
    scale = min(
        1.0,
        available_width / render_width,
        available_height / render_height,
    )
    return max(1, round(render_width * scale)), max(1, round(render_height * scale))


def _visible_window_size(render_width: int, render_height: int) -> tuple[int, int]:
    """Use DPI-aware Windows metrics when available; otherwise retain render size."""
    if sys.platform != "win32":
        return render_width, render_height
    try:
        import ctypes

        user32 = ctypes.windll.user32
        screen_width = int(user32.GetSystemMetrics(0))
        screen_height = int(user32.GetSystemMetrics(1))
    except Exception:
        return render_width, render_height
    if screen_width <= 0 or screen_height <= 0:
        return render_width, render_height
    return fit_visible_window(
        render_width, render_height, screen_width, screen_height
    )


def _put_lines(
    frame: Any,
    lines: Sequence[str],
    origin: tuple[int, int],
    colour: tuple[int, int, int],
    cv2_module: Any,
    *,
    scale: float = 0.52,
    thickness: int = 1,
    spacing: int = 22,
) -> None:
    x, y = origin
    for index, line in enumerate(lines):
        cv2_module.putText(
            frame,
            line,
            (x, y + index * spacing),
            cv2_module.FONT_HERSHEY_SIMPLEX,
            scale,
            colour,
            thickness,
            cv2_module.LINE_AA,
        )


def _decision_colour(decision: str) -> tuple[int, int, int]:
    return {
        "AGREE": (0, 210, 0),
        "DISPUTE": (0, 0, 255),
        "ABSTAIN": (0, 180, 255),
        "COVISIBLE": (255, 180, 0),
    }.get(decision, (220, 220, 220))


def _source_tile(
    spec: CameraSpec,
    packet: FramePacket,
    observation: DetectorObservation | Exception | None,
    width: int,
    height: int,
    cv2_module: Any,
) -> Any:
    import numpy as np

    header = 48
    tile = np.zeros((height, width, 3), dtype=np.uint8)
    view = packet.frame.copy()
    detections = ()
    class_names = ()
    status = "feature-only"
    colour = (210, 210, 210)
    if isinstance(observation, DetectorObservation):
        detections = observation.detections
        class_names = observation.class_names
        status = f"boxes={len(detections)}"
        colour = (0, 210, 0)
    elif isinstance(observation, Exception):
        status = f"detector error: {type(observation).__name__}"
        colour = (0, 0, 255)
    _draw_detections(view, detections, class_names, cv2_module)
    tile[header:] = _fit_image(view, width, height - header, cv2_module)
    _put_lines(
        tile,
        (f"{spec.name} | {status}", f"source={spec.source} | {spec.backend}"),
        (7, 19),
        colour,
        cv2_module,
        scale=0.43,
        spacing=20,
    )
    return tile


def _pair_tile(
    pair: PairResult,
    packet_a: FramePacket,
    packet_b: FramePacket,
    width: int,
    height: int,
    cv2_module: Any,
) -> Any:
    import numpy as np

    header = 104
    tile = np.zeros((height, width, 3), dtype=np.uint8)
    colour = _decision_colour(pair.displayed_decision)
    assessment = pair.assessment
    spatial = assessment.spatial
    features = assessment.features
    view_iou = None if spatial is None else spatial.view_overlap_iou
    box_iou = None if spatial is None else spatial.same_class_best_box_iou
    inliers = 0 if features is None else features.inliers
    object_label = "none"
    if (
        spatial is not None
        and spatial.best_box_intersection_index is not None
        and 0 <= spatial.best_box_intersection_index < len(spatial.box_intersections)
    ):
        class_id = spatial.box_intersections[
            spatial.best_box_intersection_index
        ].class_id
        object_label = dict(assessment.class_names).get(class_id, f"class_{class_id}")
    if pair.overlap_available:
        panel = _overlap_panel(packet_b.frame, assessment, cv2_module)
        tile[header:] = _fit_image(panel, width, height - header, cv2_module)
    elif (
        pair.appearance_assumption is not None
        and pair.appearance_assumption.assumed_same_object
    ):
        view_a = packet_a.frame.copy()
        view_b = packet_b.frame.copy()
        _draw_detections(
            view_a, assessment.detections_a, assessment.class_names, cv2_module
        )
        _draw_detections(
            view_b, assessment.detections_b, assessment.class_names, cv2_module
        )
        half = max(1, width // 2)
        panel_a = _fit_image(view_a, half, height - header, cv2_module)
        panel_b = _fit_image(view_b, width - half, height - header, cv2_module)
        panel = np.hstack((panel_a, panel_b))
        assumption = pair.appearance_assumption
        _put_lines(
            panel,
            (
                f"ASSUMED SAME OBJECT: {assumption.class_name}",
                f"appearance={assumption.score:.3f} | identity not proven",
            ),
            (8, max(22, (height - header) - 38)),
            (255, 0, 255),
            cv2_module,
            scale=0.48,
            thickness=2,
            spacing=21,
        )
        tile[header:] = panel
        colour = (255, 0, 255)
    else:
        body = np.full((height - header, width, 3), 18, dtype=np.uint8)
        _put_lines(
            body,
            (
                "NO VALID INTERSECTION",
                "ABSTAIN",
                pair.displayed_reason,
            ),
            (max(8, width // 14), max(35, (height - header) // 3)),
            colour,
            cv2_module,
            scale=0.64,
            thickness=2,
            spacing=34,
        )
        tile[header:] = body
    assumption_text = pair.displayed_reason
    if (
        pair.appearance_assumption is not None
        and pair.appearance_assumption.assumed_same_object
        and not pair.overlap_available
    ):
        assumption_text = (
            f"ASSUMED_OBJECT_INTERSECTION:{pair.appearance_assumption.class_name} "
            f"score={pair.appearance_assumption.score:.3f}; geometry=ABSTAIN"
        )
        object_label = pair.appearance_assumption.class_name
    _put_lines(
        tile,
        (
            f"{pair.camera_a} -> {pair.camera_b} | {pair.displayed_decision}",
            f"view_IoU={'n/a' if view_iou is None else f'{view_iou:.3f}'} | "
            f"box_IoU={'n/a' if box_iou is None else f'{box_iou:.3f}'} | object={object_label}",
            f"inliers={inliers} | skew={assessment.receive_skew_ms:.1f}ms",
            assumption_text,
        ),
        (7, 18),
        colour,
        cv2_module,
        scale=0.46,
        spacing=23,
    )
    cv2_module.rectangle(tile, (0, 0), (width - 1, height - 1), colour, 2)
    return tile


def render_dashboard(
    specs: Sequence[CameraSpec],
    packets: Mapping[str, FramePacket],
    observations: Mapping[str, DetectorObservation | Exception] | None,
    pairs: Sequence[PairResult],
    *,
    width: int,
    height: int,
    cv2_module: Any,
) -> Any:
    """Render compact sources above a larger all-pairs intersection grid."""
    import numpy as np

    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    gap = 8
    title_height = 52
    source_height = max(105, min(130, height // 7))
    source_width = (width - gap * (len(specs) + 1)) // len(specs)
    observation_map = observations or {}
    for index, spec in enumerate(specs):
        x = gap + index * (source_width + gap)
        if spec.name in packets:
            tile = _source_tile(
                spec,
                packets[spec.name],
                observation_map.get(spec.name),
                source_width,
                source_height,
                cv2_module,
            )
        else:
            tile = np.zeros((source_height, source_width, 3), dtype=np.uint8)
            _put_lines(
                tile,
                (spec.name, "CAMERA OFFLINE", "No measured frame available"),
                (12, 28),
                (80, 80, 255),
                cv2_module,
                scale=0.44,
                thickness=1,
                spacing=24,
            )
        canvas[title_height : title_height + source_height, x : x + source_width] = tile

    pair_count = len(pairs)
    columns = {1: 1, 3: 2, 6: 3, 10: 4}.get(
        pair_count, max(1, math.ceil(math.sqrt(pair_count)))
    )
    rows = math.ceil(pair_count / columns) if pair_count else 0
    grid_top = title_height + source_height + gap
    grid_height = height - grid_top - gap
    cell_width = (width - gap * (columns + 1)) // columns
    cell_height = (grid_height - gap * (rows + 1)) // rows if rows else grid_height
    for index, pair in enumerate(pairs):
        row, column = divmod(index, columns)
        x = gap + column * (cell_width + gap)
        y = grid_top + gap + row * (cell_height + gap)
        tile = _pair_tile(
            pair,
            packets[pair.camera_a],
            packets[pair.camera_b],
            cell_width,
            cell_height,
            cv2_module,
        )
        canvas[y : y + cell_height, x : x + cell_width] = tile
    if not pairs:
        _put_lines(
            canvas,
            (
                "PAIRWISE INTERSECTIONS UNAVAILABLE",
                "At least two live measured camera frames are required",
            ),
            (24, grid_top + 54),
            (0, 180, 255),
            cv2_module,
            scale=0.72,
            thickness=2,
            spacing=38,
        )

    valid = sum(pair.overlap_available for pair in pairs)
    rejected_appearance_only = sum(
        not pair.overlap_available
        and pair.appearance_assumption is not None
        and not pair.appearance_assumption.assumed_same_object
        for pair in pairs
    )
    title = (
        f"VeriSwarm multi-camera | sources={len(specs)} | pairs={pair_count} | "
            f"valid intersections={valid} | rejected appearance-only={rejected_appearance_only}"
    )
    if valid == 0:
        title += " | NO CAMERA PAIR HAS A VALID INTERSECTION"
    title_colour = (0, 180, 255) if valid == 0 else (0, 220, 0)
    _put_lines(
        canvas,
        (title, "2-D homography-projected pairwise overlap; not calibrated stereo or 3-D"),
        (8, 18),
        title_colour,
        cv2_module,
        scale=0.48,
        thickness=1,
        spacing=21,
    )
    return canvas


def _pair_json(pair: PairResult) -> dict[str, Any]:
    return {
        "camera_a": pair.camera_a,
        "camera_b": pair.camera_b,
        "displayed_decision": pair.displayed_decision,
        "displayed_reason": pair.displayed_reason,
        "overlap_available": pair.overlap_available,
        "appearance_assumption": (
            None
            if pair.appearance_assumption is None
            else asdict(pair.appearance_assumption)
        ),
        "assessment": asdict(pair.assessment),
    }


def event_record(
    sequence: int,
    specs: Sequence[CameraSpec],
    packets: Mapping[str, FramePacket],
    observations: Mapping[str, DetectorObservation | Exception] | None,
    pairs: Sequence[PairResult],
    model_sha256: str | None,
    run_id: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    observation_map = observations or {}
    cameras: dict[str, Any] = {}
    for spec in specs:
        packet = packets.get(spec.name)
        observation = observation_map.get(spec.name)
        cameras[spec.name] = {
            "frame_sequence": None if packet is None else packet.sequence,
            "received_wall_ns": None if packet is None else packet.received_wall_ns,
            "claim": (
                asdict(observation.claim)
                if isinstance(observation, DetectorObservation)
                else None
            ),
            "detections": (
                [asdict(item) for item in observation.detections]
                if isinstance(observation, DetectorObservation)
                else []
            ),
            "class_names": (
                [list(item) for item in observation.class_names]
                if isinstance(observation, DetectorObservation)
                else []
            ),
            "detector_error": (
                f"{type(observation).__name__}: {observation}"
                if isinstance(observation, Exception)
                else "camera_offline" if packet is None else None
            ),
        }
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "event_sequence": sequence,
        "recorded_at_utc": _utc_now(),
        "model_id": model_id,
        "model_sha256": model_sha256,
        "cameras": cameras,
        "pairs": [_pair_json(pair) for pair in pairs],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--camera",
        action="append",
        nargs=3,
        metavar=("NAME", "SOURCE", "BACKEND"),
        required=True,
        help="repeat 2--5 times; SOURCE is an index/path/URL",
    )
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=float, default=15.0)
    parser.add_argument(
        "--capture-fourcc",
        help="optional four-character capture codec requested from every source",
    )
    parser.add_argument("--analysis-fps", type=float, default=3.0)
    parser.add_argument("--open-timeout", type=float, default=15.0)
    parser.add_argument("--frame-set-timeout", type=float, default=5.0)
    parser.add_argument("--release-timeout", type=float, default=20.0)
    parser.add_argument("--max-receive-skew-ms", type=float, default=150.0)
    parser.add_argument("--max-frame-age-ms", type=float, default=1000.0)
    parser.add_argument("--min-focus", type=float, default=20.0)
    parser.add_argument("--min-luma", type=float, default=5.0)
    parser.add_argument("--max-luma", type=float, default=250.0)
    parser.add_argument("--m-min", type=int, default=15)
    parser.add_argument("--min-intersection-pixels", type=float, default=1.0)
    parser.add_argument(
        "--appearance-threshold",
        type=float,
        default=DEFAULT_APPEARANCE_THRESHOLD,
        help=(
            "minimum same-class crop colour/shape score for an explicitly "
            "assumed object intersection; never proves identity"
        ),
    )
    parser.add_argument(
        "--person-colour-threshold",
        type=float,
        default=DEFAULT_PERSON_COLOUR_THRESHOLD,
        help="minimum torso/clothing HSV similarity for person appearance support",
    )
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--expected-model-sha256")
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--output-root", type=Path, default=Path("results") / "covis_multicam"
    )
    parser.add_argument("--display-width", type=int, default=1600)
    parser.add_argument("--display-height", type=int, default=900)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--record-video", action="store_true")
    parser.add_argument("--video-codec", default="MJPG")
    parser.add_argument("--video-fps", type=float)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-release-probe", action="store_true")
    parser.add_argument("--dashboard-bind", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=8780)
    parser.add_argument("--no-dashboard-bridge", action="store_true")
    parser.add_argument("--rescue-mission-id")
    parser.add_argument("--rescue-endpoint")
    parser.add_argument("--rescue-outbox", type=Path)
    parser.add_argument("--rescue-model-id", default="covis-yolo")
    parser.add_argument("--rescue-publish-hz", type=float, default=2.0)
    parser.add_argument(
        "--camera-node",
        action="append",
        nargs=2,
        metavar=("CAMERA", "NODE"),
        default=[],
    )
    parser.add_argument(
        "--camera-calibration",
        action="append",
        nargs=2,
        metavar=("CAMERA", "CALIBRATION"),
        default=[],
    )
    return parser


def _mapping(values: Sequence[Sequence[str]], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for camera, value in values:
        if camera in result:
            raise ValueError(f"duplicate {label} mapping for {camera}")
        result[camera] = value
    return result


def _validate_args(args: argparse.Namespace) -> tuple[CameraSpec, ...]:
    specs = parse_camera_specs(args.camera)
    positive = (
        args.width,
        args.height,
        args.fps,
        args.analysis_fps,
        args.open_timeout,
        args.frame_set_timeout,
        args.release_timeout,
        args.max_receive_skew_ms,
        args.max_frame_age_ms,
        args.min_intersection_pixels,
        args.display_width,
        args.display_height,
    )
    if any(value <= 0 for value in positive):
        raise ValueError("dimensions, rates, timeouts and bounds must be positive")
    if args.m_min < 4:
        raise ValueError("m-min must be at least 4")
    if not 0 < args.confidence <= 1:
        raise ValueError("confidence must be in (0, 1]")
    if not 0 <= args.appearance_threshold <= 1:
        raise ValueError("appearance-threshold must be in [0, 1]")
    if not 0 <= args.person_colour_threshold <= 1:
        raise ValueError("person-colour-threshold must be in [0, 1]")
    if not 0 <= args.min_luma < args.max_luma <= 255:
        raise ValueError("luma bounds must satisfy 0 <= min < max <= 255")
    if args.min_focus < 0:
        raise ValueError("min-focus cannot be negative")
    if args.capture_fourcc is not None and (
        len(args.capture_fourcc) != 4
        or not all(0x20 <= ord(character) <= 0x7E for character in args.capture_fourcc)
    ):
        raise ValueError("capture-fourcc must contain exactly four printable ASCII characters")
    if args.display_width < 800 or args.display_height < 600:
        raise ValueError("dashboard display must be at least 800x600")
    if args.duration_seconds is not None and args.duration_seconds <= 0:
        raise ValueError("duration-seconds must be positive")
    if args.headless and args.duration_seconds is None:
        raise ValueError("headless mode requires --duration-seconds")
    if args.video_fps is not None and args.video_fps <= 0:
        raise ValueError("video-fps must be positive")
    if len(args.video_codec) != 4 or not args.video_codec.isascii():
        raise ValueError("video-codec must contain exactly four ASCII characters")
    if (args.weights is None) != (args.expected_model_sha256 is None):
        raise ValueError(
            "semantic mode requires both --weights and --expected-model-sha256"
        )
    if args.expected_model_sha256 and not re.fullmatch(
        r"[0-9a-fA-F]{64}", args.expected_model_sha256
    ):
        raise ValueError("expected-model-sha256 must contain exactly 64 hex digits")
    if not args.no_dashboard_bridge and not 1 <= args.dashboard_port <= 65535:
        raise ValueError("dashboard-port must be inside 1..65535")
    rescue_values = (args.rescue_mission_id, args.rescue_endpoint, args.rescue_outbox)
    if any(value is not None for value in rescue_values) and not all(value is not None for value in rescue_values):
        raise ValueError("rescue integration requires mission-id, endpoint and outbox together")
    if args.rescue_mission_id:
        nodes = _mapping(args.camera_node, "camera-node")
        if set(nodes) != {spec.name for spec in specs}:
            raise ValueError("rescue integration requires exactly one camera-node mapping per configured camera")
        calibrations = _mapping(args.camera_calibration, "camera-calibration")
        if set(calibrations) - set(nodes):
            raise ValueError("camera-calibration references an unconfigured camera")
        if not 0 < args.rescue_publish_hz <= 3:
            raise ValueError("rescue-publish-hz must be inside (0, 3]")
    return specs


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        specs = _validate_args(args)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "MULTICAM-%Y%m%dT%H%M%SZ"
    )
    if not RUN_ID_RE.fullmatch(run_id):
        print("ERROR: invalid run ID")
        return 2

    try:
        import cv2
    except ImportError:
        print("ERROR: OpenCV is required")
        return 2

    visible_window = _visible_window_size(args.display_width, args.display_height)

    weights_hash: str | None = None
    detector: YoloClaimProvider | None = None
    if args.weights is not None:
        if not args.weights.is_file():
            print(f"ERROR: weights file not found: {args.weights}")
            return 2
        weights_hash = _sha256(args.weights)
        if (
            args.expected_model_sha256
            and weights_hash.lower() != args.expected_model_sha256.lower()
        ):
            print("ERROR: model SHA-256 does not match the approved value")
            return 2
        try:
            detector = YoloClaimProvider(args.weights, args.confidence)
        except LiveDemoError as exc:
            print(f"ERROR: {exc}")
            return 2

    run_dir = args.output_root / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"ERROR: refusing to overwrite run directory: {run_dir}")
        return 2

    config = {
        "schema": SCHEMA,
        "run_id": run_id,
        "started_at_utc": _utc_now(),
        "experimental_not_internal_qualifier": True,
        "cameras": [
            {"name": spec.name, "source": str(spec.source), "backend": spec.backend}
            for spec in specs
        ],
        "pair_count": len(camera_pairs(specs)),
        "capture": {
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "preferred_fourcc": args.capture_fourcc,
        },
        "analysis_fps": args.analysis_fps,
        "model_sha256": weights_hash,
        "dashboard_bridge": {
            "enabled": not args.no_dashboard_bridge,
            "bind": args.dashboard_bind,
            "port": args.dashboard_port,
            "read_only": True,
        },
        "rescue_observations": {
            "enabled": bool(args.rescue_mission_id),
            "mission_id": args.rescue_mission_id,
            "model_id": args.rescue_model_id if args.rescue_mission_id else None,
            "publish_hz": args.rescue_publish_hz if args.rescue_mission_id else None,
        },
        "thresholds": {
            "max_receive_skew_ms": args.max_receive_skew_ms,
            "max_frame_age_ms": args.max_frame_age_ms,
            "min_focus": args.min_focus,
            "min_luma": args.min_luma,
            "max_luma": args.max_luma,
            "m_min": args.m_min,
            "min_intersection_pixels": args.min_intersection_pixels,
            "appearance_threshold": args.appearance_threshold,
            "person_colour_threshold": args.person_colour_threshold,
        },
        "video": {
            "enabled": args.record_video,
            "codec": args.video_codec,
            "fps": args.video_fps or args.analysis_fps,
            "resolution": [args.display_width, args.display_height],
        },
        "visible_window": {
            "width": visible_window[0],
            "height": visible_window[1],
            "render_width": args.display_width,
            "render_height": args.display_height,
            "fit_to_screen": True,
        },
    }
    _atomic_json(run_dir / "run_config.json", config)

    dashboard_server: MultiCameraDashboardServer | None = None
    observation_adapter: MultiCameraObservationAdapter | None = None
    try:
        if not args.no_dashboard_bridge:
            dashboard_server = MultiCameraDashboardServer(
                args.dashboard_bind,
                args.dashboard_port,
                [spec.name for spec in specs],
            )
            dashboard_server.start()
        if args.rescue_mission_id:
            observation_adapter = MultiCameraObservationAdapter(
                mission_id=args.rescue_mission_id,
                outbox_path=args.rescue_outbox,
                camera_nodes=_mapping(args.camera_node, "camera-node"),
                camera_calibrations=_mapping(args.camera_calibration, "camera-calibration"),
                model_id=args.rescue_model_id,
                publish_hz=args.rescue_publish_hz,
            )
    except (OSError, MultiCameraBridgeError, MultiCameraAdapterError, ValueError) as exc:
        if dashboard_server is not None:
            dashboard_server.close()
        print(f"ERROR: live integration could not start: {exc}")
        return 2

    workers = {
        spec.name: CaptureWorker(
            name=spec.name,
            source=spec.source,
            backend=spec.backend,
            cv2_module=cv2,
            width=args.width,
            height=args.height,
            fps=args.fps,
            preferred_fourcc=args.capture_fourcc,
        )
        for spec in specs
    }
    recorder = (
        CompositeVideoRecorder(
            run_dir,
            args.video_codec,
            args.video_fps or args.analysis_fps,
            cv2,
            filename="multicam_dashboard.avi",
        )
        if args.record_video
        else None
    )
    video_summary: dict[str, Any] = {"enabled": False}
    release_probe: dict[str, Any] = {}
    worker_shutdown: dict[str, Any] = {}
    errors: list[str] = []
    rescue_delivery: dict[str, Any] = {"enabled": observation_adapter is not None}
    event_count = 0
    saved_dashboards: list[dict[str, Any]] = []
    events_path = run_dir / "events.jsonl"
    events_handle = events_path.open("x", encoding="utf-8", newline="\n")
    started = time.monotonic()
    try:
        if not args.headless:
            window_flags = cv2.WINDOW_NORMAL
            if hasattr(cv2, "WINDOW_KEEPRATIO"):
                window_flags |= cv2.WINDOW_KEEPRATIO
            cv2.namedWindow(WINDOW_TITLE, window_flags)
            cv2.resizeWindow(WINDOW_TITLE, *visible_window)
        for worker in workers.values():
            worker.start()
        ready_deadline = time.monotonic() + args.open_timeout
        while time.monotonic() < ready_deadline:
            if all(worker.latest() is not None or worker.error for worker in workers.values()):
                break
            time.sleep(0.02)
        available_at_start = sum(worker.latest() is not None for worker in workers.values())
        if available_at_start == 0:
            detail = ", ".join(f"{name}={worker.error or 'no frame'}" for name, worker in workers.items())
            raise LiveDemoError(f"no configured camera produced a usable frame: {detail}")
        print(
            "READY "
            + " ".join(
                f"{name}={worker.actual_backend or 'OFFLINE'}" for name, worker in workers.items()
            )
        )
        after = {name: 0 for name in workers}
        frame_period = 1.0 / args.analysis_fps
        while True:
            loop_started = time.monotonic()
            packets = wait_available_frames(workers, after, args.frame_set_timeout)
            if not packets:
                if all(worker.error for worker in workers.values()):
                    raise LiveDemoError("every configured camera is offline")
                if args.duration_seconds is not None and time.monotonic() - started >= args.duration_seconds:
                    break
                continue
            after.update({name: packet.sequence for name, packet in packets.items()})
            active_specs = tuple(spec for spec in specs if spec.name in packets)
            observations = observe_once_per_camera(packets, detector)
            pairs = (
                assess_all_pairs(
                    active_specs,
                    packets,
                    observations,
                    cv2_module=cv2,
                    now_monotonic_ns=time.monotonic_ns(),
                    max_receive_skew_ms=args.max_receive_skew_ms,
                    max_age_ms=args.max_frame_age_ms,
                    min_focus=args.min_focus,
                    min_luma=args.min_luma,
                    max_luma=args.max_luma,
                    m_min=args.m_min,
                    min_intersection_pixels=args.min_intersection_pixels,
                    appearance_threshold=args.appearance_threshold,
                    person_colour_threshold=args.person_colour_threshold,
                )
                if len(active_specs) >= 2
                else ()
            )
            dashboard = render_dashboard(
                specs,
                packets,
                observations,
                pairs,
                width=args.display_width,
                height=args.display_height,
                cv2_module=cv2,
            )
            if recorder is not None:
                recorder.write(dashboard)
            event_count += 1
            event = event_record(
                event_count,
                specs,
                packets,
                observations,
                pairs,
                weights_hash,
                run_id,
                args.rescue_model_id if detector is not None else "FEATURE_ONLY",
            )
            events_handle.write(
                json.dumps(event, sort_keys=True, allow_nan=False) + "\n"
            )
            events_handle.flush()
            if dashboard_server is not None:
                dashboard_server.publish(dashboard, event, cv2)
            if observation_adapter is not None:
                emitted = observation_adapter.enqueue(event)
                if emitted:
                    rescue_delivery = {
                        "enabled": True,
                        "emitted": rescue_delivery.get("emitted", 0) + len(emitted),
                        "last_flush": observation_adapter.flush(
                            args.rescue_endpoint,
                            token=os.environ.get("VERISWARM_RESCUE_TOKEN", ""),
                        ),
                    }

            key = -1
            if not args.headless:
                cv2.imshow(WINDOW_TITLE, dashboard)
                key = cv2.waitKey(1) & 0xFF
            if key == ord("s"):
                screenshots = run_dir / "screenshots"
                screenshots.mkdir(exist_ok=True)
                path = screenshots / f"dashboard_{event_count:06d}.png"
                if not cv2.imwrite(str(path), dashboard):
                    raise LiveDemoError(f"failed to save dashboard: {path}")
                saved_dashboards.append(
                    {
                        "path": str(path.relative_to(run_dir)).replace("\\", "/"),
                        "sha256": _sha256(path),
                    }
                )
            if key == ord("q"):
                break
            if (
                args.duration_seconds is not None
                and time.monotonic() - started >= args.duration_seconds
            ):
                break
            delay = frame_period - (time.monotonic() - loop_started)
            if delay > 0:
                time.sleep(delay)
    except KeyboardInterrupt:
        print("Interrupted; finalizing evidence and releasing every source")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        events_handle.close()
        if dashboard_server is not None:
            dashboard_server.close()
        if recorder is not None:
            try:
                video_summary = recorder.finalize()
            except Exception as exc:
                video_summary = {
                    "enabled": True,
                    "finalized": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if not video_summary.get("finalized"):
                errors.append(
                    f"video_finalize_failed: {video_summary.get('error', 'unknown')}"
                )
        worker_shutdown = _shutdown_workers(workers, args.release_timeout)
        if not args.headless:
            cv2.destroyAllWindows()
        if not args.no_release_probe:
            for spec in specs:
                release_probe[spec.name] = _probe_release(
                    spec.source, spec.backend, cv2
                )

    workers_released = all(
        result.get("closed") for result in worker_shutdown.values()
    )
    probes_released = args.no_release_probe or all(
        result.get("opened") and result.get("read")
        for result in release_probe.values()
    )
    release_verified = workers_released and probes_released
    video_ok = recorder is None or bool(video_summary.get("finalized"))
    passed = not errors and release_verified and video_ok and event_count > 0
    summary = {
        **config,
        "finished_at_utc": _utc_now(),
        "passed": passed,
        "events": event_count,
        "events_sha256": _sha256(events_path),
        "saved_dashboards": saved_dashboards,
        "video_recording": video_summary,
        "worker_shutdown": worker_shutdown,
        "release_probe": release_probe,
        "release_verified": release_verified,
        "rescue_delivery": rescue_delivery,
        "errors": errors,
    }
    _atomic_json(run_dir / "summary.json", summary)
    print(
        f"COMPLETE run={run_id} events={event_count} "
        f"release={release_verified} passed={passed}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
