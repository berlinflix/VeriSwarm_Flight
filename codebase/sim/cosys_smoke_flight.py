"""Fail-closed, configuration-driven CoSys/AirSim transport smoke flight.

This module is deliberately isolated from the protected autonomy path.  It proves only
the simulator transport boundary: connect, validate one frozen vehicle, take off, move
from an explicit NED point A to an explicit NED point B, land, disarm, and release API
control.  It never invents route values and never substitutes defaults for safety limits.

Run from ``codebase`` after receiving Pratik's immutable route file::

    python -m sim.cosys_smoke_flight --config route.json --out airsim_smoke.json

The output path is create-once.  A failed run is retained and must not be overwritten by
a later success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


OUTPUT_SCHEMA = "veriswarm.airsim_smoke.v1"
CONFIG_SCHEMA = "veriswarm.cosys_route.v1"
NED_FRAME = "NED_METRES"


class ConfigurationError(ValueError):
    """Raised before any simulator connection when the route contract is incomplete."""


class FlightInvariantError(RuntimeError):
    """Raised when a live flight invariant fails."""


class StageTimeout(FlightInvariantError):
    """Raised when a bounded simulator operation exceeds its declared timeout."""


@dataclass(frozen=True)
class Vector3:
    x: float
    y: float
    z: float

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.z]


@dataclass(frozen=True)
class SmokeConfig:
    host: str
    port: int
    rpc_timeout_seconds: float
    vehicle_name: str
    vehicle_type: str
    frame: str
    start: Vector3
    target: Vector3
    takeoff_z_ned_m: float
    start_tolerance_m: float
    speed_mps: float
    position_tolerance_m: float
    hover_velocity_tolerance_mps: float
    dwell_seconds: float
    timeouts: Mapping[str, float]
    evidence_output_path: str


REQUIRED_TIMEOUTS = (
    "connect",
    "vehicle_check",
    "api_control",
    "arm",
    "takeoff",
    "hover",
    "move",
    "arrival",
    "land",
    "cleanup",
)


def _required(mapping: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ConfigurationError(f"missing required field: {context}.{key}")
    value = mapping[key]
    if value is None:
        raise ConfigurationError(f"null required field: {context}.{key}")
    return value


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{context} must be an object")
    return value


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{context} must be a non-empty string")
    return value.strip()


def _finite_number(value: Any, context: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{context} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigurationError(f"{context} must be finite")
    if positive and number <= 0.0:
        raise ConfigurationError(f"{context} must be greater than zero")
    return number


def _positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{context} must be a positive integer")
    return value


def _vector(value: Any, context: str) -> Vector3:
    mapping = _mapping(value, context)
    return Vector3(
        _finite_number(_required(mapping, "x", context), f"{context}.x"),
        _finite_number(_required(mapping, "y", context), f"{context}.y"),
        _finite_number(_required(mapping, "z", context), f"{context}.z"),
    )


def validate_config(raw: Mapping[str, Any]) -> SmokeConfig:
    """Validate every required route value without supplying flight defaults."""

    root = _mapping(raw, "config")
    schema = _text(_required(root, "schema", "config"), "config.schema")
    if schema != CONFIG_SCHEMA:
        raise ConfigurationError(
            f"config.schema must be {CONFIG_SCHEMA!r}, got {schema!r}"
        )

    endpoint = _mapping(_required(root, "endpoint", "config"), "config.endpoint")
    vehicle = _mapping(_required(root, "vehicle", "config"), "config.vehicle")
    route = _mapping(_required(root, "route", "config"), "config.route")
    limits = _mapping(_required(root, "limits", "config"), "config.limits")
    evidence = _mapping(
        _required(root, "evidence", "config"), "config.evidence"
    )
    timeouts_raw = _mapping(
        _required(root, "timeouts_seconds", "config"),
        "config.timeouts_seconds",
    )

    frame = _text(_required(root, "frame", "config"), "config.frame")
    if frame != NED_FRAME:
        raise ConfigurationError(
            f"config.frame must be {NED_FRAME!r}; implicit frame conversion is forbidden"
        )

    timeouts: dict[str, float] = {}
    for name in REQUIRED_TIMEOUTS:
        timeouts[name] = _finite_number(
            _required(timeouts_raw, name, "config.timeouts_seconds"),
            f"config.timeouts_seconds.{name}",
            positive=True,
        )

    start = _vector(_required(route, "a", "config.route"), "config.route.a")
    target = _vector(_required(route, "b", "config.route"), "config.route.b")
    if _distance(start, target) <= 0.0:
        raise ConfigurationError("config.route.a and config.route.b must be distinct")

    return SmokeConfig(
        host=_text(_required(endpoint, "host", "config.endpoint"), "config.endpoint.host"),
        port=_positive_int(
            _required(endpoint, "port", "config.endpoint"), "config.endpoint.port"
        ),
        rpc_timeout_seconds=_finite_number(
            _required(endpoint, "rpc_timeout_seconds", "config.endpoint"),
            "config.endpoint.rpc_timeout_seconds",
            positive=True,
        ),
        vehicle_name=_text(
            _required(vehicle, "name", "config.vehicle"), "config.vehicle.name"
        ),
        vehicle_type=_text(
            _required(vehicle, "type", "config.vehicle"), "config.vehicle.type"
        ),
        frame=frame,
        start=start,
        target=target,
        takeoff_z_ned_m=_finite_number(
            _required(route, "takeoff_z_ned_m", "config.route"),
            "config.route.takeoff_z_ned_m",
        ),
        start_tolerance_m=_finite_number(
            _required(limits, "start_tolerance_m", "config.limits"),
            "config.limits.start_tolerance_m",
            positive=True,
        ),
        speed_mps=_finite_number(
            _required(limits, "speed_mps", "config.limits"),
            "config.limits.speed_mps",
            positive=True,
        ),
        position_tolerance_m=_finite_number(
            _required(limits, "position_tolerance_m", "config.limits"),
            "config.limits.position_tolerance_m",
            positive=True,
        ),
        hover_velocity_tolerance_mps=_finite_number(
            _required(limits, "hover_velocity_tolerance_mps", "config.limits"),
            "config.limits.hover_velocity_tolerance_mps",
            positive=True,
        ),
        dwell_seconds=_finite_number(
            _required(limits, "dwell_seconds", "config.limits"),
            "config.limits.dwell_seconds",
            positive=True,
        ),
        timeouts=timeouts,
        evidence_output_path=_text(
            _required(evidence, "output_path", "config.evidence"),
            "config.evidence.output_path",
        ),
    )


def load_config(path: Path) -> tuple[SmokeConfig, str]:
    data = path.read_bytes()
    try:
        raw = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"invalid JSON config {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigurationError("config root must be an object")
    return validate_config(raw), hashlib.sha256(data).hexdigest()


def _distance(left: Vector3, right: Vector3) -> float:
    return math.sqrt(
        (left.x - right.x) ** 2
        + (left.y - right.y) ** 2
        + (left.z - right.z) ** 2
    )


def _client_vector(value: Any, context: str) -> Vector3:
    try:
        vector = Vector3(float(value.x_val), float(value.y_val), float(value.z_val))
    except (AttributeError, TypeError, ValueError) as exc:
        raise FlightInvariantError(f"invalid {context} vector") from exc
    if not all(math.isfinite(component) for component in vector.as_list()):
        raise FlightInvariantError(f"non-finite {context} vector")
    return vector


def _state_snapshot(state: Any) -> dict[str, Any]:
    try:
        kinematics = state.kinematics_estimated
        position = _client_vector(kinematics.position, "position")
        velocity = _client_vector(kinematics.linear_velocity, "velocity")
        timestamp = int(state.timestamp)
        landed_state = int(state.landed_state)
    except (AttributeError, TypeError, ValueError) as exc:
        raise FlightInvariantError("invalid multirotor state") from exc
    return {
        "timestamp": timestamp,
        "position": position.as_list(),
        "velocity": velocity.as_list(),
        "speed_mps": _distance(velocity, Vector3(0.0, 0.0, 0.0)),
        "landed_state": landed_state,
    }


def _call_with_timeout(
    label: str,
    timeout_seconds: float,
    operation: Callable[[], Any],
) -> Any:
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            results.put((True, operation()))
        except BaseException as exc:  # preserve vendor exceptions for the caller
            results.put((False, exc))

    worker = threading.Thread(target=invoke, name=f"cosys-{label}", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise StageTimeout(f"{label} exceeded {timeout_seconds:.3f}s")
    succeeded, value = results.get_nowait()
    if not succeeded:
        raise FlightInvariantError(f"{label} failed: {value}") from value
    return value


def _join_future(label: str, timeout_seconds: float, future: Any) -> None:
    if not hasattr(future, "join"):
        raise FlightInvariantError(f"{label} returned no joinable future")
    _call_with_timeout(label, timeout_seconds, future.join)


class EvidenceRecorder:
    def __init__(self, config: SmokeConfig, config_sha256: str):
        self.started_ns = time.time_ns()
        self._monotonic_started = time.monotonic()
        self.result: dict[str, Any] = {
            "schema": OUTPUT_SCHEMA,
            "created_ns": self.started_ns,
            "config_sha256": config_sha256,
            "endpoint": {"host": config.host, "port": config.port},
            "vehicle": {"name": config.vehicle_name, "type": config.vehicle_type},
            "frame": config.frame,
            "route": {"a": config.start.as_list(), "b": config.target.as_list()},
            "limits": {
                "start_tolerance_m": config.start_tolerance_m,
                "speed_mps": config.speed_mps,
                "position_tolerance_m": config.position_tolerance_m,
                "hover_velocity_tolerance_mps": config.hover_velocity_tolerance_mps,
                "dwell_seconds": config.dwell_seconds,
                "takeoff_z_ned_m": config.takeoff_z_ned_m,
            },
            "timeouts_seconds": dict(config.timeouts),
            "transitions": [],
            "initial_state": None,
            "final_state": None,
            "position_error_m": None,
            "collision_count": 0,
            "collisions": [],
            "landing_confirmed": False,
            "disarm_confirmed": False,
            "api_control_released": False,
            "abort_attempted": False,
            "errors": [],
            "pass": False,
            "process_result": "RUNNING",
        }

    def transition(self, state: str, outcome: str, detail: str = "") -> None:
        self.result["transitions"].append(
            {
                "state": state,
                "outcome": outcome,
                "detail": detail,
                "elapsed_seconds": round(
                    time.monotonic() - self._monotonic_started, 6
                ),
            }
        )

    def error(self, message: str) -> None:
        self.result["errors"].append(message)

    def finish(self, passed: bool) -> None:
        self.result["finished_ns"] = time.time_ns()
        self.result["duration_seconds"] = round(
            time.monotonic() - self._monotonic_started, 6
        )
        self.result["pass"] = passed
        self.result["process_result"] = "PASS" if passed else "FAIL"


def _record_collision(client: Any, config: SmokeConfig, recorder: EvidenceRecorder) -> None:
    collision = _call_with_timeout(
        "collision_check",
        config.timeouts["vehicle_check"],
        lambda: client.simGetCollisionInfo(vehicle_name=config.vehicle_name),
    )
    if bool(getattr(collision, "has_collided", False)):
        entry = {
            "object_name": str(getattr(collision, "object_name", "")),
            "object_id": int(getattr(collision, "object_id", -1)),
            "timestamp": float(getattr(collision, "time_stamp", 0.0)),
        }
        if entry not in recorder.result["collisions"]:
            recorder.result["collisions"].append(entry)
        recorder.result["collision_count"] = len(recorder.result["collisions"])
        raise FlightInvariantError(f"collision detected: {entry['object_name']!r}")


def _get_state(client: Any, config: SmokeConfig, timeout_name: str) -> dict[str, Any]:
    state = _call_with_timeout(
        "get_multirotor_state",
        config.timeouts[timeout_name],
        lambda: client.getMultirotorState(vehicle_name=config.vehicle_name),
    )
    return _state_snapshot(state)


def _wait_for_pose_dwell(
    client: Any,
    config: SmokeConfig,
    target: Vector3,
    timeout_name: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + config.timeouts[timeout_name]
    stable_since: float | None = None
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _get_state(client, config, timeout_name)
        position = Vector3(*last["position"])
        error = _distance(position, target)
        stable = (
            error <= config.position_tolerance_m
            and last["speed_mps"] <= config.hover_velocity_tolerance_mps
        )
        if stable:
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= config.dwell_seconds:
                last["position_error_m"] = error
                return last
        else:
            stable_since = None
        time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))
    detail = "no telemetry" if last is None else json.dumps(last, sort_keys=True)
    raise StageTimeout(
        f"{timeout_name} did not satisfy position/velocity dwell: {detail}"
    )


def _wait_for_velocity_dwell(
    client: Any,
    config: SmokeConfig,
    timeout_name: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + config.timeouts[timeout_name]
    stable_since: float | None = None
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _get_state(client, config, timeout_name)
        if last["speed_mps"] <= config.hover_velocity_tolerance_mps:
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= config.dwell_seconds:
                return last
        else:
            stable_since = None
        time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))
    detail = "no telemetry" if last is None else json.dumps(last, sort_keys=True)
    raise StageTimeout(f"{timeout_name} did not satisfy velocity dwell: {detail}")


def _safe_cleanup(
    client: Any,
    config: SmokeConfig,
    recorder: EvidenceRecorder,
    *,
    armed: bool,
    api_control: bool,
) -> None:
    if not (armed or api_control):
        return
    recorder.result["abort_attempted"] = True
    timeout = config.timeouts["cleanup"]

    if api_control:
        try:
            future = _call_with_timeout(
                "abort_hover_start",
                timeout,
                lambda: client.hoverAsync(vehicle_name=config.vehicle_name),
            )
            _join_future("abort_hover", timeout, future)
            recorder.transition("ABORT_HOVER", "PASS")
        except FlightInvariantError as exc:
            recorder.error(str(exc))
            recorder.transition("ABORT_HOVER", "FAIL", str(exc))

        try:
            future = _call_with_timeout(
                "abort_land_start",
                timeout,
                lambda: client.landAsync(
                    timeout_sec=timeout, vehicle_name=config.vehicle_name
                ),
            )
            _join_future("abort_land", timeout, future)
            recorder.result["landing_confirmed"] = True
            recorder.transition("ABORT_LAND", "PASS")
        except FlightInvariantError as exc:
            recorder.error(str(exc))
            recorder.transition("ABORT_LAND", "FAIL", str(exc))

    if armed:
        try:
            result = _call_with_timeout(
                "abort_disarm",
                timeout,
                lambda: client.armDisarm(False, vehicle_name=config.vehicle_name),
            )
            if result is False:
                raise FlightInvariantError("abort_disarm returned false")
            recorder.result["disarm_confirmed"] = True
            recorder.transition("ABORT_DISARM", "PASS")
        except FlightInvariantError as exc:
            recorder.error(str(exc))
            recorder.transition("ABORT_DISARM", "FAIL", str(exc))

    if api_control:
        try:
            _call_with_timeout(
                "abort_release_api_control",
                timeout,
                lambda: client.enableApiControl(
                    False, vehicle_name=config.vehicle_name
                ),
            )
            still_enabled = _call_with_timeout(
                "abort_verify_api_release",
                timeout,
                lambda: client.isApiControlEnabled(vehicle_name=config.vehicle_name),
            )
            if still_enabled:
                raise FlightInvariantError("API control remained enabled after cleanup")
            recorder.result["api_control_released"] = True
            recorder.transition("ABORT_RELEASE_API", "PASS")
        except FlightInvariantError as exc:
            recorder.error(str(exc))
            recorder.transition("ABORT_RELEASE_API", "FAIL", str(exc))


def run_smoke(
    config: SmokeConfig,
    config_sha256: str,
    *,
    client_factory: Callable[[SmokeConfig], Any],
    landed_state_value: int,
) -> dict[str, Any]:
    """Execute one bounded smoke flight and return its immutable evidence object."""

    recorder = EvidenceRecorder(config, config_sha256)
    client: Any | None = None
    api_control = False
    armed = False
    completed = False
    try:
        client = _call_with_timeout(
            "client_create",
            config.timeouts["connect"],
            lambda: client_factory(config),
        )
        if not _call_with_timeout("ping", config.timeouts["connect"], client.ping):
            raise FlightInvariantError("ping returned false")
        recorder.transition("CONNECT", "PASS")

        vehicles = _call_with_timeout(
            "list_vehicles", config.timeouts["vehicle_check"], client.listVehicles
        )
        if not isinstance(vehicles, Sequence) or isinstance(vehicles, (str, bytes)):
            raise FlightInvariantError("listVehicles returned an invalid roster")
        if config.vehicle_name not in vehicles:
            raise FlightInvariantError(
                f"configured vehicle {config.vehicle_name!r} absent from roster {list(vehicles)!r}"
            )
        recorder.transition("VERIFY_VEHICLE", "PASS", config.vehicle_name)

        initial = _get_state(client, config, "vehicle_check")
        recorder.result["initial_state"] = initial
        start_error = _distance(Vector3(*initial["position"]), config.start)
        if start_error > config.start_tolerance_m:
            raise FlightInvariantError(
                f"initial pose error {start_error:.6f}m exceeds "
                f"{config.start_tolerance_m:.6f}m"
            )
        _record_collision(client, config, recorder)
        recorder.transition("VERIFY_START", "PASS", f"error_m={start_error:.6f}")

        _call_with_timeout(
            "enable_api_control",
            config.timeouts["api_control"],
            lambda: client.enableApiControl(True, vehicle_name=config.vehicle_name),
        )
        api_control = True
        if not _call_with_timeout(
            "verify_api_control",
            config.timeouts["api_control"],
            lambda: client.isApiControlEnabled(vehicle_name=config.vehicle_name),
        ):
            raise FlightInvariantError("API control did not become enabled")
        recorder.transition("API_CONTROL", "PASS")

        arm_result = _call_with_timeout(
            "arm",
            config.timeouts["arm"],
            lambda: client.armDisarm(True, vehicle_name=config.vehicle_name),
        )
        if arm_result is False:
            raise FlightInvariantError("arm returned false")
        armed = True
        recorder.transition("ARM", "PASS")

        future = _call_with_timeout(
            "takeoff_start",
            config.timeouts["takeoff"],
            lambda: client.takeoffAsync(
                timeout_sec=config.timeouts["takeoff"],
                vehicle_name=config.vehicle_name,
            ),
        )
        _join_future("takeoff", config.timeouts["takeoff"], future)
        recorder.transition("TAKEOFF", "PASS")

        takeoff_target = Vector3(
            config.start.x, config.start.y, config.takeoff_z_ned_m
        )
        future = _call_with_timeout(
            "takeoff_altitude_start",
            config.timeouts["takeoff"],
            lambda: client.moveToPositionAsync(
                takeoff_target.x,
                takeoff_target.y,
                takeoff_target.z,
                config.speed_mps,
                timeout_sec=config.timeouts["takeoff"],
                vehicle_name=config.vehicle_name,
            ),
        )
        _join_future(
            "takeoff_altitude_move", config.timeouts["takeoff"], future
        )
        _wait_for_pose_dwell(client, config, takeoff_target, "takeoff")
        recorder.transition(
            "VERIFY_TAKEOFF_ALTITUDE",
            "PASS",
            f"z_ned_m={config.takeoff_z_ned_m:.6f}",
        )

        future = _call_with_timeout(
            "hover_start",
            config.timeouts["hover"],
            lambda: client.hoverAsync(vehicle_name=config.vehicle_name),
        )
        _join_future("hover", config.timeouts["hover"], future)
        _wait_for_velocity_dwell(client, config, "hover")
        recorder.transition("HOVER", "PASS")

        future = _call_with_timeout(
            "move_start",
            config.timeouts["move"],
            lambda: client.moveToPositionAsync(
                config.target.x,
                config.target.y,
                config.target.z,
                config.speed_mps,
                timeout_sec=config.timeouts["move"],
                vehicle_name=config.vehicle_name,
            ),
        )
        _join_future("move", config.timeouts["move"], future)
        recorder.transition("MOVE_A_TO_B", "PASS")

        arrived = _wait_for_pose_dwell(client, config, config.target, "arrival")
        recorder.result["position_error_m"] = arrived["position_error_m"]
        recorder.transition(
            "VERIFY_ARRIVAL",
            "PASS",
            f"error_m={arrived['position_error_m']:.6f}",
        )
        _record_collision(client, config, recorder)

        future = _call_with_timeout(
            "land_start",
            config.timeouts["land"],
            lambda: client.landAsync(
                timeout_sec=config.timeouts["land"],
                vehicle_name=config.vehicle_name,
            ),
        )
        _join_future("land", config.timeouts["land"], future)
        landed = _get_state(client, config, "land")
        if landed["landed_state"] != landed_state_value:
            raise FlightInvariantError(
                f"landed_state={landed['landed_state']} expected {landed_state_value}"
            )
        recorder.result["landing_confirmed"] = True
        recorder.transition("LAND", "PASS")

        disarm_result = _call_with_timeout(
            "disarm",
            config.timeouts["cleanup"],
            lambda: client.armDisarm(False, vehicle_name=config.vehicle_name),
        )
        if disarm_result is False:
            raise FlightInvariantError("disarm returned false")
        armed = False
        recorder.result["disarm_confirmed"] = True
        recorder.transition("DISARM", "PASS")

        _call_with_timeout(
            "release_api_control",
            config.timeouts["cleanup"],
            lambda: client.enableApiControl(False, vehicle_name=config.vehicle_name),
        )
        if _call_with_timeout(
            "verify_api_release",
            config.timeouts["cleanup"],
            lambda: client.isApiControlEnabled(vehicle_name=config.vehicle_name),
        ):
            raise FlightInvariantError("API control remained enabled after release")
        api_control = False
        recorder.result["api_control_released"] = True
        recorder.transition("RELEASE_API", "PASS")

        _record_collision(client, config, recorder)
        final_state = _get_state(client, config, "vehicle_check")
        recorder.result["final_state"] = final_state
        completed = True
    except (ConfigurationError, FlightInvariantError) as exc:
        recorder.error(str(exc))
        recorder.transition("FAIL", "FAIL", str(exc))
    except Exception as exc:  # vendor exceptions still produce evidence and cleanup
        recorder.error(f"unexpected error: {type(exc).__name__}: {exc}")
        recorder.transition("FAIL", "FAIL", recorder.result["errors"][-1])
    finally:
        if client is not None and (armed or api_control):
            _safe_cleanup(
                client,
                config,
                recorder,
                armed=armed,
                api_control=api_control,
            )

    passed = completed and all(
        (
            recorder.result["collision_count"] == 0,
            recorder.result["landing_confirmed"],
            recorder.result["disarm_confirmed"],
            recorder.result["api_control_released"],
        )
    )
    recorder.finish(passed)
    return recorder.result


def _live_client_factory(config: SmokeConfig) -> tuple[Any, int]:
    try:
        import cosysairsim  # type: ignore[import-not-found]
    except ImportError as exc:
        raise FlightInvariantError(
            "cosysairsim is not installed in this environment; install only the "
            "checksum-approved client artifact from Pratik's handoff"
        ) from exc
    client = cosysairsim.MultirotorClient(
        ip=config.host,
        port=config.port,
        timeout_value=config.rpc_timeout_seconds,
    )
    return client, int(cosysairsim.LandedState.Landed)


def _write_create_once(path: Path, result: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise ConfigurationError(f"refusing to overwrite evidence file: {path}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--out",
        type=Path,
        help=(
            "Optional explicit confirmation of config.evidence.output_path; when set, "
            "it must resolve to the same path"
        ),
    )
    args = parser.parse_args(argv)

    output_path: Path | None = args.out

    try:
        config, config_sha256 = load_config(args.config)
    except (OSError, ConfigurationError) as exc:
        result = {
            "schema": OUTPUT_SCHEMA,
            "created_ns": time.time_ns(),
            "config_path": str(args.config),
            "errors": [str(exc)],
            "pass": False,
            "process_result": "CONFIG_REFUSED",
        }
        if output_path is None:
            print(json.dumps(result, indent=2, sort_keys=True))
            return 2
        try:
            _write_create_once(output_path, result)
        except (OSError, ConfigurationError) as write_exc:
            print(f"ERROR: {write_exc}")
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2

    configured_output = Path(config.evidence_output_path)
    if not configured_output.is_absolute():
        configured_output = args.config.parent / configured_output
    configured_output = configured_output.resolve()
    if output_path is not None:
        output_path = output_path.resolve()
        if output_path != configured_output:
            print(
                "ERROR: --out does not match config.evidence.output_path: "
                f"{output_path} != {configured_output}"
            )
            return 2
    else:
        output_path = configured_output

    if output_path.exists():
        print(f"ERROR: refusing to overwrite evidence file: {output_path}")
        return 2

    try:
        client, landed_state_value = _live_client_factory(config)
        result = run_smoke(
            config,
            config_sha256,
            client_factory=lambda _config: client,
            landed_state_value=landed_state_value,
        )
        _write_create_once(output_path, result)
    except (OSError, ConfigurationError, FlightInvariantError) as exc:
        result = {
            "schema": OUTPUT_SCHEMA,
            "created_ns": time.time_ns(),
            "config_sha256": config_sha256,
            "errors": [str(exc)],
            "pass": False,
            "process_result": "STARTUP_REFUSED",
        }
        try:
            _write_create_once(output_path, result)
        except (OSError, ConfigurationError) as write_exc:
            print(f"ERROR: {write_exc}")
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
