from __future__ import annotations

import json
from pathlib import Path

import pytest

from rescue.collector import RescueCollector
from rescue.schema import (
    MOVEMENT_MEASUREMENT_SOURCES,
    MOVEMENT_REASON_BY_EVENT,
    MOVEMENT_RESULT_BY_EVENT,
    RESCUE_SCHEMA,
    RescueEventError,
    validate_rescue_event,
)


ROOT = Path(__file__).parents[1]
EXTENSION_PATH = ROOT / "config" / "rescue_cell_movement_event_extension.v1.json"
NOW_MS = 1_787_394_603_000


def _event(kind: str, payload: dict, *, source: str, seq: int = 1) -> dict:
    return {
        "schema": RESCUE_SCHEMA,
        "mission_id": "OP-VARUNA-001",
        "event_id": f"{source}:{kind}:{seq}",
        "source": source,
        "source_seq": seq,
        "observed_at_ms": NOW_MS + seq,
        "kind": kind,
        "payload": payload,
    }


def _movement_payload(**overrides) -> dict:
    payload = {
        "node": "alpha",
        "cell_id": "route_cell_05",
        "event_type": "OBSTACLE_DETECTED",
        "measurement_source": "front_depth_and_vehicle_state",
        "measured_distance_m": 3.7,
        "reason_code": "depth_below_stopping_boundary",
        "result": "NON_TERMINAL",
    }
    payload.update(overrides)
    return payload


def test_machine_contract_matches_runtime_reason_result_and_source_sets() -> None:
    contract = json.loads(EXTENSION_PATH.read_text(encoding="utf-8"))
    assert contract["schema"] == "veriswarm.rescue.event_extension.cell_movement.v1"
    movement = contract["movement_safety"]
    assert set(movement["measurement_sources"]) == MOVEMENT_MEASUREMENT_SOURCES
    assert {
        event_type: values[0]
        for event_type, values in movement["event_reason_result_map"].items()
    } == MOVEMENT_REASON_BY_EVENT
    assert {
        event_type: values[1]
        for event_type, values in movement["event_reason_result_map"].items()
    } == MOVEMENT_RESULT_BY_EVENT
    assert contract["timing_boundaries_ms"] == {
        "authorization_lease_max_age": 2000,
        "control_loop_period_max": 200,
        "depth_sample_max_age_at_decision": 250,
        "hold_command_dispatch_deadline": 400,
        "movement_transition_durable_enqueue_deadline": 500,
        "policy_on_any_timing_miss": "HOLD",
    }


def test_legacy_count_only_events_remain_replay_compatible() -> None:
    assignment = validate_rescue_event(
        _event(
            "assignment",
            {"node": "alpha", "sector_id": "sector-a", "cells_total": 2},
            source="mission.controller",
        )
    )
    coverage = validate_rescue_event(
        _event(
            "coverage",
            {
                "node": "alpha",
                "sector_id": "sector-a",
                "visited_cells": 1,
                "total_cells": 2,
            },
            source="alpha.telemetry",
        )
    )
    assert "cell_ids" not in assignment["payload"]
    assert "completed_cell_ids" not in coverage["payload"]


def test_additive_cell_fields_are_sorted_and_count_bound() -> None:
    assignment = validate_rescue_event(
        _event(
            "assignment",
            {
                "node": "alpha",
                "sector_id": "sector-a",
                "cells_total": 2,
                "cell_ids": ["route_cell_05", "route_cell_00"],
            },
            source="mission.controller",
        )
    )
    assert assignment["payload"]["cell_ids"] == ["route_cell_00", "route_cell_05"]

    reassignment = validate_rescue_event(
        _event(
            "task_reassigned",
            {
                "from_node": "alpha",
                "to_node": "bravo",
                "cells_count": 2,
                "cell_ids": ["route_cell_05", "route_cell_00"],
                "reason": "alpha_quarantined",
            },
            source="mission.controller",
        )
    )
    assert reassignment["payload"]["cell_ids"] == [
        "route_cell_00",
        "route_cell_05",
    ]


