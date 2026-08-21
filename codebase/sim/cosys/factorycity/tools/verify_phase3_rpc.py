"""Read-only live RPC verification for the Phase 3 FactoryCity integration.

This tool never requests API control, arming, motion, reset, or simulator mutation. It
binds the running server to the supplied settings, then repeatedly verifies the exact
roster, landed state, global-NED poses, separation, collisions, and API-control state.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.metadata
import json
import math
import time
from pathlib import Path

from sim.cosys.factorycity.config import load_config
from sim.cosys.factorycity.launch import calculate_launch_bounds
from sim.cosys.factorycity.phase3 import load_runtime_profile


OUTPUT_SCHEMA = "veriswarm.factorycity.phase3_rpc_observation.v1"


class LiveVerificationError(RuntimeError):
    """A failed read-only gate with safe diagnostic context."""

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics


def _captured_at():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _position(value):
    return {"x": float(value.x_val), "y": float(value.y_val), "z": float(value.z_val)}


def _finite_position(value):
    return all(math.isfinite(item) for item in value.values())


def _distance(first, second):
    return math.sqrt(
        (first["x"] - second["x"]) ** 2
        + (first["y"] - second["y"]) ** 2
        + (first["z"] - second["z"]) ** 2
    )


def _canonical_json_sha256(value):
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_create_once(path, payload):
    rendered = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(rendered)
        stream.write("\n")


def _connect(config):
    import cosysairsim

    deadline = time.monotonic() + config.limits.timeout_for("connect")
    interval = min(
        config.limits.rpc_timeout_seconds,
        config.limits.command_ttl_ms / 1000.0,
    )
    last_error = None
    while time.monotonic() < deadline:
        try:
            client = cosysairsim.MultirotorClient(
                ip=config.endpoint.host,
                port=config.endpoint.port,
                timeout_value=config.limits.rpc_timeout_seconds,
            )
            if client.ping():
                return client
        except Exception as exc:
            last_error = exc
        time.sleep(interval)
    raise LiveVerificationError(f"RPC did not become ready: {last_error}")


def _collision_record(collision):
    return {
        "has_collided": bool(collision.has_collided),
        "object_name": str(collision.object_name),
        "normal": _position(collision.normal),
        "penetration_depth_m": float(collision.penetration_depth),
        "timestamp": int(collision.time_stamp),
    }


def _verify_server_settings(client, expected_settings):
    try:
        running_settings = json.loads(client.getSettingsString())
    except (TypeError, json.JSONDecodeError) as exc:
        raise LiveVerificationError(
            f"running server returned invalid settings JSON: {exc}"
        ) from exc
    expected_hash = _canonical_json_sha256(expected_settings)
    running_hash = _canonical_json_sha256(running_settings)
    if running_settings != expected_settings:
        raise LiveVerificationError(
            "running server settings do not match the supplied generated settings",
            {
                "expected_settings_canonical_sha256": expected_hash,
                "running_settings_canonical_sha256": running_hash,
            },
        )
    return running_hash


def _observe_vehicle(
    client,
    cosysairsim,
    config,
    runtime,
    bounds,
    vehicles,
    name,
    sample_index,
    allow_support_contact,
):
    object_pose = client.simGetObjectPose(name, ned=True)
    local_pose = client.simGetVehiclePose(vehicle_name=name)
    state = client.getMultirotorState(vehicle_name=name)
    collision = client.simGetCollisionInfo(vehicle_name=name)
    actual = _position(object_pose.position)
    local = _position(local_pose.position)
    expected = {
        axis: float(vehicles[name][axis.upper()]) for axis in ("x", "y", "z")
    }
    record = {
        "vehicle_name": name,
        "expected_global_ned_m": expected,
        "observed_global_ned_m": actual,
        "observed_local_ned_m": local,
        "landed_state": int(state.landed_state),
        "api_control_enabled": bool(client.isApiControlEnabled(vehicle_name=name)),
        "collision": _collision_record(collision),
    }
    if not _finite_position(actual) or not _finite_position(local):
        raise LiveVerificationError(
            f"vehicle {name!r} returned a non-finite pose",
            {"sample_index": sample_index, "vehicle": record},
        )
    error = _distance(actual, expected)
    record["global_spawn_error_m"] = error
    if error > config.limits.spawn_position_tolerance_m:
        raise LiveVerificationError(
            f"vehicle {name!r} global-NED spawn error {error} exceeds configured tolerance",
            {"sample_index": sample_index, "vehicle": record},
        )
    if not bounds.contains(actual["x"], actual["y"]):
        raise LiveVerificationError(
            f"vehicle {name!r} spawned outside the usable square",
            {"sample_index": sample_index, "vehicle": record},
        )
    if int(state.landed_state) != int(cosysairsim.LandedState.Landed):
        raise LiveVerificationError(
            f"vehicle {name!r} is not in the landed state",
            {"sample_index": sample_index, "vehicle": record},
        )
    record["accepted_initial_support_contact"] = False
    if collision.has_collided:
        vertical_support = (
            abs(record["collision"]["normal"]["z"])
            >= runtime.minimum_ground_normal_z
        )
        shallow_support = (
            record["collision"]["penetration_depth_m"]
            <= config.limits.support_contact_max_penetration_m
        )
        if not allow_support_contact or not vertical_support or not shallow_support:
            raise LiveVerificationError(
                f"vehicle {name!r} reports an unsafe collision",
                {"sample_index": sample_index, "vehicle": record},
            )
        record["accepted_initial_support_contact"] = True
    if record["api_control_enabled"]:
        raise LiveVerificationError(
            f"vehicle {name!r} unexpectedly has API control enabled",
            {"sample_index": sample_index, "vehicle": record},
        )
    return record


def _pairwise_records(config, observations, sample_index):
    actual_positions = {
        item["vehicle_name"]: item["observed_global_ned_m"] for item in observations
    }
    pairs = []
    names = config.fleet.names
    for index, first_name in enumerate(names):
        for second_name in names[index + 1 :]:
            first = actual_positions[first_name]
            second = actual_positions[second_name]
            planar_distance = math.hypot(
                first["x"] - second["x"], first["y"] - second["y"]
            )
            record = {
                "first_vehicle": first_name,
                "second_vehicle": second_name,
                "planar_distance_m": planar_distance,
            }
            if planar_distance < config.limits.minimum_separation_m:
                raise LiveVerificationError(
                    f"live pair {first_name}/{second_name} violates minimum separation",
                    {"sample_index": sample_index, "pair": record},
                )
            pairs.append(record)
    return pairs


def verify(args):
    import cosysairsim

    config, config_hash = load_config(args.config)
    runtime, runtime_hash = load_runtime_profile(args.runtime_profile)
    settings_bytes = args.settings.read_bytes()
    settings_hash = hashlib.sha256(settings_bytes).hexdigest()
    settings = json.loads(settings_bytes.decode("utf-8"))
    vehicles = settings.get("Vehicles")
    if not isinstance(vehicles, dict) or set(vehicles) != set(config.fleet.names):
        raise LiveVerificationError(
            "generated settings roster does not match configuration"
        )

    client = _connect(config)
    running_settings_hash = _verify_server_settings(client, settings)
    observed_names = tuple(client.listVehicles())
    if set(observed_names) != set(config.fleet.names) or len(observed_names) != len(
        config.fleet.names
    ):
        raise LiveVerificationError(
            f"live RPC roster mismatch: expected {config.fleet.names}, got {observed_names}"
        )
    server_version = int(client.getServerVersion())
    minimum_client_version = int(client.getMinRequiredClientVersion())
    if server_version != runtime.cosys_protocol_version:
        raise LiveVerificationError(
            f"server protocol mismatch: expected {runtime.cosys_protocol_version}, got {server_version}"
        )
    client_protocol = int(client.getClientVersion())
    if client_protocol < minimum_client_version:
        raise LiveVerificationError("client protocol is below the server minimum")

    bounds = calculate_launch_bounds(config.launch_area)
    samples = []
    for sample_index in range(config.limits.verification_sample_count):
        current_names = tuple(client.listVehicles())
        if set(current_names) != set(config.fleet.names) or len(current_names) != len(
            config.fleet.names
        ):
            raise LiveVerificationError(
                "live RPC roster changed during the verification window",
                {"sample_index": sample_index, "observed_roster": current_names},
            )
        observations = [
            _observe_vehicle(
                client,
                cosysairsim,
                config,
                runtime,
                bounds,
                vehicles,
                name,
                sample_index,
                sample_index == 0,
            )
            for name in config.fleet.names
        ]
        pairs = _pairwise_records(config, observations, sample_index)
        samples.append(
            {
                "sample_index": sample_index,
                "captured_at_utc": _captured_at(),
                "vehicles": observations,
                "pairwise_distances": pairs,
                "minimum_pairwise_distance_m": min(
                    item["planar_distance_m"] for item in pairs
                ),
            }
        )
        if sample_index + 1 < config.limits.verification_sample_count:
            time.sleep(config.limits.verification_sample_interval_seconds)

    return {
        "schema": OUTPUT_SCHEMA,
        "status": "PASS",
        "captured_at_utc": _captured_at(),
        "configuration_sha256": config_hash,
        "runtime_profile_sha256": runtime_hash,
        "settings_sha256": settings_hash,
        "running_settings_canonical_sha256": running_settings_hash,
        "client": {
            "distribution": "cosysairsim",
            "distribution_version": importlib.metadata.version("cosysairsim"),
            "protocol_version": client_protocol,
        },
        "server": {
            "protocol_version": server_version,
            "minimum_client_protocol_version": minimum_client_version,
        },
        "checks": {
            "read_only": True,
            "running_settings_match": True,
            "roster_exact_and_stable": True,
            "all_global_spawns_match_within_configured_tolerance": True,
            "all_global_spawns_inside_usable_square": True,
            "all_vehicles_landed": True,
            "all_pairs_meet_minimum_separation": True,
            "initial_support_contacts_vertical_and_nonpenetrating": True,
            "all_later_collisions_clear": True,
            "all_api_control_disabled": True,
        },
        "configured_spawn_position_tolerance_m": (
            config.limits.spawn_position_tolerance_m
        ),
        "configured_support_contact_max_penetration_m": (
            config.limits.support_contact_max_penetration_m
        ),
        "configured_verification_sample_count": (
            config.limits.verification_sample_count
        ),
        "configured_verification_sample_interval_seconds": (
            config.limits.verification_sample_interval_seconds
        ),
        "samples": samples,
        "minimum_observed_pairwise_distance_m": min(
            sample["minimum_pairwise_distance_m"] for sample in samples
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args)
    except Exception as exc:
        result = {
            "schema": OUTPUT_SCHEMA,
            "status": "FAIL",
            "captured_at_utc": _captured_at(),
            "error": str(exc),
        }
        diagnostics = getattr(exc, "diagnostics", None)
        if diagnostics is not None:
            result["diagnostics"] = diagnostics
        _write_create_once(args.output, result)
        print(json.dumps(result, sort_keys=True))
        return 1
    _write_create_once(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
