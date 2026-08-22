from __future__ import annotations

import json
from pathlib import Path

import pytest

from rescue.movement_security import load_movement_contract
from rescue.outbox import RescueOutbox
from sim.cosys.factorycity.movement_v2 import (
    CellLedger,
    DepthSample,
    DurableMovementEvents,
    GatedCommandDispatcher,
    MovementV2Error,
    SensorDrivenMovementSupervisor,
    depth_sample_from_response,
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


def _depth(center=20.0, left=20.0, right=20.0, upper=0.0, age_ms=10):
    return DepthSample(
        captured_at_ms=1_000 - age_ms,
        decided_at_ms=1_000,
        center_m=center,
        left_m=left,
        right_m=right,
        upper_m=upper,
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
    sectors = {
        ledger.assignment_payload(node)["sector_id"]
        for node in base["vehicles"]["roster"]
    }
    assert len(sectors) == len(base["vehicles"]["roster"])


def test_reassignment_moves_only_unfinished_cells_and_empty_owner_is_valid() -> None:
    base, _ = _loaded()
    ledger = CellLedger(base)
    event = ledger.reassign(
        from_node="alpha",
        to_node="bravo",
        cell_ids=["route_cell_05"],
        reason="alpha_quarantined",
    )
    assert event["cells_count"] == 1
    assert ledger.assignment_payload("alpha")["cell_ids"] == ["route_cell_00"]
    assert "route_cell_05" in ledger.assignment_payload("bravo")["cell_ids"]
    ledger.reassign(
        from_node="alpha",
        to_node="bravo",
        cell_ids=["route_cell_00"],
        reason="alpha_unavailable",
    )
    assert ledger.assignment_payload("alpha")["cells_total"] == 0
    assert ledger.coverage_payload("alpha")["total_cells"] == 0


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
        clock_ms=lambda: 1_000,
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


def test_durable_event_deadline_includes_transition_to_enqueue_latency(tmp_path) -> None:
    base, extension = _loaded()
    ledger = CellLedger(base)
    clock = iter((1_000, 1_600))
    producer = DurableMovementEvents(
        contract=base,
        ledger=ledger,
        outbox_factory=lambda source: RescueOutbox(
            base["mission_id"], tmp_path / f"late-{source}.sqlite3"
        ),
        enqueue_deadline_ms=extension["timing_boundaries_ms"][
            "movement_transition_durable_enqueue_deadline"
        ],
        clock_ms=lambda: next(clock),
    )
    with pytest.raises(
        MovementV2Error, match="movement_transition_enqueue_deadline_missed"
    ):
        producer.movement_safety(
            node="alpha",
            cell_id="route_cell_00",
            event_type="SAFETY_HOLD",
            measurement_source="front_depth_and_vehicle_state",
            measured_distance_m=3.0,
            observed_at_ms=1_000,
        )


def test_invalid_directional_depth_is_unknown_not_free_space() -> None:
    class Response:
        width = 6
        height = 2
        time_stamp = 990_000_000
        # The center band has no valid samples. A fail-open reducer would report
        # infinity and release the route; the production reducer must report zero.
        image_data_float = [10.0, 10.0, float("nan"), float("nan"), 10.0, 10.0] * 2

    _, extension = _loaded()
    sample = depth_sample_from_response(
        Response(),
        decided_at_ms=1_000,
        config=extension["depth_sensor"],
    )
    assert sample.center_m == 0.0
    assert sample.upper_m == 0.0


def test_durable_reassignment_publishes_both_current_owner_states(tmp_path) -> None:
    base, extension = _loaded()
    ledger = CellLedger(base)
    producer = DurableMovementEvents(
        contract=base,
        ledger=ledger,
        outbox_factory=lambda source: RescueOutbox(
            base["mission_id"], tmp_path / f"transfer-{source}.sqlite3"
        ),
        enqueue_deadline_ms=extension["timing_boundaries_ms"][
            "movement_transition_durable_enqueue_deadline"
        ],
        clock_ms=lambda: 1_000,
    )
    events = producer.reassign_cells(
        from_node="alpha",
        to_node="bravo",
        cell_ids=["route_cell_00", "route_cell_05"],
        reason="alpha_quarantined",
        observed_at_ms=1_000,
    )
    assert [event["kind"] for event in events] == [
        "task_reassigned",
        "assignment",
        "assignment",
        "coverage",
        "coverage",
    ]
    alpha_assignment, bravo_assignment = events[1:3]
    assert alpha_assignment["payload"]["cell_ids"] == []
    assert alpha_assignment["payload"]["cells_total"] == 0
    assert "route_cell_05" in bravo_assignment["payload"]["cell_ids"]
    assert events[3]["payload"]["total_cells"] == 0
    assert (
        alpha_assignment["payload"]["sector_id"]
        != bravo_assignment["payload"]["sector_id"]
    )


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
    assert action == "HOVER"
    assert events == ("OBSTACLE_DETECTED", "SAFETY_HOLD")
    action, events = supervisor.decide(
        node="alpha",
        depth=_depth(center=3.0, left=8.0, right=2.0),
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )
    assert action == "DEFLECT_LEFT"
    assert events == ("DEFLECTION_SELECTED",)
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
    assert action == "HOVER"
    assert events == ("OBSTACLE_DETECTED", "SAFETY_HOLD")
    action, events = supervisor.decide(
        node="bravo",
        depth=_depth(center=2.0, left=2.0, right=2.0, upper=2.0),
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )
    assert action == "BLOCK_CELL"
    assert events == ("CELL_BLOCKED",)
    action, events = supervisor.decide(
        node="bravo",
        depth=_depth(),
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )
    assert (action, events) == ("BLOCK_CELL", ())

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
    action, events = supervisor.decide(
        node="echo",
        depth=_depth(),
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )
    assert (action, events) == ("TERMINATE_VEHICLE", ())


def test_vertical_escape_and_frozen_tie_order_are_deterministic() -> None:
    base, extension = _loaded()
    supervisor = SensorDrivenMovementSupervisor(extension, base["vehicles"]["roster"])
    obstacle = _depth(center=2.0, left=2.0, right=2.0, upper=9.0)
    supervisor.decide(
        node="delta",
        depth=obstacle,
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )
    assert supervisor.decide(
        node="delta",
        depth=obstacle,
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )[0] == "DEFLECT_UP"

    tied = _depth(center=2.0, left=9.0, right=9.0, upper=9.0)
    supervisor.decide(
        node="charlie",
        depth=tied,
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )
    assert supervisor.decide(
        node="charlie",
        depth=tied,
        collision_detected=False,
        cross_track_m=0.0,
        loop_period_ms=150,
    )[0] == "DEFLECT_LEFT"


@pytest.mark.parametrize(
    ("field", "value"),
    [("cross_track_m", float("nan")), ("loop_period_ms", -1)],
)
def test_invalid_movement_inputs_fail_closed(field, value) -> None:
    base, extension = _loaded()
    supervisor = SensorDrivenMovementSupervisor(extension, base["vehicles"]["roster"])
    arguments = {
        "node": "alpha",
        "depth": _depth(),
        "collision_detected": False,
        "cross_track_m": 0.0,
        "loop_period_ms": 150,
    }
    arguments[field] = value
    assert supervisor.decide(**arguments) == ("HOVER", ("SAFETY_HOLD",))
    with pytest.raises(MovementV2Error, match="unknown_node"):
        supervisor.decide(**{**arguments, field: 0.0, "node": "unknown"})
