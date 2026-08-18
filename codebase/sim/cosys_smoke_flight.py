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
import asyncio
import hashlib
import json
import math
import queue
import re
import threading
import time
from dataclasses import asdict, dataclass
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


class _ThreadAffineRpcWorker:
    """Own one event-loop-backed vendor client on one persistent thread."""

    def __init__(
        self,
        label: str,
        client_factory: Callable[[], Any],
        startup_timeout_seconds: float,
    ) -> None:
        self._label = label
        self._client_factory = client_factory
        self._tasks: queue.Queue[Any] = queue.Queue()
        self._ready: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name=f"cosys-rpc-{label}",
            daemon=True,
        )
        self._thread.start()
        try:
            succeeded, value = self._ready.get(timeout=startup_timeout_seconds)
        except queue.Empty as exc:
            self._closed = True
            self._tasks.put(None)
            raise StageTimeout(
                f"{label} client creation exceeded {startup_timeout_seconds:.3f}s"
            ) from exc
        if not succeeded:
            self._closed = True
            raise FlightInvariantError(
                f"{label} client creation failed: {value}"
            ) from value

    @staticmethod
    def _close_vendor_client(client: Any) -> None:
        close = getattr(client, "close", None)
        if callable(close):
            close()
            return
        transport = getattr(client, "client", None)
        close = getattr(transport, "close", None)
        if callable(close):
            close()

    def _run(self) -> None:
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        client: Any | None = None
        try:
            try:
                client = self._client_factory()
            except BaseException as exc:
                self._ready.put((False, exc))
                return
            self._ready.put((True, None))
            while True:
                task = self._tasks.get()
                if task is None:
                    return
                operation, reply = task
                try:
                    reply.put((True, operation(client)))
                except BaseException as exc:
                    reply.put((False, exc))
        finally:
            if client is not None:
                try:
                    self._close_vendor_client(client)
                except BaseException:
                    pass
            asyncio.set_event_loop(None)
            event_loop.close()

    def call(self, operation: Callable[[Any], Any]) -> Any:
        if self._closed:
            raise FlightInvariantError(f"{self._label} RPC worker is closed")
        reply: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        self._tasks.put((operation, reply))
        succeeded, value = reply.get()
        if not succeeded:
            raise value
        return value

    def close(self, timeout_seconds: float = 1.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._tasks.put(None)
        self._thread.join(timeout_seconds)


class _ThreadAffineFuture:
    """Join a vendor future only on the RPC thread that created it."""

    def __init__(self, worker: _ThreadAffineRpcWorker, future: Any) -> None:
        self._worker = worker
        self._future = future

    def join(self) -> Any:
        return self._worker.call(lambda _client: self._future.join())


class _ThreadAffineCosysClient:
    """Dispatch CoSys calls to persistent command and observation RPC clients.

    ``rpc-msgpack`` binds its Tornado/asyncio loop to the thread where the client is
    constructed.  The smoke runner intentionally starts timeout and guarded-join helper
    threads, so a raw client cannot safely be called from those helpers.  Commands and
    their futures share one owner thread; telemetry uses an independent owner thread so
    guarded joins can continue monitoring the live vehicle.
    """

    _ASYNC_METHODS = {
        "hoverAsync",
        "landAsync",
        "moveToPositionAsync",
        "takeoffAsync",
    }
    _COMMAND_METHODS = _ASYNC_METHODS | {
        "armDisarm",
        "enableApiControl",
        "reset",
    }
    _OBSERVATION_METHODS = {
        "getMultirotorState",
        "isApiControlEnabled",
        "listVehicles",
        "ping",
        "simGetCollisionInfo",
    }

    def __init__(
        self,
        client_factory: Callable[[], Any],
        startup_timeout_seconds: float,
    ) -> None:
        self._command = _ThreadAffineRpcWorker(
            "command", client_factory, startup_timeout_seconds
        )
        try:
            self._observation = _ThreadAffineRpcWorker(
                "observation", client_factory, startup_timeout_seconds
            )
        except BaseException:
            self._command.close()
            raise

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name in self._COMMAND_METHODS:
            worker = self._command
        elif name in self._OBSERVATION_METHODS:
            worker = self._observation
        else:
            raise AttributeError(f"unsupported CoSys client operation: {name}")

        def invoke(*args: Any, **kwargs: Any) -> Any:
            result = worker.call(
                lambda client: getattr(client, name)(*args, **kwargs)
            )
            if name in self._ASYNC_METHODS:
                return _ThreadAffineFuture(worker, result)
            return result

        return invoke

    def close(self) -> None:
        self._observation.close()
        self._command.close()


@dataclass(frozen=True)
class Vector3:
    x: float
    y: float
    z: float

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.z]


@dataclass(frozen=True)
class RuntimeIdentity:
    qualification_configuration: str
    environment_family: str
    world_id: str
    unreal_engine_version: str
    cosys_airsim_version: str
    client_import: str
    client_version: str
    client_artifact_sha256: str
    settings_path: str
    settings_sha256: str


@dataclass(frozen=True)
class Geofence:
    minimum: Vector3
    maximum: Vector3

    def contains(self, point: Vector3) -> bool:
        return (
            self.minimum.x <= point.x <= self.maximum.x
            and self.minimum.y <= point.y <= self.maximum.y
            and self.minimum.z <= point.z <= self.maximum.z
        )


@dataclass(frozen=True)
class TouchdownPolicy:
    expected_ground_object: str
    stationary_velocity_tolerance_mps: float
    stationary_angular_velocity_tolerance_rps: float
    max_landing_linear_speed_mps: float
    max_landing_angular_speed_rps: float
    max_landing_roll_pitch_deg: float
    max_penetration_depth_m: float
    landing_zone_radius_m: float
    landing_dwell_seconds: float
    deviation_id: str
    deviation_approved: bool
    deviation_approved_by: str | None
    deviation_approval_reference: str | None


@dataclass(frozen=True)
class SmokeConfig:
    runtime: RuntimeIdentity
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
    geofence: Geofence
    touchdown: TouchdownPolicy


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
    "reset",
)


