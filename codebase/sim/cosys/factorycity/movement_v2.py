"""Frozen cell/event and measured movement core for FactoryCity v2.

This module is additive to the accepted straight-route v1 controller.  It consumes only
front depth, CoSys collision/vehicle telemetry, immutable limits and a fresh authorization
lease.  It deliberately has no field for Unreal obstacle coordinates or evaluator truth.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from rescue.movement_security import GateDecision, evaluate_movement_command
from rescue.outbox import RescueOutbox


EXTENSION_SCHEMA = "veriswarm.factorycity.sensor_movement_extension.v2"
EVENT_SCHEMA = "veriswarm.rescue.event.v1"
MOVEMENT_EVENT_MAP = {
    "OBSTACLE_DETECTED": ("depth_below_stopping_boundary", "NON_TERMINAL"),
    "SAFETY_HOLD": ("obstacle_safety_hold", "NON_TERMINAL"),
    "DEFLECTION_SELECTED": ("safe_deflection_selected", "NON_TERMINAL"),
    "ROUTE_REJOINED": ("nominal_route_rejoined", "NON_TERMINAL"),
    "CELL_BLOCKED": ("no_safe_deflection", "TERMINAL_CELL"),
    "COLLISION_DETECTED": ("cosys_collision_detected", "TERMINAL_VEHICLE"),
}


class MovementV2Error(RuntimeError):
    """The additive movement configuration or runtime input is unsafe."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positive(mapping: Mapping[str, Any], field: str) -> float:
    value = mapping.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MovementV2Error(f"{field}_invalid")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise MovementV2Error(f"{field}_invalid")
    return number


def load_sensor_movement_extension(
    extension_path: Path,
    *,
    base_contract_path: Path,
    cell_extension_path: Path,
) -> dict[str, Any]:
    extension = json.loads(extension_path.read_text(encoding="utf-8"))
    if extension.get("schema") != EXTENSION_SCHEMA or not extension.get("development_only"):
        raise MovementV2Error("movement_extension_schema_invalid")
    authorities = (
        ("base_movement_contract", base_contract_path),
        ("cell_event_extension", cell_extension_path),
    )
    for key, authority_path in authorities:
        expected = str(extension[key]["sha256"]).casefold()
        if _sha256(authority_path).casefold() != expected:
            raise MovementV2Error(f"{key}_hash_mismatch")
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        if authority.get("schema") != extension[key]["schema"]:
            raise MovementV2Error(f"{key}_schema_mismatch")
    timing = extension["timing_boundaries_ms"]
    frozen = json.loads(cell_extension_path.read_text(encoding="utf-8"))[
        "timing_boundaries_ms"
    ]
    if timing != frozen:
        raise MovementV2Error("timing_boundaries_not_frozen")
    for field in (
        "obstacle_stopping_boundary_m",
        "clearance_release_boundary_m",
        "side_deflection_clearance_m",
        "deflection_velocity_mps",
        "deflection_duration_seconds",
        "maximum_cross_track_deflection_m",
        "route_rejoin_lateral_velocity_mps",
        "route_rejoin_tolerance_m",
        "clear_samples_required",
        "maximum_deflection_attempts",
    ):
        _positive(extension["safety"], field)
    if extension["safety"]["clearance_release_boundary_m"] <= extension["safety"][
        "obstacle_stopping_boundary_m"
    ]:
        raise MovementV2Error("clearance_release_must_exceed_stopping_boundary")
    return extension


@dataclass(frozen=True)
class DepthSample:
    captured_at_ms: int
    decided_at_ms: int
    center_m: float
    left_m: float
    right_m: float
    valid_fraction: float

    @property
    def age_ms(self) -> int:
        return self.decided_at_ms - self.captured_at_ms


