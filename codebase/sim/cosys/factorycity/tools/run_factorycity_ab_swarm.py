"""Fly five CoSys drones on one straight Point_A-to-Point_B route."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import time
from pathlib import Path

import cosysairsim


SCHEMA = "veriswarm.factorycity.ab_mission.v1"
RUN_SCHEMA = "veriswarm.factorycity.ab_mission_run.v1"


def _positive(data: dict[str, object], name: str) -> float:
    value = float(data[name])
    if not math.isfinite(value) or value <= 0.0:
        raise RuntimeError(f"{name} must be positive and finite")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_config(path: Path, map_file: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise RuntimeError("unsupported A-to-B mission schema")
    if not bool(data.get("development_only")):
        raise RuntimeError("this controller currently accepts development missions only")
    expected_map_hash = str(data["source_map"]["sha256"]).casefold()
    observed_map_hash = _sha256(map_file).casefold()
    if observed_map_hash != expected_map_hash:
        raise RuntimeError(
            f"mission/map hash mismatch: expected {expected_map_hash}, got {observed_map_hash}"
        )
    vehicles = data["vehicles"]
    if not isinstance(vehicles, list) or len(vehicles) != 5:
        raise RuntimeError("mission requires exactly five vehicles")
    if len(vehicles) != len(set(vehicles)):
        raise RuntimeError("vehicle names must be unique")
    offsets = data["formation_offsets_ned_m"]
    if set(offsets) != set(vehicles):
        raise RuntimeError("formation offsets must match the vehicle roster")
    for offset in offsets.values():
        if not isinstance(offset, list) or len(offset) != 2:
            raise RuntimeError("every formation offset must contain X and Y")
        if not all(math.isfinite(float(value)) for value in offset):
            raise RuntimeError("formation offsets must be finite")
    route = data["route"]
    limits = data["limits"]
    rpc = data["rpc"]
    flood = data["flood"]
    landing = data["landing"]
    for field in ("timeout_seconds",):
        _positive(rpc, field)
    for field in (
        "horizontal_distance_m",
        "horizontal_velocity_mps",
        "control_step_seconds",
        "arrival_correction_velocity_mps",
        "arrival_convergence_timeout_seconds",
        "arrival_tolerance_m",
        "maximum_lateral_error_m",
    ):
        _positive(route, field)
    for field in (
        "takeoff_timeout_seconds",
        "command_timeout_seconds",
        "vertical_velocity_mps",
        "minimum_pairwise_separation_m",
        "post_takeoff_settle_seconds",
        "maximum_route_runtime_seconds",
    ):
        _positive(limits, field)
    for field in (
        "desired_clearance_above_water_m",
        "minimum_clearance_above_water_m",
        "landing_surface_minimum_clearance_above_water_m",
        "rise_duration_seconds",
        "update_period_seconds",
    ):
        _positive(flood, field)
    for field in (
        "controlled_descent_velocity_mps",
        "controlled_descent_timeout_seconds",
        "controlled_descent_poll_seconds",
        "descent_target_below_surface_m",
        "confirmation_timeout_seconds",
        "contact_settle_seconds",
        "minimum_contact_center_height_above_surface_m",
        "maximum_contact_center_height_above_surface_m",
        "maximum_touchdown_linear_speed_mps",
        "maximum_touchdown_angular_speed_rps",
        "maximum_touchdown_roll_pitch_deg",
        "stationary_linear_speed_tolerance_mps",
        "stationary_angular_speed_tolerance_rps",
        "maximum_contact_penetration_depth_m",
        "landing_zone_radius_m",
        "post_disarm_stable_contact_seconds",
    ):
        _positive(landing, field)
    collision_object_name = landing.get("collision_object_name")
    if not isinstance(collision_object_name, str) or not collision_object_name.strip():
        raise RuntimeError("landing collision_object_name must be a non-empty string")
    if float(landing["minimum_contact_center_height_above_surface_m"]) >= float(
        landing["maximum_contact_center_height_above_surface_m"]
    ):
        raise RuntimeError("landing contact height bounds are invalid")
    if float(landing["controlled_descent_poll_seconds"]) >= float(
        landing["controlled_descent_timeout_seconds"]
    ):
        raise RuntimeError("landing poll period must be below descent timeout")
    if float(landing["contact_settle_seconds"]) > float(
        landing["confirmation_timeout_seconds"]
    ):
        raise RuntimeError("landing contact settle exceeds confirmation timeout")
    deviation = landing.get("landed_state_deviation")
    if not isinstance(deviation, dict):
        raise RuntimeError("landing landed_state_deviation must be an object")
    if deviation.get("id") != "QB-LANDED-STATE-001":
        raise RuntimeError("unsupported landed-state deviation")
    if not isinstance(deviation.get("approved"), bool):
        raise RuntimeError("landed-state deviation approved must be boolean")
    if deviation.get("approved"):
        for field in ("approved_by", "scope", "success_label"):
            if not isinstance(deviation.get(field), str) or not deviation[field].strip():
                raise RuntimeError(f"landed-state deviation {field} is required")
    delta = tuple(float(value) for value in route["target_delta_ned_m"])
    if len(delta) != 2 or not all(math.isfinite(value) for value in delta):
        raise RuntimeError("target_delta_ned_m must contain two finite values")
    derived_distance = math.hypot(*delta)
    if not math.isclose(
        derived_distance, float(route["horizontal_distance_m"]), abs_tol=1e-6
    ):
        raise RuntimeError("configured route distance does not match target delta")
    transform = data["coordinate_transform"]
    origin_z = float(transform["point_a_unreal_cm"][2])
    scale = float(transform["world_to_meters"])
    z_sign = float(transform["ned_to_unreal_axis_sign"]["z"])
    max_water_z_ned = (
        (float(flood["maximum_water_world_z_cm"]) - origin_z) / scale
    ) / z_sign
    if not math.isclose(
        max_water_z_ned, float(flood["peak_water_z_ned_m"]), abs_tol=1e-6
    ):
        raise RuntimeError("configured peak-water NED height does not match transform")
    if float(flood["update_period_seconds"]) > float(flood["rise_duration_seconds"]):
        raise RuntimeError("water update period cannot exceed rise duration")
    cruise_z = float(route["cruise_z_ned_m"])
    maximum_flood_clearance = max_water_z_ned - cruise_z
    if maximum_flood_clearance < float(flood["desired_clearance_above_water_m"]):
        raise RuntimeError("cruise altitude does not satisfy maximum flood clearance")
    landing_surface_z_ned = (
        (float(transform["point_b_landing_surface_world_z_cm"]) - origin_z) / scale
    ) / z_sign
    landing_clearance = max_water_z_ned - landing_surface_z_ned
    if landing_clearance < float(
        flood["landing_surface_minimum_clearance_above_water_m"]
    ):
        raise RuntimeError("Point_B landing surface is unsafe at maximum flood")
    if data["collision_policy"]["monitor_from_state"] != "EN_ROUTE":
        raise RuntimeError("collision monitoring must start only after cruise is reached")
    if data["collision_policy"].get("ignore_takeoff_collisions") is not True:
        raise RuntimeError("takeoff collision exception must be explicit")
    if data["collision_policy"].get("landing_contact_policy") != (
        "allow_only_new_configured_surface_contact_within_envelope"
    ):
        raise RuntimeError("landing collision policy must be fail-closed")
    if data["collision_policy"]["terminal_behavior"] != "disarm_release_and_exclude":
        raise RuntimeError("unsupported collision terminal behavior")
    weather = data.get("weather", {})
    if not isinstance(weather, dict):
        raise RuntimeError("weather must be an object")
    for name, raw in weather.items():
        if not hasattr(cosysairsim.WeatherParameter, name):
            raise RuntimeError(f"unsupported weather parameter {name}")
        value = float(raw)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise RuntimeError(f"weather {name} must be within [0, 1]")
    return data


def _resolve_water_object(layer_result: Path, actor_id: str) -> str:
    data = json.loads(layer_result.read_text(encoding="utf-8"))
    if data.get("status") != "PASS":
        raise RuntimeError("disaster layer result is not PASS")
    matches = [item for item in data.get("created", []) if item.get("id") == actor_id]
    if len(matches) != 1 or not matches[0].get("object_name"):
        raise RuntimeError(f"layer result does not identify one {actor_id!r} actor")
    return str(matches[0]["object_name"])


def _finite(values: tuple[float, ...], description: str) -> None:
    if not all(math.isfinite(float(value)) for value in values):
        raise RuntimeError(f"{description} contains a non-finite value")


def _local_xy(client: object, vehicle: str) -> tuple[float, float]:
    state = client.getMultirotorState(vehicle_name=vehicle)
    position = state.kinematics_estimated.position
    result = (float(position.x_val), float(position.y_val))
    _finite(result, f"{vehicle} local position")
    return result


def _world_positions(
    client: object, vehicles: tuple[str, ...]
) -> dict[str, tuple[float, float, float]]:
    positions = {}
    for vehicle in vehicles:
        pose = client.simGetObjectPose(vehicle, ned=True)
        position = pose.position
        values = (
            float(position.x_val),
            float(position.y_val),
            float(position.z_val),
        )
        _finite(values, f"{vehicle} world position")
        positions[vehicle] = values
    return positions


def _minimum_pairwise(
    positions: dict[str, tuple[float, float, float]]
) -> float:
    names = tuple(positions)
    values = [
        math.dist(positions[left], positions[right])
        for index, left in enumerate(names)
        for right in names[index + 1 :]
    ]
    return min(values) if values else math.inf


def _join_all(futures: list[object]) -> None:
    for future in futures:
        future.join()


def _copy_pose(pose: object, z_ned_m: float) -> object:
    return cosysairsim.Pose(
        cosysairsim.Vector3r(
            float(pose.position.x_val),
            float(pose.position.y_val),
            float(z_ned_m),
        ),
        cosysairsim.Quaternionr(
            float(pose.orientation.x_val),
            float(pose.orientation.y_val),
            float(pose.orientation.z_val),
            float(pose.orientation.w_val),
        ),
    )


def _raise_water_to_peak(
    client: object,
    water_name: str,
    vehicles: tuple[str, ...],
    route: dict[str, object],
    flood: dict[str, object],
    limits: dict[str, object],
    events: list[dict[str, object]],
) -> float:
    initial = client.simGetObjectPose(water_name, ned=True)
    initial_z = float(initial.position.z_val)
    peak_z = float(flood["peak_water_z_ned_m"])
    if initial_z < peak_z - 0.05:
        raise RuntimeError(
            f"water is already above configured peak: {initial_z:.3f} < {peak_z:.3f}"
        )
    if not bool(flood["manage_water_to_peak"]):
        return initial_z
    duration = float(flood["rise_duration_seconds"])
    update = float(flood["update_period_seconds"])
    steps = max(1, math.ceil(duration / update))
    started = time.monotonic()
    for index in range(1, steps + 1):
        fraction = index / steps
        requested_z = initial_z + (peak_z - initial_z) * fraction
        if not client.simSetObjectPose(
            water_name, _copy_pose(initial, requested_z), teleport=True
        ):
            raise RuntimeError(f"failed to move water actor {water_name}")
        observed = client.simGetObjectPose(water_name, ned=True)
        observed_z = float(observed.position.z_val)
        # During the rise, climb with the water while never exceeding the configured
        # cruise ceiling (negative NED Z means higher altitude).
        target_z = max(
            float(route["cruise_z_ned_m"]),
            observed_z - float(flood["desired_clearance_above_water_m"]),
        )
        _join_all(
            [
                client.moveToZAsync(
                    target_z,
                    float(limits["vertical_velocity_mps"]),
                    timeout_sec=float(limits["command_timeout_seconds"]),
                    vehicle_name=vehicle,
                )
                for vehicle in vehicles
            ]
        )
        events.append(
            {
                "state": "FLOOD_RISING",
                "step": index,
                "steps": steps,
                "water_z_ned_m": observed_z,
                "target_z_ned_m": target_z,
            }
        )
        remaining = started + duration * fraction - time.monotonic()
        if remaining > 0.0:
            time.sleep(remaining)
    return float(client.simGetObjectPose(water_name, ned=True).position.z_val)


def _collision_record(client: object, vehicle: str) -> dict[str, object]:
    value = client.simGetCollisionInfo(vehicle_name=vehicle)
    record = {
        "has_collided": bool(value.has_collided),
        "timestamp": int(value.time_stamp),
        "object_name": str(value.object_name),
        "penetration_depth_m": float(value.penetration_depth),
    }
    if record["timestamp"] < 0:
        raise RuntimeError(f"{vehicle} collision timestamp is negative")
    _finite((record["penetration_depth_m"],), f"{vehicle} collision telemetry")
    return record


def _roll_pitch_degrees(orientation: object) -> tuple[float, float]:
    x = float(orientation.x_val)
    y = float(orientation.y_val)
    z = float(orientation.z_val)
    w = float(orientation.w_val)
    _finite((x, y, z, w), "landing orientation")
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm <= 1e-9:
        raise RuntimeError("landing orientation quaternion has zero norm")
    x, y, z, w = (value / norm for value in (x, y, z, w))
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    sin_pitch = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    pitch = math.asin(sin_pitch)
    return math.degrees(roll), math.degrees(pitch)


def _landing_contact_sample(
    client: object,
    vehicle: str,
    landing: dict[str, object],
    landing_surface_z_ned_m: float,
    collision_baseline_timestamp: int,
    target_xy_ned_m: tuple[float, float],
) -> dict[str, object]:
    state = client.getMultirotorState(vehicle_name=vehicle)
    collision = _collision_record(client, vehicle)
    pose = client.simGetObjectPose(vehicle, ned=True)
    world_z_ned_m = float(pose.position.z_val)
    kinematics = state.kinematics_estimated
    local_position = kinematics.position
    linear_velocity = kinematics.linear_velocity
    angular_velocity = kinematics.angular_velocity
    local_xy = (float(local_position.x_val), float(local_position.y_val))
    linear_speed_mps = math.sqrt(
        float(linear_velocity.x_val) ** 2
        + float(linear_velocity.y_val) ** 2
        + float(linear_velocity.z_val) ** 2
    )
    vertical_speed_mps = float(linear_velocity.z_val)
    angular_speed_rps = math.sqrt(
        float(angular_velocity.x_val) ** 2
        + float(angular_velocity.y_val) ** 2
        + float(angular_velocity.z_val) ** 2
    )
    roll_deg, pitch_deg = _roll_pitch_degrees(kinematics.orientation)
    horizontal_error_m = math.hypot(
        local_xy[0] - target_xy_ned_m[0],
        local_xy[1] - target_xy_ned_m[1],
    )
    _finite(
        (
            world_z_ned_m,
            *local_xy,
            linear_speed_mps,
            vertical_speed_mps,
            angular_speed_rps,
            roll_deg,
            pitch_deg,
            horizontal_error_m,
        ),
        f"{vehicle} landing contact telemetry",
    )
    center_height_above_surface_m = landing_surface_z_ned_m - world_z_ned_m
    new_collision = (
        collision["has_collided"]
        and collision["timestamp"] > collision_baseline_timestamp
    )
    new_surface_contact = (
        new_collision
        and collision["object_name"] == landing["collision_object_name"]
    )
    violations = []
    if new_collision and not new_surface_contact:
        violations.append(f"unexpected_collision_object:{collision['object_name']}")
    if new_surface_contact:
        if not (
            float(landing["minimum_contact_center_height_above_surface_m"])
            <= center_height_above_surface_m
            <= float(landing["maximum_contact_center_height_above_surface_m"])
        ):
            violations.append("contact_height_outside_envelope")
        if linear_speed_mps > float(landing["maximum_touchdown_linear_speed_mps"]):
            violations.append("touchdown_linear_speed_exceeded")
        if angular_speed_rps > float(landing["maximum_touchdown_angular_speed_rps"]):
            violations.append("touchdown_angular_speed_exceeded")
        if max(abs(roll_deg), abs(pitch_deg)) > float(
            landing["maximum_touchdown_roll_pitch_deg"]
        ):
            violations.append("touchdown_attitude_exceeded")
        if horizontal_error_m > float(landing["landing_zone_radius_m"]):
            violations.append("landing_zone_radius_exceeded")
        if not 0.0 <= collision["penetration_depth_m"] <= float(
            landing["maximum_contact_penetration_depth_m"]
        ):
            violations.append("contact_penetration_exceeded")
    stationary = (
        linear_speed_mps
        <= float(landing["stationary_linear_speed_tolerance_mps"])
        and angular_speed_rps
        <= float(landing["stationary_angular_speed_tolerance_rps"])
    )
    return {
        "landed_state": int(state.landed_state),
        "local_xy_ned_m": list(local_xy),
        "world_z_ned_m": world_z_ned_m,
        "center_height_above_surface_m": center_height_above_surface_m,
        "horizontal_error_m": horizontal_error_m,
        "linear_speed_mps": linear_speed_mps,
        "vertical_speed_mps": vertical_speed_mps,
        "angular_speed_rps": angular_speed_rps,
        "roll_deg": roll_deg,
        "pitch_deg": pitch_deg,
        "collision": collision,
        "new_collision": new_collision,
        "new_surface_contact": new_surface_contact,
        "contact_envelope_violations": violations,
        "stationary": stationary,
        "stable_contact_candidate": new_surface_contact and not violations and stationary,
    }


def _post_disarm_landing_decision(
    sample: dict[str, object],
    landing: dict[str, object],
    elapsed_seconds: float,
    landed_state_value: int,
) -> tuple[str, bool] | None:
    """Return the terminal landing state only after continuous post-disarm dwell."""
    if not sample["stable_contact_candidate"]:
        raise RuntimeError("lost stable contact after disarm")
    if elapsed_seconds < float(landing["post_disarm_stable_contact_seconds"]):
        return None
    if int(sample["landed_state"]) == landed_state_value:
        return "LANDED", False
    deviation = landing["landed_state_deviation"]
    if not deviation["approved"]:
        raise RuntimeError("landed_state remained stale without approval")
    return "LANDED_STABLE_CONTACT_DEVIATION", True


def _terminate_collision(client: object, vehicle: str) -> None:
    try:
        client.hoverAsync(vehicle_name=vehicle).join()
    except Exception:
        pass
    try:
        client.armDisarm(False, vehicle_name=vehicle)
    except Exception:
        pass
    try:
        client.enableApiControl(False, vehicle_name=vehicle)
    except Exception:
        pass


def _observation(
    client: object,
    active: tuple[str, ...],
    water_z_ned_m: float,
    minimum_separation_m: float,
) -> dict[str, object]:
    positions = _world_positions(client, active)
    minimum_separation = _minimum_pairwise(positions)
    if minimum_separation < minimum_separation_m:
        raise RuntimeError(
            f"formation separation breached: {minimum_separation:.3f} m"
        )
    clearances = {
        name: water_z_ned_m - position[2] for name, position in positions.items()
    }
    return {
        "world_positions_ned_m": positions,
        "minimum_pairwise_separation_m": minimum_separation,
        "clearance_above_water_m": clearances,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--map-file", type=Path, required=True)
    parser.add_argument("--layer-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = _load_config(args.config, args.map_file)
    vehicles = tuple(str(value) for value in config["vehicles"])
    rpc = config["rpc"]
    route = config["route"]
    limits = config["limits"]
    flood = config["flood"]
    landing = config["landing"]
    transform = config["coordinate_transform"]
    landing_surface_z_ned_m = (
        (
            float(transform["point_b_landing_surface_world_z_cm"])
            - float(transform["point_a_unreal_cm"][2])
        )
        / float(transform["world_to_meters"])
    ) / float(transform["ned_to_unreal_axis_sign"]["z"])
    water_name = _resolve_water_object(args.layer_result, str(flood["water_actor_id"]))
    client = cosysairsim.MultirotorClient(
        ip=str(rpc["host"]),
        port=int(rpc["port"]),
        timeout_value=float(rpc["timeout_seconds"]),
    )
    states = {vehicle: "PREFLIGHT" for vehicle in vehicles}
    api_enabled: set[str] = set()
    armed: set[str] = set()
    events: list[dict[str, object]] = []
    status = "FAIL"
    failure = None
    landed_state_deviation_applied = False
    started = time.monotonic()
    weather_enabled = False
    try:
        client.confirmConnection()
        roster = tuple(sorted(client.listVehicles()))
        if roster != tuple(sorted(vehicles)):
            raise RuntimeError(f"unexpected live roster: {roster}")
        if config.get("weather"):
            client.simEnableWeather(True)
            weather_enabled = True
            for name, value in config["weather"].items():
                client.simSetWeatherParameter(
                    getattr(cosysairsim.WeatherParameter, name), float(value)
                )
        for vehicle in vehicles:
            client.enableApiControl(True, vehicle_name=vehicle)
            if not client.isApiControlEnabled(vehicle_name=vehicle):
                raise RuntimeError(f"API control was not enabled for {vehicle}")
            api_enabled.add(vehicle)
            states[vehicle] = "API_CONTROL"
            if not client.armDisarm(True, vehicle_name=vehicle):
                raise RuntimeError(f"failed to arm {vehicle}")
            armed.add(vehicle)
            states[vehicle] = "ARMED"
        _join_all(
            [
                client.takeoffAsync(
                    timeout_sec=float(limits["takeoff_timeout_seconds"]),
                    vehicle_name=vehicle,
                )
                for vehicle in vehicles
            ]
        )
        for vehicle in vehicles:
            states[vehicle] = "TAKEOFF"
        initial_water_z = float(
            client.simGetObjectPose(water_name, ned=True).position.z_val
        )
        initial_clearance_z = max(
            float(route["cruise_z_ned_m"]),
            initial_water_z - float(flood["desired_clearance_above_water_m"]),
        )
        _join_all(
            [
                client.moveToZAsync(
                    initial_clearance_z,
                    float(limits["vertical_velocity_mps"]),
                    timeout_sec=float(limits["command_timeout_seconds"]),
                    vehicle_name=vehicle,
                )
                for vehicle in vehicles
            ]
        )
        for vehicle in vehicles:
            states[vehicle] = "FLOOD_RISING"
        water_z = _raise_water_to_peak(
            client, water_name, vehicles, route, flood, limits, events
        )
        _join_all(
            [
                client.moveToZAsync(
                    float(route["cruise_z_ned_m"]),
                    float(limits["vertical_velocity_mps"]),
                    timeout_sec=float(limits["command_timeout_seconds"]),
                    vehicle_name=vehicle,
                )
                for vehicle in vehicles
            ]
        )
        time.sleep(float(limits["post_takeoff_settle_seconds"]))
        for vehicle in vehicles:
            states[vehicle] = "CRUISE_READY"
        collision_baseline = {
            vehicle: _collision_record(client, vehicle)["timestamp"]
            for vehicle in vehicles
        }
        events.append(
            {
                "state": "CRUISE_READY",
                "collision_monitoring": False,
                **_observation(
                    client,
                    vehicles,
                    water_z,
                    float(limits["minimum_pairwise_separation_m"]),
                ),
            }
        )
        dx, dy = (float(value) for value in route["target_delta_ned_m"])
        distance = float(route["horizontal_distance_m"])
        speed = float(route["horizontal_velocity_mps"])
        vx, vy = speed * dx / distance, speed * dy / distance
        yaw_degrees = math.degrees(math.atan2(dy, dx))
        yaw_mode = cosysairsim.YawMode(
            is_rate=False,
            yaw_or_rate=yaw_degrees if bool(route["yaw_faces_route"]) else 0.0,
        )
        active = set(vehicles)
        collided: dict[str, dict[str, object]] = {}
        commanded = 0.0
        step_index = 0
        route_started = time.monotonic()
        while active and commanded < distance:
            if time.monotonic() - route_started > float(
                limits["maximum_route_runtime_seconds"]
            ):
                raise RuntimeError("maximum route runtime exceeded")
            duration = min(
                float(route["control_step_seconds"]),
                (distance - commanded) / speed,
            )
            current_water = client.simGetObjectPose(water_name, ned=True)
            water_z = float(current_water.position.z_val)
            target_z = min(
                float(route["cruise_z_ned_m"]),
                water_z - float(flood["desired_clearance_above_water_m"]),
            )
            current_active = tuple(sorted(active))
            for vehicle in current_active:
                states[vehicle] = "EN_ROUTE"
            _join_all(
                [
                    client.moveByVelocityZAsync(
                        vx,
                        vy,
                        target_z,
                        duration,
                        drivetrain=cosysairsim.DrivetrainType.MaxDegreeOfFreedom,
                        yaw_mode=yaw_mode,
                        vehicle_name=vehicle,
                    )
                    for vehicle in current_active
                ]
            )
            commanded += speed * duration
            step_index += 1
            new_collisions = []
            collision_samples = {}
            for vehicle in current_active:
                sample = _collision_record(client, vehicle)
                collision_samples[vehicle] = sample
                if sample["has_collided"] and sample["timestamp"] > collision_baseline[vehicle]:
                    states[vehicle] = "DESTROYED_BY_COLLISION"
                    collided[vehicle] = sample
                    active.remove(vehicle)
                    new_collisions.append(vehicle)
                    _terminate_collision(client, vehicle)
                    armed.discard(vehicle)
                    api_enabled.discard(vehicle)
            observation = (
                _observation(
                    client,
                    tuple(sorted(active)),
                    water_z,
                    float(limits["minimum_pairwise_separation_m"]),
                )
                if active
                else {}
            )
            minimum_clearance = min(
                observation.get("clearance_above_water_m", {"none": math.inf}).values()
            )
            if minimum_clearance < float(flood["minimum_clearance_above_water_m"]):
                raise RuntimeError(
                    f"water clearance breached: {minimum_clearance:.3f} m"
                )
            events.append(
                {
                    "state": "EN_ROUTE",
                    "step": step_index,
                    "commanded_distance_m": commanded,
                    "target_z_ned_m": target_z,
                    "water_z_ned_m": water_z,
                    "new_collisions": new_collisions,
                    "collision_samples": collision_samples,
                    **observation,
                }
            )
        # A duration-integrated velocity command is not an odometry guarantee: vehicle
        # acceleration and settling can leave the swarm short of Point_B. Converge using
        # the same velocity direction and measured per-vehicle local NED odometry. A
        # shared moveToPosition target is deliberately avoided because CoSim AirSim
        # interprets that target in the world NED frame and would collapse the formation.
        pending_arrival = set(active)
        convergence_started = time.monotonic()
        convergence_timeout = float(route["arrival_convergence_timeout_seconds"])
        correction_velocity = float(route["arrival_correction_velocity_mps"])
        correction_vx = correction_velocity * dx / distance
        correction_vy = correction_velocity * dy / distance
        correction_step = float(route["control_step_seconds"])
        convergence_step = 0
        # Do not issue a correction to a drone that is already within the configured
        # Point_B tolerance. This prevents a final one-step overshoot at the roof edge.
        initial_arrival_errors = {}
        initially_arrived = []
        for vehicle in tuple(sorted(pending_arrival)):
            x, y = _local_xy(client, vehicle)
            horizontal_error = math.hypot(x - dx, y - dy)
            cross_track_error = abs(dx * y - dy * x) / distance
            initial_arrival_errors[vehicle] = {
                "horizontal_error_m": horizontal_error,
                "cross_track_error_m": cross_track_error,
            }
            if (
                horizontal_error <= float(route["arrival_tolerance_m"])
                and cross_track_error <= float(route["maximum_lateral_error_m"])
            ):
                pending_arrival.remove(vehicle)
                initially_arrived.append(vehicle)
                states[vehicle] = "ARRIVED"
                client.hoverAsync(vehicle_name=vehicle).join()
        if initially_arrived:
            current_water = client.simGetObjectPose(water_name, ned=True)
            water_z = float(current_water.position.z_val)
            observation = _observation(
                client,
                tuple(sorted(active)),
                water_z,
                float(limits["minimum_pairwise_separation_m"]),
            )
            events.append(
                {
                    "state": "ARRIVAL_CONVERGENCE",
                    "step": convergence_step,
                    "elapsed_seconds": 0.0,
                    "pending": sorted(pending_arrival),
                    "newly_arrived": initially_arrived,
                    "new_collisions": [],
                    "collision_samples": {},
                    "arrival_errors": initial_arrival_errors,
                    **observation,
                }
            )
        while pending_arrival:
            elapsed = time.monotonic() - convergence_started
            if elapsed > convergence_timeout:
                remaining = ", ".join(sorted(pending_arrival))
                raise RuntimeError(
                    f"Point_B convergence timed out for: {remaining}"
                )
            current_water = client.simGetObjectPose(water_name, ned=True)
            water_z = float(current_water.position.z_val)
            target_z = min(
                float(route["cruise_z_ned_m"]),
                water_z - float(flood["desired_clearance_above_water_m"]),
            )
            _join_all(
                [
                    client.moveByVelocityZAsync(
                        correction_vx,
                        correction_vy,
                        target_z,
                        correction_step,
                        drivetrain=cosysairsim.DrivetrainType.MaxDegreeOfFreedom,
                        yaw_mode=yaw_mode,
                        vehicle_name=vehicle,
                    )
                    for vehicle in tuple(sorted(pending_arrival))
                ]
            )
            convergence_step += 1
            new_collisions = []
            collision_samples = {}
            for vehicle in tuple(sorted(active)):
                sample = _collision_record(client, vehicle)
                collision_samples[vehicle] = sample
                if sample["has_collided"] and sample["timestamp"] > collision_baseline[vehicle]:
                    states[vehicle] = "DESTROYED_BY_COLLISION"
                    collided[vehicle] = sample
                    active.remove(vehicle)
                    pending_arrival.discard(vehicle)
                    new_collisions.append(vehicle)
                    _terminate_collision(client, vehicle)
                    armed.discard(vehicle)
                    api_enabled.discard(vehicle)
            arrival_errors = {}
            newly_arrived = []
            for vehicle in tuple(sorted(pending_arrival)):
                x, y = _local_xy(client, vehicle)
                horizontal_error = math.hypot(x - dx, y - dy)
                cross_track_error = abs(dx * y - dy * x) / distance
                arrival_errors[vehicle] = {
                    "horizontal_error_m": horizontal_error,
                    "cross_track_error_m": cross_track_error,
                }
                if (
                    horizontal_error <= float(route["arrival_tolerance_m"])
                    and cross_track_error <= float(route["maximum_lateral_error_m"])
                ):
                    pending_arrival.remove(vehicle)
                    newly_arrived.append(vehicle)
                    states[vehicle] = "ARRIVED"
                    client.hoverAsync(vehicle_name=vehicle).join()
            observation = (
                _observation(
                    client,
                    tuple(sorted(active)),
                    water_z,
                    float(limits["minimum_pairwise_separation_m"]),
                )
                if active
                else {}
            )
            minimum_clearance = min(
                observation.get("clearance_above_water_m", {"none": math.inf}).values()
            )
            if minimum_clearance < float(flood["minimum_clearance_above_water_m"]):
                raise RuntimeError(
                    f"water clearance breached during convergence: {minimum_clearance:.3f} m"
                )
            events.append(
                {
                    "state": "ARRIVAL_CONVERGENCE",
                    "step": convergence_step,
                    "elapsed_seconds": elapsed,
                    "pending": sorted(pending_arrival),
                    "newly_arrived": newly_arrived,
                    "new_collisions": new_collisions,
                    "collision_samples": collision_samples,
                    "arrival_errors": arrival_errors,
                    **observation,
                }
            )
        survivors = tuple(sorted(active))
        arrival_errors = {}
        for vehicle in survivors:
            x, y = _local_xy(client, vehicle)
            along_error = math.hypot(x - dx, y - dy)
            cross_track_error = abs(dx * y - dy * x) / distance
            arrival_errors[vehicle] = {
                "horizontal_error_m": along_error,
                "cross_track_error_m": cross_track_error,
            }
            if along_error > float(route["arrival_tolerance_m"]):
                raise RuntimeError(f"{vehicle} did not reach Point_B: {along_error:.3f} m")
            if cross_track_error > float(route["maximum_lateral_error_m"]):
                raise RuntimeError(
                    f"{vehicle} exceeded straight-line tolerance: {cross_track_error:.3f} m"
                )
            states[vehicle] = "ARRIVED"
        events.append(
            {
                "state": "ARRIVED" if survivors else "COLLISION_TERMINAL",
                "survivors": list(survivors),
                "collided": collided,
                "arrival_errors": arrival_errors,
            }
        )
        if survivors and bool(landing["land_survivors_at_point_b"]):
            for vehicle in survivors:
                states[vehicle] = "LANDING"
            landing_collision_baseline = {
                vehicle: int(_collision_record(client, vehicle)["timestamp"])
                for vehicle in survivors
            }
            descent_target_z_ned_m = landing_surface_z_ned_m + float(
                landing["descent_target_below_surface_m"]
            )
            # Start all descents without joining them. Contact, separation and the
            # touchdown envelope must be monitored while the command is active.
            descent_futures = {
                vehicle: client.moveToZAsync(
                    descent_target_z_ned_m,
                    float(landing["controlled_descent_velocity_mps"]),
                    timeout_sec=float(landing["controlled_descent_timeout_seconds"]),
                    vehicle_name=vehicle,
                )
                for vehicle in survivors
            }
            events.append(
                {
                    "state": "CONTROLLED_DESCENT_COMMANDED",
                    "landing_surface_z_ned_m": landing_surface_z_ned_m,
                    "descent_target_z_ned_m": descent_target_z_ned_m,
                    "descent_velocity_mps": float(
                        landing["controlled_descent_velocity_mps"]
                    ),
                    "collision_baselines": landing_collision_baseline,
                }
            )
            pending_landing = set(survivors)
            contact_started: dict[str, float] = {}
            contact_first_seen: dict[str, float] = {}
            contact_holds: set[str] = set()
            contact_hold_futures: dict[str, object] = {}
            disarm_started: dict[str, float] = {}
            landing_started = time.monotonic()
            while pending_landing:
                landed_now = []
                contact_disarmed_now = []
                deviation_accepted_now = []
                contact_samples = {}
                positions = _world_positions(client, survivors)
                minimum_separation = _minimum_pairwise(positions)
                if minimum_separation < float(limits["minimum_pairwise_separation_m"]):
                    raise RuntimeError(
                        "formation separation breached during landing: "
                        f"{minimum_separation:.3f} m"
                    )
                now = time.monotonic()
                for vehicle in tuple(sorted(pending_landing)):
                    sample = _landing_contact_sample(
                        client,
                        vehicle,
                        landing,
                        landing_surface_z_ned_m,
                        landing_collision_baseline[vehicle],
                        (dx, dy),
                    )
                    contact_samples[vehicle] = sample
                    violations = sample["contact_envelope_violations"]
                    if violations:
                        raise RuntimeError(
                            f"{vehicle} unsafe landing contact: {violations[0]}"
                        )
                    if (
                        sample["landed_state"]
                        == int(cosysairsim.LandedState.Landed)
                        and not sample["stable_contact_candidate"]
                    ):
                        raise RuntimeError(
                            f"{vehicle} reported Landed without stable verified contact"
                        )
                    if vehicle in disarm_started:
                        try:
                            decision = _post_disarm_landing_decision(
                                sample,
                                landing,
                                now - disarm_started[vehicle],
                                int(cosysairsim.LandedState.Landed),
                            )
                        except RuntimeError as exc:
                            raise RuntimeError(f"{vehicle} {exc}") from exc
                        if decision is not None:
                            terminal_state, deviation_applied = decision
                            states[vehicle] = terminal_state
                            pending_landing.remove(vehicle)
                            if deviation_applied:
                                deviation_accepted_now.append(vehicle)
                                landed_state_deviation_applied = True
                            else:
                                landed_now.append(vehicle)
                        continue
                    if sample["new_surface_contact"]:
                        contact_first_seen.setdefault(vehicle, now)
                        if vehicle not in contact_holds:
                            # A hover supersedes the below-surface descent command so
                            # the controller does not continue pushing into the roof.
                            contact_hold_futures[vehicle] = client.hoverAsync(
                                vehicle_name=vehicle
                            )
                            contact_holds.add(vehicle)
                        if sample["stable_contact_candidate"]:
                            contact_started.setdefault(vehicle, now)
                            if now - contact_started[vehicle] >= float(
                                landing["contact_settle_seconds"]
                            ):
                                if (
                                    client.armDisarm(False, vehicle_name=vehicle)
                                    is not True
                                ):
                                    raise RuntimeError(f"{vehicle} disarm returned false")
                                armed.discard(vehicle)
                                states[vehicle] = "CONTACT_DISARMED"
                                disarm_started[vehicle] = now
                                contact_disarmed_now.append(vehicle)
                        else:
                            contact_started.pop(vehicle, None)
                        if now - contact_first_seen[vehicle] >= float(
                            landing["confirmation_timeout_seconds"]
                        ):
                            raise RuntimeError(
                                f"{vehicle} contact did not become stably stationary"
                            )
                    else:
                        contact_started.pop(vehicle, None)
                        if vehicle in contact_holds:
                            contact_holds.remove(vehicle)
                            descent_futures[vehicle] = client.moveToZAsync(
                                descent_target_z_ned_m,
                                float(landing["controlled_descent_velocity_mps"]),
                                timeout_sec=float(
                                    landing["controlled_descent_timeout_seconds"]
                                ),
                                vehicle_name=vehicle,
                            )
                        if now - landing_started >= float(
                            landing["controlled_descent_timeout_seconds"]
                        ):
                            raise RuntimeError(
                                f"{vehicle} controlled descent timed out before contact"
                            )
                elapsed = now - landing_started
                events.append(
                    {
                        "state": "LANDING_CONFIRMATION",
                        "elapsed_seconds": elapsed,
                        "pending": sorted(pending_landing),
                        "landed_now": landed_now,
                        "contact_disarmed_now": contact_disarmed_now,
                        "deviation_accepted_now": deviation_accepted_now,
                        "minimum_pairwise_separation_m": minimum_separation,
                        "contact_samples": contact_samples,
                    }
                )
                if pending_landing:
                    time.sleep(float(landing["controlled_descent_poll_seconds"]))
        if collided and survivors:
            status = "PARTIAL_COLLISION"
        elif collided:
            status = "COLLISION"
        elif landed_state_deviation_applied:
            status = str(landing["landed_state_deviation"]["success_label"])
        else:
            status = "PASS"
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        try:
            _join_all(
                [
                    client.hoverAsync(vehicle_name=vehicle)
                    for vehicle in sorted(api_enabled)
                ]
            )
        except Exception:
            pass
        raise
    finally:
        for vehicle in tuple(sorted(armed)):
            try:
                client.armDisarm(False, vehicle_name=vehicle)
                states[vehicle] = "DISARMED_CLEANUP"
            except Exception:
                pass
        for vehicle in tuple(sorted(api_enabled)):
            try:
                client.enableApiControl(False, vehicle_name=vehicle)
            except Exception:
                pass
        if weather_enabled:
            try:
                client.simEnableWeather(False)
            except Exception:
                pass
        result = {
            "schema": RUN_SCHEMA,
            "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": status,
            "failure": failure,
            "scenario_id": config["scenario_id"],
            "development_only": config["development_only"],
            "source_map_sha256": _sha256(args.map_file),
            "config_sha256": _sha256(args.config),
            "water_object_name": water_name,
            "water_left_at_peak": bool(flood["leave_water_at_peak_after_mission"]),
            "landed_state_deviation": {
                **landing["landed_state_deviation"],
                "applied": landed_state_deviation_applied,
            },
            "vehicle_states": states,
            "events": events,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(json.dumps({"status": status, "failure": failure, "output": str(args.output)}))


if __name__ == "__main__":
    main()
