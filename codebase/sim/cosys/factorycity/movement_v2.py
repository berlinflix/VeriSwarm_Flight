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
from dataclasses import dataclass, field
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
    # Git may materialize tracked JSON as CRLF on Windows and LF on Linux/Jetson.
    # Hash the canonical LF text bytes so one immutable Git artifact has one
    # cross-platform authority identity; every other byte remains significant.
    canonical = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(canonical).hexdigest()


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
        "maximum_nominal_speed_mps",
        "control_latency_budget_seconds",
        "minimum_braking_deceleration_mps2",
        "clearance_margin_m",
        "obstacle_stopping_boundary_m",
        "clearance_release_boundary_m",
        "side_deflection_clearance_m",
        "vertical_deflection_clearance_m",
        "deflection_velocity_mps",
        "vertical_deflection_velocity_mps",
        "deflection_duration_seconds",
        "maximum_cross_track_deflection_m",
        "maximum_vertical_deflection_m",
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
    safety = extension["safety"]
    candidate_order = safety.get("candidate_order")
    if (
        not isinstance(candidate_order, list)
        or candidate_order != ["LEFT", "RIGHT", "UP"]
    ):
        raise MovementV2Error("candidate_order_not_frozen")
    speed = float(safety["maximum_nominal_speed_mps"])
    latency = float(safety["control_latency_budget_seconds"])
    deceleration = float(safety["minimum_braking_deceleration_mps2"])
    margin = float(safety["clearance_margin_m"])
    required_stopping_distance = speed * latency + speed**2 / (2 * deceleration) + margin
    if float(safety["obstacle_stopping_boundary_m"]) < required_stopping_distance:
        raise MovementV2Error("stopping_boundary_below_configured_braking_distance")
    depth = extension.get("depth_sensor")
    if not isinstance(depth, Mapping):
        raise MovementV2Error("depth_sensor_invalid")
    if depth.get("camera") != "front_depth" or depth.get("image_type") != "DepthPlanar":
        raise MovementV2Error("depth_sensor_authority_invalid")
    minimum = _positive(depth, "minimum_valid_distance_m")
    maximum = _positive(depth, "maximum_valid_distance_m")
    if maximum <= minimum:
        raise MovementV2Error("depth_distance_range_invalid")
    for field in ("center_band_fraction", "minimum_valid_fraction"):
        fraction = _positive(depth, field)
        if fraction > 1.0:
            raise MovementV2Error(f"{field}_invalid")
    return extension


