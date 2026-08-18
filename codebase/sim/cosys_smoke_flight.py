"""Fail-closed, configuration-driven CoSys/AirSim transport smoke flight.

This module is deliberately isolated from the protected autonomy path.  It proves only
the simulator transport boundary: connect, validate one frozen vehicle, take off, move
from an explicit NED point A to an explicit NED point B, land, disarm, and release API
control.  It never invents route values and never substitutes defaults for safety limits.

Run from ``codebase`` after receiving Pratik's immutable route file::

    python -m sim.cosys_smoke_flight --config route.json --out airsim_smoke.json

Run the authorized localhost no-motion gate with a new create-once ID::

    python -m sim.cosys_smoke_flight --preflight-only --config route.json --out preflight.json

The output path is create-once.  A failed run is retained and must not be overwritten by
a later success.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import json
import math
import multiprocessing
import queue
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


OUTPUT_SCHEMA = "veriswarm.airsim_smoke.v2"
PREFLIGHT_OUTPUT_SCHEMA = "veriswarm.cosys_preflight.v1"
CONFIG_SCHEMA = "veriswarm.cosys_route.v1"
NED_FRAME = "NED_METRES"
APPROVED_DEVIATION_ID = "QB-LANDED-STATE-001"
APPROVED_DEVIATION_BY = "Suyash"
APPROVED_DEVIATION_REFERENCE = (
    "SUYASH_QB_LANDED_STATE_001_DISPOSITION_2026-08-19.txt "
    "sha256:8836ACEAD75A64FF21EA5D1E0DB21B0402ACA0BDC88FDDAF4C3135C9A514EA0B"
)


class ConfigurationError(ValueError):
    """Raised before any simulator connection when the route contract is incomplete."""


class FlightInvariantError(RuntimeError):
    """Raised when a live flight invariant fails."""


class StageTimeout(FlightInvariantError):
    """Raised when a bounded simulator operation exceeds its declared timeout."""


def _close_vendor_client(client: Any) -> None:
    """Close a vendor client on the execution context that owns it."""

    close = getattr(client, "close", None)
    if callable(close):
        close()
        return
    transport = getattr(client, "client", None)
    close = getattr(transport, "close", None)
    if callable(close):
        close()


def _construct_live_cosys_client(host: str, port: int, timeout: float) -> Any:
    import cosysairsim  # type: ignore[import-not-found]

    return cosysairsim.MultirotorClient(
        ip=host,
        port=port,
        timeout_value=timeout,
    )


def _rpc_process_entry(connection: Any, client_factory: Callable[[], Any]) -> None:
    """Own one client, its event loop, RPCs and futures in one child context."""

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client: Any | None = None
    futures: dict[int, Any] = {}
    next_future_id = 1
    try:
        try:
            client = client_factory()
            connection.send(("READY", True, None))
        except BaseException as exc:
            connection.send(("READY", False, f"{type(exc).__name__}: {exc}"))
            return

        while True:
            request = connection.recv()
            kind, request_id, payload = request
            if kind == "CLOSE":
                connection.send((request_id, True, None))
                return
            try:
                if kind == "CALL":
                    method_name, args, kwargs, expect_future = payload
                    value = getattr(client, method_name)(*args, **kwargs)
                    if expect_future:
                        future_id = next_future_id
                        next_future_id += 1
                        futures[future_id] = value
                        value = future_id
                elif kind == "JOIN":
                    future_id = int(payload)
                    future = futures.pop(future_id)
                    if not hasattr(future, "join"):
                        raise RuntimeError("RPC command returned no joinable future")
                    value = future.join()
                else:
                    raise RuntimeError(f"unsupported RPC request kind: {kind}")
                connection.send((request_id, True, value))
            except BaseException as exc:
                connection.send(
                    (request_id, False, f"{type(exc).__name__}: {exc}")
                )
    except (EOFError, BrokenPipeError):
        return
    finally:
        if client is not None:
            try:
                _close_vendor_client(client)
            except BaseException:
                pass
        try:
            connection.close()
        finally:
            asyncio.set_event_loop(None)
            loop.close()


class _RpcProcessWorker:
    """Killable, single-owner RPC process with create-once future handles."""

    def __init__(
        self,
        label: str,
        client_factory: Callable[[], Any],
        startup_timeout_seconds: float,
        on_timeout: Callable[[str, float], None] | None = None,
    ) -> None:
        self.label = label
        self._on_timeout = on_timeout
        self._state_lock = threading.Lock()
        self._call_lock = threading.Lock()
        self._request_id = 0
        self._usable = True
        self._closed = False
        self._shutdown_clean: bool | None = None
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe()
        self._connection = parent_connection
        self._process = context.Process(
            target=_rpc_process_entry,
            args=(child_connection, client_factory),
            name=f"cosys-rpc-{label}",
        )
        self._process.start()
        child_connection.close()
        if not self._connection.poll(startup_timeout_seconds):
            self._poison(
                f"{label} client creation exceeded {startup_timeout_seconds:.3f}s",
                startup_timeout_seconds,
            )
            raise StageTimeout(
                f"{label} client creation exceeded {startup_timeout_seconds:.3f}s"
            )
        try:
            kind, succeeded, detail = self._connection.recv()
        except (EOFError, OSError) as exc:
            self._poison(f"{label} client process exited during creation", 0.0)
            raise FlightInvariantError(
                f"{label} client process exited during creation"
            ) from exc
        if kind != "READY" or not succeeded:
            self.close()
            raise FlightInvariantError(f"{label} client creation failed: {detail}")

    @property
    def usable(self) -> bool:
        with self._state_lock:
            return self._usable and not self._closed

    def _terminate_process(self) -> None:
        process = self._process
        if process.is_alive():
            process.terminate()
        process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)

    def _poison(
        self,
        reason: str,
        timeout_seconds: float,
        *,
        notify_timeout: bool = True,
    ) -> None:
        notify = False
        with self._state_lock:
            if self._usable:
                self._usable = False
                notify = True
            self._shutdown_clean = False
        self._terminate_process()
        if notify and notify_timeout and self._on_timeout is not None:
            self._on_timeout(reason, timeout_seconds)

    def abort_timeout(self, label: str, timeout_seconds: float) -> None:
        self._poison(
            f"{label} exceeded {timeout_seconds:.3f}s",
            timeout_seconds,
        )

    def abort_inflight(self, label: str, reason: str) -> None:
        """Terminate an active command after a safety invariant fails."""

        self._poison(
            f"{label} aborted after safety invariant: {reason}",
            0.0,
            notify_timeout=False,
        )

    def _request(
        self,
        kind: str,
        payload: Any,
        timeout_seconds: float,
        label: str,
    ) -> Any:
        with self._call_lock:
            if not self.usable:
                raise FlightInvariantError(
                    f"{self.label} RPC context is unusable"
                )
            self._request_id += 1
            request_id = self._request_id
            try:
                self._connection.send((kind, request_id, payload))
                if not self._connection.poll(timeout_seconds):
                    self._poison(
                        f"{label} exceeded {timeout_seconds:.3f}s",
                        timeout_seconds,
                    )
                    raise StageTimeout(
                        f"{label} exceeded {timeout_seconds:.3f}s; "
                        f"{self.label} RPC context terminated and is unusable"
                    )
                response_id, succeeded, value = self._connection.recv()
            except StageTimeout:
                raise
            except (EOFError, BrokenPipeError, OSError) as exc:
                self._poison(f"{label} lost its RPC process", timeout_seconds)
                raise FlightInvariantError(
                    f"{label} lost its RPC process; context is unusable"
                ) from exc
            if response_id != request_id:
                self._poison(f"{label} received an out-of-order reply", timeout_seconds)
                raise FlightInvariantError(
                    f"{label} received an out-of-order RPC reply"
                )
            if not succeeded:
                raise FlightInvariantError(f"{label} failed: {value}")
            return value

    def call(
        self,
        label: str,
        timeout_seconds: float,
        method_name: str,
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
        *,
        expect_future: bool = False,
    ) -> Any:
        return self._request(
            "CALL",
            (method_name, args, dict(kwargs), expect_future),
            timeout_seconds,
            label,
        )

    def join_future(
        self, label: str, timeout_seconds: float, future_id: int
    ) -> Any:
        return self._request("JOIN", future_id, timeout_seconds, label)

    def close(self) -> bool:
        with self._state_lock:
            if self._closed:
                return self._shutdown_clean is True
            self._closed = True
            was_usable = self._usable
            self._usable = False
        graceful = False
        if was_usable and self._process.is_alive():
            try:
                self._request_id += 1
                request_id = self._request_id
                self._connection.send(("CLOSE", request_id, None))
                if self._connection.poll(1.0):
                    response_id, succeeded, _value = self._connection.recv()
                    if response_id == request_id and succeeded:
                        self._process.join(timeout=1.0)
                        graceful = not self._process.is_alive()
            except (EOFError, BrokenPipeError, OSError):
                pass
        self._terminate_process()
        try:
            self._connection.close()
        except OSError:
            pass
        with self._state_lock:
            self._shutdown_clean = graceful
        return graceful


class _ProcessRpcFuture:
    """A future token that can only be joined by its creating RPC process."""

    def __init__(self, worker: _RpcProcessWorker, future_id: int) -> None:
        self._worker = worker
        self._future_id = future_id

    def join_with_timeout(self, label: str, timeout_seconds: float) -> Any:
        return self._worker.join_future(
            label, timeout_seconds, self._future_id
        )

    def abort_timeout(self, label: str, timeout_seconds: float) -> None:
        self._worker.abort_timeout(label, timeout_seconds)

    def abort_inflight(self, label: str, reason: str) -> None:
        self._worker.abort_inflight(label, reason)


class _ProcessAffineCosysClient:
    """Use persistent, killable owner contexts for CoSys command and telemetry RPCs."""

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
        default_timeout_seconds: float,
    ) -> None:
        self._client_factory = client_factory
        self._startup_timeout_seconds = startup_timeout_seconds
        self._default_timeout_seconds = default_timeout_seconds
        self._timeouts: list[dict[str, Any]] = []
        self._failsafe_mode = False
        self._had_forced_termination = False
        self._closed = False
        self._command = self._new_worker("command")
        try:
            self._observation = self._new_worker("observation")
        except BaseException:
            self._command.close()
            raise

    def _record_timeout(self, reason: str, timeout_seconds: float) -> None:
        self._timeouts.append(
            {"reason": reason, "timeout_seconds": timeout_seconds}
        )

    def _new_worker(self, label: str) -> _RpcProcessWorker:
        return _RpcProcessWorker(
            label,
            self._client_factory,
            self._startup_timeout_seconds,
            self._record_timeout,
        )

    def _worker_for(self, method_name: str) -> _RpcProcessWorker:
        if method_name in self._COMMAND_METHODS:
            return self._command
        if method_name in self._OBSERVATION_METHODS:
            return self._observation
        raise AttributeError(f"unsupported CoSys client operation: {method_name}")

    def _call_rpc_with_timeout(
        self,
        label: str,
        timeout_seconds: float,
        method_name: str,
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
    ) -> Any:
        if self._closed:
            raise FlightInvariantError("CoSys RPC client is closed")
        worker = self._worker_for(method_name)
        value = worker.call(
            label,
            timeout_seconds,
            method_name,
            args,
            kwargs,
            expect_future=method_name in self._ASYNC_METHODS,
        )
        if method_name in self._ASYNC_METHODS:
            return _ProcessRpcFuture(worker, int(value))
        return value

    def __getattr__(self, method_name: str) -> Callable[..., Any]:
        if (
            method_name not in self._COMMAND_METHODS
            and method_name not in self._OBSERVATION_METHODS
        ):
            raise AttributeError(method_name)

        def invoke(*args: Any, **kwargs: Any) -> Any:
            return self._call_rpc_with_timeout(
                method_name,
                self._default_timeout_seconds,
                method_name,
                args,
                kwargs,
            )

        return invoke

    def begin_failsafe_cleanup(self) -> None:
        """Discard every pre-timeout context and create fresh cleanup contexts."""

        if self._closed or self._failsafe_mode:
            return
        self._failsafe_mode = True
        command_closed = self._command.close()
        observation_closed = self._observation.close()
        if not command_closed or not observation_closed:
            self._had_forced_termination = True
        self._command = self._new_worker("failsafe-command")
        try:
            self._observation = self._new_worker("failsafe-observation")
        except BaseException:
            self._command.close()
            raise

    def diagnostics(self) -> Mapping[str, Any]:
        return {
            "execution_model": "spawned_process_per_client_context",
            "failsafe_context_created": self._failsafe_mode,
            "timeouts": list(self._timeouts),
        }

    def close(self) -> bool:
        if self._closed:
            return not self._had_forced_termination
        self._closed = True
        observation_closed = self._observation.close()
        command_closed = self._command.close()
        return (
            not self._had_forced_termination
            and observation_closed
            and command_closed
        )


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
    if deviation_id != APPROVED_DEVIATION_ID:
        raise ConfigurationError(
            f"the only recognized landed-state deviation is {APPROVED_DEVIATION_ID}"
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
        if approved_by != APPROVED_DEVIATION_BY:
            raise ConfigurationError(
                "QB-LANDED-STATE-001 must be explicitly approved by Suyash"
            )
        if approval_reference != APPROVED_DEVIATION_REFERENCE:
            raise ConfigurationError(
                "QB-LANDED-STATE-001 approval_reference does not match the "
                "frozen Suyash authorization"
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


def _rpc_call(
    client: Any,
    label: str,
    timeout_seconds: float,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Invoke an RPC with a hard timeout in the live owner's killable context."""

    process_call = getattr(client, "_call_rpc_with_timeout", None)
    if callable(process_call):
        return process_call(
            label, timeout_seconds, method_name, args, kwargs
        )
    return _call_with_timeout(
        label,
        timeout_seconds,
        lambda: getattr(client, method_name)(*args, **kwargs),
    )


