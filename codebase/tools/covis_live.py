"""Live two-camera co-visibility and semantic-agreement demonstrator.

This process is deliberately unarmed: it has no flight, actuator, receipt-signing,
or OP-TEE interface.  It acquires one frame stream per observer, proves that the
streams are healthy and temporally close, calls :mod:`protocol.covis_features`
for the existing ORB/RANSAC co-visibility gate, and optionally compares measured
YOLO ``PerceptionClaim`` values.

The feature gate answers only "do these frames share a scene?".  It must never be
presented as an adversarial-detector verdict by itself.  ``AGREE``/``DISPUTE``
are emitted only when a pinned detector produced measured claims for co-visible
frames.  Camera loss, blur, excessive skew, inadequate overlap, or inference
failure produces ``ABSTAIN``.

For the physical demo, the measured homography projects Camera A into Camera B's
image plane before calculating view-overlap IoU and same-class detector-box IoU.
Raw box coordinates from different viewpoints are never compared.

Typical Jetson use (from ``codebase``)::

    python -m tools.covis_live \
      --camera-a /dev/video0 --camera-a-name usb_webcam \
      --camera-b http://192.168.1.25:4747/video \
      --camera-b-name android_droidcam \
      --weights yolov8n.pt \
      --expected-model-sha256 f59b3d... \
      --run-id IHQ-20260819-WEBCAM-01 --require-cycles 3

Keys: ``c`` clean, ``a`` attack, ``r`` recovery, ``s`` save, ``q`` quit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from perception.claim import PerceptionClaim, claims_agree
from perception.yolo_action import Detection, frame_to_detections, model_hash
from protocol.covis_features import (
    DEFAULT_M_MIN,
    FeatureAlignmentResult,
    FeatureMatchResult,
    feature_alignment,
)

SCHEMA = "veriswarm.covis_live.v1"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
VALID_LABELS = {"unlabelled", "clean", "attack", "recovery"}
VALID_DECISIONS = {"AGREE", "DISPUTE", "ABSTAIN", "COVISIBLE"}
ATTACK_EVIDENCE_REASONS = {
    "semantic_disagreement",
    "feature_overlap_below_threshold",
}


class LiveDemoError(RuntimeError):
    """A fail-closed configuration, camera, evidence, or runtime failure."""


@dataclass(frozen=True)
class FramePacket:
    sequence: int
    received_wall_ns: int
    received_monotonic_ns: int
    frame: Any


@dataclass(frozen=True)
class CameraHealth:
    healthy: bool
    reason: str
    focus_score: float
    mean_luma: float
    age_ms: float
    width: int
    height: int


@dataclass(frozen=True)
class DetectorObservation:
    """One model invocation: its boxes and the claim derived from those boxes."""

    detections: tuple[Detection, ...]
    claim: PerceptionClaim
    class_names: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class SpatialEvidence:
    """IoU measurements after mapping both cameras into Camera B coordinates."""

    view_overlap_iou: float | None
    same_class_best_box_iou: float | None
    same_class_candidate_pairs: int
    reason: str


@dataclass(frozen=True)
class Assessment:
    decision: str
    reason: str
    receive_skew_ms: float
    camera_a: CameraHealth
    camera_b: CameraHealth
    features: FeatureMatchResult | None
    spatial: SpatialEvidence | None
    claim_a: PerceptionClaim | None
    claim_b: PerceptionClaim | None
    detections_a: tuple[Detection, ...] = ()
    detections_b: tuple[Detection, ...] = ()
    class_names: tuple[tuple[int, str], ...] = ()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_value(text: str) -> int | str:
    """Convert a bare integer to an OpenCV device index; preserve paths/URLs."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("camera source cannot be empty")
    if re.fullmatch(r"[0-9]+", stripped):
        return int(stripped)
    return stripped


def _source_identity(source: int | str) -> str:
    return str(source)


def _claim_json(claim: PerceptionClaim | None) -> dict[str, Any] | None:
    return None if claim is None else asdict(claim)


def _detections_json(detections: Sequence[Detection]) -> list[dict[str, Any]]:
    return [asdict(detection) for detection in detections]


def _feature_json(result: FeatureMatchResult | None) -> dict[str, int] | None:
    return None if result is None else asdict(result)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _focus_and_luma(frame: Any, cv2_module: Any) -> tuple[float, float]:
    gray = (
        frame
        if getattr(frame, "ndim", 0) == 2
        else cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2GRAY)
    )
    focus = float(cv2_module.Laplacian(gray, cv2_module.CV_64F).var())
    luma = float(gray.mean())
    if not math.isfinite(focus) or not math.isfinite(luma):
        raise LiveDemoError("camera health calculation returned non-finite data")
    return focus, luma


