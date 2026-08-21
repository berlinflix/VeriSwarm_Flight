"""Phase 3 adapters for live FactoryCity clearance and CoSys settings.

Scenario values enter through the validated fleet and runtime documents. This module
contains only adapter behavior and protocol validation; it contains no fleet roster,
spawn coordinate, endpoint, launch dimension, timeout, order, or policy default.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import FactoryCityConfig, SensorConfig
from .launch import (
    ClearanceProbeResult,
    ClearanceRequest,
    generate_lattice_candidates,
)


RUNTIME_SCHEMA_ID = "veriswarm.factorycity.cosys_runtime.v1"
CLEARANCE_REQUEST_SCHEMA_ID = "veriswarm.factorycity.clearance_request.v1"
CLEARANCE_RESULT_SCHEMA_ID = "veriswarm.factorycity.clearance_result.v1"
SUPPORTED_RUNTIME_STATUSES = frozenset(
    {"CANDIDATE_NOT_RUNTIME_VALIDATED", "ACCEPTED_RUNTIME_VALIDATED"}
)


class Phase3ContractError(ValueError):
    """Raised when a Phase 3 adapter or evidence contract is incomplete."""


@dataclass(frozen=True)
class Vector3Value:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class CosysRuntimeProfile:
    status: str
    settings_version: float
    sim_mode: str
    clock_speed: float
    enable_rpc: bool
    yaw_degrees: float
    auto_create: bool
    allow_api_always: bool
    enable_collisions: bool
    enable_collision_passthrough: bool
    image_type_codes: tuple[tuple[str, int], ...]
    sensor_type_codes: tuple[tuple[str, int], ...]
    update_frequency_sensor_types: frozenset[str]
    origin_unreal_units: Vector3Value
    world_to_meters: float
    ned_to_unreal_axis_sign: Vector3Value
    trace_channel: str
    trace_complex: bool
    minimum_ground_normal_z: float
    unreal_engine_version: str
    cosys_airsim_version: str
    cosys_protocol_version: int

    @property
    def image_codes(self) -> dict[str, int]:
        return dict(self.image_type_codes)

    @property
    def sensor_codes(self) -> dict[str, int]:
        return dict(self.sensor_type_codes)


def _object(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise Phase3ContractError(f"{context} must be an object with string keys")
    return value


def _exact(value: Any, fields: set[str], context: str) -> Mapping[str, Any]:
    item = _object(value, context)
    missing = sorted(fields - set(item))
    unknown = sorted(set(item) - fields)
    if missing:
        raise Phase3ContractError(f"{context} missing fields: {', '.join(missing)}")
    if unknown:
        raise Phase3ContractError(f"{context} unknown fields: {', '.join(unknown)}")
    return item


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise Phase3ContractError(f"{context} must be non-empty trimmed text")
    return value


def _bool(value: Any, context: str) -> bool:
    if type(value) is not bool:
        raise Phase3ContractError(f"{context} must be a boolean")
    return value


def _choice(value: Any, choices: frozenset[str], context: str) -> str:
    selected = _text(value, context)
    if selected not in choices:
        raise Phase3ContractError(
            f"{context} must be one of: {', '.join(sorted(choices))}"
        )
    return selected


def _number(value: Any, context: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Phase3ContractError(f"{context} must be a number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0.0):
        qualifier = "positive finite" if positive else "finite"
        raise Phase3ContractError(f"{context} must be {qualifier}")
    return result


def _positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Phase3ContractError(f"{context} must be a positive integer")
    return value


def _vector(value: Any, context: str) -> Vector3Value:
    raw = _exact(value, {"x", "y", "z"}, context)
    return Vector3Value(
        x=_number(raw["x"], f"{context}.x"),
        y=_number(raw["y"], f"{context}.y"),
        z=_number(raw["z"], f"{context}.z"),
    )


def _code_map(
    value: Any, context: str, *, allow_zero: bool = False
) -> tuple[tuple[str, int], ...]:
    raw = _object(value, context)
    if not raw:
        raise Phase3ContractError(f"{context} must not be empty")
    items: list[tuple[str, int]] = []
    values: set[int] = set()
    for key in sorted(raw):
        name = _text(key, f"{context} key")
        code = raw[key]
        if (
            isinstance(code, bool)
            or not isinstance(code, int)
            or code < (0 if allow_zero else 1)
        ):
            qualifier = "non-negative" if allow_zero else "positive"
            raise Phase3ContractError(
                f"{context}.{name} must be a {qualifier} integer"
            )
        if code in values:
            raise Phase3ContractError(f"{context} numeric codes must be unique")
        values.add(code)
        items.append((name, code))
    return tuple(items)


def validate_runtime_profile(raw: Mapping[str, Any]) -> CosysRuntimeProfile:
    root = _exact(
        raw,
        {
            "schema",
            "status",
            "runtime",
            "vehicle_defaults",
            "protocol",
            "coordinate_transform",
            "clearance_probe",
            "runtime_identity",
        },
        "runtime_profile",
    )
    if root["schema"] != RUNTIME_SCHEMA_ID:
        raise Phase3ContractError("runtime_profile.schema is unsupported")
    runtime = _exact(
        root["runtime"],
        {"settings_version", "sim_mode", "clock_speed", "enable_rpc"},
        "runtime_profile.runtime",
    )
    vehicles = _exact(
        root["vehicle_defaults"],
        {
            "yaw_degrees",
            "auto_create",
            "allow_api_always",
            "enable_collisions",
            "enable_collision_passthrough",
        },
        "runtime_profile.vehicle_defaults",
    )
    protocol = _exact(
        root["protocol"],
        {
            "image_type_codes",
            "sensor_type_codes",
            "update_frequency_sensor_types",
        },
        "runtime_profile.protocol",
    )
    transform = _exact(
        root["coordinate_transform"],
        {"origin_unreal_units", "world_to_meters", "ned_to_unreal_axis_sign"},
        "runtime_profile.coordinate_transform",
    )
    clearance = _exact(
        root["clearance_probe"],
        {
            "trace_channel",
            "trace_complex",
            "minimum_ground_normal_z",
        },
        "runtime_profile.clearance_probe",
    )
    identity = _exact(
        root["runtime_identity"],
        {
            "unreal_engine_version",
            "cosys_airsim_version",
            "cosys_protocol_version",
        },
        "runtime_profile.runtime_identity",
    )
    image_codes = _code_map(
        protocol["image_type_codes"],
        "runtime_profile.protocol.image_type_codes",
        allow_zero=True,
    )
    sensor_codes = _code_map(
        protocol["sensor_type_codes"], "runtime_profile.protocol.sensor_type_codes"
    )
    frequency_types_raw = protocol["update_frequency_sensor_types"]
    if not isinstance(frequency_types_raw, list):
        raise Phase3ContractError(
            "runtime_profile.protocol.update_frequency_sensor_types must be an array"
        )
    frequency_types = frozenset(
        _text(item, "runtime_profile.protocol.update_frequency_sensor_types item")
        for item in frequency_types_raw
    )
    if len(frequency_types) != len(frequency_types_raw):
        raise Phase3ContractError("update-frequency sensor types must be unique")
    sensor_names = {name for name, _ in sensor_codes}
    if not frequency_types <= sensor_names:
        raise Phase3ContractError(
            "update-frequency sensor types must have sensor type codes"
        )
    normal = _number(
        clearance["minimum_ground_normal_z"],
        "runtime_profile.clearance_probe.minimum_ground_normal_z",
    )
    if not 0.0 < normal <= 1.0:
        raise Phase3ContractError("minimum ground normal z must be in (0, 1]")
    axis_sign = _vector(
        transform["ned_to_unreal_axis_sign"],
        "runtime_profile.coordinate_transform.ned_to_unreal_axis_sign",
    )
    if {abs(axis_sign.x), abs(axis_sign.y), abs(axis_sign.z)} != {1.0}:
        raise Phase3ContractError("NED-to-Unreal axis signs must each be +1 or -1")
    return CosysRuntimeProfile(
        status=_choice(
            root["status"], SUPPORTED_RUNTIME_STATUSES, "runtime_profile.status"
        ),
        settings_version=_number(
            runtime["settings_version"],
            "runtime_profile.runtime.settings_version",
            positive=True,
        ),
        sim_mode=_text(runtime["sim_mode"], "runtime_profile.runtime.sim_mode"),
        clock_speed=_number(
            runtime["clock_speed"],
            "runtime_profile.runtime.clock_speed",
            positive=True,
        ),
        enable_rpc=_bool(runtime["enable_rpc"], "runtime_profile.runtime.enable_rpc"),
        yaw_degrees=_number(
            vehicles["yaw_degrees"],
            "runtime_profile.vehicle_defaults.yaw_degrees",
        ),
        auto_create=_bool(
            vehicles["auto_create"], "runtime_profile.vehicle_defaults.auto_create"
        ),
        allow_api_always=_bool(
            vehicles["allow_api_always"],
            "runtime_profile.vehicle_defaults.allow_api_always",
        ),
        enable_collisions=_bool(
            vehicles["enable_collisions"],
            "runtime_profile.vehicle_defaults.enable_collisions",
        ),
        enable_collision_passthrough=_bool(
            vehicles["enable_collision_passthrough"],
            "runtime_profile.vehicle_defaults.enable_collision_passthrough",
        ),
        image_type_codes=image_codes,
        sensor_type_codes=sensor_codes,
        update_frequency_sensor_types=frequency_types,
        origin_unreal_units=_vector(
            transform["origin_unreal_units"],
            "runtime_profile.coordinate_transform.origin_unreal_units",
        ),
        world_to_meters=_number(
            transform["world_to_meters"],
            "runtime_profile.coordinate_transform.world_to_meters",
            positive=True,
        ),
        ned_to_unreal_axis_sign=axis_sign,
        trace_channel=_text(
            clearance["trace_channel"],
            "runtime_profile.clearance_probe.trace_channel",
        ),
        trace_complex=_bool(
            clearance["trace_complex"],
            "runtime_profile.clearance_probe.trace_complex",
        ),
        minimum_ground_normal_z=normal,
        unreal_engine_version=_text(
            identity["unreal_engine_version"],
            "runtime_profile.runtime_identity.unreal_engine_version",
        ),
        cosys_airsim_version=_text(
            identity["cosys_airsim_version"],
            "runtime_profile.runtime_identity.cosys_airsim_version",
        ),
        cosys_protocol_version=_positive_int(
            identity["cosys_protocol_version"],
            "runtime_profile.runtime_identity.cosys_protocol_version",
        ),
    )


def load_runtime_profile(path: str | Path) -> tuple[CosysRuntimeProfile, str]:
    source = Path(path)
    try:
        content = source.read_bytes()
        raw = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase3ContractError(f"cannot load runtime profile {source}: {exc}") from exc
    return validate_runtime_profile(_object(raw, "runtime_profile")), hashlib.sha256(
        content
    ).hexdigest()


def _profile_for_vehicle(config: FactoryCityConfig, vehicle_name: str):
    assignment = next(
        item for item in config.sensors.vehicle_profiles if item.vehicle_name == vehicle_name
    )
    return next(
        profile for profile in config.sensors.profiles if profile.name == assignment.profile
    )


def _sensor_settings(sensor: SensorConfig, profile: CosysRuntimeProfile) -> dict[str, Any]:
    settings = {item.key: item.value for item in sensor.settings}
    camera_codes = profile.image_codes
    if sensor.sensor_type in camera_codes:
        return {
            "X": sensor.position_body_m.x,
            "Y": sensor.position_body_m.y,
            "Z": sensor.position_body_m.z,
            "Roll": sensor.rotation_body_deg.x,
            "Pitch": sensor.rotation_body_deg.y,
            "Yaw": sensor.rotation_body_deg.z,
            "CaptureSettings": [
                {
                    "ImageType": camera_codes[sensor.sensor_type],
                    "Width": settings["width"],
                    "Height": settings["height"],
                    "FOV_Degrees": settings["fov_degrees"],
                }
            ],
        }
    sensor_codes = profile.sensor_codes
    if sensor.sensor_type not in sensor_codes:
        raise Phase3ContractError(
            f"runtime profile has no CoSys code for sensor type {sensor.sensor_type!r}"
        )
    result: dict[str, Any] = {
        "SensorType": sensor_codes[sensor.sensor_type],
        "Enabled": sensor.required,
    }
    if sensor.sensor_type in profile.update_frequency_sensor_types:
        result["UpdateFrequency"] = sensor.rate_hz
    return result


def build_cosys_base_settings(
    config: FactoryCityConfig, profile: CosysRuntimeProfile
) -> dict[str, Any]:
    """Build complete non-positional CoSys settings from validated data only."""

    camera_types = set(profile.image_codes)
    vehicles: dict[str, Any] = {}
    for vehicle in config.fleet.vehicles:
        sensor_profile = _profile_for_vehicle(config, vehicle.name)
        cameras: dict[str, Any] = {}
        sensors: dict[str, Any] = {}
        for sensor in sensor_profile.sensors:
            output = _sensor_settings(sensor, profile)
            if sensor.sensor_type in camera_types:
                cameras[sensor.name] = output
            else:
                sensors[sensor.name] = output
        vehicles[vehicle.name] = {
            "VehicleType": vehicle.vehicle_type,
            "AutoCreate": profile.auto_create,
            "AllowAPIAlways": profile.allow_api_always,
            "EnableCollisions": profile.enable_collisions,
            "EnableCollisionPassthrough": profile.enable_collision_passthrough,
            "Yaw": profile.yaw_degrees,
            "Cameras": cameras,
            "Sensors": sensors,
        }
    return {
        "SettingsVersion": profile.settings_version,
        "SimMode": profile.sim_mode,
        "ClockSpeed": profile.clock_speed,
        "EnableRpc": profile.enable_rpc,
        "LocalHostIp": config.endpoint.host,
        "ApiServerPort": config.endpoint.port,
        "Vehicles": vehicles,
    }


def _sha256_text(value: str, context: str) -> str:
    text = _text(value, context).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise Phase3ContractError(f"{context} must be 64 hexadecimal digits")
    return text


def render_clearance_request(
    config: FactoryCityConfig,
    config_sha256: str,
    profile: CosysRuntimeProfile,
    runtime_profile_sha256: str,
    source_world_sha256: str,
    probe_adapter_sha256: str,
) -> bytes:
    """Render deterministic lattice and transform inputs for Unreal scene tracing."""

    _, _, candidates = generate_lattice_candidates(
        config.launch_area, config.fleet.expected_count
    )
    payload = {
        "schema": CLEARANCE_REQUEST_SCHEMA_ID,
        "provider_id": config.launch_area.ground_clearance_probe,
        "configuration_sha256": _sha256_text(
            config_sha256, "configuration_sha256"
        ),
        "runtime_profile_sha256": _sha256_text(
            runtime_profile_sha256, "runtime_profile_sha256"
        ),
        "probe_adapter_sha256": _sha256_text(
            probe_adapter_sha256, "probe_adapter_sha256"
        ),
        "world": {
            "world_id": config.world.world_id,
            "source_world_sha256": _sha256_text(
                source_world_sha256, "source_world_sha256"
            ),
            "ned_origin_id": config.world.ned_origin_id,
            "coordinate_frame": config.world.coordinate_frame,
        },
        "coordinate_transform": {
            "origin_unreal_units": {
                "x": profile.origin_unreal_units.x,
                "y": profile.origin_unreal_units.y,
                "z": profile.origin_unreal_units.z,
            },
            "world_to_meters": profile.world_to_meters,
            "ned_to_unreal_axis_sign": {
                "x": profile.ned_to_unreal_axis_sign.x,
                "y": profile.ned_to_unreal_axis_sign.y,
                "z": profile.ned_to_unreal_axis_sign.z,
            },
        },
        "probe_contract": {
            "trace_channel": profile.trace_channel,
            "trace_complex": profile.trace_complex,
            "minimum_ground_normal_z": profile.minimum_ground_normal_z,
            "takeoff_corridor_start_clearance_m": (
                config.launch_area.takeoff_corridor_start_clearance_m
            ),
            "required_clearance_m": config.limits.collision_clearance_m,
            "ground_trace_start_z_ned_m": config.limits.geofence.minimum.z,
            "ground_trace_end_z_ned_m": config.limits.geofence.maximum.z,
            "corridor_top_z_ned_m": config.limits.altitude_band.minimum_z,
        },
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "row": item.row,
                "column": item.column,
                "x_ned_m": item.x_m,
                "y_ned_m": item.y_m,
            }
            for item in candidates
        ],
    }
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


class RecordedUnrealClearanceProvider:
    """Replay hash-bound, complete Unreal trace evidence through the Phase 2 boundary."""

    def __init__(self, provider_id: str, records: Mapping[str, ClearanceProbeResult]):
        self._provider_id = provider_id
        self._records = dict(records)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def scene_validated(self) -> bool:
        return True

    def probe(self, request: ClearanceRequest) -> ClearanceProbeResult:
        result = self._records.get(request.candidate.candidate_id)
        if result is None:
            raise Phase3ContractError(
                f"missing recorded result for {request.candidate.candidate_id}"
            )
        return result


def load_clearance_result(
    path: str | Path,
    config: FactoryCityConfig,
    config_sha256: str,
    clearance_request_bytes: bytes,
) -> tuple[RecordedUnrealClearanceProvider, Mapping[str, Any], str]:
    """Load live evidence only when hashes, world, and every candidate match."""

    source = Path(path)
    try:
        content = source.read_bytes()
        raw = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase3ContractError(f"cannot load clearance result {source}: {exc}") from exc
    root = _exact(
        raw,
        {
            "schema",
            "status",
            "scene_validated",
            "captured_at_utc",
            "provider_id",
            "configuration_sha256",
            "request_sha256",
            "world",
            "coordinate_transform",
            "probes",
        },
        "clearance_result",
    )
    if root["schema"] != CLEARANCE_RESULT_SCHEMA_ID:
        raise Phase3ContractError("clearance result schema is unsupported")
    if root["status"] != "PASS" or root["scene_validated"] is not True:
        raise Phase3ContractError("clearance result is not scene-validated PASS evidence")
    provider_id = _text(root["provider_id"], "clearance_result.provider_id")
    if provider_id != config.launch_area.ground_clearance_probe:
        raise Phase3ContractError("clearance result provider does not match configuration")
    if root["configuration_sha256"] != _sha256_text(
        config_sha256, "configuration_sha256"
    ):
        raise Phase3ContractError("clearance result configuration hash mismatch")
    expected_request_hash = hashlib.sha256(clearance_request_bytes).hexdigest()
    if root["request_sha256"] != expected_request_hash:
        raise Phase3ContractError("clearance result request hash mismatch")
    world = _object(root["world"], "clearance_result.world")
    if world.get("world_id") != config.world.world_id:
        raise Phase3ContractError("clearance result world does not match configuration")
    probes = root["probes"]
    if not isinstance(probes, list):
        raise Phase3ContractError("clearance_result.probes must be an array")
    _, _, candidates = generate_lattice_candidates(
        config.launch_area, config.fleet.expected_count
    )
    expected = {item.candidate_id: item for item in candidates}
    records: dict[str, ClearanceProbeResult] = {}
    for index, item in enumerate(probes):
        context = f"clearance_result.probes[{index}]"
        probe = _exact(
            item,
            {
                "candidate_id",
                "row",
                "column",
                "x_ned_m",
                "y_ned_m",
                "ground_clear",
                "vertical_corridor_clear",
                "ground_z_ned_m",
                "evidence_id",
                "reason",
                "ground_hit",
                "corridor_hit",
            },
            context,
        )
        candidate_id = _text(probe["candidate_id"], f"{context}.candidate_id")
        candidate = expected.get(candidate_id)
        if candidate is None or candidate_id in records:
            raise Phase3ContractError(f"{context} has unexpected or duplicate candidate")
        if (
            probe["row"] != candidate.row
            or probe["column"] != candidate.column
            or not math.isclose(
                _number(probe["x_ned_m"], f"{context}.x_ned_m"), candidate.x_m
            )
            or not math.isclose(
                _number(probe["y_ned_m"], f"{context}.y_ned_m"), candidate.y_m
            )
        ):
            raise Phase3ContractError(f"{context} candidate geometry mismatch")
        ground_clear = _bool(probe["ground_clear"], f"{context}.ground_clear")
        corridor_clear = _bool(
            probe["vertical_corridor_clear"],
            f"{context}.vertical_corridor_clear",
        )
        ground_z_raw = probe["ground_z_ned_m"]
        ground_z = (
            _number(ground_z_raw, f"{context}.ground_z_ned_m")
            if ground_z_raw is not None
            else None
        )
        if ground_clear and ground_z is None:
            raise Phase3ContractError(f"{context} clear ground requires a height")
        reason = probe["reason"]
        if not isinstance(reason, str) or reason != reason.strip():
            raise Phase3ContractError(f"{context}.reason must be trimmed text")
        if (not ground_clear or not corridor_clear) and not reason:
            raise Phase3ContractError(f"{context} rejected probe requires a reason")
        records[candidate_id] = ClearanceProbeResult(
            ground_clear=ground_clear,
            vertical_corridor_clear=corridor_clear,
            ground_z_ned_m=ground_z,
            evidence_id=_text(probe["evidence_id"], f"{context}.evidence_id"),
            reason=reason,
        )
    if set(records) != set(expected):
        raise Phase3ContractError("clearance result does not cover every lattice candidate")
    provider = RecordedUnrealClearanceProvider(provider_id, records)
    return provider, root, hashlib.sha256(content).hexdigest()
