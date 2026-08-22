"""Observation-only perception adapter for the rescue data plane.

This module is Samik's producer boundary.  It converts one normalized detector
result into Suyash's frozen ``veriswarm.rescue.event.v1`` envelope and validates
the result before a caller sends or buffers it.  It deliberately emits no
control vector: semantic detections are observations, never flight commands.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from perception.yolo_action import Detection
from rescue.schema import (
    OBSERVATION_CLASSES,
    RESCUE_SCHEMA,
    RescueEventError,
    validate_rescue_event,
)


PERSON_LABEL_ALIASES = frozenset({"person", "survivor", "person_candidate"})


class PerceptionAdapterError(ValueError):
    """A detector result cannot be represented by the frozen rescue contract."""


def canonical_rescue_class(label: str) -> str:
    """Return the mission class for one model label.

    Model labels named ``person`` or ``survivor`` are intentionally downgraded
    to ``person_candidate``.  The event bus must never claim that vision alone
    confirmed a survivor.  Hazard labels must already use the frozen mission
    taxonomy; silently guessing hazard aliases would make the class map
    ambiguous.
    """
    if not isinstance(label, str) or not label.strip():
        raise PerceptionAdapterError("class-map labels must be non-empty strings")
    normalized = label.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in PERSON_LABEL_ALIASES:
        return "person_candidate"
    if normalized not in OBSERVATION_CLASSES:
        raise PerceptionAdapterError(f"unsupported rescue class: {label!r}")
    return normalized


def detection_bbox_norm(detection: Detection) -> list[float]:
    """Convert normalized centre/size coordinates to clipped ``[x1,y1,x2,y2]``."""
    if not isinstance(detection, Detection):
        raise PerceptionAdapterError("detection must be perception.yolo_action.Detection")
    x1 = max(0.0, detection.x - detection.w / 2.0)
    y1 = max(0.0, detection.y - detection.h / 2.0)
    x2 = min(1.0, detection.x + detection.w / 2.0)
    y2 = min(1.0, detection.y + detection.h / 2.0)
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
        raise PerceptionAdapterError("bbox coordinates must be finite")
    if x2 <= x1 or y2 <= y1:
        raise PerceptionAdapterError("detection must have positive visible box area")
    return [x1, y1, x2, y2]


def build_observation_event(
    detection: Detection,
    *,
    class_map: Mapping[int, str],
    mission_id: str,
    node: str,
    source_seq: int,
    observed_at_ms: int,
    event_id: str,
    observation_id: str,
    frame_id: str,
    modality: str,
    model_id: str,
    model_sha256: str | None = None,
    position_ned: Sequence[float] | None = None,
    uncertainty_m: float | None = None,
) -> dict[str, Any]:
    """Build and validate one rescue ``observation`` producer event.

    ``position_ned`` is included only when the caller supplies a genuine
    sensor/simulator-fusion estimate together with its uncertainty.  Supplying
    only one of those values fails closed instead of fabricating map evidence.

    The caller owns stable ``event_id``/``observation_id`` allocation and
    monotonic ``source_seq`` persistence so a buffered event can be replayed
    byte-for-byte after a link interruption.
    """
    if isinstance(detection.cls, bool) or detection.cls not in class_map:
        raise PerceptionAdapterError(
            f"detector class {detection.cls!r} is missing from the frozen class map"
        )
    class_id = canonical_rescue_class(class_map[detection.cls])
    if (position_ned is None) != (uncertainty_m is None):
        raise PerceptionAdapterError(
            "position_ned and uncertainty_m must be supplied together"
        )

    payload: dict[str, Any] = {
        "node": node,
        "observation_id": observation_id,
        "class_id": class_id,
        "confidence": detection.conf,
        "frame_id": frame_id,
        "modality": modality,
        "model_id": model_id,
        "model_sha256": model_sha256,
        "bbox_norm": detection_bbox_norm(detection),
    }
    if position_ned is not None:
        payload["position_ned"] = list(position_ned)
        payload["uncertainty_m"] = uncertainty_m

    candidate = {
        "schema": RESCUE_SCHEMA,
        "mission_id": mission_id,
        "event_id": event_id,
        "source": node,
        "source_seq": source_seq,
        "observed_at_ms": observed_at_ms,
        "kind": "observation",
        "payload": payload,
    }
    try:
        return validate_rescue_event(candidate, expected_mission_id=mission_id)
    except RescueEventError as error:
        raise PerceptionAdapterError(str(error)) from error