def _camera_health(
    packet: FramePacket,
    *,
    now_monotonic_ns: int,
    cv2_module: Any,
    max_age_ms: float,
    min_focus: float,
    min_luma: float,
    max_luma: float,
) -> CameraHealth:
    frame = packet.frame
    shape = getattr(frame, "shape", ())
    if len(shape) < 2 or int(shape[0]) <= 0 or int(shape[1]) <= 0:
        return CameraHealth(False, "invalid_frame_shape", 0.0, 0.0, math.inf, 0, 0)
    height, width = int(shape[0]), int(shape[1])
    age_ms = max(0.0, (now_monotonic_ns - packet.received_monotonic_ns) / 1e6)
    focus, luma = _focus_and_luma(frame, cv2_module)
    if age_ms > max_age_ms:
        reason = "stale_frame"
    elif focus < min_focus:
        reason = "blur_or_low_features"
    elif luma < min_luma:
        reason = "underexposed"
    elif luma > max_luma:
        reason = "overexposed"
    else:
        reason = "ok"
    return CameraHealth(reason == "ok", reason, focus, luma, age_ms, width, height)


def _polygon_iou(poly_a: Any, poly_b: Any, cv2_module: Any) -> float | None:
    """Return convex-polygon IoU, or ``None`` for invalid projected geometry."""
    import numpy as np

    try:
        a = np.asarray(poly_a, dtype=np.float32).reshape(-1, 2)
        b = np.asarray(poly_b, dtype=np.float32).reshape(-1, 2)
    except (TypeError, ValueError):
        return None
    if len(a) < 3 or len(b) < 3 or not np.isfinite(a).all() or not np.isfinite(b).all():
        return None
    a = cv2_module.convexHull(a).reshape(-1, 2)
    b = cv2_module.convexHull(b).reshape(-1, 2)
    area_a = float(abs(cv2_module.contourArea(a)))
    area_b = float(abs(cv2_module.contourArea(b)))
    if not math.isfinite(area_a + area_b) or area_a <= 1e-6 or area_b <= 1e-6:
        return None
    intersection, _ = cv2_module.intersectConvexConvex(a, b)
    intersection = max(0.0, float(intersection))
    union = area_a + area_b - intersection
    if union <= 1e-6 or not math.isfinite(union):
        return None
    return max(0.0, min(1.0, intersection / union))


def _project_points(points: Any, homography: Any, cv2_module: Any) -> Any | None:
    """Project 2-D points through a finite, non-degenerate homography."""
    import numpy as np

    try:
        matrix = np.asarray(homography, dtype=np.float64)
        source = np.asarray(points, dtype=np.float32).reshape(1, -1, 2)
    except (TypeError, ValueError):
        return None
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        return None
    scale = float(np.linalg.norm(matrix))
    if scale <= 1e-12 or not math.isfinite(scale):
        return None
    matrix = matrix / scale
    if abs(float(np.linalg.det(matrix))) <= 1e-12:
        return None
    try:
        projected = cv2_module.perspectiveTransform(source, matrix)[0]
    except Exception:
        return None
    return projected if np.isfinite(projected).all() else None


def _detection_polygon(detection: Detection, width: int, height: int) -> list[list[float]]:
    half_w = detection.w * width / 2.0
    half_h = detection.h * height / 2.0
    centre_x = detection.x * width
    centre_y = detection.y * height
    return [
        [centre_x - half_w, centre_y - half_h],
        [centre_x + half_w, centre_y - half_h],
        [centre_x + half_w, centre_y + half_h],
        [centre_x - half_w, centre_y + half_h],
    ]


def projected_iou_evidence(
    frame_a: Any,
    frame_b: Any,
    homography_a_to_b: Any,
    detections_a: Sequence[Detection],
    detections_b: Sequence[Detection],
    cv2_module: Any,
) -> SpatialEvidence:
    """Measure view and same-class box IoU in Camera B's image plane.

    Raw boxes from two viewpoints are never compared. Camera A geometry is
    first projected through the measured ORB/RANSAC homography.
    """
    height_a, width_a = frame_a.shape[:2]
    height_b, width_b = frame_b.shape[:2]
    view_a = [[0, 0], [width_a, 0], [width_a, height_a], [0, height_a]]
    view_b = [[0, 0], [width_b, 0], [width_b, height_b], [0, height_b]]
    projected_view_a = _project_points(view_a, homography_a_to_b, cv2_module)
    if projected_view_a is None:
        return SpatialEvidence(None, None, 0, "invalid_homography")
    view_iou = _polygon_iou(projected_view_a, view_b, cv2_module)
    if view_iou is None:
        return SpatialEvidence(None, None, 0, "invalid_projected_view")

    candidate_ious: list[float] = []
    for detection_a in detections_a:
        projected_box_a = _project_points(
            _detection_polygon(detection_a, width_a, height_a),
            homography_a_to_b,
            cv2_module,
        )
        if projected_box_a is None:
            continue
        for detection_b in detections_b:
            if detection_a.cls != detection_b.cls:
                continue
            iou = _polygon_iou(
                projected_box_a,
                _detection_polygon(detection_b, width_b, height_b),
                cv2_module,
            )
            if iou is not None:
                candidate_ious.append(iou)
    best = max(candidate_ious) if candidate_ious else None
    reason = "ok" if best is not None else "no_same_class_box_pair"
    return SpatialEvidence(view_iou, best, len(candidate_ious), reason)