@dataclass(frozen=True)
class DepthSample:
    captured_at_ms: int
    decided_at_ms: int
    center_m: float
    left_m: float
    right_m: float
    upper_m: float
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
    """Reduce a measured DepthPlanar frame into fail-closed directional clearances."""
    if (
        isinstance(decided_at_ms, bool)
        or not isinstance(decided_at_ms, int)
        or decided_at_ms < 0
    ):
        raise MovementV2Error("depth_decision_time_invalid")
    try:
        width, height = int(response.width), int(response.height)
        raw_values = list(response.image_data_float)
        values = [float(value) for value in raw_values]
        timestamp_ns = int(response.time_stamp)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise MovementV2Error("front_depth_response_invalid") from exc
    if timestamp_ns < 0:
        raise MovementV2Error("front_depth_timestamp_invalid")
    if width <= 2 or height <= 0 or len(values) != width * height:
        raise MovementV2Error("front_depth_shape_invalid")
    minimum = float(config["minimum_valid_distance_m"])
    maximum = float(config["maximum_valid_distance_m"])
    valid = [minimum <= value <= maximum and math.isfinite(value) for value in values]
    valid_fraction = sum(valid) / len(valid)
    band = float(config["center_band_fraction"])
    center_width = max(1, min(width - 2, int(round(width * band))))
    center_start = (width - center_width) // 2
    center_end = center_start + center_width

    def region_min(
        column_start: int,
        column_end: int,
        row_start: int = 0,
        row_end: int = height,
    ) -> float:
        region_size = (column_end - column_start) * (row_end - row_start)
        if region_size <= 0:
            return 0.0
        region = [
            values[row * width + column]
            for row in range(row_start, row_end)
            for column in range(column_start, column_end)
            if valid[row * width + column]
        ]
        if len(region) / region_size < float(config["minimum_valid_fraction"]):
            # Missing/invalid depth is unknown space, never infinite free space.
            return 0.0
        return min(region)

    captured_at_ms = timestamp_ns // 1_000_000
    return DepthSample(
        captured_at_ms=captured_at_ms,
        decided_at_ms=decided_at_ms,
        left_m=region_min(0, center_start),
        center_m=region_min(center_start, center_end),
        right_m=region_min(center_end, width),
        upper_m=region_min(center_start, center_end, 0, max(1, height // 2)),
        valid_fraction=valid_fraction,
    )


class CellLedger:
    """Exact configured cell ownership and coverage state."""

    def __init__(self, contract: Mapping[str, Any]):
        self.cells = tuple(contract["search_cells"]["cells"])
        self.known_ids = frozenset(str(cell["id"]) for cell in self.cells)
        self.assigned: dict[str, set[str]] = {
            node: set(str(cell["id"]) for cell in self.cells if cell["owner"] == node)
            for node in contract["vehicles"]["roster"]
        }
        self.completed: dict[str, set[str]] = {node: set() for node in self.assigned}
        self.blocked: dict[str, set[str]] = {node: set() for node in self.assigned}
        self.in_progress: dict[str, set[str]] = {node: set() for node in self.assigned}

    def validate_cell_id(self, cell_id: str) -> str:
        if cell_id not in self.known_ids:
            raise MovementV2Error(f"unknown_cell_id:{cell_id}")
        return cell_id

    def validate_node(self, node: str) -> str:
        if node not in self.assigned:
            raise MovementV2Error(f"unknown_node:{node}")
        return node

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
        self.validate_node(node)
        cell_ids = sorted(self.assigned[node])
        return {
            "node": node,
            "sector_id": f"factorycity_route:{node}",
            "cells_total": len(cell_ids),
            "cell_ids": cell_ids,
        }

    def coverage_payload(self, node: str) -> dict[str, Any]:
        self.validate_node(node)
        return {
            "node": node,
            "sector_id": f"factorycity_route:{node}",
            "visited_cells": len(self.completed[node]),
            "total_cells": len(self.assigned[node]),
            "in_progress_cell_ids": sorted(self.in_progress[node]),
            "completed_cell_ids": sorted(self.completed[node]),
            "blocked_cell_ids": sorted(self.blocked[node]),
        }

    def mark(self, node: str, cell_id: str, state: str) -> None:
        self.validate_node(node)
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

    def plan_reassignment(
        self,
        *,
        from_node: str,
        to_node: str,
        cell_ids: Sequence[str],
        reason: str,
    ) -> dict[str, Any]:
        """Validate a transfer without changing ledger state."""
        self.validate_node(from_node)
        self.validate_node(to_node)
        if from_node == to_node:
            raise MovementV2Error("reassignment_same_node")
        normalized = tuple(str(cell_id) for cell_id in cell_ids)
        if not normalized or len(set(normalized)) != len(normalized):
            raise MovementV2Error("reassignment_cells_invalid")
        if not isinstance(reason, str) or not reason.strip():
            raise MovementV2Error("reassignment_reason_invalid")
        for cell_id in normalized:
            self.validate_cell_id(cell_id)
            if cell_id not in self.assigned[from_node]:
                raise MovementV2Error(
                    f"cell_not_assigned:{from_node}:{cell_id}"
                )
            if cell_id in self.completed[from_node]:
                raise MovementV2Error(f"completed_cell_cannot_reassign:{cell_id}")

        return {
            "from_node": from_node,
            "to_node": to_node,
            "cells_count": len(normalized),
            "cell_ids": sorted(normalized),
            "reason": reason.strip(),
        }

    def apply_reassignment(self, payload: Mapping[str, Any]) -> None:
        """Apply a previously validated reassignment payload."""
        planned = self.plan_reassignment(
            from_node=str(payload.get("from_node")),
            to_node=str(payload.get("to_node")),
            cell_ids=tuple(payload.get("cell_ids", ())),
            reason=str(payload.get("reason", "")),
        )
        cells_count = payload.get("cells_count")
        if (
            isinstance(cells_count, bool)
            or not isinstance(cells_count, int)
            or cells_count != planned["cells_count"]
        ):
            raise MovementV2Error("reassignment_cells_count_mismatch")
        from_node = planned["from_node"]
        to_node = planned["to_node"]
        for cell_id in planned["cell_ids"]:
            self.assigned[from_node].remove(cell_id)
            self.assigned[to_node].add(cell_id)
            for states in (self.in_progress, self.blocked):
                states[from_node].discard(cell_id)
            self.completed[from_node].discard(cell_id)

    def reassign(
        self,
        *,
        from_node: str,
        to_node: str,
        cell_ids: Sequence[str],
        reason: str,
    ) -> dict[str, Any]:
        payload = self.plan_reassignment(
            from_node=from_node,
            to_node=to_node,
            cell_ids=cell_ids,
            reason=reason,
        )
        self.apply_reassignment(payload)
        return payload


class DurableMovementEvents:
    """Validate cell IDs and durably enqueue frozen rescue events."""

    def __init__(
        self,
        *,
        contract: Mapping[str, Any],
        ledger: CellLedger,
        outbox_factory: Callable[[str], RescueOutbox],
        enqueue_deadline_ms: int,
        clock_ms: Callable[[], int] | None = None,
    ):
        self.contract = contract
        self.ledger = ledger
        self.outboxes = {
            source: outbox_factory(source)
            for source in ["mission.controller", *(
                f"{node}.telemetry" for node in contract["vehicles"]["roster"]
            )]
        }
        self.enqueue_deadline_ms = enqueue_deadline_ms
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def emit(
        self,
        *,
        source: str,
        kind: str,
        payload: Mapping[str, Any],
        observed_at_ms: int,
    ) -> dict[str, Any]:
        if source not in self.outboxes:
            raise MovementV2Error(f"unknown_event_source:{source}")
        if (
            isinstance(observed_at_ms, bool)
            or not isinstance(observed_at_ms, int)
            or observed_at_ms < 0
        ):
            raise MovementV2Error("observed_at_ms_invalid")
        singular_cell = payload.get("cell_id")
        if singular_cell is not None:
            self.ledger.validate_cell_id(str(singular_cell))
        for field in (
            "cell_ids",
            "in_progress_cell_ids",
            "completed_cell_ids",
            "blocked_cell_ids",
        ):
            for cell_id in payload.get(field, []):
                self.ledger.validate_cell_id(str(cell_id))
        started = self.clock_ms()
        if observed_at_ms > started:
            raise MovementV2Error("movement_transition_time_in_future")
        seq = self.outboxes[source].reserve_source_sequence(source)
        event = {
            "schema": EVENT_SCHEMA,
            "mission_id": self.contract["mission_id"],
            "event_id": f"{self.contract['mission_id']}:{source}:{kind}:{seq}",
            "source": source,
            "source_seq": seq,
            "observed_at_ms": observed_at_ms,
            "kind": kind,
            "payload": dict(payload),
        }
        self.outboxes[source].enqueue(event)
        finished = self.clock_ms()
        if (
            finished < started
            or finished - started > self.enqueue_deadline_ms
            or finished - observed_at_ms > self.enqueue_deadline_ms
        ):
            raise MovementV2Error("movement_transition_enqueue_deadline_missed")
        return event

    def assignment(self, *, node: str, observed_at_ms: int) -> dict[str, Any]:
        return self.emit(
            source="mission.controller",
            kind="assignment",
            payload=self.ledger.assignment_payload(node),
            observed_at_ms=observed_at_ms,
        )

    def coverage(self, *, node: str, observed_at_ms: int) -> dict[str, Any]:
        return self.emit(
            source=f"{node}.telemetry",
            kind="coverage",
            payload=self.ledger.coverage_payload(node),
            observed_at_ms=observed_at_ms,
        )

    def reassign_cells(
        self,
        *,
        from_node: str,
        to_node: str,
        cell_ids: Sequence[str],
        reason: str,
        observed_at_ms: int,
    ) -> tuple[dict[str, Any], ...]:
        """Durably record a transfer, then publish both resulting ownership states."""
        payload = self.ledger.plan_reassignment(
            from_node=from_node,
            to_node=to_node,
            cell_ids=cell_ids,
            reason=reason,
        )
        events = [
            self.emit(
                source="mission.controller",
                kind="task_reassigned",
                payload=payload,
                observed_at_ms=observed_at_ms,
            )
        ]
        self.ledger.apply_reassignment(payload)
        events.extend(
            (
                self.assignment(node=from_node, observed_at_ms=observed_at_ms),
                self.assignment(node=to_node, observed_at_ms=observed_at_ms),
                self.coverage(node=from_node, observed_at_ms=observed_at_ms),
                self.coverage(node=to_node, observed_at_ms=observed_at_ms),
            )
        )
        return tuple(events)

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
        if event_type not in MOVEMENT_EVENT_MAP:
            raise MovementV2Error(f"movement_event_type_invalid:{event_type}")
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
        authorization: (
            Mapping[str, Any]
            | None
            | Callable[[], Mapping[str, Any] | None]
        ),
        command: Mapping[str, Any],
        now_ms: int,
        in_flight: bool,
        mutate: Callable[[], Any],
        hover: Callable[[], Any] | None = None,
        land: Callable[[], Any] | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> tuple[GateDecision, Any | None]:
        def latest_authorization() -> Mapping[str, Any] | None:
            return authorization() if callable(authorization) else authorization

        def decision_time() -> int:
            return clock_ms() if clock_ms is not None else now_ms

        decision = evaluate_movement_command(
            self.contract,
            node=node,
            authorization=latest_authorization(),
            command=command,
            now_ms=decision_time(),
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
                authorization=latest_authorization(),
                command=command,
                now_ms=decision_time(),
                in_flight=True,
            )
            if landing_decision.action != "ABORT_HOVER_LAND":
                raise MovementV2Error("quarantine_changed_before_safety_land")
            return decision, land()
        raise MovementV2Error(f"unsupported_gate_action:{decision.action}")


@dataclass
class NodeAvoidanceState:
    mode: str = "NOMINAL"
    deflection_direction: str | None = None
    deflection_attempts: int = 0
    clear_samples: int = 0
    obstacle_active: bool = False
    attempted_directions: set[str] = field(default_factory=set)


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
        vertical_offset_m: float = 0.0,
    ) -> tuple[str, tuple[str, ...]]:
        timing = self.extension["timing_boundaries_ms"]
        safety = self.extension["safety"]
        if node not in self.states:
            raise MovementV2Error(f"unknown_node:{node}")
        state = self.states[node]

        # Terminal states are sticky.  A later clean frame cannot resurrect a
        # collided vehicle or silently resume a cell that was declared blocked.
        if state.mode == "COLLIDED":
            return "TERMINATE_VEHICLE", ()
        if state.mode == "BLOCKED":
            return "BLOCK_CELL", ()

        if not isinstance(collision_detected, bool):
            return self._hold_for_invalid_input(state)
        if collision_detected:
            state.mode = "COLLIDED"
            return "TERMINATE_VEHICLE", ("COLLISION_DETECTED",)

        scalars = (
            cross_track_m,
            vertical_offset_m,
            loop_period_ms,
            depth.center_m,
            depth.left_m,
            depth.right_m,
            depth.upper_m,
            depth.valid_fraction,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in scalars
        ):
            return self._hold_for_invalid_input(state)
        if (
            loop_period_ms < 0
            or loop_period_ms > int(timing["control_loop_period_max"])
            or depth.age_ms < 0
            or depth.age_ms > int(timing["depth_sample_max_age_at_decision"])
            or any(
                value < 0.0
                for value in (
                    depth.center_m,
                    depth.left_m,
                    depth.right_m,
                    depth.upper_m,
                    depth.valid_fraction,
                )
            )
            or depth.valid_fraction > 1.0
        ):
            return self._hold_for_invalid_input(state)
        if (
            abs(cross_track_m)
            > float(safety["maximum_cross_track_deflection_m"])
            or abs(vertical_offset_m)
            > float(safety["maximum_vertical_deflection_m"])
        ):
            return self._hold_for_invalid_input(state)

        obstacle = depth.center_m < float(
            safety["obstacle_stopping_boundary_m"]
        )
        released = depth.center_m >= float(
            safety["clearance_release_boundary_m"]
        )

        if state.mode == "NOMINAL":
            if not obstacle:
                return "CONTINUE_ROUTE", ()
            state.mode = "HOLD"
            state.obstacle_active = True
            state.clear_samples = 0
            state.deflection_attempts = 0
            state.attempted_directions.clear()
            state.deflection_direction = None
            # This cycle genuinely hovers. Deflection may only be released by
            # a subsequent measured decision while the hold remains valid.
            return "HOVER", ("OBSTACLE_DETECTED", "SAFETY_HOLD")

        if state.mode == "REJOIN":
            if obstacle:
                state.mode = "HOLD"
                state.obstacle_active = True
                state.clear_samples = 0
                state.deflection_direction = None
                state.deflection_attempts = 0
                state.attempted_directions.clear()
                return "HOVER", ("OBSTACLE_DETECTED", "SAFETY_HOLD")
            if released and self._within_rejoin_limits(
                cross_track_m, vertical_offset_m
            ):
                self._reset_nominal(state)
                return "RESUME_ROUTE", ("ROUTE_REJOINED",)
            return "REJOIN_ROUTE", ()

        if state.mode == "DEFLECT":
            direction = state.deflection_direction
            if obstacle and direction and self._direction_is_permitted(
                direction,
                depth,
                cross_track_m,
                vertical_offset_m,
            ):
                return f"DEFLECT_{direction}", ()
            if obstacle:
                state.mode = "HOLD"
                state.deflection_direction = None
                state.clear_samples = 0
                return "HOVER", ("SAFETY_HOLD",)
            state.clear_samples = state.clear_samples + 1 if released else 0
            if state.clear_samples < int(safety["clear_samples_required"]):
                return "HOVER", ()
            if self._within_rejoin_limits(cross_track_m, vertical_offset_m):
                self._reset_nominal(state)
                return "RESUME_ROUTE", ("ROUTE_REJOINED",)
            state.mode = "REJOIN"
            state.deflection_direction = None
            return "REJOIN_ROUTE", ()

        if state.mode == "HOLD":
            if obstacle:
                if not state.obstacle_active:
                    state.obstacle_active = True
                    state.clear_samples = 0
                    state.deflection_attempts = 0
                    state.attempted_directions.clear()
                    return "HOVER", (
                        "OBSTACLE_DETECTED",
                        "SAFETY_HOLD",
                    )
                direction = self._select_direction(
                    state,
                    depth,
                    cross_track_m,
                    vertical_offset_m,
                )
                if direction is None:
                    state.mode = "BLOCKED"
                    return "BLOCK_CELL", ("CELL_BLOCKED",)
                state.deflection_direction = direction
                state.attempted_directions.add(direction)
                state.deflection_attempts += 1
                state.mode = "DEFLECT"
                return f"DEFLECT_{direction}", ("DEFLECTION_SELECTED",)
            state.clear_samples = state.clear_samples + 1 if released else 0
            if state.clear_samples < int(safety["clear_samples_required"]):
                return "HOVER", ()
            if self._within_rejoin_limits(cross_track_m, vertical_offset_m):
                self._reset_nominal(state)
                return "RESUME_ROUTE", ("ROUTE_REJOINED",)
            state.mode = "REJOIN"
            return "REJOIN_ROUTE", ()

        raise MovementV2Error(f"avoidance_state_invalid:{state.mode}")

    def _hold_for_invalid_input(
        self, state: NodeAvoidanceState
    ) -> tuple[str, tuple[str, ...]]:
        transitioned = state.mode != "HOLD"
        state.mode = "HOLD"
        state.clear_samples = 0
        state.deflection_direction = None
        return "HOVER", (("SAFETY_HOLD",) if transitioned else ())

    def _within_rejoin_limits(
        self, cross_track_m: float, vertical_offset_m: float
    ) -> bool:
        safety = self.extension["safety"]
        tolerance = float(safety["route_rejoin_tolerance_m"])
        return abs(cross_track_m) <= tolerance and abs(vertical_offset_m) <= tolerance

    def _direction_is_permitted(
        self,
        direction: str,
        depth: DepthSample,
        cross_track_m: float,
        vertical_offset_m: float,
    ) -> bool:
        safety = self.extension["safety"]
        if direction in {"LEFT", "RIGHT"}:
            clearance = depth.left_m if direction == "LEFT" else depth.right_m
            projected = abs(cross_track_m) + (
                float(safety["deflection_velocity_mps"])
                * float(safety["deflection_duration_seconds"])
            )
            return (
                clearance >= float(safety["side_deflection_clearance_m"])
                and projected
                <= float(safety["maximum_cross_track_deflection_m"])
            )
        if direction == "UP":
            projected = abs(vertical_offset_m) + (
                float(safety["vertical_deflection_velocity_mps"])
                * float(safety["deflection_duration_seconds"])
            )
            return (
                depth.upper_m
                >= float(safety["vertical_deflection_clearance_m"])
                and projected
                <= float(safety["maximum_vertical_deflection_m"])
            )
        return False

    def _select_direction(
        self,
        state: NodeAvoidanceState,
        depth: DepthSample,
        cross_track_m: float,
        vertical_offset_m: float,
    ) -> str | None:
        safety = self.extension["safety"]
        if state.deflection_attempts >= int(safety["maximum_deflection_attempts"]):
            return None
        clearances = {
            "LEFT": depth.left_m,
            "RIGHT": depth.right_m,
            "UP": depth.upper_m,
        }
        permitted = [
            direction
            for direction in safety["candidate_order"]
            if direction not in state.attempted_directions
            and self._direction_is_permitted(
                direction,
                depth,
                cross_track_m,
                vertical_offset_m,
            )
        ]
        # max returns the first item on ties, preserving the frozen order.
        return max(permitted, key=clearances.__getitem__) if permitted else None

    @staticmethod
    def _reset_nominal(state: NodeAvoidanceState) -> None:
        state.mode = "NOMINAL"
        state.deflection_direction = None
        state.deflection_attempts = 0
        state.clear_samples = 0
        state.obstacle_active = False
        state.attempted_directions.clear()
