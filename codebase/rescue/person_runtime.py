"""Dependency-light runtime contracts for rescue-person display decisions.

The detector and person-state assessor remain independently identifiable.  This
module only joins their evidence into a display *sidecar*; it never edits or
extends the frozen rescue event schema. Ambiguous evidence fails closed to a
red ``UNVERIFIED`` box, never to a green safe indication or a human-review gate.
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar


PERSON_DISPLAY_SIDECAR_SCHEMA = "veriswarm.rescue.person_display_sidecar.v1"
PERSON_CLASS_ID = "person_candidate"
SAFE_WALKING = "safe_walking"
DISASTER_STRESSED = "disaster_stressed"
STATE_LABELS = frozenset({SAFE_WALKING, DISASTER_STRESSED})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BBox = tuple[float, float, float, float]
_StateLabel = Literal["safe_walking", "disaster_stressed"]
_FrameT = TypeVar("_FrameT")


class PersonRuntimeError(ValueError):
    """Runtime evidence is malformed or violates ordering constraints."""


def _trimmed_text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise PersonRuntimeError(f"{field} must be a non-empty trimmed string")
    return value


def _confidence(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PersonRuntimeError(f"{field} must be a finite number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        raise PersonRuntimeError(f"{field} must be a finite number in [0, 1]")
    return result


def _timestamp_ms(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise PersonRuntimeError(f"{field} must be a non-negative integer timestamp")
    return value


def _normalized_bbox(value: Any, field: str = "bbox_norm") -> _BBox:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise PersonRuntimeError(f"{field} must contain exactly four coordinates")
    result: list[float] = []
    for index, coordinate in enumerate(value):
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise PersonRuntimeError(f"{field}[{index}] must be finite and normalized")
        number = float(coordinate)
        if not math.isfinite(number) or number < 0.0 or number > 1.0:
            raise PersonRuntimeError(f"{field}[{index}] must be finite and normalized")
        result.append(number)
    if result[2] <= result[0] or result[3] <= result[1]:
        raise PersonRuntimeError(f"{field} must satisfy x2>x1 and y2>y1")
    return (result[0], result[1], result[2], result[3])


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """Immutable identity of exact executed model bytes."""

    model_id: str
    sha256: str

    def __post_init__(self) -> None:
        _trimmed_text(self.model_id, "model_id")
        if type(self.sha256) is not str or not _SHA256_RE.fullmatch(self.sha256):
            raise PersonRuntimeError("sha256 must be exactly 64 lowercase hexadecimal characters")

    def as_mapping(self) -> dict[str, str]:
        return {"model_id": self.model_id, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class DetectionObservation:
    """One detector-owned ``person_candidate`` observation."""

    candidate_id: str
    frame_id: str
    bbox_norm: _BBox
    detector: ModelIdentity
    confidence: float
    observed_at_ms: int

    def __post_init__(self) -> None:
        _trimmed_text(self.candidate_id, "candidate_id")
        _trimmed_text(self.frame_id, "frame_id")
        if not isinstance(self.detector, ModelIdentity):
            raise PersonRuntimeError("detector must be a ModelIdentity")
        object.__setattr__(self, "bbox_norm", _normalized_bbox(self.bbox_norm))
        object.__setattr__(
            self, "confidence", _confidence(self.confidence, "detector confidence")
        )
        _timestamp_ms(self.observed_at_ms, "observed_at_ms")


@dataclass(frozen=True, slots=True)
class StateAssessment:
    """Independent state-model evidence bound to one detector candidate."""

    candidate_id: str
    frame_id: str
    bbox_norm: _BBox
    state_model: ModelIdentity
    label: _StateLabel
    confidence: float
    assessed_at_ms: int

    def __post_init__(self) -> None:
        _trimmed_text(self.candidate_id, "candidate_id")
        _trimmed_text(self.frame_id, "frame_id")
        if not isinstance(self.state_model, ModelIdentity):
            raise PersonRuntimeError("state_model must be a ModelIdentity")
        object.__setattr__(self, "bbox_norm", _normalized_bbox(self.bbox_norm))
        if self.label not in STATE_LABELS:
            raise PersonRuntimeError(
                "state label must be safe_walking or disaster_stressed"
            )
        object.__setattr__(
            self, "confidence", _confidence(self.confidence, "state confidence")
        )
        _timestamp_ms(self.assessed_at_ms, "assessed_at_ms")


@dataclass(frozen=True, slots=True)
class StateDisplayPolicy:
    """Frozen identities and thresholds used for one delivered application."""

    detector: ModelIdentity
    state_model: ModelIdentity
    minimum_detector_confidence: float
    minimum_state_confidence: float
    maximum_detection_age_ms: int
    maximum_state_age_ms: int
    maximum_assessment_delay_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.detector, ModelIdentity):
            raise PersonRuntimeError("policy detector must be a ModelIdentity")
        if not isinstance(self.state_model, ModelIdentity):
            raise PersonRuntimeError("policy state_model must be a ModelIdentity")
        object.__setattr__(
            self,
            "minimum_detector_confidence",
            _confidence(self.minimum_detector_confidence, "minimum_detector_confidence"),
        )
        object.__setattr__(
            self,
            "minimum_state_confidence",
            _confidence(self.minimum_state_confidence, "minimum_state_confidence"),
        )
        _timestamp_ms(self.maximum_detection_age_ms, "maximum_detection_age_ms")
        _timestamp_ms(self.maximum_state_age_ms, "maximum_state_age_ms")
        _timestamp_ms(
            self.maximum_assessment_delay_ms, "maximum_assessment_delay_ms"
        )


def person_display_sidecar(
    detection: DetectionObservation,
    assessment: StateAssessment | None,
    *,
    policy: StateDisplayPolicy,
    now_ms: int,
) -> dict[str, Any]:
    """Return an independent display mapping; all uncertain states are red.

    This function accepts no rescue-event mapping and therefore cannot mutate
    Suyash's event envelope.  ``reasons`` records every failed evidence gate so
    operators can distinguish a verified disaster from a review-required box.
    """

    if not isinstance(detection, DetectionObservation):
        raise PersonRuntimeError("detection must be a DetectionObservation")
    if assessment is not None and not isinstance(assessment, StateAssessment):
        raise PersonRuntimeError("assessment must be a StateAssessment or None")
    if not isinstance(policy, StateDisplayPolicy):
        raise PersonRuntimeError("policy must be a StateDisplayPolicy")
    current_time = _timestamp_ms(now_ms, "now_ms")

    reasons: list[str] = []
    if detection.detector != policy.detector:
        reasons.append("detector_identity_mismatch")
    if detection.confidence < policy.minimum_detector_confidence:
        reasons.append("detector_confidence_below_policy")
    if current_time < detection.observed_at_ms:
        reasons.append("detection_timestamp_in_future")
    elif current_time - detection.observed_at_ms > policy.maximum_detection_age_ms:
        reasons.append("detection_stale")

    if assessment is None:
        reasons.append("state_assessment_missing")
    else:
        if assessment.state_model != policy.state_model:
            reasons.append("state_model_identity_mismatch")
        if assessment.candidate_id != detection.candidate_id:
            reasons.append("candidate_id_mismatch")
        if assessment.frame_id != detection.frame_id:
            reasons.append("frame_id_mismatch")
        if assessment.bbox_norm != detection.bbox_norm:
            reasons.append("bbox_mismatch")
        if assessment.confidence < policy.minimum_state_confidence:
            reasons.append("state_confidence_below_policy")
        if assessment.assessed_at_ms < detection.observed_at_ms:
            reasons.append("assessment_precedes_detection")
        elif (
            assessment.assessed_at_ms - detection.observed_at_ms
            > policy.maximum_assessment_delay_ms
        ):
            reasons.append("assessment_delay_exceeds_policy")
        if current_time < assessment.assessed_at_ms:
            reasons.append("assessment_timestamp_in_future")
        elif current_time - assessment.assessed_at_ms > policy.maximum_state_age_ms:
            reasons.append("state_assessment_stale")

    verified = not reasons and assessment is not None
    if verified and assessment.label == SAFE_WALKING:
        box_color = "green"
        display_status = "SAFE_WALKING"
    elif verified and assessment.label == DISASTER_STRESSED:
        box_color = "red"
        display_status = "DISASTER_STRESSED"
    else:
        box_color = "red"
        display_status = "UNVERIFIED"

    return {
        "schema": PERSON_DISPLAY_SIDECAR_SCHEMA,
        "candidate_id": detection.candidate_id,
        "frame_id": detection.frame_id,
        "class_id": PERSON_CLASS_ID,
        "bbox_norm": list(detection.bbox_norm),
        "detector": detection.detector.as_mapping(),
        "detector_confidence": detection.confidence,
        "observed_at_ms": detection.observed_at_ms,
        "state_assessment": (
            None
            if assessment is None
            else {
                "candidate_id": assessment.candidate_id,
                "frame_id": assessment.frame_id,
                "bbox_norm": list(assessment.bbox_norm),
                "state_model": assessment.state_model.as_mapping(),
                "label": assessment.label,
                "confidence": assessment.confidence,
                "assessed_at_ms": assessment.assessed_at_ms,
            }
        ),
        "display": {
            "box_color": box_color,
            "status": display_status,
            "verified": verified,
            "reasons": reasons,
        },
    }


@dataclass(frozen=True, slots=True)
class FramePacket(Generic[_FrameT]):
    """One timestamped frame offered to :class:`NewestFrameMailbox`."""

    sequence: int
    source_timestamp_ms: int
    frame: _FrameT

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise PersonRuntimeError("frame sequence must be a non-negative integer")
        _timestamp_ms(self.source_timestamp_ms, "source_timestamp_ms")
        if self.frame is None:
            raise PersonRuntimeError("frame must not be None")


@dataclass(frozen=True, slots=True)
class MailboxPublishResult:
    """Atomic outcome of one accepted frame publication."""

    accepted_sequence: int
    replaced_pending_frame: bool
    dropped_frames_total: int


@dataclass(frozen=True, slots=True)
class MailboxSnapshot:
    """Lock-consistent mailbox counters and ordering watermark."""

    has_unconsumed_frame: bool
    last_sequence: int | None
    last_source_timestamp_ms: int | None
    dropped_frames_total: int
    consumed_frames_total: int


class NewestFrameMailbox(Generic[_FrameT]):
    """Thread-safe single-slot mailbox that never builds a stale-frame queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: FramePacket[_FrameT] | None = None
        self._last_sequence: int | None = None
        self._last_source_timestamp_ms: int | None = None
        self._dropped_frames_total = 0
        self._consumed_frames_total = 0

    def publish(self, packet: FramePacket[_FrameT]) -> MailboxPublishResult:
        """Atomically publish a newer frame, replacing any unconsumed frame."""

        if not isinstance(packet, FramePacket):
            raise PersonRuntimeError("packet must be a FramePacket")
        with self._lock:
            if self._last_sequence is not None and packet.sequence <= self._last_sequence:
                raise PersonRuntimeError("frame sequence must increase monotonically")
            if (
                self._last_source_timestamp_ms is not None
                and packet.source_timestamp_ms < self._last_source_timestamp_ms
            ):
                raise PersonRuntimeError("source timestamp must not decrease")
            replaced = self._pending is not None
            if replaced:
                self._dropped_frames_total += 1
            self._pending = packet
            self._last_sequence = packet.sequence
            self._last_source_timestamp_ms = packet.source_timestamp_ms
            return MailboxPublishResult(
                accepted_sequence=packet.sequence,
                replaced_pending_frame=replaced,
                dropped_frames_total=self._dropped_frames_total,
            )

    def take_newest(self) -> FramePacket[_FrameT] | None:
        """Atomically return and consume only the newest pending frame."""

        with self._lock:
            packet = self._pending
            if packet is not None:
                self._pending = None
                self._consumed_frames_total += 1
            return packet

    def snapshot(self) -> MailboxSnapshot:
        """Return counters and ordering watermarks from one atomic snapshot."""

        with self._lock:
            return MailboxSnapshot(
                has_unconsumed_frame=self._pending is not None,
                last_sequence=self._last_sequence,
                last_source_timestamp_ms=self._last_source_timestamp_ms,
                dropped_frames_total=self._dropped_frames_total,
                consumed_frames_total=self._consumed_frames_total,
            )


__all__ = [
    "DISASTER_STRESSED",
    "PERSON_DISPLAY_SIDECAR_SCHEMA",
    "SAFE_WALKING",
    "DetectionObservation",
    "FramePacket",
    "MailboxPublishResult",
    "MailboxSnapshot",
    "ModelIdentity",
    "NewestFrameMailbox",
    "PersonRuntimeError",
    "StateAssessment",
    "StateDisplayPolicy",
    "person_display_sidecar",
]
