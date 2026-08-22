from __future__ import annotations

from rescue.collector import RescueCollector
from rescue.schema import RESCUE_SCHEMA


MISSION_ID = "OP-VARUNA-001"
NOW_MS = 1_787_394_603_000


def _event(kind: str, payload: dict, *, source: str, seq: int) -> dict:
    return {
        "schema": RESCUE_SCHEMA,
        "mission_id": MISSION_ID,
        "event_id": f"replay:{source}:{kind}:{seq}",
        "source": source,
        "source_seq": seq,
        "observed_at_ms": NOW_MS + seq,
        "kind": kind,
        "payload": payload,
    }


def test_retained_replay_reconstructs_identical_cell_and_movement_state(tmp_path):
    log_path = tmp_path / "retained-cell-movement.jsonl"
    live = RescueCollector(MISSION_ID, log_path)
    events = [
        _event(
            "assignment",
            {
                "node": "alpha",
                "sector_id": "sector-alpha",
                "cells_total": 2,
                "cell_ids": ["route_cell_00", "route_cell_05"],
            },
            source="mission.controller",
            seq=1,
        ),
        _event(
            "coverage",
            {
                "node": "alpha",
                "sector_id": "sector-alpha",
                "visited_cells": 1,
                "total_cells": 2,
                "in_progress_cell_ids": [],
                "completed_cell_ids": ["route_cell_00"],
                "blocked_cell_ids": ["route_cell_05"],
            },
            source="alpha.telemetry",
            seq=1,
        ),
        _event(
            "movement_safety",
            {
                "node": "alpha",
                "cell_id": "route_cell_05",
                "event_type": "CELL_BLOCKED",
                "measurement_source": "front_depth_and_vehicle_state",
                "measured_distance_m": 1.2,
                "reason_code": "no_safe_deflection",
                "result": "TERMINAL_CELL",
            },
            source="alpha.telemetry",
            seq=2,
        ),
        _event(
            "task_reassigned",
            {
                "from_node": "alpha",
                "to_node": "bravo",
                "cells_count": 1,
                "cell_ids": ["route_cell_05"],
                "reason": "blocked_cell_transfer",
            },
            source="mission.controller",
            seq=2,
        ),
    ]
    for event in events:
        live.collect(event)

    live_state = live.state()
    restored_state = RescueCollector(MISSION_ID, log_path).state()

    assert restored_state == live_state
    assert restored_state["coverage_by_sector"][0]["blocked_cell_ids"] == [
        "route_cell_05"
    ]
    assert restored_state["reassignments"][0]["cell_ids"] == ["route_cell_05"]
    assert restored_state["movement_safety"][0]["result"] == "TERMINAL_CELL"