def _join_future(label: str, timeout_seconds: float, future: Any) -> None:
    join_with_timeout = getattr(future, "join_with_timeout", None)
    if callable(join_with_timeout):
        join_with_timeout(label, timeout_seconds)
        return
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

    join_with_timeout = getattr(future, "join_with_timeout", None)
    abort_timeout = getattr(future, "abort_timeout", None)
    abort_inflight = getattr(future, "abort_inflight", None)
    if not hasattr(future, "join"):
        if not callable(join_with_timeout):
            raise FlightInvariantError(f"{label} returned no joinable future")
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            if callable(join_with_timeout):
                join_with_timeout(label, timeout_seconds)
            else:
                future.join()
            results.put((True, None))
        except BaseException as exc:
            results.put((False, exc))

    worker = threading.Thread(
        target=invoke,
        name=f"cosys-{label}-join",
        daemon=not callable(join_with_timeout),
    )
    worker.start()

    def abort_after_safety_failure(original: BaseException) -> None:
        """Stop the command before letting a safety failure escape the join loop."""

        try:
            if callable(abort_inflight):
                abort_inflight(label, str(original))
        finally:
            worker.join(timeout=1.0)
            try:
                setattr(original, "_cosys_inflight_command_aborted", True)
                setattr(
                    original,
                    "_cosys_join_thread_exited",
                    not worker.is_alive(),
                )
            except BaseException:
                # Preserve the original safety exception even for unusual exception types.
                pass

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            succeeded, value = results.get(timeout=0.01)
        except queue.Empty:
            try:
                safety_check()
            except BaseException as exc:
                abort_after_safety_failure(exc)
                raise
            continue
        try:
            safety_check()
        except BaseException as exc:
            abort_after_safety_failure(exc)
            raise
        if not succeeded:
            if isinstance(value, FlightInvariantError):
                raise value
            raise FlightInvariantError(f"{label} failed: {value}") from value
        return
    if callable(abort_timeout):
        abort_timeout(label, timeout_seconds)
        worker.join(timeout=1.0)
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
            "rpc_execution": {
                "execution_model": "direct_or_test_double",
                "failsafe_context_created": False,
                "timeouts": [],
            },
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
        deviation = self.result["touchdown_policy"]["landed_state_deviation"]
        if passed and deviation["applied"]:
            valid_approval = (
                deviation["id"] == APPROVED_DEVIATION_ID
                and deviation["approved"] is True
                and deviation["approved_by"] == APPROVED_DEVIATION_BY
                and deviation["approval_reference"]
                == APPROVED_DEVIATION_REFERENCE
            )
            if not valid_approval:
                passed = False
                self.error(
                    "applied landed-state deviation does not match the frozen "
                    "QB-LANDED-STATE-001 approval"
                )
        self.result["pass"] = passed
        if not passed:
            self.result["process_result"] = "FAIL"
        elif deviation["applied"]:
            self.result["process_result"] = (
                "PASS_WITH_APPROVED_SIMULATOR_DEVIATION"
            )
        else:
            self.result["process_result"] = "PASS"