@pytest.mark.parametrize(
    "kind,payload,message",
    [
        (
            "assignment",
            {
                "node": "alpha",
                "sector_id": "sector-a",
                "cells_total": 2,
                "cell_ids": ["route_cell_00"],
            },
            "cell_ids count",
        ),
        (
            "coverage",
            {
                "node": "alpha",
                "sector_id": "sector-a",
                "visited_cells": 1,
                "total_cells": 2,
                "completed_cell_ids": ["route_cell_00"],
            },
            "cell-level coverage requires",
        ),
        (
            "coverage",
            {
                "node": "alpha",
                "sector_id": "sector-a",
                "visited_cells": 1,
                "total_cells": 2,
                "in_progress_cell_ids": ["route_cell_00"],
                "completed_cell_ids": ["route_cell_00"],
                "blocked_cell_ids": [],
            },
            "must be disjoint",
        ),
        (
            "task_reassigned",
            {
                "from_node": "alpha",
                "to_node": "bravo",
                "cells_count": 2,
                "cell_ids": ["route_cell_00"],
                "reason": "alpha_quarantined",
            },
            "cell_ids count",
        ),
    ],
)
def test_inconsistent_cell_evidence_fails_closed(kind, payload, message) -> None:
    with pytest.raises(RescueEventError, match=message):
        validate_rescue_event(_event(kind, payload, source="mission.controller"))


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"measurement_source": "unreal_actor_truth"}, "measurement_source"),
        ({"measured_distance_m": -0.1}, "measured_distance_m"),
        ({"reason_code": "made_up"}, "reason_code"),
        ({"result": "TERMINAL_CELL"}, "result"),
    ],
)
def test_movement_safety_rejects_unfrozen_or_inconsistent_values(
    overrides, message
) -> None:
    with pytest.raises(RescueEventError, match=message):
        validate_rescue_event(
            _event(
                "movement_safety",
                _movement_payload(**overrides),
                source="alpha.telemetry",
            )
        )


def test_only_the_subject_telemetry_stream_can_emit_movement_safety() -> None:
    with pytest.raises(RescueEventError, match="not an authorized stream"):
        validate_rescue_event(
            _event(
                "movement_safety",
                _movement_payload(),
                source="bravo.telemetry",
            )
        )


def test_projection_reconstructs_cell_heatmap_and_terminal_movement_alert(tmp_path) -> None:
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "cell-movement.jsonl")
    collector.collect(
        _event(
            "assignment",
            {
                "node": "alpha",
                "sector_id": "sector-a",
                "cells_total": 2,
                "cell_ids": ["route_cell_00", "route_cell_05"],
            },
            source="mission.controller",
            seq=1,
        )
    )
    collector.collect(
        _event(
            "coverage",
            {
                "node": "alpha",
                "sector_id": "sector-a",
                "visited_cells": 1,
                "total_cells": 2,
                "in_progress_cell_ids": [],
                "completed_cell_ids": ["route_cell_00"],
                "blocked_cell_ids": ["route_cell_05"],
            },
            source="alpha.telemetry",
            seq=1,
        )
    )
    collector.collect(
        _event(
            "movement_safety",
            _movement_payload(
                event_type="CELL_BLOCKED",
                reason_code="no_safe_deflection",
                result="TERMINAL_CELL",
            ),
            source="alpha.telemetry",
            seq=2,
        )
    )

    state = collector.state()
    assert state["assignments"][0]["cell_ids"] == [
        "route_cell_00",
        "route_cell_05",
    ]
    assert state["coverage"]["completed_cell_ids"] == ["route_cell_00"]
    assert state["coverage"]["blocked_cell_ids"] == ["route_cell_05"]
    assert state["coverage"]["cell_detail_complete"] is True
    assert state["coverage"]["cell_state_conflicts"] == []
    assert state["assignment_conflicts"] == []
    assert state["movement_safety"][0]["result"] == "TERMINAL_CELL"
    assert any(alert["kind"] == "MOVEMENT_SAFETY" for alert in state["alerts"])


def test_projection_never_silently_colors_or_assigns_conflicting_cells(tmp_path) -> None:
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "conflicts.jsonl")
    for seq, (node, sector_id) in enumerate(
        (("alpha", "sector-a"), ("bravo", "sector-b")), start=1
    ):
        collector.collect(
            _event(
                "assignment",
                {
                    "node": node,
                    "sector_id": sector_id,
                    "cells_total": 1,
                    "cell_ids": ["route_cell_00"],
                },
                source="mission.controller",
                seq=seq,
            )
        )
    collector.collect(
        _event(
            "coverage",
            {
                "node": "alpha",
                "sector_id": "sector-a",
                "visited_cells": 1,
                "total_cells": 1,
                "in_progress_cell_ids": [],
                "completed_cell_ids": ["route_cell_00"],
                "blocked_cell_ids": [],
            },
            source="alpha.telemetry",
        )
    )
    collector.collect(
        _event(
            "coverage",
            {
                "node": "bravo",
                "sector_id": "sector-b",
                "visited_cells": 0,
                "total_cells": 1,
                "in_progress_cell_ids": [],
                "completed_cell_ids": [],
                "blocked_cell_ids": ["route_cell_00"],
            },
            source="bravo.telemetry",
        )
    )

    state = collector.state()
    assert state["assignment_conflicts"] == [
        {"cell_id": "route_cell_00", "owners": ["alpha", "bravo"]}
    ]
    assert state["coverage"]["cell_state_conflicts"] == ["route_cell_00"]
    assert state["coverage"]["cell_detail_complete"] is False
    assert state["coverage"]["completed_cell_ids"] == []
    assert state["coverage"]["blocked_cell_ids"] == []