def depth_sample_from_response(
    response: Any,
    *,
    decided_at_ms: int,
    config: Mapping[str, Any],
) -> DepthSample:
    """Reduce a measured DepthPlanar frame into left/centre/right clearances."""
    width, height = int(response.width), int(response.height)
    values = list(response.image_data_float)
    if width <= 2 or height <= 0 or len(values) != width * height:
        raise MovementV2Error("front_depth_shape_invalid")
    minimum = float(config["minimum_valid_distance_m"])
    maximum = float(config["maximum_valid_distance_m"])
    valid = [minimum <= float(value) <= maximum and math.isfinite(float(value)) for value in values]
    valid_fraction = sum(valid) / len(valid)
    if valid_fraction < float(config["minimum_valid_fraction"]):
        raise MovementV2Error("front_depth_valid_fraction_low")
    band = float(config["center_band_fraction"])
    center_width = max(1, min(width - 2, int(round(width * band))))
    center_start = (width - center_width) // 2
    center_end = center_start + center_width

    def region_min(start: int, end: int) -> float:
        region = [
            float(values[row * width + column])
            for row in range(height)
            for column in range(start, end)
            if valid[row * width + column]
        ]
        return min(region) if region else math.inf

    timestamp_ns = int(response.time_stamp)
    captured_at_ms = timestamp_ns // 1_000_000
    return DepthSample(
        captured_at_ms=captured_at_ms,
        decided_at_ms=decided_at_ms,
        left_m=region_min(0, center_start),
        center_m=region_min(center_start, center_end),
        right_m=region_min(center_end, width),
        valid_fraction=valid_fraction,
    )


class CellLedger:
    """Exact configured cell ownership and coverage state."""

    def __init__(self, contract: Mapping[str, Any]):
        self.cells = tuple(contract["search_cells"]["cells"])
        self.known_ids = frozenset(str(cell["id"]) for cell in self.cells)
        self.assigned = {
            node: frozenset(str(cell["id"]) for cell in self.cells if cell["owner"] == node)
            for node in contract["vehicles"]["roster"]
        }
        self.completed: dict[str, set[str]] = {node: set() for node in self.assigned}
        self.blocked: dict[str, set[str]] = {node: set() for node in self.assigned}
        self.in_progress: dict[str, set[str]] = {node: set() for node in self.assigned}

    def validate_cell_id(self, cell_id: str) -> str:
        if cell_id not in self.known_ids:
            raise MovementV2Error(f"unknown_cell_id:{cell_id}")
        return cell_id

    def cell_for_progress(self, progress_fraction: float) -> str:
        if not math.isfinite(progress_fraction) or not 0.0 <= progress_fraction <= 1.0:
            raise MovementV2Error("route_progress_invalid")
        for index, cell in enumerate(self.cells):
            start, end = float(cell["start_fraction"]), float(cell["end_fraction"])
            if start <= progress_fraction < end or (
                index == len(self.cells) - 1 and progress_fraction == 1.0
            ):
                return self.validate_cell_id(str(cell["id"]))
        raise MovementV2Error("route_progress_has_no_cell")

    def assignment_payload(self, node: str) -> dict[str, Any]:
        cell_ids = sorted(self.assigned[node])
        return {
            "node": node,
            "sector_id": "factorycity_route",
            "cells_total": len(cell_ids),
            "cell_ids": cell_ids,
        }

    def coverage_payload(self, node: str) -> dict[str, Any]:
        return {
            "node": node,
            "sector_id": "factorycity_route",
            "visited_cells": len(self.completed[node]),
            "total_cells": len(self.assigned[node]),
            "in_progress_cell_ids": sorted(self.in_progress[node]),
            "completed_cell_ids": sorted(self.completed[node]),
            "blocked_cell_ids": sorted(self.blocked[node]),
        }

    def mark(self, node: str, cell_id: str, state: str) -> None:
        cell_id = self.validate_cell_id(cell_id)
        if cell_id not in self.assigned[node]:
            raise MovementV2Error(f"cell_not_assigned:{node}:{cell_id}")
        for states in (self.in_progress, self.completed, self.blocked):
            states[node].discard(cell_id)
        target = {
            "IN_PROGRESS": self.in_progress,
            "COMPLETED": self.completed,
            "BLOCKED": self.blocked,
        }.get(state)
        if target is None:
            raise MovementV2Error(f"cell_state_invalid:{state}")
        target[node].add(cell_id)


