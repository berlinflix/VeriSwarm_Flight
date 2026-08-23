from __future__ import annotations

from pathlib import Path

import pytest

from rescue.collector import RescueCollector
from tools.mac_fallback_rescue_simulator import MISSION_ID, run_fallback_mission


CONTRACT = (
    Path(__file__).resolve().parents[1]
    / "sim/cosys/factorycity/factorycity_joint_movement_contract.development.json"
)


@pytest.mark.parametrize("mode", ["nominal", "hold", "quarantine"])
def test_mac_fallback_modes_are_valid_and_projectable(tmp_path, mode):
    collector = RescueCollector(MISSION_ID, tmp_path / f"{mode}.jsonl")
    events = run_fallback_mission(
        contract_path=CONTRACT,
        mode=mode,
        interval_seconds=0,
        steps=12,
        publish=lambda event: collector.collect(event),
        sleep=lambda _seconds: None,
        progress=lambda _message: None,
    )

    state = collector.state()
    assert events[0]["kind"] == "mission_started"
    assert events[0]["payload"]["scenario_id"].startswith(
        "factorycity_contract_mac_fallback_"
    )
    assert events[-1]["kind"] == "mission_completed"
    assert events[-1]["payload"] == {
        "status": "PASS",
        "completed_cells": 10,
        "total_cells": 10,
    }
    assert {vehicle["node"] for vehicle in state["vehicles"]} == {
        "alpha",
        "bravo",
        "charlie",
        "delta",
        "echo",
    }
    assert state["mission_status"] == "PASS"
    assert state["coverage"]["visited_cells"] == 10
    assert len(state["hazards"]) == 1
    assert len(state["people"]) == 1


def test_nominal_fallback_retains_obstacle_hold_deflect_and_rejoin(tmp_path):
    collector = RescueCollector(MISSION_ID, tmp_path / "nominal.jsonl")
    run_fallback_mission(
        contract_path=CONTRACT,
        mode="nominal",
        interval_seconds=0,
        steps=12,
        publish=lambda event: collector.collect(event),
        sleep=lambda _seconds: None,
        progress=lambda _message: None,
    )
    event_types = [event["event_type"] for event in collector.state()["movement_safety"]]
    assert event_types == [
        "OBSTACLE_DETECTED",
        "SAFETY_HOLD",
        "DEFLECTION_SELECTED",
        "ROUTE_REJOINED",
    ]


def test_quarantine_fallback_reassigns_alpha_cell_without_stopping_mission(tmp_path):
    collector = RescueCollector(MISSION_ID, tmp_path / "quarantine.jsonl")
    run_fallback_mission(
        contract_path=CONTRACT,
        mode="quarantine",
        interval_seconds=0,
        steps=12,
        publish=lambda event: collector.collect(event),
        sleep=lambda _seconds: None,
        progress=lambda _message: None,
    )
    state = collector.state()
    alpha = next(vehicle for vehicle in state["vehicles"] if vehicle["node"] == "alpha")
    assert alpha["state"] == "QUARANTINED"
    assert state["reassignments"][-1]["cell_ids"] == ["route_cell_05"]
    assert state["reassignments"][-1]["to_node"] == "bravo"
    assert state["mission_status"] == "PASS"
