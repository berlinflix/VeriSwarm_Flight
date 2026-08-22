from __future__ import annotations

import copy
from pathlib import Path

import pytest

from rescue.collector import RescueCollector
from rescue.movement_security import (
    MovementSecurityError,
    evaluate_movement_command,
    load_movement_contract,
    reassign_unfinished_cells,
)
from rescue.schema import RESCUE_SCHEMA


CONTRACT_PATH = (
    Path(__file__).parents[1]
    / "sim"
    / "cosys"
    / "factorycity"
    / "factorycity_joint_movement_contract.development.json"
)
NOW_MS = 1_787_394_603_000


@pytest.fixture
def contract() -> dict:
    return load_movement_contract(CONTRACT_PATH)


def _authorization(decision: str, *, age_ms: int = 0, node: str = "alpha") -> dict:
    return {
        "node": node,
        "decision": decision,
        "reason": f"test_{decision.casefold()}",
        "observed_at_ms": NOW_MS - age_ms,
    }


def _valid_command() -> dict:
    return {
        "horizontal_velocity_mps": 4.0,
        "vertical_velocity_mps": 2.0,
        "horizontal_acceleration_mps2": 2.0,
        "vertical_acceleration_mps2": 1.5,
        "minimum_pairwise_separation_m": 1.5,
        "target_position_ned": [-50.0, 10.0, -10.0],
    }


def _event(kind: str, payload: dict, *, source: str, seq: int) -> dict:
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


def _person_observation() -> dict:
    return {
        "node": "alpha",
        "observation_id": "alpha-person-before-quarantine",
        "class_id": "person_candidate",
        "confidence": 0.91,
        "frame_id": "alpha-frame-44",
        "modality": "rgb",
        "model_id": "sar-alert-rgb-k0",
        "model_sha256": None,
        "bbox_norm": [0.2, 0.1, 0.5, 0.9],
        "position_ned": [-12.0, 4.0, -10.0],
        "uncertainty_m": 2.0,
    }


def test_allow_releases_only_a_fresh_authorized_command_within_all_limits(contract):
    allowed = evaluate_movement_command(
        contract,
        node="alpha",
        authorization=_authorization("ALLOW"),
        command=_valid_command(),
        now_ms=NOW_MS,
        in_flight=False,
    )
    assert allowed.decision == "ALLOW"
    assert allowed.action == "RELEASE"
    assert allowed.release_command is True

    invalid = _valid_command()
    invalid["horizontal_velocity_mps"] = 4.01
    held = evaluate_movement_command(
        contract,
        node="alpha",
        authorization=_authorization("ALLOW"),
        command=invalid,
        now_ms=NOW_MS,
        in_flight=True,
    )
    assert held.decision == "HOLD"
    assert held.action == "HOVER"
    assert held.release_command is False
    assert held.reason == "movement_limits_failed:horizontal_velocity_mps_outside_limit"


@pytest.mark.parametrize(
    "authorization,expected_reason",
    [
        (None, "authorization_missing"),
        (_authorization("ALLOW", age_ms=2001), "authorization_stale"),
        ({"node": "alpha", "decision": "FLY", "observed_at_ms": NOW_MS}, "authorization_malformed"),
        (_authorization("ALLOW", node="bravo"), "authorization_malformed"),
    ],
)
def test_missing_stale_or_malformed_authorization_fails_closed_to_hold(
    contract, authorization, expected_reason
):
    result = evaluate_movement_command(
        contract,
        node="alpha",
        authorization=authorization,
        command=_valid_command(),
        now_ms=NOW_MS,
        in_flight=True,
    )
    assert result.decision == "HOLD"
    assert result.action == "HOVER"
    assert result.release_command is False
    assert result.reason == expected_reason


def test_inflight_hold_hovers_without_releasing_or_reassigning(contract):
    result = evaluate_movement_command(
        contract,
        node="alpha",
        authorization=_authorization("HOLD"),
        command=_valid_command(),
        now_ms=NOW_MS,
        in_flight=True,
    )
    assert result.decision == "HOLD"
    assert result.action == "HOVER"
    assert result.vehicle_state == "HOLD"
    assert result.release_command is False