class DurableMovementEvents:
    """Validate cell IDs and durably enqueue frozen rescue events."""

    def __init__(
        self,
        *,
        contract: Mapping[str, Any],
        ledger: CellLedger,
        outbox_factory: Callable[[str], RescueOutbox],
        enqueue_deadline_ms: int,
    ):
        self.contract = contract
        self.ledger = ledger
        self.outboxes = {
            source: outbox_factory(source)
            for source in ["mission.controller", *(
                f"{node}.telemetry" for node in contract["vehicles"]["roster"]
            )]
        }
        self.sequences = {source: 0 for source in self.outboxes}
        self.enqueue_deadline_ms = enqueue_deadline_ms

    def emit(
        self,
        *,
        source: str,
        kind: str,
        payload: Mapping[str, Any],
        observed_at_ms: int,
    ) -> dict[str, Any]:
        for field in (
            "cell_ids",
            "in_progress_cell_ids",
            "completed_cell_ids",
            "blocked_cell_ids",
        ):
            for cell_id in payload.get(field, []):
                self.ledger.validate_cell_id(str(cell_id))
        started = time.monotonic_ns() // 1_000_000
        self.sequences[source] += 1
        seq = self.sequences[source]
        event = {
            "schema": EVENT_SCHEMA,
            "mission_id": self.contract["mission_id"],
            "event_id": f"{source}:{kind}:{seq}",
            "source": source,
            "source_seq": seq,
            "observed_at_ms": observed_at_ms,
            "kind": kind,
            "payload": dict(payload),
        }
        self.outboxes[source].enqueue(event)
        if time.monotonic_ns() // 1_000_000 - started > self.enqueue_deadline_ms:
            raise MovementV2Error("movement_transition_enqueue_deadline_missed")
        return event

    def movement_safety(
        self,
        *,
        node: str,
        cell_id: str,
        event_type: str,
        measurement_source: str,
        measured_distance_m: float,
        observed_at_ms: int,
        position_ned: Sequence[float] | None = None,
    ) -> dict[str, Any]:
        cell_id = self.ledger.validate_cell_id(cell_id)
        reason, result = MOVEMENT_EVENT_MAP[event_type]
        payload: dict[str, Any] = {
            "node": node,
            "cell_id": cell_id,
            "event_type": event_type,
            "measurement_source": measurement_source,
            "measured_distance_m": measured_distance_m,
            "reason_code": reason,
            "result": result,
        }
        if position_ned is not None:
            payload["position_ned"] = list(position_ned)
        return self.emit(
            source=f"{node}.telemetry",
            kind="movement_safety",
            payload=payload,
            observed_at_ms=observed_at_ms,
        )


class GatedCommandDispatcher:
    """Evaluate Abhijan's gate immediately before a mutating CoSys call."""

    def __init__(self, contract: Mapping[str, Any]):
        self.contract = contract

    def dispatch(
        self,
        *,
        node: str,
        authorization: Mapping[str, Any] | None,
        command: Mapping[str, Any],
        now_ms: int,
        in_flight: bool,
        mutate: Callable[[], Any],
        hover: Callable[[], Any] | None = None,
        land: Callable[[], Any] | None = None,
    ) -> tuple[GateDecision, Any | None]:
        decision = evaluate_movement_command(
            self.contract,
            node=node,
            authorization=authorization,
            command=command,
            now_ms=now_ms,
            in_flight=in_flight,
        )
        if decision.release_command:
            return decision, mutate()
        if decision.action == "DO_NOT_DISPATCH":
            return decision, None
        if decision.action == "HOVER":
            if hover is None:
                raise MovementV2Error("hover_callback_required")
            # The decision above is the immediately preceding authorization for this
            # protective mutation; no nominal movement is released.
            return decision, hover()
        if decision.action == "ABORT_HOVER_LAND":
            if hover is None or land is None:
                raise MovementV2Error("abort_callbacks_required")
            hover()
            # Re-evaluate immediately before the second mutating CoSys call. A changed,
            # missing or stale lease can only remain fail-closed; it can never resume the
            # discarded nominal command.
            landing_decision = evaluate_movement_command(
                self.contract,
                node=node,
                authorization=authorization,
                command=command,
                now_ms=now_ms,
                in_flight=True,
            )
            if landing_decision.action != "ABORT_HOVER_LAND":
                raise MovementV2Error("quarantine_changed_before_safety_land")
            return decision, land()
        raise MovementV2Error(f"unsupported_gate_action:{decision.action}")


