"""Calibrated multi-view localization and survivor-first evidence fusion.

The PBFT/receipt layer decides whether autonomous control evidence is trusted. This
module answers a different question: how should positive person observations from
different viewpoints be combined for rescue response?

A valid positive observation always produces a responder alert. A camera that did not
detect the person is not a negative vote unless the camera was healthy, shared the scene,
the candidate was expected inside its frame, and line of sight was independently proven.
Even then the miss requests security review; it never deletes the positive candidate.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence


Vec3 = tuple[float, float, float]
Matrix3 = tuple[Vec3, Vec3, Vec3]
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SECURITY_STATES = frozenset({"VERIFIED", "UNVERIFIED", "DISPUTED"})


class MultiViewFusionError(ValueError):
    """Multi-view evidence is invalid or geometrically insufficient."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MultiViewFusionError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise MultiViewFusionError(f"{name} must be finite")
    return result


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise MultiViewFusionError(f"{name} must be a safe identifier")
    return value


def _vec3(value: Sequence[float], name: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise MultiViewFusionError(f"{name} must contain three numbers")
    return tuple(
        _finite(component, f"{name}[{index}]")
        for index, component in enumerate(value)
    )  # type: ignore[return-value]


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(value: Vec3, factor: float) -> Vec3:
    return (value[0] * factor, value[1] * factor, value[2] * factor)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(value: Vec3) -> float:
    return math.sqrt(_dot(value, value))


def _unit(value: Vec3, name: str) -> Vec3:
    length = _norm(value)
    if length <= 1e-12:
        raise MultiViewFusionError(f"{name} must be non-zero")
    return _scale(value, 1.0 / length)


def _mat_vec(matrix: Matrix3, vector: Vec3) -> Vec3:
    return tuple(_dot(row, vector) for row in matrix)  # type: ignore[return-value]


def _distance(a: Vec3, b: Vec3) -> float:
    return _norm(_sub(a, b))


def _validate_rotation(matrix: Sequence[Sequence[float]]) -> Matrix3:
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 3:
        raise MultiViewFusionError("rotation_camera_to_ned must be 3x3")
    rows = tuple(_vec3(row, f"rotation_camera_to_ned[{index}]") for index, row in enumerate(matrix))
    for index, row in enumerate(rows):
        if abs(_norm(row) - 1.0) > 1e-4:
            raise MultiViewFusionError(f"rotation row {index} is not unit length")
    if any(abs(_dot(rows[left], rows[right])) > 1e-4 for left, right in ((0, 1), (0, 2), (1, 2))):
        raise MultiViewFusionError("rotation rows must be orthogonal")
    determinant = (
        rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
        - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
        + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    )
    if abs(determinant - 1.0) > 1e-4:
        raise MultiViewFusionError("rotation must be right-handed with determinant +1")
    return rows  # type: ignore[return-value]


@dataclass(frozen=True)
class CameraModel:
    camera_id: str
    calibration_id: str
    width_px: int
    height_px: int
    fx_px: float
    fy_px: float
    cx_px: float
    cy_px: float
    position_ned: Vec3
    rotation_camera_to_ned: Matrix3
    pose_uncertainty_m: float = 0.25
    bearing_uncertainty_deg: float = 1.5

    def __post_init__(self) -> None:
        object.__setattr__(self, "camera_id", _identifier(self.camera_id, "camera_id"))
        object.__setattr__(
            self, "calibration_id", _identifier(self.calibration_id, "calibration_id")
        )
        if (
            isinstance(self.width_px, bool)
            or not isinstance(self.width_px, int)
            or self.width_px < 2
        ):
            raise MultiViewFusionError("width_px must be an integer >= 2")
        if (
            isinstance(self.height_px, bool)
            or not isinstance(self.height_px, int)
            or self.height_px < 2
        ):
            raise MultiViewFusionError("height_px must be an integer >= 2")
        for field in ("fx_px", "fy_px"):
            value = _finite(getattr(self, field), field)
            if value <= 0:
                raise MultiViewFusionError(f"{field} must be positive")
            object.__setattr__(self, field, value)
        object.__setattr__(self, "cx_px", _finite(self.cx_px, "cx_px"))
        object.__setattr__(self, "cy_px", _finite(self.cy_px, "cy_px"))
        object.__setattr__(self, "position_ned", _vec3(self.position_ned, "position_ned"))
        object.__setattr__(
            self,
            "rotation_camera_to_ned",
            _validate_rotation(self.rotation_camera_to_ned),
        )
        pose_uncertainty = _finite(self.pose_uncertainty_m, "pose_uncertainty_m")
        bearing_uncertainty = _finite(
            self.bearing_uncertainty_deg, "bearing_uncertainty_deg"
        )
        if pose_uncertainty < 0:
            raise MultiViewFusionError("pose_uncertainty_m must be >= 0")
        if not 0 < bearing_uncertainty <= 45:
            raise MultiViewFusionError("bearing_uncertainty_deg must be inside (0, 45]")
        object.__setattr__(self, "pose_uncertainty_m", pose_uncertainty)
        object.__setattr__(self, "bearing_uncertainty_deg", bearing_uncertainty)


@dataclass(frozen=True)
class PersonView:
    observation_id: str
    source: str
    frame_id: str
    observed_at_ms: int
    confidence: float
    bbox_norm: tuple[float, float, float, float]
    camera: CameraModel
    range_m: float | None = None
    range_uncertainty_m: float | None = None
    evidence_security: str = "UNVERIFIED"
    evidence_security_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in ("observation_id", "source", "frame_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        if isinstance(self.observed_at_ms, bool) or not isinstance(self.observed_at_ms, int):
            raise MultiViewFusionError("observed_at_ms must be an integer")
        confidence = _finite(self.confidence, "confidence")
        if not 0 <= confidence <= 1:
            raise MultiViewFusionError("confidence must be inside [0, 1]")
        object.__setattr__(self, "confidence", confidence)
        if not isinstance(self.bbox_norm, (list, tuple)) or len(self.bbox_norm) != 4:
            raise MultiViewFusionError("bbox_norm must contain four numbers")
        box = tuple(
            _finite(value, f"bbox_norm[{index}]")
            for index, value in enumerate(self.bbox_norm)
        )
        if any(value < 0 or value > 1 for value in box):
            raise MultiViewFusionError("bbox_norm must be inside [0, 1]")
        if box[2] <= box[0] or box[3] <= box[1]:
            raise MultiViewFusionError("bbox_norm must satisfy x2>x1 and y2>y1")
        object.__setattr__(self, "bbox_norm", box)
        if (self.range_m is None) != (self.range_uncertainty_m is None):
            raise MultiViewFusionError(
                "range_m and range_uncertainty_m must be supplied together"
            )
        if self.range_m is not None:
            distance = _finite(self.range_m, "range_m")
            uncertainty = _finite(self.range_uncertainty_m, "range_uncertainty_m")
            if distance <= 0 or uncertainty < 0:
                raise MultiViewFusionError(
                    "range_m must be positive and range_uncertainty_m non-negative"
                )
            object.__setattr__(self, "range_m", distance)
            object.__setattr__(self, "range_uncertainty_m", uncertainty)
        if self.evidence_security not in SECURITY_STATES:
            raise MultiViewFusionError("unsupported evidence_security state")
        if not isinstance(self.evidence_security_reasons, (list, tuple)):
            raise MultiViewFusionError("evidence_security_reasons must be a list")
        reasons: list[str] = []
        for index, reason in enumerate(self.evidence_security_reasons):
            if not isinstance(reason, str) or not reason or len(reason) > 128:
                raise MultiViewFusionError(
                    f"evidence_security_reasons[{index}] must be 1-128 characters"
                )
            reasons.append(reason)
        object.__setattr__(self, "evidence_security_reasons", tuple(reasons))


@dataclass(frozen=True)
class MissingViewEvidence:
    camera_id: str
    source: str
    frame_id: str
    observed_at_ms: int
    healthy: bool
    inference_succeeded: bool
    shares_scene: bool
    expected_in_frame: bool
    line_of_sight_proven: bool
    reason: str

    def __post_init__(self) -> None:
        for field in ("camera_id", "source", "frame_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        if isinstance(self.observed_at_ms, bool) or not isinstance(self.observed_at_ms, int):
            raise MultiViewFusionError("observed_at_ms must be an integer")
        for field in (
            "healthy",
            "inference_succeeded",
            "shares_scene",
            "expected_in_frame",
            "line_of_sight_proven",
        ):
            if type(getattr(self, field)) is not bool:
                raise MultiViewFusionError(f"{field} must be boolean")
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 256:
            raise MultiViewFusionError("reason must be non-empty text up to 256 characters")

    @property
    def disposition(self) -> str:
        if not self.healthy or not self.inference_succeeded:
            return "ABSTAIN_UNHEALTHY"
        if not self.shares_scene or not self.expected_in_frame:
            return "ABSTAIN_NOT_COVISIBLE"
        if not self.line_of_sight_proven:
            return "ABSTAIN_OCCLUDED_OR_UNPROVEN"
        return "EXPECTED_VISIBLE_MISS"


@dataclass(frozen=True)
class BearingRay:
    observation_id: str
    source: str
    camera_id: str
    origin_ned: Vec3
    direction_ned: Vec3
    bearing_uncertainty_deg: float
    observed_at_ms: int


@dataclass(frozen=True)
class PointEstimate:
    position_ned: Vec3
    uncertainty_m: float
    method: str
    observation_ids: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class FusedPersonCandidate:
    candidate_id: str
    positive_observation_ids: tuple[str, ...]
    positive_sources: tuple[str, ...]
    confidence: float
    bearings: tuple[BearingRay, ...]
    position_ned: Vec3 | None
    uncertainty_m: float | None
    localization_method: str
    corroborated: bool
    corroborated_sources: tuple[str, ...]
    priority: str
    alert_required: bool
    expected_visible_misses: tuple[str, ...]
    missing_view_dispositions: tuple[tuple[str, str], ...]
    security_review_required: bool
    security_reasons: tuple[str, ...]
    localization_warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def enrichment_for(self, view: PersonView) -> dict[str, Any]:
        ray = next(
            (item for item in self.bearings if item.observation_id == view.observation_id),
            None,
        )
        if ray is None:
            raise MultiViewFusionError("view is not part of this fused candidate")
        enrichment: dict[str, Any] = {
            "capture_group_id": self.candidate_id,
            "camera_id": view.camera.camera_id,
            "camera_calibration_id": view.camera.calibration_id,
            "viewpoint_ned": list(ray.origin_ned),
            "bearing_ned": list(ray.direction_ned),
            "bearing_uncertainty_deg": ray.bearing_uncertainty_deg,
            "localization_method": self.localization_method,
            "evidence_security": view.evidence_security,
            "corroborated_sources": (
                list(self.corroborated_sources)
                if view.source in self.corroborated_sources
                else []
            ),
            "expected_visible_misses": list(self.expected_visible_misses),
            "security_reasons": list(self.security_reasons),
        }
        if view.range_m is not None:
            enrichment["metric_range_m"] = view.range_m
            enrichment["range_uncertainty_m"] = view.range_uncertainty_m
        if self.position_ned is not None:
            enrichment["position_ned"] = list(self.position_ned)
            enrichment["uncertainty_m"] = self.uncertainty_m
        return enrichment


def bearing_from_view(view: PersonView) -> BearingRay:
    """Return the calibrated NED ray through the person-box centre."""
    x1, y1, x2, y2 = view.bbox_norm
    u_px = (x1 + x2) * 0.5 * view.camera.width_px
    v_px = (y1 + y2) * 0.5 * view.camera.height_px
    camera_ray = _unit(
        (
            (u_px - view.camera.cx_px) / view.camera.fx_px,
            (v_px - view.camera.cy_px) / view.camera.fy_px,
            1.0,
        ),
        "camera ray",
    )
    direction_ned = _unit(
        _mat_vec(view.camera.rotation_camera_to_ned, camera_ray),
        "NED bearing",
    )
    return BearingRay(
        observation_id=view.observation_id,
        source=view.source,
        camera_id=view.camera.camera_id,
        origin_ned=view.camera.position_ned,
        direction_ned=direction_ned,
        bearing_uncertainty_deg=view.camera.bearing_uncertainty_deg,
        observed_at_ms=view.observed_at_ms,
    )


def locate_with_metric_range(view: PersonView) -> PointEstimate:
    """Locate one positive observation using a metric range along its calibrated ray."""
    if view.range_m is None or view.range_uncertainty_m is None:
        raise MultiViewFusionError("metric range is unavailable")
    ray = bearing_from_view(view)
    position = _add(ray.origin_ned, _scale(ray.direction_ned, view.range_m))
    lateral_uncertainty = view.range_m * math.tan(
        math.radians(ray.bearing_uncertainty_deg)
    )
    uncertainty = math.sqrt(
        view.camera.pose_uncertainty_m**2
        + view.range_uncertainty_m**2
        + lateral_uncertainty**2
    )
    return PointEstimate(
        position_ned=position,
        uncertainty_m=uncertainty,
        method="metric_range",
        observation_ids=(view.observation_id,),
        sources=(view.source,),
    )


def triangulate_positive_views(
    first: PersonView,
    second: PersonView,
    *,
    max_time_skew_ms: int = 500,
    min_parallax_deg: float = 3.0,
    max_ray_gap_m: float = 3.0,
) -> PointEstimate:
    """Triangulate two positive, calibrated bearing rays.

    A non-detection cannot be used as a ray and therefore cannot triangulate a person.
    """
    if first.camera.camera_id == second.camera.camera_id:
        raise MultiViewFusionError("triangulation requires different cameras")
    if abs(first.observed_at_ms - second.observed_at_ms) > max_time_skew_ms:
        raise MultiViewFusionError("positive views exceed the time-skew limit")
    ray_a, ray_b = bearing_from_view(first), bearing_from_view(second)
    cosine = max(-1.0, min(1.0, _dot(ray_a.direction_ned, ray_b.direction_ned)))
    angle_deg = math.degrees(math.acos(cosine))
    if angle_deg < min_parallax_deg:
        raise MultiViewFusionError("positive views have insufficient parallax")

    origin_delta = _sub(ray_a.origin_ned, ray_b.origin_ned)
    denominator = 1.0 - cosine**2
    if denominator <= 1e-12:
        raise MultiViewFusionError("positive view rays are parallel")
    d_term = _dot(ray_a.direction_ned, origin_delta)
    e_term = _dot(ray_b.direction_ned, origin_delta)
    distance_a = (cosine * e_term - d_term) / denominator
    distance_b = (e_term - cosine * d_term) / denominator
    if distance_a <= 0 or distance_b <= 0:
        raise MultiViewFusionError("triangulated point lies behind a camera")
    point_a = _add(ray_a.origin_ned, _scale(ray_a.direction_ned, distance_a))
    point_b = _add(ray_b.origin_ned, _scale(ray_b.direction_ned, distance_b))
    ray_gap = _distance(point_a, point_b)
    if ray_gap > max_ray_gap_m:
        raise MultiViewFusionError("positive view rays do not intersect within tolerance")
    position = _scale(_add(point_a, point_b), 0.5)
    average_range = (distance_a + distance_b) * 0.5
    angular_uncertainty = math.radians(
        max(ray_a.bearing_uncertainty_deg, ray_b.bearing_uncertainty_deg)
    )
    geometry_uncertainty = average_range * math.tan(angular_uncertainty) / max(
        math.sin(math.radians(angle_deg)), 1e-6
    )
    uncertainty = math.sqrt(
        first.camera.pose_uncertainty_m**2
        + second.camera.pose_uncertainty_m**2
        + geometry_uncertainty**2
        + (ray_gap * 0.5) ** 2
    )
    return PointEstimate(
        position_ned=position,
        uncertainty_m=uncertainty,
        method="ray_triangulation",
        observation_ids=tuple(sorted((first.observation_id, second.observation_id))),
        sources=tuple(sorted((first.source, second.source))),
    )


def _consistent_point_fusion(
    estimates: Sequence[PointEstimate],
    *,
    max_spread_m: float,
) -> tuple[Vec3 | None, float | None, tuple[str, ...]]:
    if not estimates:
        return None, None, ()
    warnings: list[str] = []
    if len(estimates) > 1:
        spread = max(
            _distance(left.position_ned, right.position_ned)
            for left, right in itertools.combinations(estimates, 2)
        )
        if spread > max_spread_m:
            return None, None, (f"location_estimates_conflict:spread_m={spread:.3f}",)
    weights = [1.0 / max(estimate.uncertainty_m**2, 1e-6) for estimate in estimates]
    total_weight = sum(weights)
    position = tuple(
        sum(weight * estimate.position_ned[axis] for weight, estimate in zip(weights, estimates))
        / total_weight
        for axis in range(3)
    )
    residual = max(_distance(position, estimate.position_ned) for estimate in estimates)
    uncertainty = math.sqrt(1.0 / total_weight + residual**2)
    return position, uncertainty, tuple(warnings)  # type: ignore[return-value]


def fuse_person_views(
    candidate_id: str,
    positive_views: Iterable[PersonView],
    *,
    missing_views: Iterable[MissingViewEvidence] = (),
    max_time_skew_ms: int = 500,
    min_parallax_deg: float = 3.0,
    max_ray_gap_m: float = 3.0,
    max_location_spread_m: float = 5.0,
) -> FusedPersonCandidate:
    """Fuse a caller-grouped person candidate without negative-vote suppression.

    The caller must group detections believed to represent the same person using tracking,
    appearance, calibrated geometry or simulator identity. This function deliberately does
    not infer identity from raw cross-camera bounding-box IoU.
    """
    candidate_id = _identifier(candidate_id, "candidate_id")
    views = tuple(positive_views)
    missing = tuple(missing_views)
    if not views:
        raise MultiViewFusionError("at least one positive person view is required")
    if len({view.observation_id for view in views}) != len(views):
        raise MultiViewFusionError("positive observation IDs must be unique")
    positive_camera_ids = {view.camera.camera_id for view in views}
    missing_camera_ids = [view.camera_id for view in missing]
    if len(set(missing_camera_ids)) != len(missing_camera_ids):
        raise MultiViewFusionError("missing-view camera IDs must be unique")
    if positive_camera_ids.intersection(missing_camera_ids):
        raise MultiViewFusionError(
            "the same camera cannot be both positive and missing in one capture group"
        )
    bearings = tuple(bearing_from_view(view) for view in views)
    estimates: list[PointEstimate] = []
    localization_warnings: list[str] = []
    for view in views:
        if view.range_m is not None:
            estimates.append(locate_with_metric_range(view))
    for first, second in itertools.combinations(views, 2):
        try:
            estimates.append(
                triangulate_positive_views(
                    first,
                    second,
                    max_time_skew_ms=max_time_skew_ms,
                    min_parallax_deg=min_parallax_deg,
                    max_ray_gap_m=max_ray_gap_m,
                )
            )
        except MultiViewFusionError as error:
            localization_warnings.append(str(error))

    position, uncertainty, consistency_warnings = _consistent_point_fusion(
        estimates, max_spread_m=max_location_spread_m
    )
    localization_warnings.extend(consistency_warnings)
    methods = {estimate.method for estimate in estimates}
    if position is None:
        method = "bearing_only"
    elif methods == {"metric_range"}:
        method = "metric_range"
    elif methods == {"ray_triangulation"}:
        method = "ray_triangulation"
    else:
        method = "multi_view_fusion"

    positive_sources = tuple(sorted({view.source for view in views}))
    synchronized_sources = {
        source
        for first, second in itertools.combinations(views, 2)
        if first.source != second.source
        and abs(first.observed_at_ms - second.observed_at_ms) <= max_time_skew_ms
        for source in (first.source, second.source)
    }
    if len(views) > 1 and (
        max(view.observed_at_ms for view in views)
        - min(view.observed_at_ms for view in views)
        > max_time_skew_ms
    ):
        localization_warnings.append("positive_views_span_time_limit")

    def missing_disposition(view: MissingViewEvidence) -> str:
        nearest_positive_skew = min(
            abs(view.observed_at_ms - positive.observed_at_ms) for positive in views
        )
        if nearest_positive_skew > max_time_skew_ms:
            return "ABSTAIN_STALE"
        return view.disposition

    missing_dispositions = tuple(
        sorted((view.camera_id, missing_disposition(view)) for view in missing)
    )
    expected_misses = tuple(
        camera_id
        for camera_id, disposition in missing_dispositions
        if disposition == "EXPECTED_VISIBLE_MISS"
    )
    security_reasons = [
        f"positive_view_disputed:{view.camera.camera_id}"
        for view in views
        if view.evidence_security == "DISPUTED"
    ]
    security_reasons.extend(
        f"{view.camera.camera_id}:{reason}"
        for view in views
        for reason in view.evidence_security_reasons
    )
    security_reasons.extend(f"expected_visible_miss:{camera}" for camera in expected_misses)
    return FusedPersonCandidate(
        candidate_id=candidate_id,
        positive_observation_ids=tuple(sorted(view.observation_id for view in views)),
        positive_sources=positive_sources,
        confidence=max(view.confidence for view in views),
        bearings=bearings,
        position_ned=position,
        uncertainty_m=uncertainty,
        localization_method=method,
        corroborated=len(synchronized_sources) >= 2,
        corroborated_sources=tuple(sorted(synchronized_sources)),
        priority="HIGH",
        alert_required=True,
        expected_visible_misses=expected_misses,
        missing_view_dispositions=missing_dispositions,
        security_review_required=bool(security_reasons),
        security_reasons=tuple(sorted(security_reasons)),
        localization_warnings=tuple(sorted(set(localization_warnings))),
    )
