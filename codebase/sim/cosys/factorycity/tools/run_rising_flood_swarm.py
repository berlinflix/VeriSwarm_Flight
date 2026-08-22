"""Raise FactoryCity floodwater while maintaining five-drone clearance above it."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import time
from pathlib import Path

import cosysairsim


def _positive(data: dict[str, object], name: str) -> float:
    value = float(data[name])
    if not math.isfinite(value) or value <= 0.0:
        raise RuntimeError(f"{name} must be positive and finite")
    return value


def _load_config(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "veriswarm.factorycity.rising_flood.v1":
        raise RuntimeError("unsupported rising-flood config schema")
    vehicles = data.get("vehicles")
    if not isinstance(vehicles, list) or not vehicles:
        raise RuntimeError("vehicles must be a non-empty list")
    if len(vehicles) != len(set(vehicles)):
        raise RuntimeError("vehicles must be unique")
    rpc = data["rpc"]
    flight = data["flight"]
    flood = data["flood"]
    if not isinstance(rpc, dict) or not isinstance(flight, dict) or not isinstance(flood, dict):
        raise RuntimeError("rpc, flight, and flood must be objects")
    for field in ("timeout_seconds",):
        _positive(rpc, field)
    for field in (
        "takeoff_timeout_seconds",
        "command_timeout_seconds",
        "vertical_velocity_mps",
        "desired_clearance_above_water_m",
        "minimum_clearance_above_water_m",
        "minimum_pairwise_separation_m",
        "settle_seconds",
    ):
        _positive(flight, field)
    for field in (
        "rise_height_m",
        "rise_duration_seconds",
        "update_period_seconds",
        "peak_keepalive_seconds",
        "peak_evidence_interval_seconds",
        "recede_duration_seconds",
    ):
        _positive(flood, field)
    if flood.get("peak_hold_mode") not in ("timed", "until_interrupted"):
        raise RuntimeError("peak_hold_mode must be timed or until_interrupted")
    if flood["peak_hold_mode"] == "timed":
        _positive(flood, "hold_at_peak_seconds")
    if float(flight["desired_clearance_above_water_m"]) <= float(
        flight["minimum_clearance_above_water_m"]
    ):
        raise RuntimeError("desired water clearance must exceed minimum clearance")
    if float(flood["update_period_seconds"]) > float(flood["rise_duration_seconds"]):
        raise RuntimeError("flood update period cannot exceed rise duration")
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
        raise RuntimeError(f"layer result does not identify exactly one {actor_id!r} actor")
    return str(matches[0]["object_name"])


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


def _finite_pose(pose: object, description: str) -> None:
    values = (
        pose.position.x_val,
        pose.position.y_val,
        pose.position.z_val,
        pose.orientation.x_val,
        pose.orientation.y_val,
        pose.orientation.z_val,
        pose.orientation.w_val,
    )
    if not all(math.isfinite(float(value)) for value in values):
        raise RuntimeError(f"{description} returned a non-finite pose")


def _pairwise_minimum(positions: dict[str, tuple[float, float, float]]) -> float:
    names = tuple(positions)
    distances: list[float] = []
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            distances.append(math.dist(positions[left], positions[right]))
    return min(distances) if distances else math.inf


def _join_all(futures: list[object]) -> None:
    for future in futures:
        future.join()


def _move_fleet_to_water_clearance(
    client: object,
    vehicles: tuple[str, ...],
    water_z_ned_m: float,
    flight: dict[str, object],
) -> float:
    target_z = water_z_ned_m - float(flight["desired_clearance_above_water_m"])
    futures = [
        client.moveToZAsync(
            target_z,
            float(flight["vertical_velocity_mps"]),
            timeout_sec=float(flight["command_timeout_seconds"]),
            vehicle_name=vehicle,
        )
        for vehicle in vehicles
    ]
    _join_all(futures)
    return target_z


def _verify_fleet(
    client: object,
    vehicles: tuple[str, ...],
    water_z_ned_m: float,
    flight: dict[str, object],
    collision_baseline: dict[str, int] | None = None,
) -> dict[str, object]:
    positions: dict[str, tuple[float, float, float]] = {}
    clearances: dict[str, float] = {}
    collisions: dict[str, object] = {}
    for vehicle in vehicles:
        # simGetVehiclePose is start-relative for each vehicle, so five correctly
        # separated drones can all report local X=Y=0. Scene-object poses share the
        # world NED frame used by the movable flood actor and are required here.
        pose = client.simGetObjectPose(vehicle, ned=True)
        _finite_pose(pose, vehicle)
        positions[vehicle] = (
            float(pose.position.x_val),
            float(pose.position.y_val),
            float(pose.position.z_val),
        )
        clearance = water_z_ned_m - float(pose.position.z_val)
        clearances[vehicle] = clearance
        collision = client.simGetCollisionInfo(vehicle_name=vehicle)
        collisions[vehicle] = {
            "has_collided": bool(collision.has_collided),
            "object_name": str(collision.object_name),
            "penetration_depth_m": float(collision.penetration_depth),
            "timestamp": int(collision.time_stamp),
        }
    minimum_clearance = min(clearances.values())
    minimum_separation = _pairwise_minimum(positions)
    if minimum_clearance < float(flight["minimum_clearance_above_water_m"]):
        raise RuntimeError(
            f"water clearance breached: {minimum_clearance:.3f} m"
        )
    if minimum_separation < float(flight["minimum_pairwise_separation_m"]):
        raise RuntimeError(
            f"swarm separation breached: {minimum_separation:.3f} m"
        )
    collided = [
        name
        for name, value in collisions.items()
        if collision_baseline is not None
        and value["has_collided"]
        and value["timestamp"] > collision_baseline[name]
    ]
    if collided:
        raise RuntimeError(f"vehicle collision detected: {collided}")
    return {
        "minimum_clearance_above_water_m": minimum_clearance,
        "minimum_pairwise_separation_m": minimum_separation,
        "clearances_m": clearances,
        "collisions": collisions,
        "collision_timestamps": {
            name: int(value["timestamp"]) for name, value in collisions.items()
        },
    }


def _water_transition(
    client: object,
    water_name: str,
    initial_pose: object,
    vehicles: tuple[str, ...],
    flight: dict[str, object],
    height_m: float,
    duration_seconds: float,
    update_period_seconds: float,
    events: list[dict[str, object]],
    phase: str,
    collision_baseline: dict[str, int],
) -> None:
    steps = max(1, math.ceil(duration_seconds / update_period_seconds))
    transition_started = time.monotonic()
    for index in range(1, steps + 1):
        fraction = index / steps
        requested_z = float(initial_pose.position.z_val) - height_m * fraction
        requested_pose = _copy_pose(initial_pose, requested_z)
        if not client.simSetObjectPose(water_name, requested_pose, teleport=True):
            raise RuntimeError(f"failed to move water actor {water_name}")
        observed = client.simGetObjectPose(water_name, ned=True)
        _finite_pose(observed, "water actor")
        observed_z = float(observed.position.z_val)
        _move_fleet_to_water_clearance(client, vehicles, observed_z, flight)
        observation = _verify_fleet(
            client, vehicles, observed_z, flight, collision_baseline
        )
        events.append(
            {
                "phase": phase,
                "step": index,
                "steps": steps,
                "water_z_ned_m": observed_z,
                **observation,
            }
        )
        scheduled_step_end = transition_started + duration_seconds * fraction
        remaining = scheduled_step_end - time.monotonic()
        if remaining > 0.0:
            time.sleep(remaining)


def _hold_flood_at_peak(
    client: object,
    water_name: str,
    vehicles: tuple[str, ...],
    flight: dict[str, object],
    flood: dict[str, object],
    collision_baseline: dict[str, int],
    events: list[dict[str, object]],
) -> str:
    mode = str(flood["peak_hold_mode"])
    keepalive = float(flood["peak_keepalive_seconds"])
    evidence_interval = float(flood["peak_evidence_interval_seconds"])
    started = time.monotonic()
    deadline = (
        started + float(flood["hold_at_peak_seconds"])
        if mode == "timed"
        else None
    )
    next_evidence = started
    sample = 0
    print(
        "Flood is at peak; drones are holding above water. "
        "Press Ctrl+C for safe recession and landing.",
        flush=True,
    )
    try:
        while deadline is None or time.monotonic() < deadline:
            peak = client.simGetObjectPose(water_name, ned=True)
            _finite_pose(peak, "peak water actor")
            water_z = float(peak.position.z_val)
            _move_fleet_to_water_clearance(client, vehicles, water_z, flight)
            observation = _verify_fleet(
                client, vehicles, water_z, flight, collision_baseline
            )
            now = time.monotonic()
            if now >= next_evidence:
                sample += 1
                events.append(
                    {
                        "phase": "peak_hold",
                        "sample": sample,
                        "water_z_ned_m": water_z,
                        **observation,
                    }
                )
                next_evidence = now + evidence_interval
            time.sleep(keepalive)
    except KeyboardInterrupt:
        events.append({"phase": "operator_stop", "reason": "keyboard_interrupt"})
        return "operator_stop"
    return "timed_hold_complete"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--layer-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = _load_config(args.config)
    water_name = _resolve_water_object(args.layer_result, str(config["water_actor_id"]))
    vehicles = tuple(str(item) for item in config["vehicles"])
    rpc = config["rpc"]
    flight = config["flight"]
    flood = config["flood"]
    client = cosysairsim.MultirotorClient(
        ip=str(rpc["host"]),
        port=int(rpc["port"]),
        timeout_value=float(rpc["timeout_seconds"]),
    )
    events: list[dict[str, object]] = []
    initial_water_pose = None
    api_enabled: list[str] = []
    armed: list[str] = []
    status = "FAIL"
    failure = None
    try:
        client.confirmConnection()
        live_roster = tuple(sorted(client.listVehicles()))
        if live_roster != tuple(sorted(vehicles)):
            raise RuntimeError(f"unexpected live roster: {live_roster}")
        initial_water_pose = client.simGetObjectPose(water_name, ned=True)
        _finite_pose(initial_water_pose, "initial water actor")
        for name, value in config.get("weather", {}).items():
            client.simEnableWeather(True)
            client.simSetWeatherParameter(
                getattr(cosysairsim.WeatherParameter, name), float(value)
            )
        for vehicle in vehicles:
            client.enableApiControl(True, vehicle_name=vehicle)
            api_enabled.append(vehicle)
            if not client.armDisarm(True, vehicle_name=vehicle):
                raise RuntimeError(f"failed to arm {vehicle}")
            armed.append(vehicle)
        _join_all(
            [
                client.takeoffAsync(
                    timeout_sec=float(flight["takeoff_timeout_seconds"]),
                    vehicle_name=vehicle,
                )
                for vehicle in vehicles
            ]
        )
        initial_z = float(initial_water_pose.position.z_val)
        _move_fleet_to_water_clearance(client, vehicles, initial_z, flight)
        time.sleep(float(flight["settle_seconds"]))
        initial_observation = _verify_fleet(client, vehicles, initial_z, flight)
        collision_baseline = initial_observation["collision_timestamps"]
        events.append({"phase": "initial_climb", **initial_observation})
        _water_transition(
            client,
            water_name,
            initial_water_pose,
            vehicles,
            flight,
            float(flood["rise_height_m"]),
            float(flood["rise_duration_seconds"]),
            float(flood["update_period_seconds"]),
            events,
            "rising",
            collision_baseline,
        )
        hold_exit = _hold_flood_at_peak(
            client,
            water_name,
            vehicles,
            flight,
            flood,
            collision_baseline,
            events,
        )
        events.append({"phase": "peak_hold_exit", "reason": hold_exit})
        if not bool(flood["restore_initial_level_before_landing"]):
            raise RuntimeError("landing is forbidden while floodwater remains raised")
        peak_pose = _copy_pose(
            initial_water_pose,
            float(initial_water_pose.position.z_val) - float(flood["rise_height_m"]),
        )
        _water_transition(
            client,
            water_name,
            peak_pose,
            vehicles,
            flight,
            -float(flood["rise_height_m"]),
            float(flood["recede_duration_seconds"]),
            float(flood["update_period_seconds"]),
            events,
            "receding",
            collision_baseline,
        )
        status = "PASS"
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if initial_water_pose is not None:
            try:
                client.simSetObjectPose(water_name, initial_water_pose, teleport=True)
            except Exception:
                pass
        for vehicle in reversed(armed):
            try:
                client.landAsync(
                    timeout_sec=float(flight["command_timeout_seconds"]),
                    vehicle_name=vehicle,
                ).join()
                client.armDisarm(False, vehicle_name=vehicle)
            except Exception:
                pass
        for vehicle in reversed(api_enabled):
            try:
                client.enableApiControl(False, vehicle_name=vehicle)
            except Exception:
                pass
        try:
            client.simEnableWeather(False)
        except Exception:
            pass
        result = {
            "schema": "veriswarm.factorycity.rising_flood_run.v1",
            "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": status,
            "failure": failure,
            "scenario_id": config["scenario_id"],
            "development_only": config["development_only"],
            "water_object_name": water_name,
            "events": events,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(json.dumps({"status": status, "output": str(args.output), "failure": failure}))


if __name__ == "__main__":
    main()