def test_quarantine_aborts_alpha_and_deterministically_reassigns_unfinished_cells(contract):
    result = evaluate_movement_command(
        contract,
        node="alpha",
        authorization=_authorization("QUARANTINE"),
        command=_valid_command(),
        now_ms=NOW_MS,
        in_flight=True,
    )
    assert result.decision == "QUARANTINE"
    assert result.action == "ABORT_HOVER_LAND"
    assert result.vehicle_state == "QUARANTINED"
    assert result.release_command is False

    reassignments = reassign_unfinished_cells(
        contract,
        unavailable_node="alpha",
        completed_cell_ids=frozenset(),
        healthy_nodes=frozenset({"bravo", "charlie", "delta", "echo"}),
    )
    assert [(item.cell_id, item.from_node, item.to_node) for item in reassignments] == [
        ("route_cell_00", "alpha", "bravo"),
        ("route_cell_05", "alpha", "charlie"),
    ]


def test_completed_alpha_cell_is_not_reassigned(contract):
    reassignments = reassign_unfinished_cells(
        contract,
        unavailable_node="alpha",
        completed_cell_ids=frozenset({"route_cell_00"}),
        healthy_nodes=frozenset({"bravo", "charlie", "delta", "echo"}),
    )
    assert [(item.cell_id, item.to_node) for item in reassignments] == [
        ("route_cell_05", "bravo")
    ]


def test_quarantine_events_project_and_preserve_alpha_positive_observation(contract, tmp_path):
    collector = RescueCollector("OP-VARUNA-001", tmp_path / "movement-security.jsonl")
    collector.collect(
        _event(
            "observation",
            _person_observation(),
            source="alpha.perception",
            seq=1,
        )
    )
    collector.collect(
        _event(
            "authorization",
            {"node": "alpha", "decision": "QUARANTINE", "reason": "model_hash_not_approved"},
            source=contract["events"]["authorization_source"],
            seq=1,
        )
    )
    collector.collect(
        _event(
            "vehicle_state",
            {"node": "alpha", "state": "QUARANTINED", "position_ned": [-20.0, 5.0, -10.0]},
            source="alpha.telemetry",
            seq=1,
        )
    )
    reassignments = reassign_unfinished_cells(
        contract,
        unavailable_node="alpha",
        completed_cell_ids=frozenset(),
        healthy_nodes=frozenset({"bravo", "charlie", "delta", "echo"}),
    )
    for seq, item in enumerate(reassignments, start=1):
        collector.collect(
            _event(
                "task_reassigned",
                {
                    "from_node": item.from_node,
                    "to_node": item.to_node,
                    "cells_count": 1,
                    "reason": f"quarantine:{item.cell_id}",
                },
                source=contract["events"]["mission_source"],
                seq=seq,
            )
        )

    state = collector.state()
    assert len(state["people"]) == 1
    assert state["people"][0]["observation_ids"] == [
        "alpha-person-before-quarantine"
    ]
    assert state["vehicles"][0]["state"] == "QUARANTINED"
    assert len(state["reassignments"]) == 2


def test_gate_and_reassignment_do_not_consume_hidden_simulator_truth(contract):
    altered = copy.deepcopy(contract)
    altered["evaluation_truth"]["survivor_locations"] = [[9999.0, 9999.0, 9999.0]]
    altered["obstacle_and_collision_policy"]["evaluation_only_disaster_obstacles_unreal_cm"] = []

    original_result = evaluate_movement_command(
        contract,
        node="alpha",
        authorization=_authorization("ALLOW"),
        command=_valid_command(),
        now_ms=NOW_MS,
        in_flight=False,
    )
    altered_result = evaluate_movement_command(
        altered,
        node="alpha",
        authorization=_authorization("ALLOW"),
        command=_valid_command(),
        now_ms=NOW_MS,
        in_flight=False,
    )
    assert altered_result == original_result


def test_reassignment_fails_when_no_healthy_vehicle_is_available(contract):
    with pytest.raises(MovementSecurityError, match="no_healthy_vehicle"):
        reassign_unfinished_cells(
            contract,
            unavailable_node="alpha",
            completed_cell_ids=frozenset(),
            healthy_nodes=frozenset(),
        )
