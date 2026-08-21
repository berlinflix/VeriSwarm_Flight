"""Deterministic, configuration-driven launch placement for FactoryCity.

The functions in this module are pure apart from calls to the supplied clearance provider.
They contain no fleet roster, scenario dimensions, endpoint, spawn coordinates, or scene
assumptions. A live provider is introduced only after FactoryCity integration; tests use
explicit synthetic providers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .config import FactoryCityConfig, LaunchAreaConfig


LAUNCH_MANIFEST_SCHEMA_ID = "veriswarm.factorycity.launch_manifest.v1"
_FLOAT_REL_TOL = 1e-12
_FLOAT_ABS_TOL = 1e-12


class LaunchPlacementError(ValueError):
    """Raised when a launch plan cannot be generated or rendered safely."""


class ClearanceContractError(LaunchPlacementError):
    """Raised when a scene-clearance provider violates its declared contract."""


class InsufficientLaunchCapacity(LaunchPlacementError):
    """Raised when the evaluated lattice cannot hold the configured fleet safely."""


@dataclass(frozen=True)
class SquareBounds:
    minimum_x_m: float
    maximum_x_m: float
    minimum_y_m: float
    maximum_y_m: float

    @property
    def side_length_m(self) -> float:
        return self.maximum_x_m - self.minimum_x_m

    def contains(self, x_m: float, y_m: float) -> bool:
        return (
            self.minimum_x_m <= x_m <= self.maximum_x_m
            and self.minimum_y_m <= y_m <= self.maximum_y_m
        )


@dataclass(frozen=True)
class CandidatePoint:
    candidate_id: str
    row: int
    column: int
    x_m: float
    y_m: float


@dataclass(frozen=True)
class ClearanceRequest:
    candidate: CandidatePoint
    altitude_minimum_z_ned_m: float
    altitude_maximum_z_ned_m: float
    required_clearance_m: float


@dataclass(frozen=True)
class ClearanceProbeResult:
    ground_clear: bool
    vertical_corridor_clear: bool
    ground_z_ned_m: float | None
    evidence_id: str
    reason: str


class SceneClearanceProvider(Protocol):
    """Boundary implemented by live FactoryCity geometry analysis in Phase 3."""

    @property
    def provider_id(self) -> str:
        """Return the stable provider identity declared by scenario configuration."""

    @property
    def scene_validated(self) -> bool:
        """Return whether results came from the intended live scene."""

    def probe(self, request: ClearanceRequest) -> ClearanceProbeResult:
        """Evaluate ground support and the configured vertical flight corridor."""


@dataclass(frozen=True)
class ClearanceEvaluation:
    candidate: CandidatePoint
    ground_clear: bool
    vertical_corridor_clear: bool
    ground_z_ned_m: float | None
    evidence_id: str
    reason: str

    @property
    def accepted(self) -> bool:
        return self.ground_clear and self.vertical_corridor_clear


@dataclass(frozen=True)
class LaunchPosition:
    vehicle_name: str
    candidate_id: str
    x_m: float
    y_m: float
    ground_z_ned_m: float
    z_ned_m: float
    clearance_evidence_id: str


@dataclass(frozen=True)
class PairwiseDistance:
    first_vehicle: str
    second_vehicle: str
    distance_m: float


@dataclass(frozen=True)
class LaunchPlan:
    generator: str
    deterministic_seed: int
    provider_id: str
    provider_scene_validated: bool
    bounds: SquareBounds
    lattice_intervals_per_axis: int
    candidate_count: int
    accepted_candidate_count: int
    evaluations: tuple[ClearanceEvaluation, ...]
    positions: tuple[LaunchPosition, ...]
    pairwise_distances: tuple[PairwiseDistance, ...]
    minimum_pairwise_distance_m: float | None


def calculate_launch_bounds(area: LaunchAreaConfig) -> SquareBounds:
    """Calculate inclusive usable bounds after applying configured edge clearance."""

    usable_half_side = (area.side_length_m / 2.0) - area.edge_clearance_m
    if usable_half_side <= 0.0:
        raise LaunchPlacementError("configured edge clearance leaves no usable square")
    return SquareBounds(
        minimum_x_m=area.center.x - usable_half_side,
        maximum_x_m=area.center.x + usable_half_side,
        minimum_y_m=area.center.y - usable_half_side,
        maximum_y_m=area.center.y + usable_half_side,
    )


def generate_lattice_candidates(
    area: LaunchAreaConfig,
    requested_count: int,
) -> tuple[SquareBounds, int, tuple[CandidatePoint, ...]]:
    """Generate a square lattice derived only from fleet and separation configuration."""

    if isinstance(requested_count, bool) or not isinstance(requested_count, int):
        raise LaunchPlacementError("requested launch count must be an integer")
    if requested_count <= 0:
        raise LaunchPlacementError("requested launch count must be greater than zero")

    bounds = calculate_launch_bounds(area)
    if requested_count == 1:
        point = CandidatePoint(
            candidate_id="r0c0",
            row=0,
            column=0,
            x_m=area.center.x,
            y_m=area.center.y,
        )
        return bounds, 0, (point,)

    capacity_intervals = math.ceil(math.sqrt(requested_count)) - 1
    separation_ratio = (
        bounds.side_length_m * math.sqrt(2.0) / area.minimum_separation_m
    )
    separation_intervals = (
        math.ceil(separation_ratio)
        if math.isfinite(separation_ratio)
        else requested_count
    )
    # Fleet size provides a configuration-derived resource bound. A finer grid than N
    # intervals per axis cannot improve the requested N-point count enough to justify
    # unbounded work for an accidentally microscopic separation value.
    separation_intervals = min(separation_intervals, requested_count)
    intervals = max(1, capacity_intervals, separation_intervals)
    candidates: list[CandidatePoint] = []
    for row in range(intervals + 1):
        y_m = bounds.minimum_y_m + (bounds.side_length_m * row / intervals)
        for column in range(intervals + 1):
            x_m = bounds.minimum_x_m + (
                bounds.side_length_m * column / intervals
            )
            candidates.append(
                CandidatePoint(
                    candidate_id=f"r{row}c{column}",
                    row=row,
                    column=column,
                    x_m=x_m,
                    y_m=y_m,
                )
            )
    return bounds, intervals, tuple(candidates)


def _nonempty_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ClearanceContractError(f"{context} must be a non-empty trimmed string")
    return value


def _validate_probe_result(
    result: Any,
    candidate: CandidatePoint,
) -> ClearanceProbeResult:
    context = f"clearance result for {candidate.candidate_id}"
    if not isinstance(result, ClearanceProbeResult):
        raise ClearanceContractError(f"{context} has the wrong result type")
    if type(result.ground_clear) is not bool:
        raise ClearanceContractError(f"{context}.ground_clear must be a boolean")
    if type(result.vertical_corridor_clear) is not bool:
        raise ClearanceContractError(
            f"{context}.vertical_corridor_clear must be a boolean"
        )
    evidence_id = _nonempty_text(result.evidence_id, f"{context}.evidence_id")
    if not isinstance(result.reason, str) or result.reason != result.reason.strip():
        raise ClearanceContractError(f"{context}.reason must be a trimmed string")
    if not result.ground_clear or not result.vertical_corridor_clear:
        _nonempty_text(result.reason, f"{context}.reason")
    ground_z = result.ground_z_ned_m
    if result.ground_clear:
        if (
            isinstance(ground_z, bool)
            or not isinstance(ground_z, (int, float))
            or not math.isfinite(float(ground_z))
        ):
            raise ClearanceContractError(
                f"{context}.ground_z_ned_m must be finite when ground is clear"
            )
        ground_z = float(ground_z)
    elif ground_z is not None:
        if (
            isinstance(ground_z, bool)
            or not isinstance(ground_z, (int, float))
            or not math.isfinite(float(ground_z))
        ):
            raise ClearanceContractError(
                f"{context}.ground_z_ned_m must be finite or null"
            )
        ground_z = float(ground_z)
    return ClearanceProbeResult(
        ground_clear=result.ground_clear,
        vertical_corridor_clear=result.vertical_corridor_clear,
        ground_z_ned_m=ground_z,
        evidence_id=evidence_id,
        reason=result.reason,
    )


def _seed_rank(seed: int, candidate_id: str) -> int:
    payload = f"{seed}:{candidate_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), byteorder="big")


def _distance_squared(first: CandidatePoint, second: CandidatePoint) -> float:
    return ((first.x_m - second.x_m) ** 2) + ((first.y_m - second.y_m) ** 2)


def _select_maxmin(
    evaluations: tuple[ClearanceEvaluation, ...],
    requested_count: int,
    center_x_m: float,
    center_y_m: float,
    seed: int,
) -> tuple[ClearanceEvaluation, ...]:
    available = [item for item in evaluations if item.accepted]
    if len(available) < requested_count:
        raise InsufficientLaunchCapacity(
            "clearance provider accepted fewer candidates than the configured fleet"
        )

    selected: list[ClearanceEvaluation] = []
    remaining = {item.candidate.candidate_id: item for item in available}
    first = max(
        available,
        key=lambda item: (
            ((item.candidate.x_m - center_x_m) ** 2)
            + ((item.candidate.y_m - center_y_m) ** 2),
            _seed_rank(seed, item.candidate.candidate_id),
        ),
    )
    selected.append(first)
    del remaining[first.candidate.candidate_id]

    minimum_distances = {
        candidate_id: _distance_squared(item.candidate, first.candidate)
        for candidate_id, item in remaining.items()
    }
    while len(selected) < requested_count:
        candidate_id, next_item = max(
            remaining.items(),
            key=lambda pair: (
                minimum_distances[pair[0]],
                _seed_rank(seed, pair[0]),
            ),
        )
        selected.append(next_item)
        del remaining[candidate_id]
        del minimum_distances[candidate_id]
        for other_id, other in remaining.items():
            distance = _distance_squared(other.candidate, next_item.candidate)
            minimum_distances[other_id] = min(
                minimum_distances[other_id], distance
            )
    return tuple(selected)


def _pairwise_distances(
    positions: tuple[LaunchPosition, ...],
) -> tuple[PairwiseDistance, ...]:
    distances: list[PairwiseDistance] = []
    for first_index, first in enumerate(positions):
        for second in positions[first_index + 1 :]:
            distance = math.hypot(first.x_m - second.x_m, first.y_m - second.y_m)
            distances.append(
                PairwiseDistance(
                    first_vehicle=first.vehicle_name,
                    second_vehicle=second.vehicle_name,
                    distance_m=distance,
                )
            )
    return tuple(distances)


def _validate_ground_z(config: FactoryCityConfig, ground_z_ned_m: float) -> float:
    geofence = config.limits.geofence
    if not geofence.minimum.z <= ground_z_ned_m <= geofence.maximum.z:
        raise LaunchPlacementError(
            "selected ground surface lies outside the configured vertical geofence"
        )
    if ground_z_ned_m <= config.limits.altitude_band.maximum_z:
        raise LaunchPlacementError(
            "configured NED flight altitude band is not entirely above selected ground"
        )
    spawn_z_ned_m = (
        ground_z_ned_m - config.launch_area.initial_spawn_clearance_m
    )
    if not (
        config.limits.geofence.minimum.z
        <= spawn_z_ned_m
        <= config.limits.geofence.maximum.z
    ):
        raise LaunchPlacementError(
            "configured initial spawn height lies outside the vertical geofence"
        )
    return spawn_z_ned_m


def generate_launch_plan(
    config: FactoryCityConfig,
    clearance_provider: SceneClearanceProvider,
) -> LaunchPlan:
    """Generate and validate a deterministic launch plan for configured fleet size N."""

    provider_id = _nonempty_text(
        getattr(clearance_provider, "provider_id", None), "clearance provider id"
    )
    if provider_id != config.launch_area.ground_clearance_probe:
        raise ClearanceContractError(
            "clearance provider id does not match launch-area configuration"
        )
    scene_validated = getattr(clearance_provider, "scene_validated", None)
    if type(scene_validated) is not bool:
        raise ClearanceContractError("clearance provider scene_validated must be boolean")

    bounds, intervals, candidates = generate_lattice_candidates(
        config.launch_area, config.fleet.expected_count
    )
    evaluations: list[ClearanceEvaluation] = []
    for candidate in candidates:
        request = ClearanceRequest(
            candidate=candidate,
            altitude_minimum_z_ned_m=config.limits.altitude_band.minimum_z,
            altitude_maximum_z_ned_m=config.limits.altitude_band.maximum_z,
            required_clearance_m=config.limits.collision_clearance_m,
        )
        try:
            raw_result = clearance_provider.probe(request)
        except Exception as exc:
            raise ClearanceContractError(
                f"clearance provider failed for {candidate.candidate_id}: {exc}"
            ) from exc
        result = _validate_probe_result(raw_result, candidate)
        evaluations.append(
            ClearanceEvaluation(
                candidate=candidate,
                ground_clear=result.ground_clear,
                vertical_corridor_clear=result.vertical_corridor_clear,
                ground_z_ned_m=result.ground_z_ned_m,
                evidence_id=result.evidence_id,
                reason=result.reason,
            )
        )

    selected = _select_maxmin(
        tuple(evaluations),
        requested_count=config.fleet.expected_count,
        center_x_m=config.launch_area.center.x,
        center_y_m=config.launch_area.center.y,
        seed=config.launch_area.deterministic_seed,
    )
    for evaluation in selected:
        _validate_ground_z(config, float(evaluation.ground_z_ned_m))
    positions = tuple(
        LaunchPosition(
            vehicle_name=vehicle.name,
            candidate_id=evaluation.candidate.candidate_id,
            x_m=evaluation.candidate.x_m,
            y_m=evaluation.candidate.y_m,
            ground_z_ned_m=float(evaluation.ground_z_ned_m),
            z_ned_m=_validate_ground_z(
                config, float(evaluation.ground_z_ned_m)
            ),
            clearance_evidence_id=evaluation.evidence_id,
        )
        for vehicle, evaluation in zip(config.fleet.vehicles, selected, strict=True)
    )
    if any(not bounds.contains(position.x_m, position.y_m) for position in positions):
        raise LaunchPlacementError("selected launch position lies outside usable bounds")
    pairwise = _pairwise_distances(positions)
    minimum_distance = min(
        (item.distance_m for item in pairwise),
        default=None,
    )
    if minimum_distance is not None and (
        minimum_distance < config.launch_area.minimum_separation_m
        and not math.isclose(
            minimum_distance,
            config.launch_area.minimum_separation_m,
            rel_tol=_FLOAT_REL_TOL,
            abs_tol=_FLOAT_ABS_TOL,
        )
    ):
        raise InsufficientLaunchCapacity(
            "max-min selection cannot satisfy configured minimum separation"
        )
    return LaunchPlan(
        generator=config.launch_area.formation_generator,
        deterministic_seed=config.launch_area.deterministic_seed,
        provider_id=provider_id,
        provider_scene_validated=scene_validated,
        bounds=bounds,
        lattice_intervals_per_axis=intervals,
        candidate_count=len(candidates),
        accepted_candidate_count=sum(item.accepted for item in evaluations),
        evaluations=tuple(evaluations),
        positions=positions,
        pairwise_distances=pairwise,
        minimum_pairwise_distance_m=minimum_distance,
    )


def _same_number(first: float, second: float) -> bool:
    return math.isclose(
        first,
        second,
        rel_tol=_FLOAT_REL_TOL,
        abs_tol=_FLOAT_ABS_TOL,
    )


def validate_launch_plan(config: FactoryCityConfig, plan: LaunchPlan) -> None:
    """Revalidate a plan before it can cross an artifact or RPC boundary."""

    if not isinstance(plan, LaunchPlan):
        raise LaunchPlacementError("launch plan has the wrong type")
    if plan.generator != config.launch_area.formation_generator:
        raise LaunchPlacementError("launch-plan generator does not match configuration")
    if plan.deterministic_seed != config.launch_area.deterministic_seed:
        raise LaunchPlacementError("launch-plan seed does not match configuration")
    if type(plan.provider_scene_validated) is not bool:
        raise LaunchPlacementError("launch-plan scene validation flag must be boolean")
    plan_names = tuple(position.vehicle_name for position in plan.positions)
    if plan_names != config.fleet.names:
        raise LaunchPlacementError(
            "launch-plan roster or order does not match validated configuration"
        )
    if plan.provider_id != config.launch_area.ground_clearance_probe:
        raise LaunchPlacementError("launch-plan provider does not match configuration")

    expected_bounds, expected_intervals, expected_candidates = (
        generate_lattice_candidates(config.launch_area, config.fleet.expected_count)
    )
    if plan.bounds != expected_bounds:
        raise LaunchPlacementError("launch-plan bounds do not match configuration")
    if plan.lattice_intervals_per_axis != expected_intervals:
        raise LaunchPlacementError("launch-plan lattice does not match configuration")
    if plan.candidate_count != len(expected_candidates):
        raise LaunchPlacementError("launch-plan candidate count is inconsistent")
    if len(plan.evaluations) != len(expected_candidates):
        raise LaunchPlacementError("launch-plan clearance evaluation count is inconsistent")
    if tuple(item.candidate for item in plan.evaluations) != expected_candidates:
        raise LaunchPlacementError(
            "launch-plan clearance evaluations do not match the configured lattice"
        )
    if any(
        type(item.ground_clear) is not bool
        or type(item.vertical_corridor_clear) is not bool
        for item in plan.evaluations
    ):
        raise LaunchPlacementError("launch-plan clearance flags must be boolean")
    if plan.accepted_candidate_count != sum(
        item.accepted for item in plan.evaluations
    ):
        raise LaunchPlacementError("launch-plan accepted candidate count is inconsistent")

    evaluation_by_id = {
        item.candidate.candidate_id: item for item in plan.evaluations
    }
    if len(evaluation_by_id) != len(plan.evaluations):
        raise LaunchPlacementError("launch-plan candidate identities must be unique")
    selected_candidate_ids: set[str] = set()
    for position in plan.positions:
        if position.candidate_id in selected_candidate_ids:
            raise LaunchPlacementError("launch-plan selected candidates must be unique")
        selected_candidate_ids.add(position.candidate_id)
        evaluation = evaluation_by_id.get(position.candidate_id)
        if evaluation is None or not evaluation.accepted:
            raise LaunchPlacementError(
                "launch-plan position lacks an accepted clearance evaluation"
            )
        values = (
            position.x_m,
            position.y_m,
            position.ground_z_ned_m,
            position.z_ned_m,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in values
        ):
            raise LaunchPlacementError("launch-plan positions must be finite numbers")
        if not plan.bounds.contains(position.x_m, position.y_m):
            raise LaunchPlacementError("launch-plan position lies outside usable bounds")
        expected_spawn_z = _validate_ground_z(
            config, float(position.ground_z_ned_m)
        )
        if not (
            _same_number(position.x_m, evaluation.candidate.x_m)
            and _same_number(position.y_m, evaluation.candidate.y_m)
            and evaluation.ground_z_ned_m is not None
            and _same_number(
                position.ground_z_ned_m, evaluation.ground_z_ned_m
            )
            and _same_number(position.z_ned_m, expected_spawn_z)
            and position.clearance_evidence_id == evaluation.evidence_id
        ):
            raise LaunchPlacementError(
                "launch-plan position does not match its clearance evaluation"
            )

    expected_pairwise = _pairwise_distances(plan.positions)
    if len(plan.pairwise_distances) != len(expected_pairwise):
        raise LaunchPlacementError("launch-plan pairwise distance count is inconsistent")
    for claimed, expected in zip(
        plan.pairwise_distances, expected_pairwise, strict=True
    ):
        if (
            claimed.first_vehicle != expected.first_vehicle
            or claimed.second_vehicle != expected.second_vehicle
            or not _same_number(claimed.distance_m, expected.distance_m)
        ):
            raise LaunchPlacementError("launch-plan pairwise distances are inconsistent")
    expected_minimum = min(
        (item.distance_m for item in expected_pairwise), default=None
    )
    if expected_minimum is None:
        if plan.minimum_pairwise_distance_m is not None:
            raise LaunchPlacementError("launch-plan minimum distance is inconsistent")
    elif plan.minimum_pairwise_distance_m is None or not _same_number(
        plan.minimum_pairwise_distance_m, expected_minimum
    ):
        raise LaunchPlacementError("launch-plan minimum distance is inconsistent")
    if expected_minimum is not None and (
        expected_minimum < config.launch_area.minimum_separation_m
        and not _same_number(
            expected_minimum, config.launch_area.minimum_separation_m
        )
    ):
        raise LaunchPlacementError(
            "launch-plan positions violate configured minimum separation"
        )


def _json_bytes(value: Any, *, pretty: bool) -> bytes:
    options: dict[str, Any] = {
        "allow_nan": False,
        "ensure_ascii": False,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    try:
        rendered = json.dumps(value, **options)
    except (TypeError, ValueError) as exc:
        raise LaunchPlacementError(f"artifact is not valid finite JSON: {exc}") from exc
    return (rendered + ("\n" if pretty else "")).encode("utf-8")


def render_cosys_settings(
    config: FactoryCityConfig,
    plan: LaunchPlan,
    base_settings: Mapping[str, Any],
) -> bytes:
    """Patch configured vehicle positions into an explicit CoSys settings document.

    Global runtime values, orientation, sensors, and any simulator-specific settings remain
    supplied by ``base_settings``. This function changes only VehicleType and X/Y/Z for the
    exact configured roster and never mutates the caller's mapping.
    """

    validate_launch_plan(config, plan)
    if not isinstance(base_settings, Mapping):
        raise LaunchPlacementError("base CoSys settings must be an object")
    rendered = copy.deepcopy(dict(base_settings))
    vehicles = rendered.get("Vehicles")
    if not isinstance(vehicles, Mapping):
        raise LaunchPlacementError("base CoSys settings must contain a Vehicles object")
    if set(vehicles) != set(config.fleet.names):
        raise LaunchPlacementError(
            "base CoSys settings Vehicles must match the exact configured roster"
        )

    output_vehicles: dict[str, Any] = {}
    positions = {position.vehicle_name: position for position in plan.positions}
    for vehicle in config.fleet.vehicles:
        base_vehicle = vehicles[vehicle.name]
        if not isinstance(base_vehicle, Mapping):
            raise LaunchPlacementError(
                f"base settings vehicle {vehicle.name!r} must be an object"
            )
        output_vehicle = copy.deepcopy(dict(base_vehicle))
        existing_type = output_vehicle.get("VehicleType")
        if existing_type is not None and existing_type != vehicle.vehicle_type:
            raise LaunchPlacementError(
                f"base settings VehicleType mismatch for {vehicle.name!r}"
            )
        position = positions[vehicle.name]
        output_vehicle["VehicleType"] = vehicle.vehicle_type
        output_vehicle["X"] = position.x_m
        output_vehicle["Y"] = position.y_m
        output_vehicle["Z"] = position.z_ned_m
        output_vehicles[vehicle.name] = output_vehicle
    rendered["Vehicles"] = output_vehicles
    return _json_bytes(rendered, pretty=True)


def _evaluation_record(evaluation: ClearanceEvaluation) -> dict[str, Any]:
    return {
        "candidate_id": evaluation.candidate.candidate_id,
        "column": evaluation.candidate.column,
        "evidence_id": evaluation.evidence_id,
        "ground_clear": evaluation.ground_clear,
        "ground_z_ned_m": evaluation.ground_z_ned_m,
        "reason": evaluation.reason,
        "row": evaluation.candidate.row,
        "vertical_corridor_clear": evaluation.vertical_corridor_clear,
        "x_m": evaluation.candidate.x_m,
        "y_m": evaluation.candidate.y_m,
    }


def render_launch_manifest(
    config: FactoryCityConfig,
    source_config_sha256: str,
    plan: LaunchPlan,
    settings_bytes: bytes,
) -> bytes:
    """Render deterministic launch evidence with exact input and settings hashes."""

    validate_launch_plan(config, plan)
    if not isinstance(source_config_sha256, str) or not all(
        character in "0123456789abcdefABCDEF" for character in source_config_sha256
    ) or len(source_config_sha256) != 64:
        raise LaunchPlacementError("source configuration SHA-256 must be 64 hex digits")
    if not isinstance(settings_bytes, bytes):
        raise LaunchPlacementError("settings artifact must be bytes")

    minimum_distance = plan.minimum_pairwise_distance_m
    body: dict[str, Any] = {
        "schema": LAUNCH_MANIFEST_SCHEMA_ID,
        "source_configuration": {
            "configuration_id": config.configuration_id,
            "sha256": source_config_sha256.lower(),
            "status": config.status,
        },
        "generator": {
            "id": plan.generator,
            "deterministic_seed": plan.deterministic_seed,
            "lattice_intervals_per_axis": plan.lattice_intervals_per_axis,
        },
        "clearance_provider": {
            "id": plan.provider_id,
            "scene_validated": plan.provider_scene_validated,
        },
        "inputs": {
            "altitude_band_ned_m": {
                "maximum_z": config.limits.altitude_band.maximum_z,
                "minimum_z": config.limits.altitude_band.minimum_z,
            },
            "center_ned_m": {
                "x": config.launch_area.center.x,
                "y": config.launch_area.center.y,
            },
            "collision_clearance_m": config.limits.collision_clearance_m,
            "edge_clearance_m": config.launch_area.edge_clearance_m,
            "frame": config.launch_area.frame,
            "initial_spawn_clearance_m": (
                config.launch_area.initial_spawn_clearance_m
            ),
            "takeoff_corridor_start_clearance_m": (
                config.launch_area.takeoff_corridor_start_clearance_m
            ),
            "minimum_separation_m": config.launch_area.minimum_separation_m,
            "requested_count": config.fleet.expected_count,
            "side_length_m": config.launch_area.side_length_m,
        },
        "usable_bounds_ned_m": {
            "maximum_x": plan.bounds.maximum_x_m,
            "maximum_y": plan.bounds.maximum_y_m,
            "minimum_x": plan.bounds.minimum_x_m,
            "minimum_y": plan.bounds.minimum_y_m,
        },
        "candidate_summary": {
            "accepted": plan.accepted_candidate_count,
            "evaluated": plan.candidate_count,
            "rejected": plan.candidate_count - plan.accepted_candidate_count,
        },
        "clearance_evaluations": [
            _evaluation_record(item) for item in plan.evaluations
        ],
        "positions": [
            {
                "candidate_id": position.candidate_id,
                "clearance_evidence_id": position.clearance_evidence_id,
                "vehicle_name": position.vehicle_name,
                "x_m": position.x_m,
                "y_m": position.y_m,
                "ground_z_ned_m": position.ground_z_ned_m,
                "z_ned_m": position.z_ned_m,
            }
            for position in plan.positions
        ],
        "pairwise_distances": [
            {
                "distance_m": item.distance_m,
                "first_vehicle": item.first_vehicle,
                "second_vehicle": item.second_vehicle,
            }
            for item in plan.pairwise_distances
        ],
        "checks": {
            "all_positions_inside_usable_square": all(
                plan.bounds.contains(position.x_m, position.y_m)
                for position in plan.positions
            ),
            "all_selected_clearance_passed": all(
                next(
                    item.accepted
                    for item in plan.evaluations
                    if item.candidate.candidate_id == position.candidate_id
                )
                for position in plan.positions
            ),
            "minimum_separation_passed": minimum_distance is None
            or minimum_distance >= config.launch_area.minimum_separation_m
            or math.isclose(
                minimum_distance,
                config.launch_area.minimum_separation_m,
                rel_tol=_FLOAT_REL_TOL,
                abs_tol=_FLOAT_ABS_TOL,
            ),
            "roster_complete": tuple(
                position.vehicle_name for position in plan.positions
            )
            == config.fleet.names,
        },
        "metrics": {
            "minimum_pairwise_distance_m": minimum_distance,
        },
        "settings_artifact": {
            "path": config.world.generated_settings_path,
            "sha256": hashlib.sha256(settings_bytes).hexdigest(),
        },
    }
    body_hash = hashlib.sha256(_json_bytes(body, pretty=False)).hexdigest()
    body["manifest_payload_sha256"] = body_hash
    return _json_bytes(body, pretty=True)


def write_create_once(path: str | Path, artifact_bytes: bytes) -> str:
    """Write an artifact without overwriting evidence and return its SHA-256."""

    if not isinstance(artifact_bytes, bytes):
        raise LaunchPlacementError("artifact content must be bytes")
    destination = Path(path)
    try:
        with destination.open("xb") as stream:
            stream.write(artifact_bytes)
            stream.flush()
    except FileExistsError as exc:
        raise LaunchPlacementError(
            f"refusing to overwrite existing artifact: {destination}"
        ) from exc
    except OSError as exc:
        raise LaunchPlacementError(
            f"cannot write artifact {destination}: {exc}"
        ) from exc
    return hashlib.sha256(artifact_bytes).hexdigest()