def _assert_inside_geofence(
    state: Mapping[str, Any], config: SmokeConfig, context: str
) -> None:
    position = Vector3(*state["position"])
    if not config.geofence.contains(position):
        raise FlightInvariantError(
            f"{context} position {position.as_list()} is outside the configured geofence"
        )


def _assert_api_control(client: Any, config: SmokeConfig, context: str) -> None:
    enabled = _rpc_call(
        client,
        f"{context}_api_control_check",
        config.timeouts["api_control"],
        "isApiControlEnabled",
        vehicle_name=config.vehicle_name,
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
        collision = _rpc_call(
            self.client,
            "collision_check",
            self.config.timeouts[timeout_name],
            "simGetCollisionInfo",
            vehicle_name=self.config.vehicle_name,
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
    state = _rpc_call(
        client,
        "get_multirotor_state",
        config.timeouts[timeout_name],
        "getMultirotorState",
        vehicle_name=config.vehicle_name,
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
    if _rpc_call(
        client,
        "pre_reset_api_control_check",
        config.timeouts["api_control"],
        "isApiControlEnabled",
        vehicle_name=config.vehicle_name,
    ):
        raise FlightInvariantError("refusing reset while API control remains enabled")

    recorder.result["reset_attempted"] = True
    _rpc_call(client, "reset", timeout, "reset")
    deadline = time.monotonic() + timeout
    stable_since: float | None = None
    last_state: dict[str, Any] | None = None
    last_collision: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if _rpc_call(
            client,
            "reset_api_control_check",
            config.timeouts["api_control"],
            "isApiControlEnabled",
            vehicle_name=config.vehicle_name,
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
            future = _rpc_call(
                client,
                "abort_hover_start",
                timeout,
                "hoverAsync",
                vehicle_name=config.vehicle_name,
            )
            _join_future("abort_hover", timeout, future)
            recorder.transition("ABORT_HOVER", "PASS")
        except FlightInvariantError as exc:
            recorder.error(str(exc))
            recorder.transition("ABORT_HOVER", "FAIL", str(exc))

        try:
            future = _rpc_call(
                client,
                "abort_land_start",
                timeout,
                "landAsync",
                timeout_sec=timeout,
                vehicle_name=config.vehicle_name,
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
            result = _rpc_call(
                client,
                "abort_disarm",
                timeout,
                "armDisarm",
                False,
                vehicle_name=config.vehicle_name,
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
            _rpc_call(
                client,
                "abort_release_api_control",
                timeout,
                "enableApiControl",
                False,
                vehicle_name=config.vehicle_name,
            )
            still_enabled = _rpc_call(
                client,
                "abort_verify_api_release",
                timeout,
                "isApiControlEnabled",
                vehicle_name=config.vehicle_name,
            )
            if still_enabled:
                raise FlightInvariantError("API control remained enabled after cleanup")
            recorder.result["api_control_released"] = True
            recorder.transition("ABORT_RELEASE_API", "PASS")
        except FlightInvariantError as exc:
            recorder.error(str(exc))
            recorder.transition("ABORT_RELEASE_API", "FAIL", str(exc))


def _begin_failsafe_cleanup_context(
    client: Any | None,
    exc: BaseException,
    recorder: EvidenceRecorder,
) -> None:
    """Replace aborted/expired live RPC contexts before any cleanup command."""

    if client is None:
        return
    aborted_inflight = bool(
        getattr(exc, "_cosys_inflight_command_aborted", False)
    )
    if not isinstance(exc, StageTimeout) and not aborted_inflight:
        return
    begin_failsafe = getattr(client, "begin_failsafe_cleanup", None)
    if not callable(begin_failsafe):
        return
    transition = (
        "RPC_ABORT_FAILSAFE_CONTEXT"
        if aborted_inflight
        else "RPC_TIMEOUT_FAILSAFE_CONTEXT"
    )
    try:
        begin_failsafe()
        recorder.transition(transition, "PASS")
    except FlightInvariantError as cleanup_context_exc:
        recorder.error(str(cleanup_context_exc))
        recorder.transition(transition, "FAIL", str(cleanup_context_exc))


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
        if not _rpc_call(
            client, "ping", config.timeouts["connect"], "ping"
        ):
            raise FlightInvariantError("ping returned false")
        recorder.transition("CONNECT", "PASS")

        vehicles = _rpc_call(
            client,
            "list_vehicles",
            config.timeouts["vehicle_check"],
            "listVehicles",
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

        _rpc_call(
            client,
            "enable_api_control",
            config.timeouts["api_control"],
            "enableApiControl",
            True,
            vehicle_name=config.vehicle_name,
        )
        api_control = True
        if not _rpc_call(
            client,
            "verify_api_control",
            config.timeouts["api_control"],
            "isApiControlEnabled",
            vehicle_name=config.vehicle_name,
        ):
            raise FlightInvariantError("API control did not become enabled")
        recorder.transition("API_CONTROL", "PASS")

        arm_result = _rpc_call(
            client,
            "arm",
            config.timeouts["arm"],
            "armDisarm",
            True,
            vehicle_name=config.vehicle_name,
        )
        if arm_result is False:
            raise FlightInvariantError("arm returned false")
        armed = True
        collision_monitor.check_inflight("arm")
        recorder.transition("ARM", "PASS")

        future = _rpc_call(
            client,
            "takeoff_start",
            config.timeouts["takeoff"],
            "takeoffAsync",
            timeout_sec=config.timeouts["takeoff"],
            vehicle_name=config.vehicle_name,
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
        future = _rpc_call(
            client,
            "takeoff_altitude_start",
            config.timeouts["takeoff"],
            "moveToPositionAsync",
            takeoff_target.x,
            takeoff_target.y,
            takeoff_target.z,
            config.speed_mps,
            timeout_sec=config.timeouts["takeoff"],
            vehicle_name=config.vehicle_name,
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

        future = _rpc_call(
            client,
            "hover_start",
            config.timeouts["hover"],
            "hoverAsync",
            vehicle_name=config.vehicle_name,
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

        future = _rpc_call(
            client,
            "move_start",
            config.timeouts["move"],
            "moveToPositionAsync",
            config.target.x,
            config.target.y,
            config.target.z,
            config.speed_mps,
            timeout_sec=config.timeouts["move"],
            vehicle_name=config.vehicle_name,
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

        future = _rpc_call(
            client,
            "final_hover_start",
            config.timeouts["hover"],
            "hoverAsync",
            vehicle_name=config.vehicle_name,
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

        future = _rpc_call(
            client,
            "land_start",
            config.timeouts["land"],
            "landAsync",
            timeout_sec=config.timeouts["land"],
            vehicle_name=config.vehicle_name,
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

        disarm_result = _rpc_call(
            client,
            "disarm",
            config.timeouts["cleanup"],
            "armDisarm",
            False,
            vehicle_name=config.vehicle_name,
        )
        if disarm_result is not True:
            raise FlightInvariantError("disarm did not return true")
        armed = False
        recorder.result["disarm_confirmed"] = True
        recorder.transition("DISARM", "PASS")

        _rpc_call(
            client,
            "release_api_control",
            config.timeouts["cleanup"],
            "enableApiControl",
            False,
            vehicle_name=config.vehicle_name,
        )
        if _rpc_call(
            client,
            "verify_api_release",
            config.timeouts["cleanup"],
            "isApiControlEnabled",
            vehicle_name=config.vehicle_name,
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
        _begin_failsafe_cleanup_context(client, exc, recorder)
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
        if client is not None:
            diagnostics = getattr(client, "diagnostics", None)
            if callable(diagnostics):
                recorder.result["rpc_execution"] = dict(diagnostics())

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
    factory = functools.partial(
        _construct_live_cosys_client,
        config.host,
        config.port,
        config.rpc_timeout_seconds,
    )
    client = _ProcessAffineCosysClient(
        factory,
        config.timeouts["connect"],
        config.rpc_timeout_seconds,
    )
    return client, int(cosysairsim.LandedState.Landed)


def _validate_read_only_preflight_scope(config: SmokeConfig) -> None:
    """Refuse any non-local Q-B preflight before opening a client or RPC process."""

    if config.runtime.qualification_configuration != "Q-B":
        raise FlightInvariantError("read-only preflight requires Q-B runtime")
    if config.host != "127.0.0.1" or config.port != 41451:
        raise FlightInvariantError(
            "read-only preflight requires endpoint 127.0.0.1:41451"
        )
    if config.vehicle_name != "Drone1":
        raise FlightInvariantError(
            "read-only preflight requires exact raw vehicle name Drone1"
        )


def run_read_only_preflight(
    config: SmokeConfig,
    config_sha256: str,
    client: Any,
) -> dict[str, Any]:
    """Capture the authorized Q-B no-motion RPC gate without enabling control."""

    started_ns = time.time_ns()
    result: dict[str, Any] = {
        "schema": PREFLIGHT_OUTPUT_SCHEMA,
        "created_ns": started_ns,
        "config_sha256": config_sha256,
        "endpoint": {"host": config.host, "port": config.port},
        "vehicle": config.vehicle_name,
        "operations": [
            "client_construction",
            "ping",
            "listVehicles",
            "getMultirotorState",
            "client_shutdown",
        ],
        "ping": None,
        "vehicles": None,
        "state": None,
        "client_shutdown": False,
        "errors": [],
        "pass": False,
        "process_result": "FAIL",
    }
    try:
        _validate_read_only_preflight_scope(config)
        ping = _rpc_call(
            client, "preflight_ping", config.timeouts["connect"], "ping"
        )
        result["ping"] = ping
        if ping is not True:
            raise FlightInvariantError("preflight ping returned false")
        vehicles = _rpc_call(
            client,
            "preflight_list_vehicles",
            config.timeouts["vehicle_check"],
            "listVehicles",
        )
        result["vehicles"] = list(vehicles) if isinstance(vehicles, Sequence) else vehicles
        if not isinstance(vehicles, Sequence) or isinstance(vehicles, (str, bytes)):
            raise FlightInvariantError("preflight listVehicles returned invalid roster")
        if config.vehicle_name not in vehicles:
            raise FlightInvariantError("preflight roster does not contain Drone1")
        state = _rpc_call(
            client,
            "preflight_get_multirotor_state",
            config.timeouts["vehicle_check"],
            "getMultirotorState",
            vehicle_name=config.vehicle_name,
        )
        result["state"] = _state_snapshot(state)
        result["pass"] = True
        result["process_result"] = "PASS"
    except (ConfigurationError, FlightInvariantError) as exc:
        result["errors"].append(str(exc))
    except Exception as exc:
        result["errors"].append(
            f"unexpected error: {type(exc).__name__}: {exc}"
        )
    result["finished_ns"] = time.time_ns()
    return result


def _close_runtime_client(client: Any) -> tuple[bool, str | None]:
    close = getattr(client, "close", None)
    if not callable(close):
        return False, "client exposes no close operation"
    try:
        closed = close()
    except BaseException as exc:
        return False, f"client shutdown failed: {type(exc).__name__}: {exc}"
    if closed is False:
        return False, "client shutdown required forced termination"
    return True, None


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
        "--preflight-only",
        action="store_true",
        help=(
            "Run the create-once no-motion Q-B gate: construct, ping, listVehicles, "
            "getMultirotorState(Drone1), then clean shutdown"
        ),
    )
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
    selected_schema = (
        PREFLIGHT_OUTPUT_SCHEMA if args.preflight_only else OUTPUT_SCHEMA
    )

    try:
        config, config_sha256 = load_config(args.config)
    except (OSError, ConfigurationError) as exc:
        result = {
            "schema": selected_schema,
            "created_ns": time.time_ns(),
            "config_path": str(args.config),
            "errors": [str(exc)],
            "pass": False,
            "process_result": "FAIL",
            "failure_class": "CONFIG_REFUSED",
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

    if args.preflight_only:
        try:
            _validate_read_only_preflight_scope(config)
        except FlightInvariantError as exc:
            result = {
                "schema": PREFLIGHT_OUTPUT_SCHEMA,
                "created_ns": time.time_ns(),
                "config_sha256": config_sha256,
                "errors": [str(exc)],
                "pass": False,
                "process_result": "FAIL",
                "failure_class": "PREFLIGHT_SCOPE_REFUSED",
            }
            try:
                _write_create_once(output_path, result)
            except (OSError, ConfigurationError) as write_exc:
                print(f"ERROR: {write_exc}")
                return 2
            print(json.dumps(result, indent=2, sort_keys=True))
            return 2

    client: Any | None = None
    try:
        client, landed_state_value = _live_client_factory(config)
        if args.preflight_only:
            result = run_read_only_preflight(config, config_sha256, client)
            shutdown_ok, shutdown_error = _close_runtime_client(client)
            client = None
            result["client_shutdown"] = shutdown_ok
            if not shutdown_ok:
                result["pass"] = False
                result["process_result"] = "FAIL"
                result["errors"].append(shutdown_error)
        else:
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
            "schema": selected_schema,
            "created_ns": time.time_ns(),
            "config_sha256": config_sha256,
            "errors": [str(exc)],
            "pass": False,
            "process_result": "FAIL",
            "failure_class": "STARTUP_REFUSED",
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
            _close_runtime_client(client)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
