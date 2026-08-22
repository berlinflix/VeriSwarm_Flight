from __future__ import annotations

import json
from pathlib import Path

from rescue.movement_security import load_movement_contract
from rescue.outbox import RescueOutbox
from sim.cosys.factorycity.movement_v2 import (
    CellLedger,
    DepthSample,
    DurableMovementEvents,
    GatedCommandDispatcher,
    SensorDrivenMovementSupervisor,
    load_sensor_movement_extension,
)


ROOT = Path(__file__).parents[1]
FACTORYCITY = ROOT / "sim" / "cosys" / "factorycity"
BASE = FACTORYCITY / "factorycity_joint_movement_contract.development.json"
EXTENSION = FACTORYCITY / "factorycity_sensor_movement_extension.v2.development.json"
CELL_EXTENSION = ROOT / "config" / "rescue_cell_movement_event_extension.v1.json"


def _loaded():
    base = load_movement_contract(BASE)
    extension = load_sensor_movement_extension(
        EXTENSION,
        base_contract_path=BASE,
        cell_extension_path=CELL_EXTENSION,
    )
    return base, extension


def _depth(center=20.0, left=20.0, right=20.0, age_ms=10):
    return DepthSample(
        captured_at_ms=1_000 - age_ms,
        decided_at_ms=1_000,
        center_m=center,
        left_m=left,
        right_m=right,
        valid_fraction=1.0,
    )


def test_v2_is_additive_and_never_reinterprets_straight_route_v1() -> None:
    base, extension = _loaded()
    assert base["schema"] == "veriswarm.factorycity.joint_movement_contract.v1"
    assert base["route"]["geometry"] == "ONE_STRAIGHT_CENTROID_SEGMENT_NO_DEVIATION"
    assert extension["schema"] == "veriswarm.factorycity.sensor_movement_extension.v2"
    serialized = json.dumps(extension)
    assert "evaluation_only_disaster_obstacles_unreal_cm" not in serialized
    assert "survivor_locations" not in serialized


def test_exact_assignment_and_coverage_cell_ids_come_from_base_contract() -> None:
    base, _ = _loaded()
    ledger = CellLedger(base)
    assert ledger.assignment_payload("alpha")["cell_ids"] == [
        "route_cell_00",
        "route_cell_05",
    ]
    ledger.mark("alpha", "route_cell_00", "COMPLETED")
    ledger.mark("alpha", "route_cell_05", "BLOCKED")
    coverage = ledger.coverage_payload("alpha")
    assert coverage["completed_cell_ids"] == ["route_cell_00"]
    assert coverage["blocked_cell_ids"] == ["route_cell_05"]
    assert coverage["in_progress_cell_ids"] == []


def test_durable_movement_event_uses_frozen_mapping_and_exact_cell(tmp_path) -> None:
    base, extension = _loaded()
    ledger = CellLedger(base)
    producer = DurableMovementEvents(
        contract=base,
        ledger=ledger,
        outbox_factory=lambda source: RescueOutbox(
            base["mission_id"], tmp_path / f"{source}.sqlite3"
        ),
        enqueue_deadline_ms=extension["timing_boundaries_ms"][
            "movement_transition_durable_enqueue_deadline"
        ],
    )
    event = producer.movement_safety(
        node="alpha",
        cell_id="route_cell_00",
        event_type="OBSTACLE_DETECTED",
        measurement_source="front_depth_and_vehicle_state",
        measured_distance_m=3.4,
        observed_at_ms=1000,
    )
    assert event["payload"]["reason_code"] == "depth_below_stopping_boundary"
    assert event["payload"]["result"] == "NON_TERMINAL"
    assert producer.outboxes["alpha.telemetry"].status()["pending"] == 1