def _observation(value: Any) -> DetectorObservation:
    """Accept legacy claim-only test providers while production retains boxes."""
    if isinstance(value, DetectorObservation):
        return value
    if isinstance(value, PerceptionClaim):
        return DetectorObservation((), value)
    raise TypeError("detector provider must return DetectorObservation or PerceptionClaim")


def assess_pair(
    packet_a: FramePacket,
    packet_b: FramePacket,
    *,
    cv2_module: Any,
    now_monotonic_ns: int | None = None,
    max_receive_skew_ms: float = 150.0,
    max_age_ms: float = 1000.0,
    min_focus: float = 20.0,
    min_luma: float = 5.0,
    max_luma: float = 250.0,
    m_min: int = DEFAULT_M_MIN,
    alignment_provider: Callable[[Any, Any], FeatureAlignmentResult] = feature_alignment,
    feature_matcher: Callable[[Any, Any], FeatureMatchResult] | None = None,
    claim_provider: Callable[[Any], DetectorObservation | PerceptionClaim] | None = None,
) -> Assessment:
    """Pure pair decision used by the live loop and focused tests."""
    if max_receive_skew_ms <= 0 or max_age_ms <= 0 or min_focus < 0:
        raise ValueError("health thresholds must be non-negative and time bounds positive")
    if not 0 <= min_luma < max_luma <= 255:
        raise ValueError("luma bounds must satisfy 0 <= min < max <= 255")
    if m_min < 4:
        raise ValueError("m_min must be at least 4 for homography evidence")
    now_ns = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
    health_a = _camera_health(
        packet_a,
        now_monotonic_ns=now_ns,
        cv2_module=cv2_module,
        max_age_ms=max_age_ms,
        min_focus=min_focus,
        min_luma=min_luma,
        max_luma=max_luma,
    )
    health_b = _camera_health(
        packet_b,
        now_monotonic_ns=now_ns,
        cv2_module=cv2_module,
        max_age_ms=max_age_ms,
        min_focus=min_focus,
        min_luma=min_luma,
        max_luma=max_luma,
    )
    receive_skew_ms = abs(
        packet_a.received_monotonic_ns - packet_b.received_monotonic_ns
    ) / 1e6
    if not health_a.healthy:
        return Assessment(
            "ABSTAIN", f"camera_a_{health_a.reason}", receive_skew_ms,
            health_a, health_b, None, None, None, None,
        )
    if not health_b.healthy:
        return Assessment(
            "ABSTAIN", f"camera_b_{health_b.reason}", receive_skew_ms,
            health_a, health_b, None, None, None, None,
        )
    if receive_skew_ms > max_receive_skew_ms:
        return Assessment(
            "ABSTAIN", "host_receive_skew_exceeded", receive_skew_ms,
            health_a, health_b, None, None, None, None,
        )

    observation_a: DetectorObservation | None = None
    observation_b: DetectorObservation | None = None
    if claim_provider is not None:
        try:
            observation_a = _observation(claim_provider(packet_a.frame))
            observation_b = _observation(claim_provider(packet_b.frame))
        except Exception as exc:
            return Assessment(
                "ABSTAIN", f"detector_error:{type(exc).__name__}", receive_skew_ms,
                health_a, health_b, None, None, None, None,
            )

    claim_a = None if observation_a is None else observation_a.claim
    claim_b = None if observation_b is None else observation_b.claim
    detections_a = () if observation_a is None else observation_a.detections
    detections_b = () if observation_b is None else observation_b.detections
    class_names = () if observation_a is None else observation_a.class_names

    try:
        if feature_matcher is not None:
            alignment = FeatureAlignmentResult(
                feature_matcher(packet_a.frame, packet_b.frame), None
            )
        else:
            alignment = alignment_provider(packet_a.frame, packet_b.frame)
        if not isinstance(alignment, FeatureAlignmentResult):
            raise TypeError("alignment provider returned an invalid result")
        features = alignment.match
    except Exception as exc:
        return Assessment(
            "ABSTAIN", f"feature_match_error:{type(exc).__name__}", receive_skew_ms,
            health_a, health_b, None, None, claim_a, claim_b,
            detections_a, detections_b, class_names,
        )
    if features.inliers < m_min:
        return Assessment(
            "ABSTAIN", "feature_overlap_below_threshold", receive_skew_ms,
            health_a, health_b, features, None, claim_a, claim_b,
            detections_a, detections_b, class_names,
        )
    spatial = projected_iou_evidence(
        packet_a.frame,
        packet_b.frame,
        alignment.homography_a_to_b,
        detections_a,
        detections_b,
        cv2_module,
    )
    if claim_provider is None:
        return Assessment(
            "COVISIBLE", "feature_overlap_only_no_semantic_detector", receive_skew_ms,
            health_a, health_b, features, spatial, None, None,
        )

    agreement = claims_agree(claim_a, claim_b)
    if agreement is None:
        return Assessment(
            "ABSTAIN", "unmeasured_semantic_claim", receive_skew_ms,
            health_a, health_b, features, spatial, claim_a, claim_b,
            detections_a, detections_b, class_names,
        )
    if agreement:
        return Assessment(
            "AGREE", "semantic_agreement", receive_skew_ms,
            health_a, health_b, features, spatial, claim_a, claim_b,
            detections_a, detections_b, class_names,
        )
    return Assessment(
        "DISPUTE", "semantic_disagreement", receive_skew_ms,
        health_a, health_b, features, spatial, claim_a, claim_b,
        detections_a, detections_b, class_names,
    )


