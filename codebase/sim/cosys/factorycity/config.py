"""Strict, configuration-only contracts for the FactoryCity fleet.

This module contains no scenario roster, coordinates, network endpoint, limit, timeout,
or lifecycle choice.  It accepts those values only from a complete configuration and
rejects the document before RPC creation when any contract is missing or inconsistent.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence


CONFIG_SCHEMA_ID = "veriswarm.factorycity.fleet_config.v1"
SUPPORTED_STATUSES = frozenset(
    {"TEMPLATE_NOT_SCENE_VALIDATED", "CANDIDATE", "ACCEPTED"}
)
SUPPORTED_FRAMES = frozenset({"NED_METRES"})
SUPPORTED_ARMING_POLICIES = frozenset({"staggered", "simultaneous"})
SUPPORTED_FAILURE_POLICIES = frozenset({"abort_all", "isolate_failed_vehicle"})
SUPPORTED_TAKEOFF_MODES = frozenset({"staggered", "simultaneous"})
SUPPORTED_LANDING_MODES = frozenset({"staggered", "simultaneous"})
SUPPORTED_ABORT_ACTIONS = frozenset({"hover_then_land", "land_immediately"})
SUPPORTED_API_CONTROL_RELEASE_POLICIES = frozenset(
    {"always_release", "retain_until_process_exit"}
)
SUPPORTED_DISARM_POLICIES = frozenset({"after_landing_verification"})
SUPPORTED_RESET_POLICIES = frozenset({"rpc_reset", "restart_simulator"})
SUPPORTED_GENERATORS = frozenset({"maxmin_lattice"})
SUPPORTED_ALTITUDE_REFERENCES = frozenset({"ground_surface_ned"})
SUPPORTED_SENSOR_TYPES = frozenset(
    {
        "rgb_camera",
        "depth_camera",
        "lidar",
        "imu",
        "barometer",
        "magnetometer",
        "gnss",
    }
)
REQUIRED_STAGE_TIMEOUTS = frozenset(
    {
        "connect",
        "verify",
        "api_control",
        "arm",
        "takeoff",
        "hover",
        "command",
        "land",
        "cleanup",
        "reset",
    }
)
REQUIRED_NAVIGATION_SENSOR_TYPES = frozenset(
    {"rgb_camera", "imu", "barometer", "magnetometer", "gnss"}
)


class ConfigurationError(ValueError):
    """Raised before RPC creation when a fleet contract is unsafe or incomplete."""


@dataclass(frozen=True)
class Vector2:
    x: float
    y: float


@dataclass(frozen=True)
class Vector3:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class VehicleConfig:
    name: str
    vehicle_type: str
    role: str


@dataclass(frozen=True)
class FleetConfig:
    expected_count: int
    vehicles: tuple[VehicleConfig, ...]
    arming_policy: str
    failure_policy: str

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(vehicle.name for vehicle in self.vehicles)


@dataclass(frozen=True)
class LaunchAreaConfig:
    frame: str
    center: Vector2
    side_length_m: float
    edge_clearance_m: float
    minimum_separation_m: float
    altitude_reference: str
    ground_clearance_probe: str
    formation_generator: str
    deterministic_seed: int

    @property
    def usable_side_length_m(self) -> float:
        return self.side_length_m - (2.0 * self.edge_clearance_m)


@dataclass(frozen=True)
class EndpointConfig:
    host: str
    port: int


@dataclass(frozen=True)
class GeofenceConfig:
    frame: str
    minimum: Vector3
    maximum: Vector3


@dataclass(frozen=True)
class AltitudeBand:
    minimum_z: float
    maximum_z: float


@dataclass(frozen=True)
class StageTimeout:
    stage: str
    seconds: float


@dataclass(frozen=True)
class LimitsConfig:
    geofence: GeofenceConfig
    altitude_band: AltitudeBand
    speed_mps: float
    acceleration_mps2: float
    yaw_rate_dps: float
    command_ttl_ms: int
    minimum_separation_m: float
    collision_clearance_m: float
    state_staleness_ms: int
    sensor_staleness_ms: int
    rpc_timeout_seconds: float
    stage_timeouts: tuple[StageTimeout, ...]

    def timeout_for(self, stage: str) -> float:
        for item in self.stage_timeouts:
            if item.stage == stage:
                return item.seconds
        raise KeyError(stage)


@dataclass(frozen=True)
class VehicleAltitudeOffset:
    vehicle_name: str
    offset_m: float


@dataclass(frozen=True)
class LifecycleConfig:
    takeoff_mode: str
    takeoff_order: tuple[str, ...]
    takeoff_altitude_offsets: tuple[VehicleAltitudeOffset, ...]
    hover_dwell_seconds: float
    abort_action: str
    landing_mode: str
    landing_order: tuple[str, ...]
    disarm_policy: str
    api_control_release_policy: str
    cleanup_timeout_seconds: float
    reset_policy: str
    retry_count: int


@dataclass(frozen=True)
class SensorSetting:
    key: str
    value: bool | int | float | str


@dataclass(frozen=True)
class SensorConfig:
    name: str
    sensor_type: str
    frame: str
    rate_hz: float
    required: bool
    calibration_id: str
    position_body_m: Vector3
    rotation_body_deg: Vector3
    settings: tuple[SensorSetting, ...]


@dataclass(frozen=True)
class SensorProfile:
    name: str
    sensors: tuple[SensorConfig, ...]


@dataclass(frozen=True)
class VehicleSensorProfile:
    vehicle_name: str
    profile: str


@dataclass(frozen=True)
class SensorsConfig:
    profiles: tuple[SensorProfile, ...]
    vehicle_profiles: tuple[VehicleSensorProfile, ...]


@dataclass(frozen=True)
class WorldConfig:
    scenario_id: str
    world_id: str
    coordinate_frame: str
    ned_origin_id: str
    artifact_manifest_path: str
    generated_settings_path: str


@dataclass(frozen=True)
class EvidenceConfig:
    output_dir: str
    large_artifact_store_uri: str
    create_once: bool
    retain_raw_locally: bool


@dataclass(frozen=True)
class FactoryCityConfig:
    schema: str
    configuration_id: str
    status: str
    fleet: FleetConfig
    launch_area: LaunchAreaConfig
    endpoint: EndpointConfig
    limits: LimitsConfig
    lifecycle: LifecycleConfig
    sensors: SensorsConfig
    world: WorldConfig
    evidence: EvidenceConfig


def _object(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{context} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{context} keys must be strings")
    return value


def _exact_fields(
    value: Any, required: Sequence[str] | set[str] | frozenset[str], context: str
) -> Mapping[str, Any]:
    mapping = _object(value, context)
    required_set = set(required)
    present = set(mapping)
    missing = sorted(required_set - present)
    unknown = sorted(present - required_set)
    if missing:
        raise ConfigurationError(
            f"{context} missing required fields: {', '.join(missing)}"
        )
    if unknown:
        raise ConfigurationError(
            f"{context} contains unknown fields: {', '.join(unknown)}"
        )
    return mapping


def _array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{context} must be an array")
    return value


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{context} must be a non-empty string")
    if value != value.strip():
        raise ConfigurationError(f"{context} must not contain outer whitespace")
    return value


def _choice(value: Any, choices: frozenset[str], context: str) -> str:
    selected = _text(value, context)
    if selected not in choices:
        raise ConfigurationError(
            f"{context} must be one of {sorted(choices)!r}, got {selected!r}"
        )
    return selected


def _boolean(value: Any, context: str) -> bool:
    if type(value) is not bool:
        raise ConfigurationError(f"{context} must be a boolean")
    return value


def _finite(value: Any, context: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{context} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigurationError(f"{context} must be finite")
    if positive and number <= 0.0:
        raise ConfigurationError(f"{context} must be greater than zero")
    return number


def _nonnegative(value: Any, context: str) -> float:
    number = _finite(value, context)
    if number < 0.0:
        raise ConfigurationError(f"{context} must be non-negative")
    return number


def _positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{context} must be a positive integer")
    return value


def _nonnegative_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(f"{context} must be a non-negative integer")
    return value


def _vector2(value: Any, context: str) -> Vector2:
    mapping = _exact_fields(value, {"x", "y"}, context)
    return Vector2(
        x=_finite(mapping["x"], f"{context}.x"),
        y=_finite(mapping["y"], f"{context}.y"),
    )


def _vector3(value: Any, context: str) -> Vector3:
    mapping = _exact_fields(value, {"x", "y", "z"}, context)
    return Vector3(
        x=_finite(mapping["x"], f"{context}.x"),
        y=_finite(mapping["y"], f"{context}.y"),
        z=_finite(mapping["z"], f"{context}.z"),
    )


def _portable_path(value: Any, context: str) -> str:
    path = _text(value, context)
    if "\\" in path:
        raise ConfigurationError(f"{context} must use portable forward slashes")
    if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute():
        raise ConfigurationError(f"{context} must be repository-relative")
    parts = PurePosixPath(path).parts
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or str(PurePosixPath(path)) != path
    ):
        raise ConfigurationError(
            f"{context} must be a normalized repository-relative path"
        )
    return path


def _uri(value: Any, context: str) -> str:
    uri = _text(value, context)
    if not re.fullmatch(r"[a-z][a-z0-9+.-]*://[^\s]+", uri):
        raise ConfigurationError(f"{context} must be an explicit URI")
    return uri


def _unique(values: Sequence[str], context: str) -> None:
    folded: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in folded:
            raise ConfigurationError(f"{context} contains duplicate value {value!r}")
        folded.add(key)


def _permutation(values: tuple[str, ...], roster: tuple[str, ...], context: str) -> None:
    if len(values) != len(roster) or set(values) != set(roster):
        raise ConfigurationError(f"{context} must be an exact fleet-roster permutation")


def _parse_fleet(value: Any) -> FleetConfig:
    context = "config.fleet"
    raw = _exact_fields(
        value,
        {"expected_count", "vehicles", "arming_policy", "failure_policy"},
        context,
    )
    vehicles: list[VehicleConfig] = []
    for index, item in enumerate(_array(raw["vehicles"], f"{context}.vehicles")):
        item_context = f"{context}.vehicles[{index}]"
        vehicle = _exact_fields(item, {"name", "type", "role"}, item_context)
        vehicles.append(
            VehicleConfig(
                name=_text(vehicle["name"], f"{item_context}.name"),
                vehicle_type=_text(vehicle["type"], f"{item_context}.type"),
                role=_text(vehicle["role"], f"{item_context}.role"),
            )
        )
    expected_count = _positive_int(raw["expected_count"], f"{context}.expected_count")
    if len(vehicles) != expected_count:
        raise ConfigurationError(
            f"{context}.expected_count does not match the vehicle roster length"
        )
    names = tuple(vehicle.name for vehicle in vehicles)
    _unique(names, f"{context}.vehicles names")
    return FleetConfig(
        expected_count=expected_count,
        vehicles=tuple(vehicles),
        arming_policy=_choice(
            raw["arming_policy"], SUPPORTED_ARMING_POLICIES, f"{context}.arming_policy"
        ),
        failure_policy=_choice(
            raw["failure_policy"], SUPPORTED_FAILURE_POLICIES, f"{context}.failure_policy"
        ),
    )


def _parse_launch_area(value: Any) -> LaunchAreaConfig:
    context = "config.launch_area"
    raw = _exact_fields(
        value,
        {
            "frame",
            "center_ned_m",
            "side_length_m",
            "edge_clearance_m",
            "minimum_separation_m",
            "altitude_reference",
            "ground_clearance_probe",
            "formation_generator",
            "deterministic_seed",
        },
        context,
    )
    side_length = _finite(raw["side_length_m"], f"{context}.side_length_m", positive=True)
    edge_clearance = _nonnegative(
        raw["edge_clearance_m"], f"{context}.edge_clearance_m"
    )
    usable_side = side_length - (2.0 * edge_clearance)
    if usable_side <= 0.0:
        raise ConfigurationError(
            f"{context}.edge_clearance_m leaves no usable launch area"
        )
    minimum_separation = _finite(
        raw["minimum_separation_m"],
        f"{context}.minimum_separation_m",
        positive=True,
    )
    if minimum_separation > math.sqrt(2.0) * usable_side:
        raise ConfigurationError(
            f"{context}.minimum_separation_m exceeds the usable-square diagonal"
        )
    return LaunchAreaConfig(
        frame=_choice(raw["frame"], SUPPORTED_FRAMES, f"{context}.frame"),
        center=_vector2(raw["center_ned_m"], f"{context}.center_ned_m"),
        side_length_m=side_length,
        edge_clearance_m=edge_clearance,
        minimum_separation_m=minimum_separation,
        altitude_reference=_choice(
            raw["altitude_reference"],
            SUPPORTED_ALTITUDE_REFERENCES,
            f"{context}.altitude_reference",
        ),
        ground_clearance_probe=_text(
            raw["ground_clearance_probe"], f"{context}.ground_clearance_probe"
        ),
        formation_generator=_choice(
            raw["formation_generator"],
            SUPPORTED_GENERATORS,
            f"{context}.formation_generator",
        ),
        deterministic_seed=_nonnegative_int(
            raw["deterministic_seed"], f"{context}.deterministic_seed"
        ),
    )


def _parse_endpoint(value: Any) -> EndpointConfig:
    context = "config.endpoint"
    raw = _exact_fields(value, {"host", "port"}, context)
    port = _positive_int(raw["port"], f"{context}.port")
    if port > 65535:
        raise ConfigurationError(f"{context}.port must be at most 65535")
    return EndpointConfig(host=_text(raw["host"], f"{context}.host"), port=port)


def _parse_limits(value: Any) -> LimitsConfig:
    context = "config.limits"
    raw = _exact_fields(
        value,
        {
            "geofence",
            "altitude_band_ned_m",
            "speed_mps",
            "acceleration_mps2",
            "yaw_rate_dps",
            "command_ttl_ms",
            "minimum_separation_m",
            "collision_clearance_m",
            "state_staleness_ms",
            "sensor_staleness_ms",
            "rpc_timeout_seconds",
            "stage_timeouts_seconds",
        },
        context,
    )
    geofence_raw = _exact_fields(
        raw["geofence"], {"frame", "min", "max"}, f"{context}.geofence"
    )
    geofence = GeofenceConfig(
        frame=_choice(
            geofence_raw["frame"], SUPPORTED_FRAMES, f"{context}.geofence.frame"
        ),
        minimum=_vector3(geofence_raw["min"], f"{context}.geofence.min"),
        maximum=_vector3(geofence_raw["max"], f"{context}.geofence.max"),
    )
    if not (
        geofence.minimum.x < geofence.maximum.x
        and geofence.minimum.y < geofence.maximum.y
        and geofence.minimum.z < geofence.maximum.z
    ):
        raise ConfigurationError(
            f"{context}.geofence.min must be strictly below max on every axis"
        )
    altitude_raw = _exact_fields(
        raw["altitude_band_ned_m"], {"min_z", "max_z"}, f"{context}.altitude_band_ned_m"
    )
    altitude = AltitudeBand(
        minimum_z=_finite(
            altitude_raw["min_z"], f"{context}.altitude_band_ned_m.min_z"
        ),
        maximum_z=_finite(
            altitude_raw["max_z"], f"{context}.altitude_band_ned_m.max_z"
        ),
    )
    if altitude.minimum_z >= altitude.maximum_z:
        raise ConfigurationError(
            f"{context}.altitude_band_ned_m.min_z must be less than max_z"
        )
    if not (
        geofence.minimum.z <= altitude.minimum_z
        and altitude.maximum_z <= geofence.maximum.z
    ):
        raise ConfigurationError(
            f"{context}.altitude_band_ned_m must lie inside the geofence"
        )
    timeout_raw = _exact_fields(
        raw["stage_timeouts_seconds"],
        REQUIRED_STAGE_TIMEOUTS,
        f"{context}.stage_timeouts_seconds",
    )
    stage_timeouts = tuple(
        StageTimeout(
            stage=name,
            seconds=_finite(
                timeout_raw[name],
                f"{context}.stage_timeouts_seconds.{name}",
                positive=True,
            ),
        )
        for name in sorted(REQUIRED_STAGE_TIMEOUTS)
    )
    return LimitsConfig(
        geofence=geofence,
        altitude_band=altitude,
        speed_mps=_finite(raw["speed_mps"], f"{context}.speed_mps", positive=True),
        acceleration_mps2=_finite(
            raw["acceleration_mps2"], f"{context}.acceleration_mps2", positive=True
        ),
        yaw_rate_dps=_finite(
            raw["yaw_rate_dps"], f"{context}.yaw_rate_dps", positive=True
        ),
        command_ttl_ms=_positive_int(raw["command_ttl_ms"], f"{context}.command_ttl_ms"),
        minimum_separation_m=_finite(
            raw["minimum_separation_m"],
            f"{context}.minimum_separation_m",
            positive=True,
        ),
        collision_clearance_m=_finite(
            raw["collision_clearance_m"],
            f"{context}.collision_clearance_m",
            positive=True,
        ),
        state_staleness_ms=_positive_int(
            raw["state_staleness_ms"], f"{context}.state_staleness_ms"
        ),
        sensor_staleness_ms=_positive_int(
            raw["sensor_staleness_ms"], f"{context}.sensor_staleness_ms"
        ),
        rpc_timeout_seconds=_finite(
            raw["rpc_timeout_seconds"],
            f"{context}.rpc_timeout_seconds",
            positive=True,
        ),
        stage_timeouts=stage_timeouts,
    )


def _name_array(value: Any, context: str) -> tuple[str, ...]:
    names = tuple(
        _text(item, f"{context}[{index}]")
        for index, item in enumerate(_array(value, context))
    )
    _unique(names, context)
    return names


def _parse_lifecycle(value: Any, fleet: FleetConfig) -> LifecycleConfig:
    context = "config.lifecycle"
    raw = _exact_fields(
        value,
        {
            "takeoff_mode",
            "takeoff_order",
            "takeoff_altitude_offsets_m",
            "hover_dwell_seconds",
            "abort_action",
            "landing_mode",
            "landing_order",
            "disarm_policy",
            "api_control_release_policy",
            "cleanup_timeout_seconds",
            "reset_policy",
            "retry_count",
        },
        context,
    )
    takeoff_order = _name_array(raw["takeoff_order"], f"{context}.takeoff_order")
    landing_order = _name_array(raw["landing_order"], f"{context}.landing_order")
    _permutation(takeoff_order, fleet.names, f"{context}.takeoff_order")
    _permutation(landing_order, fleet.names, f"{context}.landing_order")
    offsets: list[VehicleAltitudeOffset] = []
    for index, item in enumerate(
        _array(raw["takeoff_altitude_offsets_m"], f"{context}.takeoff_altitude_offsets_m")
    ):
        item_context = f"{context}.takeoff_altitude_offsets_m[{index}]"
        offset = _exact_fields(item, {"vehicle_name", "offset_m"}, item_context)
        offsets.append(
            VehicleAltitudeOffset(
                vehicle_name=_text(offset["vehicle_name"], f"{item_context}.vehicle_name"),
                offset_m=_finite(offset["offset_m"], f"{item_context}.offset_m"),
            )
        )
    offset_names = tuple(item.vehicle_name for item in offsets)
    _unique(offset_names, f"{context}.takeoff_altitude_offsets_m vehicle names")
    _permutation(
        offset_names, fleet.names, f"{context}.takeoff_altitude_offsets_m vehicle names"
    )
    return LifecycleConfig(
        takeoff_mode=_choice(
            raw["takeoff_mode"], SUPPORTED_TAKEOFF_MODES, f"{context}.takeoff_mode"
        ),
        takeoff_order=takeoff_order,
        takeoff_altitude_offsets=tuple(offsets),
        hover_dwell_seconds=_finite(
            raw["hover_dwell_seconds"], f"{context}.hover_dwell_seconds", positive=True
        ),
        abort_action=_choice(
            raw["abort_action"], SUPPORTED_ABORT_ACTIONS, f"{context}.abort_action"
        ),
        landing_mode=_choice(
            raw["landing_mode"], SUPPORTED_LANDING_MODES, f"{context}.landing_mode"
        ),
        landing_order=landing_order,
        disarm_policy=_choice(
            raw["disarm_policy"],
            SUPPORTED_DISARM_POLICIES,
            f"{context}.disarm_policy",
        ),
        api_control_release_policy=_choice(
            raw["api_control_release_policy"],
            SUPPORTED_API_CONTROL_RELEASE_POLICIES,
            f"{context}.api_control_release_policy",
        ),
        cleanup_timeout_seconds=_finite(
            raw["cleanup_timeout_seconds"],
            f"{context}.cleanup_timeout_seconds",
            positive=True,
        ),
        reset_policy=_choice(
            raw["reset_policy"], SUPPORTED_RESET_POLICIES, f"{context}.reset_policy"
        ),
        retry_count=_nonnegative_int(raw["retry_count"], f"{context}.retry_count"),
    )


def _setting_scalar(value: Any, context: str) -> bool | int | float | str:
    if type(value) is bool:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigurationError(f"{context} must be finite")
        return value
    if isinstance(value, str) and value and value == value.strip():
        return value
    raise ConfigurationError(f"{context} must be a JSON scalar")


def _validate_sensor_settings(sensor_type: str, settings: Mapping[str, Any], context: str) -> None:
    if sensor_type in {"rgb_camera", "depth_camera"}:
        expected = {"width", "height", "fov_degrees"}
        checked = _exact_fields(settings, expected, context)
        _positive_int(checked["width"], f"{context}.width")
        _positive_int(checked["height"], f"{context}.height")
        fov = _finite(checked["fov_degrees"], f"{context}.fov_degrees", positive=True)
        if fov > 180.0:
            raise ConfigurationError(f"{context}.fov_degrees must be at most 180")
        return
    if sensor_type == "lidar":
        expected = {
            "channels",
            "points_per_second",
            "rotation_frequency_hz",
            "vertical_fov_upper_degrees",
            "vertical_fov_lower_degrees",
        }
        checked = _exact_fields(settings, expected, context)
        _positive_int(checked["channels"], f"{context}.channels")
        _positive_int(checked["points_per_second"], f"{context}.points_per_second")
        _finite(
            checked["rotation_frequency_hz"],
            f"{context}.rotation_frequency_hz",
            positive=True,
        )
        upper = _finite(
            checked["vertical_fov_upper_degrees"],
            f"{context}.vertical_fov_upper_degrees",
        )
        lower = _finite(
            checked["vertical_fov_lower_degrees"],
            f"{context}.vertical_fov_lower_degrees",
        )
        if lower >= upper:
            raise ConfigurationError(
                f"{context}.vertical_fov_lower_degrees must be below upper"
            )
        return
    if settings:
        raise ConfigurationError(f"{context} must be empty for sensor type {sensor_type!r}")


def _parse_sensor(value: Any, context: str) -> SensorConfig:
    raw = _exact_fields(
        value,
        {
            "name",
            "type",
            "frame",
            "rate_hz",
            "required",
            "calibration_id",
            "position_body_m",
            "rotation_body_deg",
            "settings",
        },
        context,
    )
    sensor_type = _choice(raw["type"], SUPPORTED_SENSOR_TYPES, f"{context}.type")
    settings_raw = _object(raw["settings"], f"{context}.settings")
    _validate_sensor_settings(sensor_type, settings_raw, f"{context}.settings")
    settings = tuple(
        SensorSetting(
            key=key,
            value=_setting_scalar(settings_raw[key], f"{context}.settings.{key}"),
        )
        for key in sorted(settings_raw)
    )
    return SensorConfig(
        name=_text(raw["name"], f"{context}.name"),
        sensor_type=sensor_type,
        frame=_text(raw["frame"], f"{context}.frame"),
        rate_hz=_finite(raw["rate_hz"], f"{context}.rate_hz", positive=True),
        required=_boolean(raw["required"], f"{context}.required"),
        calibration_id=_text(raw["calibration_id"], f"{context}.calibration_id"),
        position_body_m=_vector3(raw["position_body_m"], f"{context}.position_body_m"),
        rotation_body_deg=_vector3(
            raw["rotation_body_deg"], f"{context}.rotation_body_deg"
        ),
        settings=settings,
    )


def _parse_sensors(value: Any, fleet: FleetConfig) -> SensorsConfig:
    context = "config.sensors"
    raw = _exact_fields(value, {"profiles", "vehicle_profiles"}, context)
    profiles: list[SensorProfile] = []
    for profile_index, item in enumerate(_array(raw["profiles"], f"{context}.profiles")):
        item_context = f"{context}.profiles[{profile_index}]"
        profile_raw = _exact_fields(item, {"name", "sensors"}, item_context)
        sensors = tuple(
            _parse_sensor(sensor, f"{item_context}.sensors[{sensor_index}]")
            for sensor_index, sensor in enumerate(
                _array(profile_raw["sensors"], f"{item_context}.sensors")
            )
        )
        if not sensors:
            raise ConfigurationError(f"{item_context}.sensors must not be empty")
        sensor_names = tuple(sensor.name for sensor in sensors)
        _unique(sensor_names, f"{item_context}.sensors names")
        required_types = {
            sensor.sensor_type for sensor in sensors if sensor.required
        }
        missing_navigation = REQUIRED_NAVIGATION_SENSOR_TYPES - required_types
        if missing_navigation:
            raise ConfigurationError(
                f"{item_context} missing required sensor types: "
                f"{', '.join(sorted(missing_navigation))}"
            )
        if not ({"depth_camera", "lidar"} & required_types):
            raise ConfigurationError(
                f"{item_context} requires depth_camera or lidar clearance sensing"
            )
        profiles.append(
            SensorProfile(
                name=_text(profile_raw["name"], f"{item_context}.name"),
                sensors=sensors,
            )
        )
    if not profiles:
        raise ConfigurationError(f"{context}.profiles must not be empty")
    profile_names = tuple(profile.name for profile in profiles)
    _unique(profile_names, f"{context}.profiles names")
    available_profiles = set(profile_names)
    assignments: list[VehicleSensorProfile] = []
    for index, item in enumerate(
        _array(raw["vehicle_profiles"], f"{context}.vehicle_profiles")
    ):
        item_context = f"{context}.vehicle_profiles[{index}]"
        assignment = _exact_fields(item, {"vehicle_name", "profile"}, item_context)
        profile = _text(assignment["profile"], f"{item_context}.profile")
        if profile not in available_profiles:
            raise ConfigurationError(f"{item_context}.profile refers to an unknown profile")
        assignments.append(
            VehicleSensorProfile(
                vehicle_name=_text(
                    assignment["vehicle_name"], f"{item_context}.vehicle_name"
                ),
                profile=profile,
            )
        )
    assignment_names = tuple(item.vehicle_name for item in assignments)
    _unique(assignment_names, f"{context}.vehicle_profiles vehicle names")
    _permutation(
        assignment_names, fleet.names, f"{context}.vehicle_profiles vehicle names"
    )
    return SensorsConfig(
        profiles=tuple(profiles), vehicle_profiles=tuple(assignments)
    )


def _parse_world(value: Any) -> WorldConfig:
    context = "config.world"
    raw = _exact_fields(
        value,
        {
            "scenario_id",
            "world_id",
            "coordinate_frame",
            "ned_origin_id",
            "artifact_manifest_path",
            "generated_settings_path",
        },
        context,
    )
    return WorldConfig(
        scenario_id=_text(raw["scenario_id"], f"{context}.scenario_id"),
        world_id=_text(raw["world_id"], f"{context}.world_id"),
        coordinate_frame=_choice(
            raw["coordinate_frame"], SUPPORTED_FRAMES, f"{context}.coordinate_frame"
        ),
        ned_origin_id=_text(raw["ned_origin_id"], f"{context}.ned_origin_id"),
        artifact_manifest_path=_portable_path(
            raw["artifact_manifest_path"], f"{context}.artifact_manifest_path"
        ),
        generated_settings_path=_portable_path(
            raw["generated_settings_path"], f"{context}.generated_settings_path"
        ),
    )


def _parse_evidence(value: Any) -> EvidenceConfig:
    context = "config.evidence"
    raw = _exact_fields(
        value,
        {"output_dir", "large_artifact_store_uri", "create_once", "retain_raw_locally"},
        context,
    )
    create_once = _boolean(raw["create_once"], f"{context}.create_once")
    if not create_once:
        raise ConfigurationError(f"{context}.create_once must be true")
    return EvidenceConfig(
        output_dir=_portable_path(raw["output_dir"], f"{context}.output_dir"),
        large_artifact_store_uri=_uri(
            raw["large_artifact_store_uri"], f"{context}.large_artifact_store_uri"
        ),
        create_once=create_once,
        retain_raw_locally=_boolean(
            raw["retain_raw_locally"], f"{context}.retain_raw_locally"
        ),
    )


def validate_config(raw: Mapping[str, Any]) -> FactoryCityConfig:
    """Validate a complete FactoryCity fleet contract without operational defaults."""

    root = _exact_fields(
        raw,
        {
            "schema",
            "configuration_id",
            "status",
            "fleet",
            "launch_area",
            "endpoint",
            "limits",
            "lifecycle",
            "sensors",
            "world",
            "evidence",
        },
        "config",
    )
    schema = _text(root["schema"], "config.schema")
    if schema != CONFIG_SCHEMA_ID:
        raise ConfigurationError(
            f"config.schema must be {CONFIG_SCHEMA_ID!r}, got {schema!r}"
        )
    fleet = _parse_fleet(root["fleet"])
    launch_area = _parse_launch_area(root["launch_area"])
    endpoint = _parse_endpoint(root["endpoint"])
    limits = _parse_limits(root["limits"])
    lifecycle = _parse_lifecycle(root["lifecycle"], fleet)
    sensors = _parse_sensors(root["sensors"], fleet)
    world = _parse_world(root["world"])
    evidence = _parse_evidence(root["evidence"])

    if launch_area.frame != limits.geofence.frame:
        raise ConfigurationError(
            "config.launch_area.frame must match config.limits.geofence.frame"
        )
    half_side = launch_area.side_length_m / 2.0
    if not (
        limits.geofence.minimum.x <= launch_area.center.x - half_side
        and launch_area.center.x + half_side <= limits.geofence.maximum.x
        and limits.geofence.minimum.y <= launch_area.center.y - half_side
        and launch_area.center.y + half_side <= limits.geofence.maximum.y
    ):
        raise ConfigurationError(
            "config.launch_area square must lie inside the horizontal geofence"
        )
    if launch_area.frame != world.coordinate_frame:
        raise ConfigurationError(
            "config.launch_area.frame must match config.world.coordinate_frame"
        )
    if not math.isclose(
        launch_area.minimum_separation_m,
        limits.minimum_separation_m,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ConfigurationError(
            "launch-area and runtime minimum-separation values must match"
        )
    if not math.isclose(
        lifecycle.cleanup_timeout_seconds,
        limits.timeout_for("cleanup"),
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ConfigurationError(
            "lifecycle cleanup timeout must match limits stage cleanup timeout"
        )
    if limits.rpc_timeout_seconds > limits.timeout_for("connect"):
        raise ConfigurationError(
            "RPC timeout must not exceed the connect-stage timeout"
        )

    return FactoryCityConfig(
        schema=schema,
        configuration_id=_text(root["configuration_id"], "config.configuration_id"),
        status=_choice(root["status"], SUPPORTED_STATUSES, "config.status"),
        fleet=fleet,
        launch_area=launch_area,
        endpoint=endpoint,
        limits=limits,
        lifecycle=lifecycle,
        sensors=sensors,
        world=world,
        evidence=evidence,
    )


def load_config(path: str | Path) -> tuple[FactoryCityConfig, str]:
    """Load a UTF-8 JSON configuration and return it with its byte-level SHA-256."""

    source = Path(path)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise ConfigurationError(f"cannot read configuration {source}: {exc}") from exc
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"configuration {source} is not valid UTF-8 JSON") from exc
    return validate_config(_object(raw, "config")), hashlib.sha256(data).hexdigest()