def _required(mapping: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ConfigurationError(f"missing required field: {context}.{key}")
    value = mapping[key]
    if value is None:
        raise ConfigurationError(f"null required field: {context}.{key}")
    return value


def _present(mapping: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ConfigurationError(f"missing required field: {context}.{key}")
    return mapping[key]


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{context} must be an object")
    return value


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{context} must be a non-empty string")
    return value.strip()


def _sha256(value: Any, context: str) -> str:
    digest = _text(value, context)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ConfigurationError(
            f"{context} must be a 64-character lowercase SHA-256"
        )
    return digest


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

    runtime_raw = _mapping(
        _required(root, "runtime", "config"), "config.runtime"
    )
    endpoint = _mapping(_required(root, "endpoint", "config"), "config.endpoint")
    vehicle = _mapping(_required(root, "vehicle", "config"), "config.vehicle")
    route = _mapping(_required(root, "route", "config"), "config.route")
    limits = _mapping(_required(root, "limits", "config"), "config.limits")
    evidence = _mapping(
        _required(root, "evidence", "config"), "config.evidence"
    )
    geofence_raw = _mapping(
        _required(root, "geofence", "config"), "config.geofence"
    )
    touchdown_raw = _mapping(
        _required(root, "touchdown", "config"), "config.touchdown"
    )
    deviation_raw = _mapping(
        _required(touchdown_raw, "landed_state_deviation", "config.touchdown"),
        "config.touchdown.landed_state_deviation",
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

    takeoff_z_ned_m = _finite_number(
        _required(route, "takeoff_z_ned_m", "config.route"),
        "config.route.takeoff_z_ned_m",
    )
    geofence = Geofence(
        minimum=_vector(
            _required(geofence_raw, "min", "config.geofence"),
            "config.geofence.min",
        ),
        maximum=_vector(
            _required(geofence_raw, "max", "config.geofence"),
            "config.geofence.max",
        ),
    )
    if not (
        geofence.minimum.x < geofence.maximum.x
        and geofence.minimum.y < geofence.maximum.y
        and geofence.minimum.z < geofence.maximum.z
    ):
        raise ConfigurationError(
            "config.geofence.min must be strictly less than max on every axis"
        )
    takeoff_point = Vector3(start.x, start.y, takeoff_z_ned_m)
    for label, point in (("route.a", start), ("route.takeoff", takeoff_point), ("route.b", target)):
        if not geofence.contains(point):
            raise ConfigurationError(
                f"config.{label} lies outside the configured geofence"
            )

    decision = _text(
        _required(
            runtime_raw, "qualification_configuration", "config.runtime"
        ),
        "config.runtime.qualification_configuration",
    )
    if decision == "Q-A":
        raise ConfigurationError(
            "Q-A is the legacy AirSim 1.8 exception and requires a separate, "
            "explicitly approved legacy client mode"
        )
    if decision not in {"Q-B", "Q-C"}:
        raise ConfigurationError(
            "config.runtime.qualification_configuration must be Q-B or Q-C for "
            "the accepted cosysairsim client"
        )
    environment_family = _text(
        _required(runtime_raw, "environment_family", "config.runtime"),
        "config.runtime.environment_family",
    )
    expected_family = "Blocks" if decision == "Q-B" else "CityEnviron"
    if environment_family != expected_family:
        raise ConfigurationError(
            f"{decision} requires environment_family={expected_family!r}"
        )
    unreal_version = _text(
        _required(runtime_raw, "unreal_engine_version", "config.runtime"),
        "config.runtime.unreal_engine_version",
    )
    if unreal_version != "5.8.1":
        raise ConfigurationError("accepted Q-B/Q-C runtime requires Unreal Engine 5.8.1")
    cosys_version = _text(
        _required(runtime_raw, "cosys_airsim_version", "config.runtime"),
        "config.runtime.cosys_airsim_version",
    )
    if cosys_version != "3.4.1":
        raise ConfigurationError("accepted Q-B/Q-C runtime requires CoSys-AirSim 3.4.1")
    client_import = _text(
        _required(runtime_raw, "client_import", "config.runtime"),
        "config.runtime.client_import",
    )
    if client_import != "cosysairsim":
        raise ConfigurationError(
            "accepted Q-B/Q-C runtime requires client_import='cosysairsim'"
        )

    stationary_tolerance = _finite_number(
        _required(
            touchdown_raw,
            "stationary_velocity_tolerance_mps",
            "config.touchdown",
        ),
        "config.touchdown.stationary_velocity_tolerance_mps",
        positive=True,
    )
    if stationary_tolerance > 0.05:
        raise ConfigurationError(
            "config.touchdown.stationary_velocity_tolerance_mps must be <= 0.05"
        )
    stationary_angular_tolerance = _finite_number(
        _required(
            touchdown_raw,
            "stationary_angular_velocity_tolerance_rps",
            "config.touchdown",
        ),
        "config.touchdown.stationary_angular_velocity_tolerance_rps",
        positive=True,
    )
    max_landing_linear_speed = _finite_number(
        _required(
            touchdown_raw, "max_landing_linear_speed_mps", "config.touchdown"
        ),
        "config.touchdown.max_landing_linear_speed_mps",
        positive=True,
    )
    if max_landing_linear_speed < stationary_tolerance:
        raise ConfigurationError(
            "config.touchdown.max_landing_linear_speed_mps must be >= "
            "stationary_velocity_tolerance_mps"
        )
    max_landing_angular_speed = _finite_number(
        _required(
            touchdown_raw, "max_landing_angular_speed_rps", "config.touchdown"
        ),
        "config.touchdown.max_landing_angular_speed_rps",
        positive=True,
    )
    if max_landing_angular_speed < stationary_angular_tolerance:
        raise ConfigurationError(
            "config.touchdown.max_landing_angular_speed_rps must be >= "
            "stationary_angular_velocity_tolerance_rps"
        )
    max_landing_roll_pitch = _finite_number(
        _required(
            touchdown_raw, "max_landing_roll_pitch_deg", "config.touchdown"
        ),
        "config.touchdown.max_landing_roll_pitch_deg",
        positive=True,
    )
    if max_landing_roll_pitch > 90.0:
        raise ConfigurationError(
            "config.touchdown.max_landing_roll_pitch_deg must be <= 90"
        )
    max_penetration_depth = _finite_number(
        _required(
            touchdown_raw, "max_penetration_depth_m", "config.touchdown"
        ),
        "config.touchdown.max_penetration_depth_m",
    )
    if max_penetration_depth < 0.0:
        raise ConfigurationError(
            "config.touchdown.max_penetration_depth_m must be >= 0"
        )
    landing_zone_radius = _finite_number(
        _required(touchdown_raw, "landing_zone_radius_m", "config.touchdown"),
        "config.touchdown.landing_zone_radius_m",
        positive=True,
    )
    landing_dwell_seconds = _finite_number(
        _required(touchdown_raw, "landing_dwell_seconds", "config.touchdown"),
        "config.touchdown.landing_dwell_seconds",
        positive=True,
    )
    if not math.isclose(landing_dwell_seconds, 2.0, abs_tol=1e-9):
        raise ConfigurationError(
            "config.touchdown.landing_dwell_seconds must be exactly 2.0"
        )
    deviation_id = _text(
        _required(deviation_raw, "id", "config.touchdown.landed_state_deviation"),
        "config.touchdown.landed_state_deviation.id",
    )
    if deviation_id != "QB-LANDED-STATE-001":
        raise ConfigurationError(
            "the only recognized landed-state deviation is QB-LANDED-STATE-001"
        )
    deviation_approved = _required(
        deviation_raw, "approved", "config.touchdown.landed_state_deviation"
    )
    if not isinstance(deviation_approved, bool):
        raise ConfigurationError(
            "config.touchdown.landed_state_deviation.approved must be boolean"
        )
    approved_by_raw = _present(
        deviation_raw,
        "approved_by",
        "config.touchdown.landed_state_deviation",
    )
    approval_reference_raw = _present(
        deviation_raw,
        "approval_reference",
        "config.touchdown.landed_state_deviation",
    )
    approved_by = None
    approval_reference = None
    if deviation_approved:
        approved_by = _text(
            approved_by_raw,
            "config.touchdown.landed_state_deviation.approved_by",
        )
        approval_reference = _text(
            approval_reference_raw,
            "config.touchdown.landed_state_deviation.approval_reference",
        )
        if approved_by.casefold() != "suyash":
            raise ConfigurationError(
                "QB-LANDED-STATE-001 must be explicitly approved by Suyash"
            )
    elif approved_by_raw is not None or approval_reference_raw is not None:
        raise ConfigurationError(
            "unapproved landed-state deviation must keep approval fields null"
        )

    return SmokeConfig(
        runtime=RuntimeIdentity(
            qualification_configuration=decision,
            environment_family=environment_family,
            world_id=_text(
                _required(runtime_raw, "world_id", "config.runtime"),
                "config.runtime.world_id",
            ),
            unreal_engine_version=unreal_version,
            cosys_airsim_version=cosys_version,
            client_import=client_import,
            client_version=_text(
                _required(runtime_raw, "client_version", "config.runtime"),
                "config.runtime.client_version",
            ),
            client_artifact_sha256=_sha256(
                _required(
                    runtime_raw, "client_artifact_sha256", "config.runtime"
                ),
                "config.runtime.client_artifact_sha256",
            ),
            settings_path=_text(
                _required(runtime_raw, "settings_path", "config.runtime"),
                "config.runtime.settings_path",
            ),
            settings_sha256=_sha256(
                _required(runtime_raw, "settings_sha256", "config.runtime"),
                "config.runtime.settings_sha256",
            ),
        ),
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
        takeoff_z_ned_m=takeoff_z_ned_m,
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
        geofence=geofence,
        touchdown=TouchdownPolicy(
            expected_ground_object=_text(
                _required(
                    touchdown_raw, "expected_ground_object", "config.touchdown"
                ),
                "config.touchdown.expected_ground_object",
            ),
            stationary_velocity_tolerance_mps=stationary_tolerance,
            stationary_angular_velocity_tolerance_rps=(
                stationary_angular_tolerance
            ),
            max_landing_linear_speed_mps=max_landing_linear_speed,
            max_landing_angular_speed_rps=max_landing_angular_speed,
            max_landing_roll_pitch_deg=max_landing_roll_pitch,
            max_penetration_depth_m=max_penetration_depth,
            landing_zone_radius_m=landing_zone_radius,
            landing_dwell_seconds=landing_dwell_seconds,
            deviation_id=deviation_id,
            deviation_approved=deviation_approved,
            deviation_approved_by=approved_by,
            deviation_approval_reference=approval_reference,
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


def _flight_contract_sha256(config: SmokeConfig) -> str:
    payload = asdict(config)
    payload.pop("evidence_output_path", None)
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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


def _client_quaternion(value: Any) -> tuple[float, float, float, float]:
    try:
        quaternion = (
            float(value.w_val),
            float(value.x_val),
            float(value.y_val),
            float(value.z_val),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise FlightInvariantError("invalid orientation quaternion") from exc
    if not all(math.isfinite(component) for component in quaternion):
        raise FlightInvariantError("non-finite orientation quaternion")
    norm = math.sqrt(sum(component * component for component in quaternion))
    if norm <= 0.0:
        raise FlightInvariantError("zero-norm orientation quaternion")
    return tuple(component / norm for component in quaternion)


def _quaternion_to_euler_degrees(
    quaternion: tuple[float, float, float, float]
) -> tuple[float, float, float]:
    w, x, y, z = quaternion
    sin_roll_cos_pitch = 2.0 * (w * x + y * z)
    cos_roll_cos_pitch = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sin_roll_cos_pitch, cos_roll_cos_pitch)

    sin_pitch = 2.0 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2.0, sin_pitch) if abs(sin_pitch) >= 1.0 else math.asin(sin_pitch)

    sin_yaw_cos_pitch = 2.0 * (w * z + x * y)
    cos_yaw_cos_pitch = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(sin_yaw_cos_pitch, cos_yaw_cos_pitch)
    return tuple(math.degrees(value) for value in (roll, pitch, yaw))


def _state_snapshot(state: Any) -> dict[str, Any]:
    try:
        kinematics = state.kinematics_estimated
        position = _client_vector(kinematics.position, "position")
        velocity = _client_vector(kinematics.linear_velocity, "velocity")
        angular_velocity = _client_vector(
            kinematics.angular_velocity, "angular velocity"
        )
        orientation = _client_quaternion(kinematics.orientation)
        roll_deg, pitch_deg, yaw_deg = _quaternion_to_euler_degrees(orientation)
        timestamp = int(state.timestamp)
        landed_state = int(state.landed_state)
    except (AttributeError, TypeError, ValueError) as exc:
        raise FlightInvariantError("invalid multirotor state") from exc
    return {
        "timestamp": timestamp,
        "position": position.as_list(),
        "velocity": velocity.as_list(),
        "speed_mps": _distance(velocity, Vector3(0.0, 0.0, 0.0)),
        "angular_velocity_rps": angular_velocity.as_list(),
        "angular_speed_rps": _distance(
            angular_velocity, Vector3(0.0, 0.0, 0.0)
        ),
        "attitude_deg": {
            "roll": roll_deg,
            "pitch": pitch_deg,
            "yaw": yaw_deg,
        },
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


def _join_future_guarded(
    label: str,
    timeout_seconds: float,
    future: Any,
    safety_check: Callable[[], None],
) -> None:
    """Join an async simulator command while polling its phase safety contract."""

    if not hasattr(future, "join"):
        raise FlightInvariantError(f"{label} returned no joinable future")
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            future.join()
            results.put((True, None))
        except BaseException as exc:
            results.put((False, exc))

    worker = threading.Thread(
        target=invoke, name=f"cosys-{label}-join", daemon=True
    )
    worker.start()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            succeeded, value = results.get(timeout=0.01)
        except queue.Empty:
            safety_check()
            continue
        safety_check()
        if not succeeded:
            raise FlightInvariantError(f"{label} failed: {value}") from value
        return
    raise StageTimeout(f"{label} exceeded {timeout_seconds:.3f}s")


class EvidenceRecorder:
    def __init__(self, config: SmokeConfig, config_sha256: str):
        self.started_ns = time.time_ns()
        self._monotonic_started = time.monotonic()
        self.result: dict[str, Any] = {
            "schema": OUTPUT_SCHEMA,
            "created_ns": self.started_ns,
            "config_sha256": config_sha256,
            "flight_contract_sha256": _flight_contract_sha256(config),
            "runtime": {
                "qualification_configuration": (
                    config.runtime.qualification_configuration
                ),
                "environment_family": config.runtime.environment_family,
                "world_id": config.runtime.world_id,
                "unreal_engine_version": config.runtime.unreal_engine_version,
                "cosys_airsim_version": config.runtime.cosys_airsim_version,
                "client_import": config.runtime.client_import,
                "client_version": config.runtime.client_version,
                "client_artifact_sha256": (
                    config.runtime.client_artifact_sha256
                ),
                "settings_path": config.runtime.settings_path,
                "settings_sha256": config.runtime.settings_sha256,
            },
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
            "geofence": {
                "min": config.geofence.minimum.as_list(),
                "max": config.geofence.maximum.as_list(),
            },
            "touchdown_policy": {
                "expected_ground_object": (
                    config.touchdown.expected_ground_object
                ),
                "stationary_velocity_tolerance_mps": (
                    config.touchdown.stationary_velocity_tolerance_mps
                ),
                "stationary_angular_velocity_tolerance_rps": (
                    config.touchdown.stationary_angular_velocity_tolerance_rps
                ),
                "max_landing_linear_speed_mps": (
                    config.touchdown.max_landing_linear_speed_mps
                ),
                "max_landing_angular_speed_rps": (
                    config.touchdown.max_landing_angular_speed_rps
                ),
                "max_landing_roll_pitch_deg": (
                    config.touchdown.max_landing_roll_pitch_deg
                ),
                "max_penetration_depth_m": (
                    config.touchdown.max_penetration_depth_m
                ),
                "landing_zone_radius_m": (
                    config.touchdown.landing_zone_radius_m
                ),
                "landing_dwell_seconds": (
                    config.touchdown.landing_dwell_seconds
                ),
                "landed_state_deviation": {
                    "id": config.touchdown.deviation_id,
                    "approved": config.touchdown.deviation_approved,
                    "approved_by": config.touchdown.deviation_approved_by,
                    "approval_reference": (
                        config.touchdown.deviation_approval_reference
                    ),
                    "applied": False,
                },
            },
            "timeouts_seconds": dict(config.timeouts),
            "transitions": [],
            "initial_state": None,
            "final_state": None,
            "position_error_m": None,
            "collision_count": 0,
            "collisions": [],
            "initial_collision": None,
            "ground_contacts": [],
            "touchdown_dwell": {
                "required_seconds": config.touchdown.landing_dwell_seconds,
                "contact_event_timestamp": None,
                "started_elapsed_seconds": None,
                "reset_count": 0,
                "completed_seconds": None,
            },
            "touchdown_threshold_violations": [],
            "landing_confirmed": False,
            "landing_state": None,
            "disarm_confirmed": False,
            "api_control_released": False,
            "reset_attempted": False,
            "reset_confirmed": False,
            "reset_state": None,
            "abort_attempted": False,
            "cleanup_complete": False,
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


def _assert_inside_geofence(
    state: Mapping[str, Any], config: SmokeConfig, context: str
) -> None:
    position = Vector3(*state["position"])
    if not config.geofence.contains(position):
        raise FlightInvariantError(
            f"{context} position {position.as_list()} is outside the configured geofence"
        )


def _assert_api_control(client: Any, config: SmokeConfig, context: str) -> None:
    enabled = _call_with_timeout(
        f"{context}_api_control_check",
        config.timeouts["api_control"],
        lambda: client.isApiControlEnabled(vehicle_name=config.vehicle_name),
    )
    if not enabled:
        raise FlightInvariantError(f"API control lost during {context}")


class CollisionMonitor:
    def __init__(
        self,
        client: Any,
        config: SmokeConfig,
        recorder: EvidenceRecorder,
        landed_state_value: int,
    ):
        self.client = client
        self.config = config
        self.recorder = recorder
        self.landed_state_value = landed_state_value
        self.baseline_ground_timestamp: float | None = None

    def _read(self, timeout_name: str) -> dict[str, Any]:
        collision = _call_with_timeout(
            "collision_check",
            self.config.timeouts[timeout_name],
            lambda: self.client.simGetCollisionInfo(
                vehicle_name=self.config.vehicle_name
            ),
        )
        has_collided = bool(getattr(collision, "has_collided", False))
        try:
            timestamp = float(getattr(collision, "time_stamp", 0.0))
            object_id = int(getattr(collision, "object_id", -1))
            penetration_depth = float(collision.penetration_depth)
            normal = _client_vector(collision.normal, "collision normal")
            impact_point = _client_vector(
                collision.impact_point, "collision impact point"
            )
            collision_position = _client_vector(
                collision.position, "collision position"
            )
        except (TypeError, ValueError) as exc:
            raise FlightInvariantError("invalid collision telemetry") from exc
        except AttributeError as exc:
            raise FlightInvariantError(
                "collision telemetry missing penetration_depth"
            ) from exc
        if not math.isfinite(timestamp) or not math.isfinite(penetration_depth):
            raise FlightInvariantError("non-finite collision telemetry")
        return {
            "has_collided": has_collided,
            "object_name": str(getattr(collision, "object_name", "")),
            "object_id": object_id,
            "timestamp": timestamp,
            "penetration_depth_m": penetration_depth,
            "normal": normal.as_list(),
            "impact_point": impact_point.as_list(),
            "position": collision_position.as_list(),
        }

    def _fail_collision(self, entry: Mapping[str, Any], reason: str) -> None:
        retained = dict(entry)
        retained["reason"] = reason
        if retained not in self.recorder.result["collisions"]:
            self.recorder.result["collisions"].append(retained)
        self.recorder.result["collision_count"] = len(
            self.recorder.result["collisions"]
        )
        raise FlightInvariantError(
            f"collision detected: {entry['object_name']!r}: {reason}"
        )

    def baseline(self, initial_state: Mapping[str, Any]) -> None:
        entry = self._read("vehicle_check")
        self.recorder.result["initial_collision"] = entry
        if not entry["has_collided"]:
            return
        if entry["object_name"] != self.config.touchdown.expected_ground_object:
            self._fail_collision(entry, "non-ground startup contact")
        if initial_state["landed_state"] != self.landed_state_value:
            self._fail_collision(entry, "ground contact while not landed at startup")
        if (
            initial_state["speed_mps"]
            > self.config.touchdown.stationary_velocity_tolerance_mps
        ):
            raise FlightInvariantError(
                "startup Ground contact is not stationary: linear speed "
                f"{initial_state['speed_mps']:.6f}m/s exceeds "
                f"{self.config.touchdown.stationary_velocity_tolerance_mps:.6f}m/s"
            )
        if (
            initial_state["angular_speed_rps"]
            > self.config.touchdown.stationary_angular_velocity_tolerance_rps
        ):
            raise FlightInvariantError(
                "startup Ground contact is not stationary: angular speed "
                f"{initial_state['angular_speed_rps']:.6f}rad/s exceeds "
                f"{self.config.touchdown.stationary_angular_velocity_tolerance_rps:.6f}rad/s"
            )
        roll = abs(float(initial_state["attitude_deg"]["roll"]))
        pitch = abs(float(initial_state["attitude_deg"]["pitch"]))
        if max(roll, pitch) > self.config.touchdown.max_landing_roll_pitch_deg:
            raise FlightInvariantError(
                "startup Ground-contact roll/pitch exceeds "
                f"{self.config.touchdown.max_landing_roll_pitch_deg:.6f}deg"
            )
        if (
            float(entry["penetration_depth_m"])
            > self.config.touchdown.max_penetration_depth_m
        ):
            raise FlightInvariantError(
                "startup Ground-contact penetration depth "
                f"{float(entry['penetration_depth_m']):.6f}m exceeds "
                f"{self.config.touchdown.max_penetration_depth_m:.6f}m"
            )
        self.baseline_ground_timestamp = float(entry["timestamp"])
        contact = dict(entry)
        contact["phase"] = "startup"
        self.recorder.result["ground_contacts"].append(contact)

    def check_inflight(self, phase: str) -> None:
        entry = self._read("vehicle_check")
        if not entry["has_collided"]:
            return
        if entry["object_name"] != self.config.touchdown.expected_ground_object:
            self._fail_collision(entry, f"non-ground contact during {phase}")
        timestamp = float(entry["timestamp"])
        if (
            self.baseline_ground_timestamp is None
            or timestamp > self.baseline_ground_timestamp
        ):
            self._fail_collision(entry, f"new ground contact during {phase}")

    def _landing_envelope(
        self,
        state: Mapping[str, Any],
        collision: Mapping[str, Any],
        phase: str,
        *,
        enforce_landing_zone: bool = True,
    ) -> tuple[float, list[str]]:
        if (
            collision["has_collided"]
            and collision["object_name"]
            != self.config.touchdown.expected_ground_object
        ):
            self._fail_collision(collision, f"non-ground contact during {phase}")
        violations: list[str] = []
        if (
            float(collision["penetration_depth_m"])
            > self.config.touchdown.max_penetration_depth_m
        ):
            violations.append(
                f"penetration depth "
                f"{float(collision['penetration_depth_m']):.6f}m exceeds "
                f"{self.config.touchdown.max_penetration_depth_m:.6f}m"
            )
        if state["speed_mps"] > self.config.touchdown.max_landing_linear_speed_mps:
            violations.append(
                f"linear speed {state['speed_mps']:.6f}m/s exceeds "
                f"{self.config.touchdown.max_landing_linear_speed_mps:.6f}m/s"
            )
        if (
            state["angular_speed_rps"]
            > self.config.touchdown.max_landing_angular_speed_rps
        ):
            violations.append(
                f"angular speed {state['angular_speed_rps']:.6f}rad/s exceeds "
                f"{self.config.touchdown.max_landing_angular_speed_rps:.6f}rad/s"
            )
        roll = abs(float(state["attitude_deg"]["roll"]))
        pitch = abs(float(state["attitude_deg"]["pitch"]))
        if max(roll, pitch) > self.config.touchdown.max_landing_roll_pitch_deg:
            violations.append(
                f"roll/pitch ({roll:.6f},{pitch:.6f})deg exceeds "
                f"{self.config.touchdown.max_landing_roll_pitch_deg:.6f}deg"
            )
        position = Vector3(*state["position"])
        horizontal_error = math.hypot(
            position.x - self.config.target.x,
            position.y - self.config.target.y,
        )
        if (
            enforce_landing_zone
            and horizontal_error > self.config.touchdown.landing_zone_radius_m
        ):
            violations.append(
                f"landing-zone error {horizontal_error:.6f}m exceeds "
                f"{self.config.touchdown.landing_zone_radius_m:.6f}m"
            )
        return horizontal_error, violations

    def check_landing(
        self, phase: str, *, enforce_landing_zone: bool = True
    ) -> None:
        state = _get_state(self.client, self.config, "land")
        collision = self._read("land")
        _, violations = self._landing_envelope(
            state,
            collision,
            phase,
            enforce_landing_zone=enforce_landing_zone,
        )
        if violations:
            raise FlightInvariantError(f"{phase} envelope violation: {violations[0]}")

    def wait_for_touchdown(
        self, timeout_name: str, *, enforce_landing_zone: bool = True
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.timeouts[timeout_name]
        last_state: dict[str, Any] | None = None
        last_collision: dict[str, Any] | None = None
        stale_landed_state_seen = False
        contact_timestamp: float | None = None
        stable_since: float | None = None
        last_violation_signature: tuple[str, ...] | None = None
        while time.monotonic() < deadline:
            last_state = _get_state(self.client, self.config, timeout_name)
            last_collision = self._read(timeout_name)
            horizontal_error, violations = self._landing_envelope(
                last_state,
                last_collision,
                "touchdown",
                enforce_landing_zone=enforce_landing_zone,
            )
            timestamp = float(last_collision["timestamp"])
            new_ground_contact = (
                last_collision["has_collided"]
                and last_collision["object_name"]
                == self.config.touchdown.expected_ground_object
                and (
                    self.baseline_ground_timestamp is None
                    or timestamp > self.baseline_ground_timestamp
                )
            )
            if new_ground_contact and contact_timestamp is None:
                contact_timestamp = timestamp
                contact = dict(last_collision)
                contact["phase"] = "touchdown"
                self.recorder.result["ground_contacts"].append(contact)
                self.recorder.result["touchdown_dwell"][
                    "contact_event_timestamp"
                ] = contact_timestamp

            landed_state_ok = (
                last_state["landed_state"] == self.landed_state_value
            )
            if contact_timestamp is None:
                if violations:
                    raise FlightInvariantError(
                        f"touchdown descent envelope violation: {violations[0]}"
                    )
                stable_since = None
                time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))
                continue

            if (
                last_state["speed_mps"]
                > self.config.touchdown.stationary_velocity_tolerance_mps
            ):
                violations.append(
                    f"stationary linear speed {last_state['speed_mps']:.6f}m/s exceeds "
                    f"{self.config.touchdown.stationary_velocity_tolerance_mps:.6f}m/s"
                )
            if (
                last_state["angular_speed_rps"]
                > self.config.touchdown.stationary_angular_velocity_tolerance_rps
            ):
                violations.append(
                    "stationary angular speed "
                    f"{last_state['angular_speed_rps']:.6f}rad/s exceeds "
                    f"{self.config.touchdown.stationary_angular_velocity_tolerance_rps:.6f}rad/s"
                )
            if not landed_state_ok:
                stale_landed_state_seen = True
                if not self.config.touchdown.deviation_approved:
                    violations.append(
                        "landed state is stale and QB-LANDED-STATE-001 is not approved"
                    )

            if violations:
                signature = tuple(violations)
                if signature != last_violation_signature:
                    self.recorder.result["touchdown_threshold_violations"].append(
                        {
                            "elapsed_seconds": round(
                                time.monotonic()
                                - self.recorder._monotonic_started,
                                6,
                            ),
                            "violations": list(violations),
                            "state": dict(last_state),
                            "collision": dict(last_collision),
                        }
                    )
                last_violation_signature = signature
                if stable_since is not None:
                    self.recorder.result["touchdown_dwell"]["reset_count"] += 1
                stable_since = None
            else:
                last_violation_signature = None
                if stable_since is None:
                    stable_since = time.monotonic()
                    self.recorder.result["touchdown_dwell"][
                        "started_elapsed_seconds"
                    ] = round(
                        stable_since - self.recorder._monotonic_started, 6
                    )
                elapsed_stable = time.monotonic() - stable_since
                if elapsed_stable >= self.config.touchdown.landing_dwell_seconds:
                    if not landed_state_ok:
                        self.recorder.result["touchdown_policy"][
                            "landed_state_deviation"
                        ]["applied"] = True
                    self.recorder.result["touchdown_dwell"][
                        "completed_seconds"
                    ] = elapsed_stable
                    retained = dict(last_state)
                    retained["ground_contact_timestamp"] = contact_timestamp
                    retained["landing_zone_error_m"] = horizontal_error
                    retained["stable_dwell_seconds"] = elapsed_stable
                    return retained
            time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))

        detail = {
            "state": last_state,
            "collision": last_collision,
            "stale_landed_state_seen": stale_landed_state_seen,
        }
        if stale_landed_state_seen and not self.config.touchdown.deviation_approved:
            raise FlightInvariantError(
                "physical touchdown observed but landed_state remained stale; "
                "QB-LANDED-STATE-001 requires Suyash approval: "
                + json.dumps(detail, sort_keys=True)
            )
        raise StageTimeout(
            f"touchdown did not prove stationary Ground contact: "
            f"{json.dumps(detail, sort_keys=True)}"
        )


def _get_state(client: Any, config: SmokeConfig, timeout_name: str) -> dict[str, Any]:
    state = _call_with_timeout(
        "get_multirotor_state",
        config.timeouts[timeout_name],
        lambda: client.getMultirotorState(vehicle_name=config.vehicle_name),
    )
    snapshot = _state_snapshot(state)
    _assert_inside_geofence(snapshot, config, timeout_name)
    return snapshot


def _wait_for_pose_dwell(
    client: Any,
    config: SmokeConfig,
    target: Vector3,
    timeout_name: str,
    safety_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + config.timeouts[timeout_name]
    stable_since: float | None = None
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if safety_check is not None:
            safety_check()
        _assert_api_control(client, config, timeout_name)
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
    safety_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + config.timeouts[timeout_name]
    stable_since: float | None = None
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if safety_check is not None:
            safety_check()
        _assert_api_control(client, config, timeout_name)
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


def _reset_and_verify(
    client: Any,
    config: SmokeConfig,
    recorder: EvidenceRecorder,
    collision_monitor: CollisionMonitor,
) -> dict[str, Any]:
    timeout = config.timeouts["reset"]
    if _call_with_timeout(
        "pre_reset_api_control_check",
        config.timeouts["api_control"],
        lambda: client.isApiControlEnabled(vehicle_name=config.vehicle_name),
    ):
        raise FlightInvariantError("refusing reset while API control remains enabled")

    recorder.result["reset_attempted"] = True
    _call_with_timeout("reset", timeout, client.reset)
    deadline = time.monotonic() + timeout
    stable_since: float | None = None
    last_state: dict[str, Any] | None = None
    last_collision: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if _call_with_timeout(
            "reset_api_control_check",
            config.timeouts["api_control"],
            lambda: client.isApiControlEnabled(vehicle_name=config.vehicle_name),
        ):
            raise FlightInvariantError("API control became enabled during reset verification")
        last_state = _get_state(client, config, "reset")
        last_collision = collision_monitor._read("reset")
        if (
            last_collision["has_collided"]
            and last_collision["object_name"]
            != config.touchdown.expected_ground_object
        ):
            collision_monitor._fail_collision(
                last_collision, "non-ground contact after reset"
            )
        if (
            float(last_collision["penetration_depth_m"])
            > config.touchdown.max_penetration_depth_m
        ):
            raise FlightInvariantError(
                "reset penetration depth "
                f"{float(last_collision['penetration_depth_m']):.6f}m exceeds "
                f"{config.touchdown.max_penetration_depth_m:.6f}m"
            )
        position_error = _distance(Vector3(*last_state["position"]), config.start)
        roll = abs(float(last_state["attitude_deg"]["roll"]))
        pitch = abs(float(last_state["attitude_deg"]["pitch"]))
        stable = all(
            (
                position_error <= config.start_tolerance_m,
                last_state["speed_mps"]
                <= config.touchdown.stationary_velocity_tolerance_mps,
                last_state["angular_speed_rps"]
                <= config.touchdown.stationary_angular_velocity_tolerance_rps,
                max(roll, pitch)
                <= config.touchdown.max_landing_roll_pitch_deg,
                last_state["landed_state"] == collision_monitor.landed_state_value,
            )
        )
        if stable:
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= config.dwell_seconds:
                retained = dict(last_state)
                retained["position_error_m"] = position_error
                retained["api_control_enabled"] = False
                retained["collision"] = dict(last_collision)
                recorder.result["reset_state"] = retained
                recorder.result["reset_confirmed"] = True
                return retained
        else:
            stable_since = None
        time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))
    raise StageTimeout(
        "reset did not restore A/stationary/landed/API-off state: "
        + json.dumps(
            {"state": last_state, "collision": last_collision}, sort_keys=True
        )
    )


def _safe_cleanup(
    client: Any,
    config: SmokeConfig,
    recorder: EvidenceRecorder,
    collision_monitor: CollisionMonitor | None,
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
            if collision_monitor is None:
                raise FlightInvariantError(
                    "cannot confirm cleanup touchdown without collision baseline"
                )
            _join_future_guarded(
                "abort_land",
                timeout,
                future,
                lambda: collision_monitor.check_landing(
                    "abort_land_async", enforce_landing_zone=False
                ),
            )
            landed = collision_monitor.wait_for_touchdown(
                "cleanup", enforce_landing_zone=False
            )
            recorder.result["landing_confirmed"] = True
            recorder.result["landing_state"] = landed
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
            if result is not True:
                raise FlightInvariantError("abort_disarm did not return true")
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
    collision_monitor: CollisionMonitor | None = None
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
        collision_monitor = CollisionMonitor(
            client, config, recorder, landed_state_value
        )
        collision_monitor.baseline(initial)
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
        collision_monitor.check_inflight("arm")
        recorder.transition("ARM", "PASS")

        future = _call_with_timeout(
            "takeoff_start",
            config.timeouts["takeoff"],
            lambda: client.takeoffAsync(
                timeout_sec=config.timeouts["takeoff"],
                vehicle_name=config.vehicle_name,
            ),
        )
        _join_future_guarded(
            "takeoff",
            config.timeouts["takeoff"],
            future,
            lambda: collision_monitor.check_inflight("takeoff"),
        )
        _assert_api_control(client, config, "takeoff")
        collision_monitor.check_inflight("takeoff")
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
        _join_future_guarded(
            "takeoff_altitude_move",
            config.timeouts["takeoff"],
            future,
            lambda: collision_monitor.check_inflight("takeoff_altitude"),
        )
        _assert_api_control(client, config, "takeoff_altitude")
        _wait_for_pose_dwell(
            client,
            config,
            takeoff_target,
            "takeoff",
            lambda: collision_monitor.check_inflight("takeoff_altitude_dwell"),
        )
        collision_monitor.check_inflight("takeoff_altitude")
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
        _join_future_guarded(
            "hover",
            config.timeouts["hover"],
            future,
            lambda: collision_monitor.check_inflight("hover"),
        )
        _assert_api_control(client, config, "hover")
        _wait_for_velocity_dwell(
            client,
            config,
            "hover",
            lambda: collision_monitor.check_inflight("hover_dwell"),
        )
        collision_monitor.check_inflight("hover")
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
        _join_future_guarded(
            "move",
            config.timeouts["move"],
            future,
            lambda: collision_monitor.check_inflight("move"),
        )
        _assert_api_control(client, config, "move")
        collision_monitor.check_inflight("move")
        recorder.transition("MOVE_A_TO_B", "PASS")

        arrived = _wait_for_pose_dwell(
            client,
            config,
            config.target,
            "arrival",
            lambda: collision_monitor.check_inflight("arrival_dwell"),
        )
        recorder.result["position_error_m"] = arrived["position_error_m"]
        recorder.transition(
            "VERIFY_ARRIVAL",
            "PASS",
            f"error_m={arrived['position_error_m']:.6f}",
        )
        collision_monitor.check_inflight("arrival")

        future = _call_with_timeout(
            "final_hover_start",
            config.timeouts["hover"],
            lambda: client.hoverAsync(vehicle_name=config.vehicle_name),
        )
        _join_future_guarded(
            "final_hover",
            config.timeouts["hover"],
            future,
            lambda: collision_monitor.check_inflight("final_hover"),
        )
        _assert_api_control(client, config, "final_hover")
        _wait_for_velocity_dwell(
            client,
            config,
            "hover",
            lambda: collision_monitor.check_inflight("final_hover_dwell"),
        )
        collision_monitor.check_inflight("final_hover")
        recorder.transition("FINAL_HOVER", "PASS")

        future = _call_with_timeout(
            "land_start",
            config.timeouts["land"],
            lambda: client.landAsync(
                timeout_sec=config.timeouts["land"],
                vehicle_name=config.vehicle_name,
            ),
        )
        _join_future_guarded(
            "land",
            config.timeouts["land"],
            future,
            lambda: collision_monitor.check_landing("land_async"),
        )
        _assert_api_control(client, config, "land")
        landed = collision_monitor.wait_for_touchdown("land")
        recorder.result["landing_confirmed"] = True
        recorder.result["landing_state"] = landed
        recorder.transition(
            "LAND",
            "PASS",
            f"landed_state={landed['landed_state']},speed_mps={landed['speed_mps']:.6f}",
        )

        disarm_result = _call_with_timeout(
            "disarm",
            config.timeouts["cleanup"],
            lambda: client.armDisarm(False, vehicle_name=config.vehicle_name),
        )
        if disarm_result is not True:
            raise FlightInvariantError("disarm did not return true")
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

        reset_state = _reset_and_verify(
            client, config, recorder, collision_monitor
        )
        recorder.result["final_state"] = reset_state
        recorder.transition(
            "RESET_VERIFY",
            "PASS",
            f"position_error_m={reset_state['position_error_m']:.6f}",
        )
        completed = True
    except (ConfigurationError, FlightInvariantError) as exc:
        recorder.error(str(exc))
        recorder.transition("FAIL", "FAIL", str(exc))
    except Exception as exc:  # vendor exceptions still produce evidence and cleanup
        recorder.error(f"unexpected error: {type(exc).__name__}: {exc}")
        recorder.transition("FAIL", "FAIL", recorder.result["errors"][-1])
    finally:
        cleanup_required = client is not None and (armed or api_control)
        if cleanup_required:
            _safe_cleanup(
                client,
                config,
                recorder,
                collision_monitor,
                armed=armed,
                api_control=api_control,
            )
        if (
            client is not None
            and collision_monitor is not None
            and not recorder.result["reset_confirmed"]
            and not recorder.result["reset_attempted"]
            and recorder.result["disarm_confirmed"]
            and recorder.result["api_control_released"]
        ):
            try:
                reset_state = _reset_and_verify(
                    client, config, recorder, collision_monitor
                )
                recorder.result["final_state"] = reset_state
                recorder.transition(
                    "ABORT_RESET_VERIFY",
                    "PASS",
                    f"position_error_m={reset_state['position_error_m']:.6f}",
                )
            except FlightInvariantError as exc:
                recorder.error(str(exc))
                recorder.transition("ABORT_RESET_VERIFY", "FAIL", str(exc))
        recorder.result["cleanup_complete"] = (
            not cleanup_required
            or (
                recorder.result["landing_confirmed"]
                and recorder.result["disarm_confirmed"]
                and recorder.result["api_control_released"]
            )
        )

    passed = completed and all(
        (
            recorder.result["collision_count"] == 0,
            recorder.result["landing_confirmed"],
            recorder.result["disarm_confirmed"],
            recorder.result["api_control_released"],
            recorder.result["reset_confirmed"],
            recorder.result["cleanup_complete"],
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
    client = _ThreadAffineCosysClient(
        lambda: cosysairsim.MultirotorClient(
            ip=config.host,
            port=config.port,
            timeout_value=config.rpc_timeout_seconds,
        ),
        config.timeouts["connect"],
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
            "Optional create-once output override for distinct cold/abort runs; this "
            "does not change flight_contract_sha256"
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
    else:
        output_path = configured_output

    if output_path.exists():
        print(f"ERROR: refusing to overwrite evidence file: {output_path}")
        return 2

    client: Any | None = None
    try:
        client, landed_state_value = _live_client_factory(config)
        result = run_smoke(
            config,
            config_sha256,
            client_factory=lambda _config: client,
            landed_state_value=landed_state_value,
        )
        result["run_id"] = output_path.stem
        result["evidence_output_path"] = str(output_path)
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
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