def test_gate_is_evaluated_before_mutation_and_hold_releases_nothing() -> None:
    base, _ = _loaded()
    dispatcher = GatedCommandDispatcher(base)
    calls = []
    command = {
        "horizontal_velocity_mps": 4.0,
        "vertical_velocity_mps": 2.0,
        "horizontal_acceleration_mps2": 2.0,
        "vertical_acceleration_mps2": 1.5,
        "minimum_pairwise_separation_m": 1.5,
        "target_position_ned": [-50.0, 10.0, -10.0],
    }
    authorization = {
        "node": "alpha",
        "decision": "ALLOW",
        "reason": "model_hash_approved",
        "observed_at_ms": 1000,
    }
    decision, value = dispatcher.dispatch(
        node="alpha",
        authorization=authorization,
        command=command,
        now_ms=1000,
        in_flight=True,
        mutate=lambda: calls.append("mutated") or "future",
    )
    assert decision.action == "RELEASE"
    assert calls == ["mutated"]
    assert value == "future"

    authorization["decision"] = "HOLD"
    held, value = dispatcher.dispatch(
        node="alpha",
        authorization=authorization,
        command=command,
        now_ms=1000,
        in_flight=True,
        mutate=lambda: calls.append("unsafe"),
        hover=lambda: calls.append("hover") or "hover-future",
    )
    assert held.action == "HOVER"
    assert value == "hover-future"
    assert calls == ["mutated", "hover"]

    authorization["decision"] = "QUARANTINE"
    quarantined, value = dispatcher.dispatch(
        node="alpha",
        authorization=authorization,
        command=command,
        now_ms=1000,
        in_flight=True,
        mutate=lambda: calls.append("unsafe"),
        hover=lambda: calls.append("abort-hover"),
        land=lambda: calls.append("abort-land") or "land-future",
    )
    assert quarantined.action == "ABORT_HOVER_LAND"
    assert value == "land-future"
    assert calls[-2:] == ["abort-hover", "abort-land"]


def test_measured_obstacle_selects_clear_side_then_rejoins() -> None:
    base, extension = _loaded()
    supervisor = SensorDrivenMovementSupervisor(extension, base["vehicles"]["roster"])
    action, events = supervisor.decide(
        node="alpha",
        depth=_depth(center=3.0, left=8.0, right=2.0),
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )
    assert action == "DEFLECT_LEFT"
    assert events == (
        "OBSTACLE_DETECTED",
        "SAFETY_HOLD",
        "DEFLECTION_SELECTED",
    )
    for _ in range(extension["safety"]["clear_samples_required"] - 1):
        action, _ = supervisor.decide(
            node="alpha",
            depth=_depth(center=9.0),
            collision_detected=False,
            cross_track_m=1.0,
            loop_period_ms=150,
        )
    action, events = supervisor.decide(
        node="alpha",
        depth=_depth(center=9.0),
        collision_detected=False,
        cross_track_m=0.2,
        loop_period_ms=150,
    )
    assert action == "RESUME_ROUTE"
    assert events == ("ROUTE_REJOINED",)


def test_no_clear_side_blocks_cell_and_timing_miss_holds() -> None:
    base, extension = _loaded()
    supervisor = SensorDrivenMovementSupervisor(extension, base["vehicles"]["roster"])
    action, events = supervisor.decide(
        node="bravo",
        depth=_depth(center=2.0, left=2.0, right=2.0),
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )
    assert action == "BLOCK_CELL"
    assert events[-1] == "CELL_BLOCKED"

    action, events = supervisor.decide(
        node="charlie",
        depth=_depth(age_ms=251),
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )
    assert action == "HOVER"
    assert events == ("SAFETY_HOLD",)


def test_measured_collision_is_terminal_vehicle_transition() -> None:
    base, extension = _loaded()
    supervisor = SensorDrivenMovementSupervisor(extension, base["vehicles"]["roster"])
    action, events = supervisor.decide(
        node="echo",
        depth=_depth(),
        collision_detected=True,
        cross_track_m=0.0,
        loop_period_ms=150,
    )
    assert action == "TERMINATE_VEHICLE"
    assert events == ("COLLISION_DETECTED",)
