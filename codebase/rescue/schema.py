"""Validation for producer events entering the rescue mission data plane.

The existing :mod:`node.events` log owns persistence, ordering and its hash chain.
This module validates the rescue-specific envelope before anything reaches that log.
It deliberately has no simulator, model or dashboard dependency, so Samik and
Pratik can test their producers without waiting for the integrated mission.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any


RESCUE_SCHEMA = "veriswarm.rescue.event.v1"

EVENT_KINDS = frozenset({
    "mission_started",
    "assignment",
    "coverage",
    "vehicle_state",
    "observation",
    "hazard",
    "authorization",
    "task_reassigned",
    "link_state",
    "mission_completed",
})

PERSON_CLASS = "person_candidate"
HAZARD_CLASSES = frozenset({
    "water_or_flood",
    "road_blocked",
    "debris",
    "fire",
    "smoke",
    "structure_damage",
    "landslide",
})
OBSERVATION_CLASSES = frozenset({PERSON_CLASS, *HAZARD_CLASSES})
MODALITIES = frozenset({"rgb", "thermal", "synthetic_thermal"})
LOCALIZATION_METHODS = frozenset({
    "bearing_only",
    "metric_range",
    "ray_triangulation",
    "multi_view_fusion",
    "external_pose_fusion",
})
EVIDENCE_SECURITY_STATES = frozenset({"VERIFIED", "UNVERIFIED", "DISPUTED"})
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
NODE_STREAM_ROLES = {
    "coverage": frozenset({"telemetry"}),
    "vehicle_state": frozenset({"telemetry"}),
    "observation": frozenset({"perception"}),
    "hazard": frozenset({"fusion"}),
    "link_state": frozenset({"telemetry"}),
}


class RescueEventError(ValueError):
    """A producer event violates the shared rescue contract."""


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise RescueEventError(f"{field} must be a safe 1-128 character identifier")
    return value


def _text(value: Any, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise RescueEventError(f"{field} must be non-empty text up to {maximum} characters")
    return value.strip()


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RescueEventError(f"{field} must be an integer >= {minimum}")
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RescueEventError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise RescueEventError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise RescueEventError(f"{field} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise RescueEventError(f"{field} must be <= {maximum}")
    return result


def _vector(value: Any, field: str, *, length: int) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise RescueEventError(f"{field} must contain exactly {length} numbers")
    return [_number(item, f"{field}[{index}]") for index, item in enumerate(value)]


def _identifier_list(value: Any, field: str, *, maximum: int = 32) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise RescueEventError(f"{field} must be a list of at most {maximum} identifiers")
    normalized = [_identifier(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if len(set(normalized)) != len(normalized):
        raise RescueEventError(f"{field} must not contain duplicates")
    return sorted(normalized)


def _text_list(value: Any, field: str, *, maximum: int = 32) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise RescueEventError(f"{field} must be a list of at most {maximum} strings")
    normalized = [
        _text(item, f"{field}[{index}]", maximum=256)
        for index, item in enumerate(value)
    ]
    return sorted(set(normalized))


def _source_owns_node(source: str, node: str, kind: str) -> bool:
    """Return whether a producer stream is authorized to speak for its node.

    Exact node sources remain valid for backward compatibility. Role-scoped sources let
    telemetry, perception and fusion processes keep independent monotonic sequences.
    """
    if source == node:
        return True
    return any(
        source == f"{node}.{role}" for role in NODE_STREAM_ROLES.get(kind, ())
    )


def _optional_position(payload: dict[str, Any]) -> None:
    if "position_ned" in payload and payload["position_ned"] is not None:
        payload["position_ned"] = _vector(payload["position_ned"], "position_ned", length=3)
    if "uncertainty_m" in payload and payload["uncertainty_m"] is not None:
        payload["uncertainty_m"] = _number(
            payload["uncertainty_m"], "uncertainty_m", minimum=0.0
        )


def _validate_mission_started(payload: dict[str, Any]) -> None:
    payload["scenario_id"] = _identifier(payload.get("scenario_id"), "scenario_id")
    frame = payload.get("coordinate_frame")
    if frame != "NED":
        raise RescueEventError("coordinate_frame must be NED")


def _validate_assignment(payload: dict[str, Any]) -> None:
    payload["node"] = _identifier(payload.get("node"), "node")
    payload["sector_id"] = _identifier(payload.get("sector_id"), "sector_id")
    payload["cells_total"] = _integer(payload.get("cells_total"), "cells_total", minimum=1)


def _validate_coverage(payload: dict[str, Any]) -> None:
    payload["node"] = _identifier(payload.get("node"), "node")
    payload["sector_id"] = _identifier(payload.get("sector_id"), "sector_id")
    payload["visited_cells"] = _integer(payload.get("visited_cells"), "visited_cells")
    payload["total_cells"] = _integer(payload.get("total_cells"), "total_cells", minimum=1)
    if payload["visited_cells"] > payload["total_cells"]:
        raise RescueEventError("visited_cells cannot exceed total_cells")


def _validate_vehicle_state(payload: dict[str, Any]) -> None:
    payload["node"] = _identifier(payload.get("node"), "node")
    state = payload.get("state")
    if state not in {"READY", "SEARCHING", "HOLD", "QUARANTINED", "LANDING", "LANDED", "FAILED"}:
        raise RescueEventError("unsupported vehicle state")
    _optional_position(payload)
    if "battery_pct" in payload:
        payload["battery_pct"] = _number(
            payload["battery_pct"], "battery_pct", minimum=0.0, maximum=100.0
        )


def _validate_observation(payload: dict[str, Any]) -> None:
    payload["node"] = _identifier(payload.get("node"), "node")
    payload["observation_id"] = _identifier(
        payload.get("observation_id"), "observation_id"
    )
    class_id = payload.get("class_id")
    if class_id not in OBSERVATION_CLASSES:
        raise RescueEventError(f"unsupported observation class: {class_id!r}")
    payload["confidence"] = _number(
        payload.get("confidence"), "confidence", minimum=0.0, maximum=1.0
    )
    payload["frame_id"] = _identifier(payload.get("frame_id"), "frame_id")
    modality = payload.get("modality")
    if modality not in MODALITIES:
        raise RescueEventError(f"unsupported modality: {modality!r}")
    payload["model_id"] = _identifier(payload.get("model_id"), "model_id")
    model_sha256 = payload.get("model_sha256")
    if model_sha256 is not None:
        if not isinstance(model_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", model_sha256):
            raise RescueEventError("model_sha256 must be 64 hexadecimal characters or null")
        payload["model_sha256"] = model_sha256.lower()
    bbox = _vector(payload.get("bbox_norm"), "bbox_norm", length=4)
    if any(value < 0.0 or value > 1.0 for value in bbox):
        raise RescueEventError("bbox_norm coordinates must be inside [0, 1]")
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise RescueEventError("bbox_norm must satisfy x2>x1 and y2>y1")
    payload["bbox_norm"] = bbox
    _optional_position(payload)
    if ("camera_id" in payload) != ("camera_calibration_id" in payload):
        raise RescueEventError(
            "camera_id and camera_calibration_id must be supplied together"
        )
    if "camera_id" in payload:
        payload["camera_id"] = _identifier(payload["camera_id"], "camera_id")
        payload["camera_calibration_id"] = _identifier(
            payload["camera_calibration_id"], "camera_calibration_id"
        )
    if "capture_group_id" in payload:
        payload["capture_group_id"] = _identifier(
            payload["capture_group_id"], "capture_group_id"
        )
    if ("viewpoint_ned" in payload) != ("bearing_ned" in payload):
        raise RescueEventError("viewpoint_ned and bearing_ned must be supplied together")
    if "viewpoint_ned" in payload:
        payload["viewpoint_ned"] = _vector(
            payload["viewpoint_ned"], "viewpoint_ned", length=3
        )
        bearing = _vector(payload["bearing_ned"], "bearing_ned", length=3)
        bearing_norm = math.sqrt(sum(component**2 for component in bearing))
        if abs(bearing_norm - 1.0) > 1e-4:
            raise RescueEventError("bearing_ned must be a unit vector")
        payload["bearing_ned"] = bearing
        payload["bearing_uncertainty_deg"] = _number(
            payload.get("bearing_uncertainty_deg"),
            "bearing_uncertainty_deg",
            minimum=0.000001,
            maximum=45.0,
        )
    elif "bearing_uncertainty_deg" in payload:
        raise RescueEventError("bearing_uncertainty_deg requires viewpoint/bearing data")
    if "localization_method" in payload:
        method = payload["localization_method"]
        if method not in LOCALIZATION_METHODS:
            raise RescueEventError("unsupported localization_method")
        if method == "bearing_only" and payload.get("position_ned") is not None:
            raise RescueEventError("bearing_only localization cannot contain position_ned")
        if method != "bearing_only" and payload.get("position_ned") is None:
            raise RescueEventError(f"{method} localization requires position_ned")
        if method != "bearing_only" and payload.get("uncertainty_m") is None:
            raise RescueEventError(f"{method} localization requires uncertainty_m")
    if ("metric_range_m" in payload) != ("range_uncertainty_m" in payload):
        raise RescueEventError(
            "metric_range_m and range_uncertainty_m must be supplied together"
        )
    if "metric_range_m" in payload:
        payload["metric_range_m"] = _number(
            payload["metric_range_m"], "metric_range_m", minimum=0.000001
        )
        payload["range_uncertainty_m"] = _number(
            payload["range_uncertainty_m"], "range_uncertainty_m", minimum=0.0
        )
    if "evidence_security" in payload:
        if payload["evidence_security"] not in EVIDENCE_SECURITY_STATES:
            raise RescueEventError("unsupported evidence_security state")
    for field in ("corroborated_sources", "expected_visible_misses"):
        if field in payload:
            payload[field] = _identifier_list(payload[field], field)
    if "security_reasons" in payload:
        payload["security_reasons"] = _text_list(
            payload["security_reasons"], "security_reasons"
        )


def _validate_hazard(payload: dict[str, Any]) -> None:
    payload["node"] = _identifier(payload.get("node"), "node")
    payload["hazard_id"] = _identifier(payload.get("hazard_id"), "hazard_id")
    if payload.get("class_id") not in HAZARD_CLASSES:
        raise RescueEventError("hazard class is unsupported")
    payload["confidence"] = _number(
        payload.get("confidence"), "confidence", minimum=0.0, maximum=1.0
    )
    _optional_position(payload)


def _validate_authorization(payload: dict[str, Any]) -> None:
    payload["node"] = _identifier(payload.get("node"), "node")
    if payload.get("decision") not in {"ALLOW", "HOLD", "QUARANTINE"}:
        raise RescueEventError("authorization decision must be ALLOW, HOLD or QUARANTINE")
    payload["reason"] = _text(payload.get("reason"), "reason", maximum=256)


def _validate_task_reassigned(payload: dict[str, Any]) -> None:
    payload["from_node"] = _identifier(payload.get("from_node"), "from_node")
    payload["to_node"] = _identifier(payload.get("to_node"), "to_node")
    if payload["from_node"] == payload["to_node"]:
        raise RescueEventError("task reassignment requires different nodes")
    payload["cells_count"] = _integer(payload.get("cells_count"), "cells_count", minimum=1)
    payload["reason"] = _text(payload.get("reason"), "reason", maximum=256)


def _validate_link_state(payload: dict[str, Any]) -> None:
    payload["node"] = _identifier(payload.get("node"), "node")
    if payload.get("state") not in {"ONLINE", "DEGRADED", "OFFLINE"}:
        raise RescueEventError("link state must be ONLINE, DEGRADED or OFFLINE")


def _validate_mission_completed(payload: dict[str, Any]) -> None:
    if payload.get("status") not in {"PASS", "PARTIAL", "ABORTED"}:
        raise RescueEventError("mission status must be PASS, PARTIAL or ABORTED")
    payload["completed_cells"] = _integer(payload.get("completed_cells"), "completed_cells")
    payload["total_cells"] = _integer(payload.get("total_cells"), "total_cells", minimum=1)
    if payload["completed_cells"] > payload["total_cells"]:
        raise RescueEventError("completed_cells cannot exceed total_cells")


_PAYLOAD_VALIDATORS = {
    "mission_started": _validate_mission_started,
    "assignment": _validate_assignment,
    "coverage": _validate_coverage,
    "vehicle_state": _validate_vehicle_state,
    "observation": _validate_observation,
    "hazard": _validate_hazard,
    "authorization": _validate_authorization,
    "task_reassigned": _validate_task_reassigned,
    "link_state": _validate_link_state,
    "mission_completed": _validate_mission_completed,
}


def validate_rescue_event(
    candidate: Mapping[str, Any],
    *,
    expected_mission_id: str | None = None,
) -> dict[str, Any]:
    """Return a normalized copy or raise :class:`RescueEventError`."""
    if not isinstance(candidate, Mapping):
        raise RescueEventError("event must be a JSON object")
    if candidate.get("schema") != RESCUE_SCHEMA:
        raise RescueEventError(f"schema must be {RESCUE_SCHEMA}")

    mission_id = _identifier(candidate.get("mission_id"), "mission_id")
    if expected_mission_id is not None and mission_id != expected_mission_id:
        raise RescueEventError(
            f"mission_id mismatch: expected {expected_mission_id}, got {mission_id}"
        )
    event_id = _identifier(candidate.get("event_id"), "event_id")
    source = _identifier(candidate.get("source"), "source")
    source_seq = _integer(candidate.get("source_seq"), "source_seq", minimum=1)
    observed_at_ms = _integer(candidate.get("observed_at_ms"), "observed_at_ms")
    kind = candidate.get("kind")
    if kind not in EVENT_KINDS:
        raise RescueEventError(f"unsupported event kind: {kind!r}")
    raw_payload = candidate.get("payload")
    if not isinstance(raw_payload, Mapping):
        raise RescueEventError("payload must be a JSON object")

    # JSON round-trip gives the collector an owned, serialization-safe copy.
    try:
        payload = json.loads(json.dumps(raw_payload, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise RescueEventError(f"payload is not finite JSON: {error}") from error
    _PAYLOAD_VALIDATORS[kind](payload)

    # Node producers may speak only for their own subject. Mission-manager events
    # (assignment/authorization/reassignment) intentionally target other nodes and are
    # excluded. Role-scoped sources prevent independent producer sequence collisions.
    if kind in NODE_STREAM_ROLES:
        if not _source_owns_node(source, payload["node"], kind):
            raise RescueEventError(
                "source is not an authorized stream for payload node: "
                f"source={source}, node={payload['node']}, kind={kind}"
            )
    if kind == "observation" and payload.get("corroborated_sources"):
        if payload["node"] not in payload["corroborated_sources"]:
            raise RescueEventError(
                "corroborated_sources must include the observation-producing node"
            )

    return {
        "schema": RESCUE_SCHEMA,
        "mission_id": mission_id,
        "event_id": event_id,
        "source": source,
        "source_seq": source_seq,
        "observed_at_ms": observed_at_ms,
        "kind": kind,
        "payload": payload,
    }
