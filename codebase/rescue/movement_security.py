"""Configuration-driven authorization gate for the FactoryCity movement contract.

This module does not issue CoSys commands.  It translates the frozen movement/security
contract into deterministic release, hover and abort instructions that Pratik's runtime
can enforce at every command boundary.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONTRACT_SCHEMA = "veriswarm.factorycity.joint_movement_contract.v1"
AUTHORIZATION_DECISIONS = frozenset({"ALLOW", "HOLD", "QUARANTINE"})


class MovementSecurityError(ValueError):
    """The movement contract or gate input is unsafe or inconsistent."""


@dataclass(frozen=True)
class AuthorizationResolution:
    decision: str
    reason: str


@dataclass(frozen=True)
class GateDecision:
    decision: str
    reason: str
    action: str
    release_command: bool
    vehicle_state: str


@dataclass(frozen=True)
class CellReassignment:
    cell_id: str
    from_node: str
    to_node: str


def load_movement_contract(path: Path) -> dict[str, Any]:
    """Load and minimally validate the frozen development contract."""
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise MovementSecurityError(f"movement_contract_not_found:{path}") from error
    except json.JSONDecodeError as error:
        raise MovementSecurityError(f"movement_contract_invalid_json:{error}") from error
    if not isinstance(contract, dict) or contract.get("schema") != CONTRACT_SCHEMA:
        raise MovementSecurityError("movement_contract_schema_invalid")

    roster = contract.get("vehicles", {}).get("roster")
    if (
        not isinstance(roster, list)
        or not roster
        or any(not isinstance(node, str) or not node for node in roster)
        or len(roster) != len(set(roster))
    ):
        raise MovementSecurityError("movement_contract_roster_invalid")

    cells = contract.get("search_cells", {}).get("cells")
    if not isinstance(cells, list) or not cells:
        raise MovementSecurityError("movement_contract_cells_invalid")
    cell_ids: set[str] = set()
    for cell in cells:
        if not isinstance(cell, dict):
            raise MovementSecurityError("movement_contract_cell_invalid")
        cell_id = cell.get("id")
        owner = cell.get("owner")
        if (
            not isinstance(cell_id, str)
            or not cell_id
            or cell_id in cell_ids
            or owner not in roster
        ):
            raise MovementSecurityError("movement_contract_cell_invalid")
        cell_ids.add(cell_id)

    timeout = contract.get("authorization_policy", {}).get(
        "freshness_timeout_seconds"
    )
    if not _finite_number(timeout) or float(timeout) <= 0.0:
        raise MovementSecurityError("movement_contract_freshness_invalid")
    if contract.get("authorization_policy", {}).get(
        "missing_stale_or_malformed_authorization"
    ) != "HOLD":
        raise MovementSecurityError("movement_contract_must_fail_closed")
    return contract


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def resolve_authorization(
    contract: Mapping[str, Any],
    *,
    node: str,
    authorization: Mapping[str, Any] | None,
    now_ms: int,
) -> AuthorizationResolution:
    """Resolve a fresh normalized authorization or fail closed to HOLD."""
    if not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms < 0:
        raise MovementSecurityError("now_ms_invalid")
    if authorization is None:
        return AuthorizationResolution("HOLD", "authorization_missing")
    if not isinstance(authorization, Mapping):
        return AuthorizationResolution("HOLD", "authorization_malformed")

    decision = authorization.get("decision")
    reason = authorization.get("reason")
    observed_at_ms = authorization.get("observed_at_ms")
    if (
        authorization.get("node") != node
        or decision not in AUTHORIZATION_DECISIONS
        or not isinstance(reason, str)
        or not reason
        or not isinstance(observed_at_ms, int)
        or isinstance(observed_at_ms, bool)
        or observed_at_ms < 0
        or observed_at_ms > now_ms
    ):
        return AuthorizationResolution("HOLD", "authorization_malformed")

    timeout_ms = int(
        float(contract["authorization_policy"]["freshness_timeout_seconds"]) * 1000
    )
    if now_ms - observed_at_ms > timeout_ms:
        return AuthorizationResolution("HOLD", "authorization_stale")
    return AuthorizationResolution(str(decision), reason)


def _command_limit_failure(
    contract: Mapping[str, Any], command: Mapping[str, Any] | None
) -> str | None:
    if not isinstance(command, Mapping):
        return "command_malformed"
    limits = contract.get("command_limits")
    if not isinstance(limits, Mapping):
        raise MovementSecurityError("movement_contract_limits_invalid")

    checks = (
        ("horizontal_velocity_mps", "horizontal_velocity_mps"),
        ("vertical_velocity_mps", "vertical_velocity_mps"),
        ("horizontal_acceleration_mps2", "horizontal_acceleration_mps2"),
        ("vertical_acceleration_mps2", "vertical_acceleration_mps2"),
    )
    for command_field, limit_field in checks:
        value = command.get(command_field)
        limit = limits.get(limit_field)
        if (
            not _finite_number(value)
            or float(value) < 0.0
            or not _finite_number(limit)
            or float(value) > float(limit)
        ):
            return f"{command_field}_outside_limit"

    separation = command.get("minimum_pairwise_separation_m")
    configured_separation = limits.get("minimum_pairwise_separation_m")
    if (
        not _finite_number(separation)
        or not _finite_number(configured_separation)
        or float(separation) < float(configured_separation)
    ):
        return "minimum_pairwise_separation_m_outside_limit"

    target = command.get("target_position_ned")
    if (
        not isinstance(target, Sequence)
        or isinstance(target, (str, bytes))
        or len(target) != 3
        or not all(_finite_number(component) for component in target)
    ):
        return "target_position_ned_malformed"
    geofence = limits.get("enroute_geofence_ned_m")
    if not isinstance(geofence, Mapping):
        raise MovementSecurityError("movement_contract_geofence_invalid")
    x, y, z = (float(component) for component in target)
    bounds = (
        (x, "x_min", "x_max"),
        (y, "y_min", "y_max"),
        (z, "z_min", "z_max"),
    )
    for value, lower_name, upper_name in bounds:
        lower = geofence.get(lower_name)
        upper = geofence.get(upper_name)
        if (
            not _finite_number(lower)
            or not _finite_number(upper)
            or value < float(lower)
            or value > float(upper)
        ):
            return "target_position_outside_geofence"
    return None


def evaluate_movement_command(
    contract: Mapping[str, Any],
    *,
    node: str,
    authorization: Mapping[str, Any] | None,
    command: Mapping[str, Any] | None,
    now_ms: int,
    in_flight: bool,
) -> GateDecision:
    """Return the only movement action permitted by authorization and limits."""
    roster = contract.get("vehicles", {}).get("roster", [])
    if node not in roster:
        raise MovementSecurityError(f"unknown_vehicle:{node}")
    resolution = resolve_authorization(
        contract, node=node, authorization=authorization, now_ms=now_ms
    )

    if resolution.decision == "QUARANTINE":
        return GateDecision(
            decision="QUARANTINE",
            reason=resolution.reason,
            action="ABORT_HOVER_LAND" if in_flight else "DO_NOT_DISPATCH",
            release_command=False,
            vehicle_state="QUARANTINED",
        )
    if resolution.decision == "HOLD":
        return GateDecision(
            decision="HOLD",
            reason=resolution.reason,
            action="HOVER" if in_flight else "DO_NOT_DISPATCH",
            release_command=False,
            vehicle_state="HOLD",
        )

    limit_failure = _command_limit_failure(contract, command)
    if limit_failure is not None:
        return GateDecision(
            decision="HOLD",
            reason=f"movement_limits_failed:{limit_failure}",
            action="HOVER" if in_flight else "DO_NOT_DISPATCH",
            release_command=False,
            vehicle_state="HOLD",
        )
    return GateDecision(
        decision="ALLOW",
        reason=resolution.reason,
        action="RELEASE",
        release_command=True,
        vehicle_state="SEARCHING",
    )


def reassign_unfinished_cells(
    contract: Mapping[str, Any],
    *,
    unavailable_node: str,
    completed_cell_ids: set[str] | frozenset[str],
    healthy_nodes: set[str] | frozenset[str],
) -> tuple[CellReassignment, ...]:
    """Apply the frozen lexical/rotating reassignment policy."""
    roster = list(contract.get("vehicles", {}).get("roster", []))
    if unavailable_node not in roster:
        raise MovementSecurityError(f"unknown_vehicle:{unavailable_node}")
    known_nodes = set(roster)
    if unavailable_node in healthy_nodes or not set(healthy_nodes).issubset(known_nodes):
        raise MovementSecurityError("healthy_roster_invalid")

    cells = contract.get("search_cells", {}).get("cells", [])
    known_cell_ids = {cell.get("id") for cell in cells if isinstance(cell, Mapping)}
    if not set(completed_cell_ids).issubset(known_cell_ids):
        raise MovementSecurityError("completed_cells_unknown")
    unfinished = sorted(
        str(cell["id"])
        for cell in cells
        if cell.get("owner") == unavailable_node
        and cell.get("id") not in completed_cell_ids
    )
    if not unfinished:
        return ()

    unavailable_index = roster.index(unavailable_node)
    rotation = roster[unavailable_index + 1 :] + roster[:unavailable_index]
    eligible = [node for node in rotation if node in healthy_nodes]
    if not eligible:
        raise MovementSecurityError("no_healthy_vehicle_for_reassignment")
    return tuple(
        CellReassignment(
            cell_id=cell_id,
            from_node=unavailable_node,
            to_node=eligible[index % len(eligible)],
        )
        for index, cell_id in enumerate(unfinished)
    )