class CycleTracker:
    """Count explicit clean -> attack -> recovery evidence sequences."""

    def __init__(self) -> None:
        self.phase = "clean"
        self.completed = 0

    def observe(self, operator_label: str, decision: str, reason: str) -> bool:
        if operator_label not in VALID_LABELS or decision not in VALID_DECISIONS:
            raise ValueError("invalid cycle observation")
        advanced = False
        if self.phase == "clean" and operator_label == "clean" and decision == "AGREE":
            self.phase = "attack"
            advanced = True
        elif (
            self.phase == "attack"
            and operator_label == "attack"
            and (
                decision == "DISPUTE"
                or (decision == "ABSTAIN" and reason in ATTACK_EVIDENCE_REASONS)
            )
        ):
            self.phase = "recovery"
            advanced = True
        elif (
            self.phase == "recovery"
            and operator_label == "recovery"
            and decision == "AGREE"
        ):
            self.completed += 1
            self.phase = "clean"
            advanced = True
        return advanced


class CaptureWorker:
    """Continuously retain only the newest frame from one OpenCV source."""

    def __init__(
        self,
        *,
        name: str,
        source: int | str,
        cv2_module: Any,
        width: int,
        height: int,
        fps: float,
    ) -> None:
        self.name = name
        self.source = source
        self.cv2 = cv2_module
        self.width = width
        self.height = height
        self.fps = fps
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture: Any | None = None
        self._latest: FramePacket | None = None
        self._sequence = 0
        self._consecutive_failures = 0
        self.error: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise LiveDemoError(f"{self.name} capture already started")
        self._thread = threading.Thread(
            target=self._run,
            name=f"covis-{self.name}",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            capture = self.cv2.VideoCapture()
            self._capture = capture
            if hasattr(self.cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                capture.set(self.cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000)
            if hasattr(self.cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
                capture.set(self.cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1000)
            opened = capture.open(self.source)
            if not opened or not capture.isOpened():
                self.error = f"could not open source {_source_identity(self.source)!r}"
                self._ready.set()
                return
            capture.set(self.cv2.CAP_PROP_BUFFERSIZE, 1)
            capture.set(self.cv2.CAP_PROP_FRAME_WIDTH, self.width)
            capture.set(self.cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            capture.set(self.cv2.CAP_PROP_FPS, self.fps)
            while not self._stop.is_set():
                ok, frame = capture.read()
                received_monotonic_ns = time.monotonic_ns()
                received_wall_ns = time.time_ns()
                if not ok or frame is None:
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= 30:
                        self.error = "30 consecutive camera reads failed"
                        self._ready.set()
                        return
                    time.sleep(0.01)
                    continue
                self._consecutive_failures = 0
                with self._lock:
                    self._sequence += 1
                    self._latest = FramePacket(
                        sequence=self._sequence,
                        received_wall_ns=received_wall_ns,
                        received_monotonic_ns=received_monotonic_ns,
                        frame=frame,
                    )
                self._ready.set()
        except Exception as exc:  # retained and surfaced by the main thread
            self.error = f"{type(exc).__name__}: {exc}"
            self._ready.set()
        finally:
            capture = self._capture
            if capture is not None:
                capture.release()

    def wait_ready(self, timeout: float) -> None:
        if not self._ready.wait(timeout):
            raise LiveDemoError(f"{self.name} produced no frame within {timeout:.1f}s")
        if self.error:
            raise LiveDemoError(f"{self.name}: {self.error}")
        if self.latest() is None:
            raise LiveDemoError(f"{self.name} opened but produced no usable frame")

    def latest(self) -> FramePacket | None:
        with self._lock:
            return self._latest

    def close(self, join_timeout: float = 3.0) -> bool:
        self._stop.set()
        capture = self._capture
        if capture is not None:
            capture.release()
        thread = self._thread
        if thread is not None:
            thread.join(join_timeout)
            return not thread.is_alive()
        return True


class YoloClaimProvider:
    def __init__(self, weights: Path, confidence: float) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise LiveDemoError(
                "semantic mode requires a Jetson-compatible PyTorch/Ultralytics "
                "installation; do not install generic desktop torch on Jetson"
            ) from exc
        self.model = YOLO(str(weights))
        self.confidence = confidence
        names = self.model.names
        if isinstance(names, Mapping):
            items = names.items()
        else:
            items = enumerate(names)
        self.class_names = tuple(
            sorted((int(class_id), str(name)) for class_id, name in items)
        )

    def __call__(self, frame: Any) -> DetectorObservation:
        detections = tuple(
            frame_to_detections(frame, self.model, conf=self.confidence)
        )
        return DetectorObservation(
            detections=detections,
            claim=PerceptionClaim.from_detections(detections),
            class_names=self.class_names,
        )


def _wait_new_pair(
    worker_a: CaptureWorker,
    worker_b: CaptureWorker,
    after_a: int,
    after_b: int,
    timeout: float,
) -> tuple[FramePacket, FramePacket]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if worker_a.error:
            raise LiveDemoError(f"{worker_a.name}: {worker_a.error}")
        if worker_b.error:
            raise LiveDemoError(f"{worker_b.name}: {worker_b.error}")
        packet_a = worker_a.latest()
        packet_b = worker_b.latest()
        if (
            packet_a is not None
            and packet_b is not None
            and packet_a.sequence > after_a
            and packet_b.sequence > after_b
        ):
            return packet_a, packet_b
        time.sleep(0.005)
    raise LiveDemoError("timed out waiting for a fresh frame from both cameras")


def _event_record(
    *,
    sequence: int,
    packet_a: FramePacket,
    packet_b: FramePacket,
    assessment: Assessment,
    operator_label: str,
    camera_a_name: str,
    camera_b_name: str,
    model_sha256: str | None,
    m_min: int,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "event_sequence": sequence,
        "recorded_at_utc": _utc_now(),
        "operator_label": operator_label,
        "decision": assessment.decision,
        "reason": assessment.reason,
        "model_sha256": model_sha256,
        "m_min": m_min,
        "host_receive_skew_ms": assessment.receive_skew_ms,
        "camera_a": {
            "name": camera_a_name,
            "frame_sequence": packet_a.sequence,
            "received_wall_ns": packet_a.received_wall_ns,
            "health": asdict(assessment.camera_a),
            "claim": _claim_json(assessment.claim_a),
            "detections": _detections_json(assessment.detections_a),
        },
        "camera_b": {
            "name": camera_b_name,
            "frame_sequence": packet_b.sequence,
            "received_wall_ns": packet_b.received_wall_ns,
            "health": asdict(assessment.camera_b),
            "claim": _claim_json(assessment.claim_b),
            "detections": _detections_json(assessment.detections_b),
        },
        "class_names": {str(key): value for key, value in assessment.class_names},
        "features": _feature_json(assessment.features),
        "projected_iou": (
            None if assessment.spatial is None else asdict(assessment.spatial)
        ),
    }


def _save_frames(
    run_dir: Path,
    event: dict[str, Any],
    packet_a: FramePacket,
    packet_b: FramePacket,
    annotated: Any,
    cv2_module: Any,
) -> None:
    frames = run_dir / "frames"
    frames.mkdir(exist_ok=True)
    stem = (
        f"{event['event_sequence']:06d}_{event['operator_label']}_"
        f"{event['decision'].lower()}"
    )
    outputs = {
        "camera_a": frames / f"{stem}_a.png",
        "camera_b": frames / f"{stem}_b.png",
        "annotated": frames / f"{stem}_combined.png",
    }
    images = {
        "camera_a": packet_a.frame,
        "camera_b": packet_b.frame,
        "annotated": annotated,
    }
    evidence: dict[str, Any] = {}
    for key, path in outputs.items():
        if not cv2_module.imwrite(str(path), images[key]):
            raise LiveDemoError(f"failed to save {path}")
        evidence[key] = {
            "path": str(path.relative_to(run_dir)).replace("\\", "/"),
            "sha256": _sha256(path),
        }
    event["saved_frames"] = evidence


_BOX_COLOURS = (
    (40, 220, 40),
    (255, 160, 40),
    (40, 180, 255),
    (220, 80, 220),
    (255, 220, 40),
    (80, 120, 255),
)


def _draw_detections(
    frame: Any,
    detections: Sequence[Detection],
    class_names: Sequence[tuple[int, str]],
    cv2_module: Any,
) -> None:
    """Draw the exact normalized detections used by the semantic decision."""
    height, width = frame.shape[:2]
    names = dict(class_names)
    for detection in detections:
        x1 = max(0, min(width - 1, round((detection.x - detection.w / 2.0) * width)))
        y1 = max(0, min(height - 1, round((detection.y - detection.h / 2.0) * height)))
        x2 = max(0, min(width - 1, round((detection.x + detection.w / 2.0) * width)))
        y2 = max(0, min(height - 1, round((detection.y + detection.h / 2.0) * height)))
        if x2 <= x1 or y2 <= y1:
            continue
        colour = _BOX_COLOURS[detection.cls % len(_BOX_COLOURS)]
        cv2_module.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
        class_name = names.get(detection.cls, f"class_{detection.cls}")
        label = f"{class_name} {detection.conf:.2f}"
        (label_width, label_height), baseline = cv2_module.getTextSize(
            label, cv2_module.FONT_HERSHEY_SIMPLEX, 0.48, 1
        )
        label_top = max(0, y1 - label_height - baseline - 6)
        label_right = min(width - 1, x1 + label_width + 8)
        cv2_module.rectangle(
            frame,
            (x1, label_top),
            (label_right, y1),
            colour,
            -1,
        )
        cv2_module.putText(
            frame,
            label,
            (x1 + 4, max(label_height + 1, y1 - baseline - 3)),
            cv2_module.FONT_HERSHEY_SIMPLEX,
            0.48,
            (0, 0, 0),
            1,
            cv2_module.LINE_AA,
        )


def _annotated_pair(
    packet_a: FramePacket,
    packet_b: FramePacket,
    assessment: Assessment,
    operator_label: str,
    cv2_module: Any,
    camera_a_name: str = "Camera A",
    camera_b_name: str = "Camera B",
) -> Any:
    a = packet_a.frame.copy()
    b = packet_b.frame.copy()
    height = min(a.shape[0], b.shape[0], 540)

    def resized(frame: Any) -> Any:
        width = max(1, int(frame.shape[1] * height / frame.shape[0]))
        return cv2_module.resize(frame, (width, height))

    a, b = resized(a), resized(b)
    _draw_detections(
        a, assessment.detections_a, assessment.class_names, cv2_module
    )
    _draw_detections(
        b, assessment.detections_b, assessment.class_names, cv2_module
    )
    a = cv2_module.copyMakeBorder(
        a, 66, 0, 0, 0, cv2_module.BORDER_CONSTANT, value=(0, 0, 0)
    )
    b = cv2_module.copyMakeBorder(
        b, 66, 0, 0, 0, cv2_module.BORDER_CONSTANT, value=(0, 0, 0)
    )
    colour = {
        "AGREE": (0, 200, 0),
        "DISPUTE": (0, 0, 255),
        "ABSTAIN": (0, 180, 255),
        "COVISIBLE": (255, 180, 0),
    }[assessment.decision]
    text = (
        f"{assessment.decision} | {assessment.reason} | "
        f"label={operator_label} | receive_skew={assessment.receive_skew_ms:.1f}ms"
    )
    inliers = 0 if assessment.features is None else assessment.features.inliers
    details = (
        f"inliers={inliers} | focus={assessment.camera_a.focus_score:.0f}/"
        f"{assessment.camera_b.focus_score:.0f}"
    )
    if assessment.spatial is not None:
        view_iou = assessment.spatial.view_overlap_iou
        box_iou = assessment.spatial.same_class_best_box_iou
        details += (
            f" | view_IoU={'n/a' if view_iou is None else f'{view_iou:.2f}'}"
            f" | box_IoU={'n/a' if box_iou is None else f'{box_iou:.2f}'}"
        )
    for frame, camera_name, detection_count in (
        (a, camera_a_name, len(assessment.detections_a)),
        (b, camera_b_name, len(assessment.detections_b)),
    ):
        cv2_module.putText(
            frame,
            f"{camera_name} | boxes={detection_count} | {text}",
            (8, 24),
            cv2_module.FONT_HERSHEY_SIMPLEX,
            0.48, colour, 1, cv2_module.LINE_AA,
        )
        cv2_module.putText(
            frame, details, (8, 50), cv2_module.FONT_HERSHEY_SIMPLEX,
            0.48, colour, 1, cv2_module.LINE_AA,
        )
    return cv2_module.hconcat([a, b])


def _probe_release(
    source: int | str,
    cv2_module: Any,
    attempts: int = 60,
) -> dict[str, Any]:
    capture = cv2_module.VideoCapture(source)
    try:
        if not capture.isOpened():
            return {"opened": False, "read": False}
        for _ in range(attempts):
            ok, frame = capture.read()
            if ok and frame is not None:
                return {"opened": True, "read": True}
            time.sleep(0.05)
        return {"opened": True, "read": False}
    finally:
        capture.release()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-a", required=True, help="index, /dev/video path, or URL")
    parser.add_argument("--camera-b", required=True, help="index, /dev/video path, or URL")
    parser.add_argument("--camera-a-name", default="usb_webcam")
    parser.add_argument("--camera-b-name", default="android_droidcam")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--open-timeout", type=float, default=10.0)
    parser.add_argument("--pair-timeout", type=float, default=3.0)
    parser.add_argument(
        "--max-receive-skew-ms",
        "--max-skew-ms",
        dest="max_receive_skew_ms",
        type=float,
        default=150.0,
        help="host receive-time skew bound; not a hardware exposure-sync claim",
    )
    parser.add_argument("--max-frame-age-ms", type=float, default=1000.0)
    parser.add_argument("--min-focus", type=float, default=20.0)
    parser.add_argument("--min-luma", type=float, default=5.0)
    parser.add_argument("--max-luma", type=float, default=250.0)
    parser.add_argument("--m-min", type=int, default=DEFAULT_M_MIN)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--expected-model-sha256")
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--output-root", type=Path, default=Path("results") / "covis_live"
    )
    parser.add_argument("--require-cycles", type=int)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-release-probe", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("COVIS-%Y%m%dT%H%M%SZ")
    if not RUN_ID_RE.fullmatch(run_id):
        print("ERROR: run ID must contain only letters, digits, dot, dash or underscore")
        return 2
    if args.width <= 0 or args.height <= 0 or args.fps <= 0:
        print("ERROR: width, height and fps must be positive")
        return 2
    if not 0 < args.confidence <= 1:
        print("ERROR: confidence must be in (0, 1]")
        return 2
    if args.require_cycles is not None and args.require_cycles < 0:
        print("ERROR: require-cycles cannot be negative")
        return 2
    if args.duration_seconds is not None and args.duration_seconds <= 0:
        print("ERROR: duration-seconds must be positive")
        return 2
    if args.open_timeout <= 0 or args.pair_timeout <= 0:
        print("ERROR: camera timeouts must be positive")
        return 2
    if args.max_receive_skew_ms <= 0 or args.max_frame_age_ms <= 0:
        print("ERROR: receive-skew and frame-age bounds must be positive")
        return 2
    if args.min_focus < 0 or not 0 <= args.min_luma < args.max_luma <= 255:
        print("ERROR: invalid focus/luma health thresholds")
        return 2
    if args.m_min < 4:
        print("ERROR: m-min must be at least 4")
        return 2
    if not args.camera_a_name.strip() or not args.camera_b_name.strip():
        print("ERROR: camera names cannot be empty")
        return 2
    if args.camera_a_name == args.camera_b_name:
        print("ERROR: camera names must be distinct")
        return 2

    required_cycles = (
        args.require_cycles
        if args.require_cycles is not None
        else (3 if args.weights is not None else 0)
    )
    if required_cycles and args.headless:
        print("ERROR: required cycles need the interactive c/a/r labels")
        return 2

    try:
        source_a = _source_value(args.camera_a)
        source_b = _source_value(args.camera_b)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    if _source_identity(source_a) == _source_identity(source_b):
        print("ERROR: camera A and camera B must be distinct sources")
        return 2
    try:
        import cv2
    except ImportError:
        print("ERROR: opencv-python is required")
        return 2

    run_dir = args.output_root.resolve() / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"ERROR: refusing to overwrite run directory: {run_dir}")
        return 2

    claim_provider: Callable[[Any], PerceptionClaim] | None = None
    weights_hash: str | None = None
    try:
        if args.weights is not None:
            weights = args.weights.resolve(strict=True)
            if not args.expected_model_sha256:
                raise LiveDemoError(
                    "--expected-model-sha256 is required with --weights"
                )
            expected = args.expected_model_sha256.strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise LiveDemoError("expected model SHA-256 must be 64 hex characters")
            weights_hash = model_hash(str(weights))
            if weights_hash != expected:
                raise LiveDemoError(
                    f"model hash mismatch: expected {expected}, got {weights_hash}"
                )
            claim_provider = YoloClaimProvider(weights, args.confidence)
        elif args.expected_model_sha256:
            raise LiveDemoError("--expected-model-sha256 requires --weights")
    except Exception as exc:
        _atomic_json(
            run_dir / "summary.json",
            {
                "schema": SCHEMA,
                "run_id": run_id,
                "pass": False,
                "errors": [f"{type(exc).__name__}: {exc}"],
                "finished_at_utc": _utc_now(),
            },
        )
        print(f"ERROR: {exc}")
        return 1

    config_record = {
        "schema": SCHEMA,
        "run_id": run_id,
        "started_at_utc": _utc_now(),
        "command": list(sys.argv if argv is None else ["covis_live", *argv]),
        "unarmed": True,
        "camera_a": {"name": args.camera_a_name, "source": args.camera_a},
        "camera_b": {"name": args.camera_b_name, "source": args.camera_b},
        "capture": {"width": args.width, "height": args.height, "fps": args.fps},
        "thresholds": {
            "max_receive_skew_ms": args.max_receive_skew_ms,
            "timestamp_semantics": "host_receive_time_not_sensor_exposure_time",
            "max_frame_age_ms": args.max_frame_age_ms,
            "min_focus": args.min_focus,
            "min_luma": args.min_luma,
            "max_luma": args.max_luma,
            "m_min": args.m_min,
            "confidence": args.confidence,
        },
        "model_sha256": weights_hash,
        "semantic_mode": claim_provider is not None,
        "required_cycles": required_cycles,
    }
    _atomic_json(run_dir / "run_config.json", config_record)

    worker_a = CaptureWorker(
        name=args.camera_a_name,
        source=source_a,
        cv2_module=cv2,
        width=args.width,
        height=args.height,
        fps=args.fps,
    )
    worker_b = CaptureWorker(
        name=args.camera_b_name,
        source=source_b,
        cv2_module=cv2,
        width=args.width,
        height=args.height,
        fps=args.fps,
    )
    tracker = CycleTracker()
    counts: Counter[str] = Counter()
    errors: list[str] = []
    event_sequence = 0
    after_a = after_b = -1
    operator_label = "unlabelled"
    last_saved_signature: tuple[str, str] | None = None
    started_monotonic = time.monotonic()
    stop_reason = "unknown"
    released = {"camera_a_worker": False, "camera_b_worker": False}
    release_probe: dict[str, Any] = {}

    events_path = run_dir / "events.jsonl"
    events_handle = events_path.open("x", encoding="utf-8", newline="\n")
    try:
        worker_a.start()
        worker_b.start()
        worker_a.wait_ready(args.open_timeout)
        worker_b.wait_ready(args.open_timeout)
        print(
            f"READY {args.camera_a_name}={args.camera_a} "
            f"{args.camera_b_name}={args.camera_b} semantic={claim_provider is not None}"
        )
        if not args.headless:
            print("keys: c=clean a=attack r=recovery s=save q=quit")

        while True:
            packet_a, packet_b = _wait_new_pair(
                worker_a, worker_b, after_a, after_b, args.pair_timeout
            )
            after_a, after_b = packet_a.sequence, packet_b.sequence
            assessment = assess_pair(
                packet_a,
                packet_b,
                cv2_module=cv2,
                max_receive_skew_ms=args.max_receive_skew_ms,
                max_age_ms=args.max_frame_age_ms,
                min_focus=args.min_focus,
                min_luma=args.min_luma,
                max_luma=args.max_luma,
                m_min=args.m_min,
                claim_provider=claim_provider,
            )
            annotated = _annotated_pair(
                packet_a,
                packet_b,
                assessment,
                operator_label,
                cv2,
                args.camera_a_name,
                args.camera_b_name,
            )
            key = -1
            if not args.headless:
                cv2.imshow("VeriSwarm co-visibility | c/a/r label | s save | q quit", annotated)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("c"), ord("a"), ord("r")):
                    operator_label = {
                        ord("c"): "clean",
                        ord("a"): "attack",
                        ord("r"): "recovery",
                    }[key]
                    annotated = _annotated_pair(
                        packet_a,
                        packet_b,
                        assessment,
                        operator_label,
                        cv2,
                        args.camera_a_name,
                        args.camera_b_name,
                    )

            event_sequence += 1
            event = _event_record(
                sequence=event_sequence,
                packet_a=packet_a,
                packet_b=packet_b,
                assessment=assessment,
                operator_label=operator_label,
                camera_a_name=args.camera_a_name,
                camera_b_name=args.camera_b_name,
                model_sha256=weights_hash,
                m_min=args.m_min,
            )
            manual_label = key in (ord("c"), ord("a"), ord("r"))
            manual_save = key == ord("s")
            advanced = tracker.observe(
                operator_label, assessment.decision, assessment.reason
            ) if manual_label else False
            event["cycle"] = {
                "advanced": advanced,
                "next_phase": tracker.phase,
                "completed": tracker.completed,
            }
            signature = (operator_label, assessment.decision)
            if manual_label or manual_save or signature != last_saved_signature:
                _save_frames(run_dir, event, packet_a, packet_b, annotated, cv2)
                last_saved_signature = signature
            events_handle.write(
                json.dumps(event, sort_keys=True, allow_nan=False) + "\n"
            )
            events_handle.flush()
            counts[assessment.decision] += 1
            print(
                f"{event_sequence:05d} {operator_label:10s} "
                f"{assessment.decision:9s} {assessment.reason} "
                f"inliers={0 if assessment.features is None else assessment.features.inliers} "
                f"receive_skew_ms={assessment.receive_skew_ms:.1f} cycles={tracker.completed}",
                flush=True,
            )
            if key == ord("q"):
                stop_reason = "operator_q"
                break
            if (
                args.duration_seconds is not None
                and time.monotonic() - started_monotonic >= args.duration_seconds
            ):
                stop_reason = "duration_elapsed"
                break
    except KeyboardInterrupt:
        stop_reason = "keyboard_interrupt"
    except Exception as exc:
        stop_reason = "error"
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        events_handle.close()
        released["camera_a_worker"] = worker_a.close()
        released["camera_b_worker"] = worker_b.close()
        if not args.headless:
            cv2.destroyAllWindows()
        if not args.no_release_probe:
            release_probe = {
                "camera_a": _probe_release(source_a, cv2),
                "camera_b": _probe_release(source_b, cv2),
            }

    release_ok = all(released.values()) and (
        args.no_release_probe
        or all(item.get("opened") and item.get("read") for item in release_probe.values())
    )
    cycles_ok = tracker.completed >= required_cycles
    passed = not errors and release_ok and cycles_ok
    summary = {
        **config_record,
        "finished_at_utc": _utc_now(),
        "pass": passed,
        "stop_reason": stop_reason,
        "events": event_sequence,
        "decision_counts": dict(counts),
        "cycles_completed": tracker.completed,
        "cycles_required": required_cycles,
        "worker_release": released,
        "release_probe": release_probe,
        "release_verified": release_ok,
        "errors": errors,
        "events_sha256": _sha256(events_path),
    }
    _atomic_json(run_dir / "summary.json", summary)
    print(
        f"COMPLETE pass={passed} cycles={tracker.completed}/{required_cycles} "
        f"release={release_ok} evidence={run_dir}",
        flush=True,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
