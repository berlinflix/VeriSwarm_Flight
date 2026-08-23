"""Mac-native, contract-driven fallback producer for the VeriSwarm dashboard.

This is intentionally a data-plane simulation, not CoSys/AirSim or Unreal evidence.
It preserves the frozen five-vehicle roster, route, cells and rescue event schema so
the command-centre integration remains demonstrable when the Windows simulator host
is unavailable.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import uuid
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from rescue.schema import RESCUE_SCHEMA, validate_rescue_event


MISSION_ID = "OP-VARUNA-001"
DEFAULT_CONTRACT = (
    Path(__file__).resolve().parents[1]
    / "sim/cosys/factorycity/factorycity_joint_movement_contract.development.json"
)


class FallbackError(RuntimeError):
    """The fallback mission cannot safely continue."""


class EventFactory:
    def __init__(self, mission_id: str, run_id: str) -> None:
        self.mission_id = mission_id
        self.run_id = run_id
        self.sequences: Counter[str] = Counter()
        self.last_observed_at_ms = 0

    def make(self, kind: str, payload: dict[str, Any], *, source: str) -> dict[str, Any]:
        self.sequences[source] += 1
        observed_at_ms = max(int(time.time() * 1000), self.last_observed_at_ms + 1)
        self.last_observed_at_ms = observed_at_ms
        event = {
            "schema": RESCUE_SCHEMA,
            "mission_id": self.mission_id,
            "event_id": f"mac-fallback:{self.run_id}:{source}:{self.sequences[source]}",
            "source": source,
            "source_seq": self.sequences[source],
            "observed_at_ms": observed_at_ms,
            "kind": kind,
            "payload": payload,
        }
        return validate_rescue_event(event, expected_mission_id=self.mission_id)


class HttpPublisher:
    def __init__(self, collector_url: str, timeout_seconds: float = 3.0) -> None:
        self.events_url = f"{collector_url.rstrip('/')}/events"
        self.timeout_seconds = timeout_seconds

    def __call__(self, event: dict[str, Any]) -> None:
        request = Request(
            self.events_url,
            data=json.dumps(event, allow_nan=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise FallbackError(f"collector rejected event ({error.code}): {detail}") from error
        except (URLError, TimeoutError) as error:
            raise FallbackError(f"collector unavailable at {self.events_url}: {error}") from error
        if not body.get("ok"):
            raise FallbackError(f"collector rejected event: {body}")


def _load_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FallbackError(f"cannot read frozen movement contract {path}: {error}") from error
    if contract.get("mission_id") != MISSION_ID:
        raise FallbackError("movement contract mission_id does not match OP-VARUNA-001")
    roster = contract.get("vehicles", {}).get("roster")
    cells = contract.get("search_cells", {}).get("cells")
    if roster != ["alpha", "bravo", "charlie", "delta", "echo"] or len(cells or []) != 10:
        raise FallbackError("movement contract does not contain the frozen five-node/ten-cell plan")
    return contract


def _interpolate(start: list[float], end: list[float], fraction: float) -> list[float]:
    return [round(a + ((b - a) * fraction), 4) for a, b in zip(start, end, strict=True)]


def _position(
    contract: dict[str, Any], node: str, progress: float, *, deflection_m: float = 0.0
) -> list[float]:
    start = contract["route"]["centroid_start_ned_m"]
    end = contract["route"]["centroid_end_ned_m"]
    centroid = _interpolate(start, end, progress)
    initial = contract["vehicles"]["initial_poses_ned_m"][node]
    return [
        round(centroid[0] + initial[0], 4),
        round(centroid[1] + initial[1] + deflection_m, 4),
        centroid[2],
    ]


def _owned_cells(cells: list[dict[str, Any]], owner_by_cell: dict[str, str], node: str) -> list[dict[str, Any]]:
    return [cell for cell in cells if owner_by_cell[cell["id"]] == node]


def run_fallback_mission(
    *,
    contract_path: Path = DEFAULT_CONTRACT,
    mode: str = "nominal",
    interval_seconds: float = 0.25,
    steps: int = 24,
    publish: Callable[[dict[str, Any]], None],
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Run one deterministic mission and return the validated emitted events."""
    if mode not in {"nominal", "hold", "quarantine"}:
        raise FallbackError("mode must be nominal, hold or quarantine")
    if steps < 12:
        raise FallbackError("steps must be at least 12")
    if interval_seconds < 0:
        raise FallbackError("interval_seconds cannot be negative")

    contract = _load_contract(contract_path)
    roster: list[str] = contract["vehicles"]["roster"]
    cells: list[dict[str, Any]] = contract["search_cells"]["cells"]
    initial_poses = contract["vehicles"]["initial_poses_ned_m"]
    owner_by_cell = {cell["id"]: cell["owner"] for cell in cells}
    run_id = uuid.uuid4().hex[:12]
    factory = EventFactory(MISSION_ID, run_id)
    emitted: list[dict[str, Any]] = []

    def emit(kind: str, payload: dict[str, Any], *, source: str) -> None:
        event = factory.make(kind, payload, source=source)
        publish(event)
        emitted.append(event)

    emit(
        "mission_started",
        {
            "scenario_id": f"factorycity_contract_mac_fallback_{mode}_v1",
            "coordinate_frame": "NED",
        },
        source="mission.controller",
    )
    for node in roster:
        assigned = [cell["id"] for cell in cells if cell["owner"] == node]
        emit(
            "assignment",
            {
                "node": node,
                "sector_id": f"route-{node}",
                "cells_total": len(assigned),
                "cell_ids": assigned,
            },
            source="mission.controller",
        )
        emit(
            "authorization",
            {"node": node, "decision": "ALLOW", "reason": "mac_fallback_operator_start"},
            source="mac-fallback.operator",
        )
        emit("link_state", {"node": node, "state": "ONLINE"}, source=f"{node}.telemetry")
        emit(
            "vehicle_state",
            {"node": node, "state": "READY", "position_ned": initial_poses[node], "battery_pct": 100.0},
            source=f"{node}.telemetry",
        )

    progress(f"START mode={mode} run={run_id} roster=5 cells=10")
    sleep(interval_seconds)

    node_progress = {node: 0.0 for node in roster}
    held_node = "bravo"
    held = False
    resumed = False
    quarantined = False
    obstacle_stage = 0
    hazard_emitted = False
    person_emitted = False

    for step in range(steps + 1):
        base_progress = step / steps

        if mode == "hold" and not held and base_progress >= 0.42:
            held = True
            emit(
                "authorization",
                {"node": held_node, "decision": "HOLD", "reason": "operator_safety_review"},
                source="mac-fallback.operator",
            )
            progress("HOLD bravo: operator safety review; other vehicles continue")
        if mode == "hold" and held and not resumed and base_progress >= 0.58:
            resumed = True
            emit(
                "authorization",
                {"node": held_node, "decision": "ALLOW", "reason": "operator_safety_review_cleared"},
                source="mac-fallback.operator",
            )
            progress("ALLOW bravo: safety review cleared; route rejoined")

        if mode == "quarantine" and not quarantined and base_progress >= 0.52:
            quarantined = True
            emit(
                "authorization",
                {"node": "alpha", "decision": "QUARANTINE", "reason": "simulated_integrity_policy_failure"},
                source="mac-fallback.operator",
            )
            emit(
                "task_reassigned",
                {
                    "from_node": "alpha",
                    "to_node": "bravo",
                    "cells_count": 1,
                    "cell_ids": ["route_cell_05"],
                    "reason": "quarantined_vehicle_reassignment",
                },
                source="mission.controller",
            )
            owner_by_cell["route_cell_05"] = "bravo"
            progress("QUARANTINE alpha; route_cell_05 reassigned to bravo")

        for node in roster:
            if mode == "quarantine" and node == "alpha" and quarantined:
                node_progress[node] = min(node_progress[node], 0.52)
                state = "QUARANTINED"
            elif mode == "hold" and node == held_node and held and not resumed:
                state = "HOLD"
            else:
                catchup = min(1.0, base_progress + (0.10 if node == held_node and resumed else 0.0))
                node_progress[node] = max(node_progress[node], catchup)
                state = "SEARCHING"

            deflection = 0.0
            if mode == "nominal" and node == "alpha" and 0.50 <= base_progress <= 0.70:
                node_progress[node] = min(node_progress[node], 0.55)
                if obstacle_stage == 0:
                    state = "HOLD"
                    for event_type, reason in (
                        ("OBSTACLE_DETECTED", "depth_below_stopping_boundary"),
                        ("SAFETY_HOLD", "obstacle_safety_hold"),
                    ):
                        emit(
                            "movement_safety",
                            {
                                "node": node,
                                "cell_id": "route_cell_05",
                                "event_type": event_type,
                                "measurement_source": "front_depth_and_vehicle_state",
                                "measured_distance_m": 3.7,
                                "reason_code": reason,
                                "result": "NON_TERMINAL",
                                "position_ned": _position(contract, node, node_progress[node]),
                            },
                            source=f"{node}.telemetry",
                        )
                    progress("OBSTACLE alpha: HOLD; safe deflection being selected")
                    obstacle_stage = 1
                elif obstacle_stage == 1:
                    state = "HOLD"
                    deflection = 2.5
                    emit(
                        "movement_safety",
                        {
                            "node": node,
                            "cell_id": "route_cell_05",
                            "event_type": "DEFLECTION_SELECTED",
                            "measurement_source": "front_depth_and_vehicle_state",
                            "measured_distance_m": 3.7,
                            "reason_code": "safe_deflection_selected",
                            "result": "NON_TERMINAL",
                            "position_ned": _position(contract, node, node_progress[node], deflection_m=deflection),
                        },
                        source=f"{node}.telemetry",
                    )
                    progress("DEFLECT alpha: temporary lateral clearance")
                    obstacle_stage = 2
                elif obstacle_stage == 2:
                    emit(
                        "movement_safety",
                        {
                            "node": node,
                            "cell_id": "route_cell_05",
                            "event_type": "ROUTE_REJOINED",
                            "measurement_source": "front_depth_and_vehicle_state",
                            "measured_distance_m": 6.2,
                            "reason_code": "nominal_route_rejoined",
                            "result": "NON_TERMINAL",
                            "position_ned": _position(contract, node, node_progress[node]),
                        },
                        source=f"{node}.telemetry",
                    )
                    progress("REJOIN alpha: nominal A-to-B route restored")
                    obstacle_stage = 3
            elif mode == "nominal" and node == "alpha" and obstacle_stage >= 3:
                node_progress[node] = min(1.0, base_progress + 0.10)

            position_ned = _position(contract, node, node_progress[node], deflection_m=deflection)
            emit(
                "vehicle_state",
                {
                    "node": node,
                    "state": state,
                    "position_ned": position_ned,
                    "battery_pct": round(max(38.0, 100.0 - (node_progress[node] * 52.0)), 1),
                },
                source=f"{node}.telemetry",
            )

            owned = _owned_cells(cells, owner_by_cell, node)
            completed = [cell["id"] for cell in owned if node_progress[node] >= cell["end_fraction"]]
            in_progress = [
                cell["id"]
                for cell in owned
                if cell["start_fraction"] <= node_progress[node] < cell["end_fraction"]
            ]
            emit(
                "coverage",
                {
                    "node": node,
                    "sector_id": f"route-{node}",
                    "visited_cells": len(completed),
                    "total_cells": len(owned),
                    "in_progress_cell_ids": in_progress,
                    "completed_cell_ids": completed,
                    "blocked_cell_ids": [],
                },
                source=f"{node}.telemetry",
            )

        if not hazard_emitted and base_progress >= 0.33:
            hazard_emitted = True
            emit(
                "hazard",
                {
                    "node": "delta",
                    "hazard_id": f"fallback-flood-front-{run_id}",
                    "class_id": "water_or_flood",
                    "confidence": 0.96,
                    "position_ned": _interpolate(
                        contract["route"]["centroid_start_ned_m"],
                        contract["route"]["centroid_end_ned_m"],
                        0.36,
                    ),
                    "uncertainty_m": 1.5,
                },
                source="delta.fusion",
            )
            progress("HAZARD synthetic flood-front marker published")
        if not person_emitted and base_progress >= 0.68:
            person_emitted = True
            emit(
                "observation",
                {
                    "node": "charlie",
                    "observation_id": f"fallback-person-{run_id}",
                    "class_id": "person_candidate",
                    "confidence": 0.91,
                    "frame_id": f"fallback-frame-{run_id}",
                    "modality": "synthetic_thermal",
                    "model_id": "mac-fallback-perception-v1",
                    "model_sha256": None,
                    "bbox_norm": [0.31, 0.22, 0.48, 0.76],
                    "position_ned": _interpolate(
                        contract["route"]["centroid_start_ned_m"],
                        contract["route"]["centroid_end_ned_m"],
                        0.71,
                    ),
                    "uncertainty_m": 2.0,
                    "localization_method": "external_pose_fusion",
                    "evidence_security": "UNVERIFIED",
                    "security_reasons": ["synthetic_mac_fallback_observation"],
                },
                source="charlie.perception",
            )
            progress("PERSON synthetic candidate marker published")

        sleep(interval_seconds)

    for node in roster:
        if mode == "quarantine" and node == "alpha":
            continue
        final_position = _position(contract, node, 1.0)
        for state in ("LANDING", "LANDED"):
            emit(
                "vehicle_state",
                {"node": node, "state": state, "position_ned": final_position, "battery_pct": 46.0},
                source=f"{node}.telemetry",
            )
            sleep(interval_seconds)

    emit(
        "mission_completed",
        {"status": "PASS", "completed_cells": 10, "total_cells": 10},
        source="mission.controller",
    )
    progress(f"PASS mode={mode} completed_cells=10/10 events={len(emitted)}")
    return emitted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the clearly labeled Mac fallback five-drone mission producer"
    )
    parser.add_argument("--collector-url", default="http://127.0.0.1:8770")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--mode", choices=("nominal", "hold", "quarantine"), default="nominal")
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--steps", type=int, default=24)
    args = parser.parse_args(argv)
    try:
        run_fallback_mission(
            contract_path=args.contract,
            mode=args.mode,
            interval_seconds=args.interval,
            steps=args.steps,
            publish=HttpPublisher(args.collector_url),
        )
    except (FallbackError, OSError, ValueError) as error:
        print(f"FAIL {error}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
