"""Run the additive authorization-aware, sensor-driven FactoryCity movement-v2 mission.

The accepted nominal-v1 runner remains unchanged.  This runner consumes only measured
CoSim depth/vehicle/collision telemetry, the immutable route/safety contracts and a
normalized authorization lease.  It durably enqueues rescue events before network
delivery; it never reads evaluator obstacle coordinates or survivor truth.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import cosysairsim

from rescue.movement_security import load_movement_contract
from rescue.outbox import RescueOutbox
from sim.cosys.factorycity.movement_v2 import (
    CellLedger,
    DurableMovementEvents,
    GatedCommandDispatcher,
    MovementV2Error,
    SensorDrivenMovementSupervisor,
    depth_sample_from_response,
    load_sensor_movement_extension,
)
from sim.cosys.factorycity.tools.run_factorycity_ab_swarm import (
    _collision_record,
    _copy_pose,
    _join_all,
    _landing_contact_sample,
    _load_config,
    _local_xy,
    _minimum_pairwise,
    _resolve_water_object,
    _world_positions,
)


AUTHORIZATION_SNAPSHOT_SCHEMA = "veriswarm.factorycity.authorization_snapshot.v1"
RUN_SCHEMA = "veriswarm.factorycity.movement_v2_run.v1"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _finite_vector(values: Sequence[object], name: str) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if not result or not all(math.isfinite(value) for value in result):
        raise MovementV2Error(f"{name}_invalid")
    return result


class AuthorizationFileProvider:
    """Reload a normalized authorization file at every command boundary.

    Accepted input is either one canonical ``authorization`` rescue event or a local
    snapshot containing canonical events under ``authorizations``.  Parse/read failures
    return ``None`` so the frozen gate fails closed to HOLD.
    """

    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def _normalize(candidate: object, node: str) -> dict[str, Any] | None:
        if not isinstance(candidate, Mapping):
            return None
        if candidate.get("schema") == "veriswarm.rescue.event.v1":
            if candidate.get("kind") != "authorization":
                return None
            payload = candidate.get("payload")
            if not isinstance(payload, Mapping) or payload.get("node") != node:
                return None
            normalized = dict(payload)
            normalized["observed_at_ms"] = candidate.get("observed_at_ms")
            return normalized
        if candidate.get("node") == node:
            return dict(candidate)
        return None

    def latest(self, node: str) -> dict[str, Any] | None:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        direct = self._normalize(document, node)
        if direct is not None:
            return direct
        if not isinstance(document, Mapping):
            return None
        if document.get("schema") != AUTHORIZATION_SNAPSHOT_SCHEMA:
            return None
        authorizations = document.get("authorizations")
        if isinstance(authorizations, Mapping):
            return self._normalize(authorizations.get(node), node)
        if isinstance(authorizations, list):
            matches = [
                normalized
                for value in authorizations
                if (normalized := self._normalize(value, node)) is not None
            ]
            if matches:
                return max(matches, key=lambda value: int(value.get("observed_at_ms", -1)))
        return None


def _outbox_factory(
    *, mission_id: str, output_directory: Path
) -> Callable[[str], RescueOutbox]:
    names = {"mission.controller": "mission-controller"}

    def create(source: str) -> RescueOutbox:
        stem = names.get(source, source.removesuffix(".telemetry"))
        if not stem or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in stem):
            raise MovementV2Error(f"outbox_source_invalid:{source}")
        return RescueOutbox(
            mission_id,
            output_directory / f"{stem}-rescue-outbox.sqlite3",
        )

    return create


def _world_position(client: object, node: str) -> tuple[float, float, float]:
    return _world_positions(client, (node,))[node]


def _local_position(client: object, node: str) -> tuple[float, float, float]:
    position = client.getMultirotorState(vehicle_name=node).kinematics_estimated.position
    return _finite_vector(
        (position.x_val, position.y_val, position.z_val),
        f"{node}_local_position",
    )


def _route_metrics(
    local_position: Sequence[float], dx: float, dy: float, distance: float
) -> tuple[float, float]:
    x, y = float(local_position[0]), float(local_position[1])
    progress = max(0.0, min(1.0, (x * dx + y * dy) / (distance * distance)))
    signed_cross_track = (dx * y - dy * x) / distance
    return progress, signed_cross_track


def _command_payload(
    contract: Mapping[str, Any],
    *,
    target_world_ned: Sequence[float],
    horizontal_velocity_mps: float,
    vertical_velocity_mps: float,
    minimum_pairwise_separation_m: float,
) -> dict[str, Any]:
    limits = contract["command_limits"]
    return {
        "horizontal_velocity_mps": abs(float(horizontal_velocity_mps)),
        "vertical_velocity_mps": abs(float(vertical_velocity_mps)),
        "horizontal_acceleration_mps2": float(limits["horizontal_acceleration_mps2"]),
        "vertical_acceleration_mps2": float(limits["vertical_acceleration_mps2"]),
        "minimum_pairwise_separation_m": float(minimum_pairwise_separation_m),
        "target_position_ned": list(_finite_vector(target_world_ned, "target_world_ned")),
    }


def _gate_separation(
    contract: Mapping[str, Any], positions: Mapping[str, Sequence[float]]
) -> float:
    """Return measured separation, or the configured floor for one vehicle.

    Pairwise separation is undefined for fewer than two active vehicles.  Passing
    ``inf`` would correctly fail the finite-number gate, so the single-survivor case
    uses the immutable configured minimum.  It does not claim a measured distance.
    """

    configured = float(
        contract["command_limits"]["minimum_pairwise_separation_m"]
    )
    if len(positions) < 2:
        return configured
    measured = float(_minimum_pairwise(positions))
    if not math.isfinite(measured):
        raise MovementV2Error("minimum_pairwise_separation_invalid")
    return measured


def _join_if_future(value: Any) -> Any:
    join = getattr(value, "join", None)
    return join() if callable(join) else value


def _fail_closed_hover_or_disarm_landed(
    client: object,
    nodes: Sequence[str],
) -> tuple[str, ...]:
    """Leave airborne vehicles hovering; disarm only confirmed landed vehicles.

    A movement-v2 exception can occur after takeoff.  Unconditionally disarming in
    ``finally`` drops a simulated vehicle through the active flood.  HOLD semantics
    require no new nominal motion, so an airborne vehicle is commanded to hover and
    remains armed.  The operator can then stop/reset Play mode deliberately.  A vehicle
    already confirmed landed may still be disarmed during cleanup.
    """

    failures: list[str] = []
    for node in tuple(sorted(nodes)):
        try:
            state = client.getMultirotorState(vehicle_name=node)
            if state.landed_state == cosysairsim.LandedState.Landed:
                if not client.armDisarm(False, vehicle_name=node):
                    failures.append(f"{node}:disarm_failed")
                continue
            _join_if_future(client.hoverAsync(vehicle_name=node))
        except Exception as error:
            failures.append(f"{node}:{type(error).__name__}:{error}")
    return tuple(failures)


class LiveCoSimCommandAdapter:
    """Map supervisor actions to real CoSim calls through the frozen gate."""

    def __init__(
        self,
        *,
        client: object,
        contract: Mapping[str, Any],
        extension: Mapping[str, Any],
        authorization_provider: AuthorizationFileProvider,
        dispatcher: GatedCommandDispatcher,
        route_dx: float,
        route_dy: float,
        route_distance: float,
        cruise_z_ned_m: float,
        yaw_mode: object,
    ):
        self.client = client
        self.contract = contract
        self.extension = extension
        self.authorization_provider = authorization_provider
        self.dispatcher = dispatcher
        self.dx = route_dx
        self.dy = route_dy
        self.distance = route_distance
        self.route_unit = (route_dx / route_distance, route_dy / route_distance)
        self.left_unit = (-self.route_unit[1], self.route_unit[0])
        self.cruise_z = cruise_z_ned_m
        self.yaw_mode = yaw_mode

    def _hover(self, node: str) -> Any:
        return _join_if_future(self.client.hoverAsync(vehicle_name=node))

    def _land(self, node: str) -> Any:
        timeout = float(self.contract["command_limits"]["command_timeout_seconds"])
        return self.client.landAsync(timeout_sec=timeout, vehicle_name=node)

    def dispatch_mutation(
        self,
        *,
        node: str,
        command: Mapping[str, Any],
        in_flight: bool,
        mutate: Callable[[], Any],
    ) -> tuple[Any, Any | None]:
        now_ms = _now_ms()
        return self.dispatcher.dispatch(
            node=node,
            authorization=lambda: self.authorization_provider.latest(node),
            command=command,
            now_ms=now_ms,
            in_flight=in_flight,
            mutate=mutate,
            hover=lambda: self._hover(node),
            land=lambda: self._land(node),
            clock_ms=_now_ms,
        )

    def execute(
        self,
        *,
        node: str,
        action: str,
        duration_seconds: float,
        minimum_separation_m: float,
    ) -> tuple[Any, Any | None]:
        local = _local_position(self.client, node)
        world = _world_position(self.client, node)
        safety = self.extension["safety"]
        vx = vy = 0.0
        target_local_z = local[2]
        target_world = list(world)

        if action in {"CONTINUE_ROUTE", "RESUME_ROUTE"}:
            speed = float(safety["maximum_nominal_speed_mps"])
            vx, vy = speed * self.route_unit[0], speed * self.route_unit[1]
            target_local_z = self.cruise_z
            target_world[0] += vx * duration_seconds
            target_world[1] += vy * duration_seconds
            target_world[2] += self.cruise_z - local[2]
        elif action in {"DEFLECT_LEFT", "DEFLECT_RIGHT"}:
            direction = 1.0 if action == "DEFLECT_LEFT" else -1.0
            speed = float(safety["deflection_velocity_mps"])
            vx = direction * speed * self.left_unit[0]
            vy = direction * speed * self.left_unit[1]
            target_world[0] += vx * duration_seconds
            target_world[1] += vy * duration_seconds
        elif action == "DEFLECT_UP":
            vertical_speed = float(safety["vertical_deflection_velocity_mps"])
            target_local_z = local[2] - vertical_speed * duration_seconds
            target_world[2] -= vertical_speed * duration_seconds
        elif action == "REJOIN_ROUTE":
            _, cross_track = _route_metrics(local, self.dx, self.dy, self.distance)
            direction = 0.0 if cross_track == 0.0 else -math.copysign(1.0, cross_track)
            speed = float(safety["route_rejoin_lateral_velocity_mps"])
            vx = direction * speed * self.left_unit[0]
            vy = direction * speed * self.left_unit[1]
            target_local_z = self.cruise_z
            target_world[0] += vx * duration_seconds
            target_world[1] += vy * duration_seconds
            target_world[2] += self.cruise_z - local[2]
        elif action in {"HOVER", "BLOCK_CELL", "TERMINATE_VEHICLE"}:
            command = _command_payload(
                self.contract,
                target_world_ned=world,
                horizontal_velocity_mps=0.0,
                vertical_velocity_mps=0.0,
                minimum_pairwise_separation_m=minimum_separation_m,
            )
            return self.dispatch_mutation(
                node=node,
                command=command,
                in_flight=True,
                mutate=lambda: self._hover(node),
            )
        else:
            raise MovementV2Error(f"live_action_unsupported:{action}")

        # Unit-vector multiplication can produce values such as
        # 4.000000000000001 for an exact configured 4.0 m/s command.  Normalize
        # only the telemetry supplied to the gate; the original velocity
        # components still reach CoSim unchanged.
        horizontal_speed = round(math.hypot(vx, vy), 12)
        vertical_speed = round(
            abs(target_local_z - local[2]) / max(duration_seconds, 1e-9), 12
        )
        command = _command_payload(
            self.contract,
            target_world_ned=target_world,
            horizontal_velocity_mps=horizontal_speed,
            vertical_velocity_mps=vertical_speed,
            minimum_pairwise_separation_m=minimum_separation_m,
        )
        return self.dispatch_mutation(
            node=node,
            command=command,
            in_flight=True,
            mutate=lambda: self.client.moveByVelocityZAsync(
                vx,
                vy,
                target_local_z,
                duration_seconds,
                drivetrain=cosysairsim.DrivetrainType.MaxDegreeOfFreedom,
                yaw_mode=self.yaw_mode,
                vehicle_name=node,
            ),
        )


def _emit_vehicle_state(
    events: DurableMovementEvents,
    client: object,
    node: str,
    state: str,
    observed_at_ms: int,
) -> dict[str, Any]:
    return events.emit(
        source=f"{node}.telemetry",
        kind="vehicle_state",
        payload={
            "node": node,
            "state": state,
            "position_ned": list(_world_position(client, node)),
        },
        observed_at_ms=observed_at_ms,
    )


def _capture_depth(
    client: object,
    node: str,
    extension: Mapping[str, Any],
    decided_at_ms: int,
) -> Any:
    response = client.simGetImages(
        [
            cosysairsim.ImageRequest(
                str(extension["depth_sensor"]["camera"]),
                cosysairsim.ImageType.DepthPlanar,
                pixels_as_float=True,
                compress=False,
            )
        ],
        vehicle_name=node,
    )
    if len(response) != 1:
        raise MovementV2Error(f"front_depth_response_count_invalid:{node}")
    return depth_sample_from_response(
        response[0],
        decided_at_ms=decided_at_ms,
        config=extension["depth_sensor"],
    )


def _mark_coverage(
    *,
    contract: Mapping[str, Any],
    ledger: CellLedger,
    events: DurableMovementEvents,
    node: str,
    progress: float,
    observed_at_ms: int,
) -> str:
    current_cell = ledger.cell_for_progress(progress)
    changed = False
    if current_cell in ledger.assigned[node] and current_cell not in ledger.completed[node]:
        if current_cell not in ledger.in_progress[node]:
            ledger.mark(node, current_cell, "IN_PROGRESS")
            changed = True
    for cell in contract["search_cells"]["cells"]:
        cell_id = str(cell["id"])
        if (
            cell_id in ledger.assigned[node]
            and cell_id not in ledger.completed[node]
            and cell_id not in ledger.blocked[node]
            and progress >= float(cell["end_fraction"])
        ):
            ledger.mark(node, cell_id, "COMPLETED")
            changed = True
    if changed:
        events.coverage(node=node, observed_at_ms=observed_at_ms)
    return current_cell


def _reassign_unfinished(
    *,
    unavailable: str,
    healthy: set[str],
    roster: Sequence[str],
    ledger: CellLedger,
    events: DurableMovementEvents,
    observed_at_ms: int,
    reason: str,
) -> None:
    if not healthy:
        return
    unfinished = sorted(
        ledger.assigned[unavailable]
        - ledger.completed[unavailable]
        - ledger.blocked[unavailable]
    )
    if not unfinished:
        return
    start = roster.index(unavailable)
    rotation = list(roster[start + 1 :]) + list(roster[:start])
    recipients = [node for node in rotation if node in healthy]
    if not recipients:
        return
    grouped: dict[str, list[str]] = {}
    for index, cell_id in enumerate(unfinished):
        grouped.setdefault(recipients[index % len(recipients)], []).append(cell_id)
    for recipient, cell_ids in grouped.items():
        events.reassign_cells(
            from_node=unavailable,
            to_node=recipient,
            cell_ids=cell_ids,
            reason=reason,
            observed_at_ms=observed_at_ms,
        )


def _preflight_command(
    contract: Mapping[str, Any],
    client: object,
    node: str,
    target_z: float,
    minimum_separation_m: float,
) -> dict[str, Any]:
    world = list(_world_position(client, node))
    local = _local_position(client, node)
    world[2] += target_z - local[2]
    limits = contract["command_limits"]
    return _command_payload(
        contract,
        target_world_ned=world,
        horizontal_velocity_mps=0.0,
        vertical_velocity_mps=float(limits["vertical_velocity_mps"]),
        minimum_pairwise_separation_m=minimum_separation_m,
    )


def _require_release(decision: Any, operation: str, node: str) -> None:
    if not decision.release_command:
        raise MovementV2Error(
            f"{operation}_not_authorized:{node}:{decision.decision}:{decision.reason}"
        )


def _controlled_point_b_land(
    *,
    client: object,
    survivors: tuple[str, ...],
    mission: Mapping[str, Any],
    contract: Mapping[str, Any],
    adapter: LiveCoSimCommandAdapter,
    armed: set[str],
) -> None:
    landing = mission["landing"]
    transform = mission["coordinate_transform"]
    surface_z = (
        (
            float(transform["point_b_landing_surface_world_z_cm"])
            - float(transform["point_a_unreal_cm"][2])
        )
        / float(transform["world_to_meters"])
    ) / float(transform["ned_to_unreal_axis_sign"]["z"])
    baselines = {
        node: int(_collision_record(client, node)["timestamp"]) for node in survivors
    }
    target_z = surface_z + float(landing["descent_target_below_surface_m"])
    minimum_separation = _minimum_pairwise(_world_positions(client, survivors))
    futures = []
    for node in survivors:
        command = _preflight_command(
            contract, client, node, target_z, minimum_separation
        )
        decision, future = adapter.dispatch_mutation(
            node=node,
            command=command,
            in_flight=True,
            mutate=lambda node=node: client.moveToZAsync(
                target_z,
                float(landing["controlled_descent_velocity_mps"]),
                timeout_sec=float(landing["controlled_descent_timeout_seconds"]),
                vehicle_name=node,
            ),
        )
        _require_release(decision, "landing", node)
        futures.append(future)
    _join_all(futures)
    contact_timestamps: dict[str, int] = {}
    started = time.monotonic()
    stable_since: dict[str, float] = {}
    pending = set(survivors)
    while pending:
        if time.monotonic() - started > float(landing["confirmation_timeout_seconds"]):
            raise MovementV2Error(
                "movement_v2_landing_contact_timeout:" + ",".join(sorted(pending))
            )
        positions = _world_positions(client, survivors)
        if _minimum_pairwise(positions) < float(
            contract["command_limits"]["minimum_pairwise_separation_m"]
        ):
            raise MovementV2Error("movement_v2_landing_separation_breached")
        now = time.monotonic()
        for node in tuple(sorted(pending)):
            sample = _landing_contact_sample(
                client, node, landing, surface_z, baselines[node]
            )
            if sample["new_surface_contact"]:
                contact_timestamps[node] = int(sample["collision"]["timestamp"])
            latched = (
                node in contact_timestamps
                and sample["collision"]["object_name"] == landing["collision_object_name"]
                and int(sample["collision"]["timestamp"]) == contact_timestamps[node]
            )
            stable = (
                latched
                and sample["within_contact_height"]
                and sample["vertical_motion_settled"]
            )
            if not stable:
                stable_since.pop(node, None)
                continue
            stable_since.setdefault(node, now)
            if now - stable_since[node] < float(landing["contact_settle_seconds"]):
                continue
            client.armDisarm(False, vehicle_name=node)
            armed.discard(node)
            pending.remove(node)
        if pending:
            time.sleep(float(landing["confirmation_poll_seconds"]))
    deadline = time.monotonic() + float(landing["post_disarm_confirmation_timeout_seconds"])
    while time.monotonic() < deadline:
        if all(
            client.getMultirotorState(vehicle_name=node).landed_state
            == cosysairsim.LandedState.Landed
            for node in survivors
        ):
            return
        time.sleep(float(landing["confirmation_poll_seconds"]))
    raise MovementV2Error("movement_v2_landed_enum_timeout")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission-config", type=Path, required=True)
    parser.add_argument("--movement-contract", type=Path, required=True)
    parser.add_argument("--movement-extension", type=Path, required=True)
    parser.add_argument("--cell-extension", type=Path, required=True)
    parser.add_argument("--map-file", type=Path, required=True)
    parser.add_argument("--layer-result", type=Path, required=True)
    parser.add_argument("--authorization-file", type=Path, required=True)
    parser.add_argument("--outbox-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    mission = _load_config(args.mission_config, args.map_file)
    contract = load_movement_contract(args.movement_contract)
    extension = load_sensor_movement_extension(
        args.movement_extension,
        base_contract_path=args.movement_contract,
        cell_extension_path=args.cell_extension,
    )
    if contract["map_binding"]["sha256"].casefold() != mission["source_map"][
        "sha256"
    ].casefold():
        raise MovementV2Error("mission_contract_map_binding_mismatch")
    roster = tuple(str(node) for node in contract["vehicles"]["roster"])
    if tuple(mission["vehicles"]) != roster:
        raise MovementV2Error("mission_contract_roster_mismatch")
    route = mission["route"]
    flood = mission["flood"]
    limits = mission["limits"]
    rpc = mission["rpc"]
    dx, dy = (float(value) for value in route["target_delta_ned_m"])
    distance = float(route["horizontal_distance_m"])
    yaw_mode = cosysairsim.YawMode(
        is_rate=False,
        yaw_or_rate=(
            math.degrees(math.atan2(dy, dx)) if route["yaw_faces_route"] else 0.0
        ),
    )
    client = cosysairsim.MultirotorClient(
        ip=str(rpc["host"]),
        port=int(rpc["port"]),
        timeout_value=float(rpc["timeout_seconds"]),
    )
    provider = AuthorizationFileProvider(args.authorization_file)
    ledger = CellLedger(contract)
    movement_events = DurableMovementEvents(
        contract=contract,
        ledger=ledger,
        outbox_factory=_outbox_factory(
            mission_id=str(contract["mission_id"]),
            output_directory=args.outbox_directory,
        ),
        enqueue_deadline_ms=int(
            extension["timing_boundaries_ms"][
                "movement_transition_durable_enqueue_deadline"
            ]
        ),
    )
    dispatcher = GatedCommandDispatcher(contract)
    supervisor = SensorDrivenMovementSupervisor(extension, roster)
    adapter = LiveCoSimCommandAdapter(
        client=client,
        contract=contract,
        extension=extension,
        authorization_provider=provider,
        dispatcher=dispatcher,
        route_dx=dx,
        route_dy=dy,
        route_distance=distance,
        cruise_z_ned_m=float(route["cruise_z_ned_m"]),
        yaw_mode=yaw_mode,
    )

    states = {node: "READY" for node in roster}
    api_enabled: set[str] = set()
    armed: set[str] = set()
    active: set[str] = set(roster)
    collided: set[str] = set()
    weather_enabled = False
    status = "FAIL"
    failure: str | None = None
    mission_announced = False
    started_at_ms = _now_ms()
    loop_count = 0
    minimum_observed_separation = math.inf
    water_name = _resolve_water_object(
        args.layer_result, str(flood["water_actor_id"])
    )
    try:
        client.confirmConnection()
        if tuple(sorted(client.listVehicles())) != tuple(sorted(roster)):
            raise MovementV2Error("live_roster_mismatch")

        initial_water = client.simGetObjectPose(water_name, ned=True)
        initial_water_z = float(initial_water.position.z_val)
        initial_target_z = max(
            float(route["cruise_z_ned_m"]),
            initial_water_z - float(flood["desired_clearance_above_water_m"]),
        )
        startup_positions = _world_positions(client, roster)
        startup_separation = _gate_separation(contract, startup_positions)
        # A missing, stale, malformed, HOLD or QUARANTINE lease must not create a
        # false ACTIVE mission on the dashboard or mutate any vehicle state.
        for node in roster:
            preflight = _preflight_command(
                contract, client, node, initial_target_z, startup_separation
            )
            decision, _ = adapter.dispatch_mutation(
                node=node,
                command=preflight,
                in_flight=False,
                mutate=lambda: None,
            )
            _require_release(decision, "preflight", node)

        movement_events.emit(
            source="mission.controller",
            kind="mission_started",
            payload={
                "scenario_id": str(contract["scenario_id"]),
                "coordinate_frame": "NED",
            },
            observed_at_ms=_now_ms(),
        )
        mission_announced = True
        for node in roster:
            movement_events.assignment(node=node, observed_at_ms=_now_ms())
            movement_events.emit(
                source=f"{node}.telemetry",
                kind="link_state",
                payload={"node": node, "state": "ONLINE"},
                observed_at_ms=_now_ms(),
            )
            _emit_vehicle_state(movement_events, client, node, "READY", _now_ms())

        if mission.get("weather"):
            client.simEnableWeather(True)
            weather_enabled = True
            for name, value in mission["weather"].items():
                client.simSetWeatherParameter(
                    getattr(cosysairsim.WeatherParameter, name), float(value)
                )

        for node in roster:
            preflight = _preflight_command(
                contract, client, node, initial_target_z, startup_separation
            )
            decision, _ = adapter.dispatch_mutation(
                node=node,
                command=preflight,
                in_flight=False,
                mutate=lambda node=node: client.enableApiControl(
                    True, vehicle_name=node
                ),
            )
            _require_release(decision, "api_control", node)
            api_enabled.add(node)
            decision, arm_result = adapter.dispatch_mutation(
                node=node,
                command=preflight,
                in_flight=False,
                mutate=lambda node=node: client.armDisarm(True, vehicle_name=node),
            )
            _require_release(decision, "arm", node)
            if not arm_result:
                raise MovementV2Error(f"arm_failed:{node}")
            armed.add(node)
        takeoff_futures = []
        takeoff_separation = _gate_separation(
            contract, _world_positions(client, roster)
        )
        for node in roster:
            command = _preflight_command(
                contract, client, node, initial_target_z, takeoff_separation
            )
            decision, future = adapter.dispatch_mutation(
                node=node,
                command=command,
                in_flight=False,
                mutate=lambda node=node: client.takeoffAsync(
                    timeout_sec=float(limits["takeoff_timeout_seconds"]),
                    vehicle_name=node,
                ),
            )
            _require_release(decision, "takeoff", node)
            takeoff_futures.append(future)
        _join_all(takeoff_futures)

        peak_z = float(flood["peak_water_z_ned_m"])
        rise_steps = max(
            1,
            math.ceil(
                float(flood["rise_duration_seconds"])
                / float(flood["update_period_seconds"])
            ),
        )
        for index in range(1, rise_steps + 1):
            fraction = index / rise_steps
            requested_z = initial_water_z + (peak_z - initial_water_z) * fraction
            if not client.simSetObjectPose(
                water_name, _copy_pose(initial_water, requested_z), teleport=True
            ):
                raise MovementV2Error("water_actor_move_failed")
            climb_target = max(
                float(route["cruise_z_ned_m"]),
                requested_z - float(flood["desired_clearance_above_water_m"]),
            )
            futures = []
            current_positions = _world_positions(client, roster)
            separation = _gate_separation(contract, current_positions)
            for node in roster:
                command = _preflight_command(
                    contract, client, node, climb_target, separation
                )
                decision, future = adapter.dispatch_mutation(
                    node=node,
                    command=command,
                    in_flight=True,
                    mutate=lambda node=node: client.moveToZAsync(
                        climb_target,
                        float(limits["vertical_velocity_mps"]),
                        timeout_sec=float(limits["command_timeout_seconds"]),
                        vehicle_name=node,
                    ),
                )
                _require_release(decision, "flood_climb", node)
                futures.append(future)
            _join_all(futures)
        time.sleep(float(limits["post_takeoff_settle_seconds"]))

        collision_baseline = {
            node: int(_collision_record(client, node)["timestamp"]) for node in roster
        }
        control_period = min(
            float(route["control_step_seconds"]),
            int(extension["timing_boundaries_ms"]["control_loop_period_max"]) / 1000.0,
        )
        previous_loop_ms = _now_ms()
        route_started = time.monotonic()
        completed_progress: dict[str, float] = {node: 0.0 for node in roster}
        while active and min(completed_progress[node] for node in active) < 1.0:
            if time.monotonic() - route_started > float(
                contract["command_limits"]["maximum_route_runtime_seconds"]
            ):
                raise MovementV2Error("movement_v2_route_runtime_exceeded")
            loop_started_ms = _now_ms()
            loop_period_ms = max(0, loop_started_ms - previous_loop_ms)
            previous_loop_ms = loop_started_ms
            positions = _world_positions(client, tuple(sorted(active)))
            minimum_separation = _gate_separation(contract, positions)
            if len(positions) >= 2:
                minimum_observed_separation = min(
                    minimum_observed_separation, minimum_separation
                )
            if minimum_separation < float(
                contract["command_limits"]["minimum_pairwise_separation_m"]
            ):
                raise MovementV2Error("movement_v2_separation_breached")
            futures = []
            terminal_nodes: list[tuple[str, str]] = []
            for node in tuple(sorted(active)):
                decided_at_ms = _now_ms()
                depth = _capture_depth(client, node, extension, decided_at_ms)
                local = _local_position(client, node)
                progress, cross_track = _route_metrics(local, dx, dy, distance)
                completed_progress[node] = progress
                cell_id = _mark_coverage(
                    contract=contract,
                    ledger=ledger,
                    events=movement_events,
                    node=node,
                    progress=progress,
                    observed_at_ms=decided_at_ms,
                )
                collision = _collision_record(client, node)
                collision_detected = bool(
                    collision["has_collided"]
                    and int(collision["timestamp"]) > collision_baseline[node]
                )
                action, transitions = supervisor.decide(
                    node=node,
                    depth=depth,
                    collision_detected=collision_detected,
                    cross_track_m=cross_track,
                    vertical_offset_m=local[2] - float(route["cruise_z_ned_m"]),
                    loop_period_ms=loop_period_ms,
                )
                for transition in transitions:
                    movement_events.movement_safety(
                        node=node,
                        cell_id=cell_id,
                        event_type=transition,
                        measurement_source=(
                            "simGetCollisionInfo_and_vehicle_state"
                            if transition == "COLLISION_DETECTED"
                            else "front_depth_and_vehicle_state"
                        ),
                        measured_distance_m=(
                            float(collision["penetration_depth_m"])
                            if transition == "COLLISION_DETECTED"
                            else float(depth.center_m)
                        ),
                        observed_at_ms=decided_at_ms,
                        position_ned=positions[node],
                    )
                decision, future = adapter.execute(
                    node=node,
                    action=action,
                    duration_seconds=control_period,
                    minimum_separation_m=minimum_separation,
                )
                if decision.action == "ABORT_HOVER_LAND":
                    states[node] = "QUARANTINED"
                    terminal_nodes.append((node, "authorization_quarantine"))
                elif action == "TERMINATE_VEHICLE":
                    states[node] = "FAILED"
                    collided.add(node)
                    terminal_nodes.append((node, "collision_terminal"))
                elif action == "BLOCK_CELL":
                    if cell_id in ledger.assigned[node]:
                        ledger.mark(node, cell_id, "BLOCKED")
                        movement_events.coverage(node=node, observed_at_ms=_now_ms())
                    states[node] = "HOLD"
                elif decision.action == "HOVER" or action == "HOVER":
                    states[node] = "HOLD"
                else:
                    states[node] = "SEARCHING"
                if future is not None:
                    futures.append(future)
                _emit_vehicle_state(
                    movement_events, client, node, states[node], _now_ms()
                )
                if (
                    "SAFETY_HOLD" in transitions
                    and _now_ms() - decided_at_ms
                    > int(
                        extension["timing_boundaries_ms"][
                            "hold_command_dispatch_deadline"
                        ]
                    )
                ):
                    raise MovementV2Error("hold_dispatch_deadline_missed")
            _join_all(futures)
            for node, reason in terminal_nodes:
                active.discard(node)
                if node in armed:
                    client.armDisarm(False, vehicle_name=node)
                    armed.discard(node)
                if node in api_enabled:
                    client.enableApiControl(False, vehicle_name=node)
                    api_enabled.discard(node)
                _reassign_unfinished(
                    unavailable=node,
                    healthy=set(active),
                    roster=roster,
                    ledger=ledger,
                    events=movement_events,
                    observed_at_ms=_now_ms(),
                    reason=reason,
                )
            loop_count += 1

        survivors = tuple(sorted(active))
        for node in survivors:
            progress, _ = _route_metrics(_local_position(client, node), dx, dy, distance)
            if progress < 1.0 - float(route["arrival_tolerance_m"]) / distance:
                raise MovementV2Error(f"movement_v2_arrival_failed:{node}:{progress}")
            for cell_id in tuple(sorted(ledger.assigned[node])):
                if cell_id not in ledger.completed[node] and cell_id not in ledger.blocked[node]:
                    ledger.mark(node, cell_id, "COMPLETED")
            movement_events.coverage(node=node, observed_at_ms=_now_ms())
            _emit_vehicle_state(movement_events, client, node, "LANDING", _now_ms())
        if survivors:
            _controlled_point_b_land(
                client=client,
                survivors=survivors,
                mission=mission,
                contract=contract,
                adapter=adapter,
                armed=armed,
            )
            for node in survivors:
                states[node] = "LANDED"
                _emit_vehicle_state(movement_events, client, node, "LANDED", _now_ms())

        completed_cells = len(set().union(*ledger.completed.values()))
        blocked_cells = len(set().union(*ledger.blocked.values()))
        total_cells = len(ledger.known_ids)
        mission_status = "PASS" if completed_cells == total_cells and not blocked_cells else "PARTIAL"
        movement_events.emit(
            source="mission.controller",
            kind="mission_completed",
            payload={
                "status": mission_status,
                "completed_cells": completed_cells,
                "total_cells": total_cells,
            },
            observed_at_ms=_now_ms(),
        )
        status = mission_status
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        if mission_announced:
            try:
                movement_events.emit(
                    source="mission.controller",
                    kind="mission_completed",
                    payload={
                        "status": "ABORTED",
                        "completed_cells": len(
                            set().union(*ledger.completed.values())
                        ),
                        "total_cells": len(ledger.known_ids),
                    },
                    observed_at_ms=_now_ms(),
                )
            except Exception as event_error:
                failure += (
                    "; abort_event_failed="
                    f"{type(event_error).__name__}: {event_error}"
                )
        raise
    finally:
        if failure is not None:
            cleanup_failures = _fail_closed_hover_or_disarm_landed(client, armed)
            if cleanup_failures:
                failure += "; fail_closed_cleanup=" + "|".join(cleanup_failures)
        else:
            for node in tuple(sorted(armed)):
                try:
                    client.armDisarm(False, vehicle_name=node)
                except Exception:
                    pass
        for node in tuple(sorted(api_enabled)):
            try:
                client.enableApiControl(False, vehicle_name=node)
            except Exception:
                pass
        if weather_enabled:
            try:
                client.simEnableWeather(False)
            except Exception:
                pass
        result = {
            "schema": RUN_SCHEMA,
            "status": status,
            "failure": failure,
            "started_at_ms": started_at_ms,
            "finished_at_ms": _now_ms(),
            "loop_count": loop_count,
            "minimum_observed_separation_m": (
                minimum_observed_separation
                if math.isfinite(minimum_observed_separation)
                else None
            ),
            "states": states,
            "collided": sorted(collided),
            "outbox_directory": str(args.outbox_directory),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({"status": status, "failure": failure, "output": str(args.output)}))


if __name__ == "__main__":
    main()