@dataclass
class NodeAvoidanceState:
    mode: str = "NOMINAL"
    deflection_side: str | None = None
    deflection_attempts: int = 0
    clear_samples: int = 0


class SensorDrivenMovementSupervisor:
    """Measured obstacle/hold/deflection/rejoin/block/collision transitions."""

    def __init__(self, extension: Mapping[str, Any], roster: Sequence[str]):
        self.extension = extension
        self.states = {node: NodeAvoidanceState() for node in roster}

    def decide(
        self,
        *,
        node: str,
        depth: DepthSample,
        collision_detected: bool,
        cross_track_m: float,
        loop_period_ms: int,
    ) -> tuple[str, tuple[str, ...]]:
        timing = self.extension["timing_boundaries_ms"]
        safety = self.extension["safety"]
        state = self.states[node]
        if collision_detected:
            state.mode = "COLLIDED"
            return "TERMINATE_VEHICLE", ("COLLISION_DETECTED",)
        if (
            loop_period_ms > int(timing["control_loop_period_max"])
            or depth.age_ms < 0
            or depth.age_ms > int(timing["depth_sample_max_age_at_decision"])
        ):
            state.mode = "HOLD"
            return "HOVER", ("SAFETY_HOLD",)
        if depth.center_m < float(safety["obstacle_stopping_boundary_m"]):
            state.mode = "HOLD"
            choices = {
                "LEFT": depth.left_m,
                "RIGHT": depth.right_m,
            }
            permitted = [
                (distance, side)
                for side, distance in choices.items()
                if distance >= float(safety["side_deflection_clearance_m"])
            ]
            if permitted and state.deflection_attempts < int(safety["maximum_deflection_attempts"]):
                _, state.deflection_side = max(permitted)
                state.deflection_attempts += 1
                state.mode = "DEFLECT"
                return f"DEFLECT_{state.deflection_side}", (
                    "OBSTACLE_DETECTED",
                    "SAFETY_HOLD",
                    "DEFLECTION_SELECTED",
                )
            state.mode = "BLOCKED"
            return "BLOCK_CELL", (
                "OBSTACLE_DETECTED",
                "SAFETY_HOLD",
                "CELL_BLOCKED",
            )
        if state.mode in {"HOLD", "DEFLECT", "REJOIN"}:
            if depth.center_m >= float(safety["clearance_release_boundary_m"]):
                state.clear_samples += 1
            else:
                state.clear_samples = 0
            if state.clear_samples >= int(safety["clear_samples_required"]):
                if abs(cross_track_m) <= float(safety["route_rejoin_tolerance_m"]):
                    state.mode = "NOMINAL"
                    state.deflection_side = None
                    state.clear_samples = 0
                    return "RESUME_ROUTE", ("ROUTE_REJOINED",)
                state.mode = "REJOIN"
                return "REJOIN_ROUTE", ()
            return "HOVER", ()
        if abs(cross_track_m) > float(safety["maximum_cross_track_deflection_m"]):
            state.mode = "HOLD"
            return "HOVER", ("SAFETY_HOLD",)
        return "CONTINUE_ROUTE", ()
