import copy
import hashlib
import json
from pathlib import Path

import pytest

from sim.cosys.factorycity.config import (
    CONFIG_SCHEMA_ID,
    ConfigurationError,
    load_config,
    validate_config,
)


TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "sim"
    / "cosys"
    / "factorycity"
    / "factorycity_five_drone.template.json"
)
SCHEMA_PATH = TEMPLATE_PATH.with_name("factorycity_fleet_config.schema.json")


@pytest.fixture
def document():
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _set_roster(document, names):
    document["fleet"]["expected_count"] = len(names)
    document["fleet"]["vehicles"] = [
        {"name": name, "type": "TestVehicle", "role": "member"}
        for name in names
    ]
    document["lifecycle"]["takeoff_order"] = list(names)
    document["lifecycle"]["landing_order"] = list(reversed(names))
    document["lifecycle"]["takeoff_altitude_offsets_m"] = [
        {"vehicle_name": name, "offset_m": index * 0.1}
        for index, name in enumerate(names)
    ]
    document["sensors"]["vehicle_profiles"] = [
        {"vehicle_name": name, "profile": "standard-navigation-v1"}
        for name in names
    ]


def _assert_rejected(document, match):
    with pytest.raises(ConfigurationError, match=match):
        validate_config(document)


def test_five_drone_template_loads_and_hashes_exact_bytes():
    config, digest = load_config(TEMPLATE_PATH)

    assert config.schema == CONFIG_SCHEMA_ID
    assert config.status == "ACCEPTED"
    assert config.fleet.expected_count == 5
    assert config.fleet.names == ("alpha", "bravo", "charlie", "delta", "echo")
    assert config.launch_area.side_length_m == 10.0
    assert config.launch_area.usable_side_length_m == 9.0
    assert config.launch_area.initial_spawn_clearance_m == 0.2243
    assert config.launch_area.takeoff_corridor_start_clearance_m == 1.5
    assert config.limits.spawn_position_tolerance_m == 0.02
    assert config.limits.support_contact_max_penetration_m == 0.01
    assert digest == hashlib.sha256(TEMPLATE_PATH.read_bytes()).hexdigest()


def test_portable_schema_and_runtime_contract_share_the_same_identity():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema"]["const"] == CONFIG_SCHEMA_ID
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
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
    }


def test_contract_is_generic_over_roster_area_endpoint_and_limits(document):
    names = ("red", "green", "blue")
    _set_roster(document, names)
    document["launch_area"]["side_length_m"] = 24.5
    document["launch_area"]["minimum_separation_m"] = 3.5
    document["limits"]["minimum_separation_m"] = 3.5
    document["endpoint"] = {"host": "simulator.internal", "port": 40123}
    document["limits"]["speed_mps"] = 4.25

    config = validate_config(document)

    assert config.fleet.names == names
    assert config.launch_area.side_length_m == 24.5
    assert config.endpoint.host == "simulator.internal"
    assert config.endpoint.port == 40123
    assert config.limits.speed_mps == 4.25


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda d: d.update({"unexpected": 1}), "unknown fields"),
        (lambda d: d.pop("endpoint"), "missing required fields"),
        (lambda d: d["fleet"].update({"unexpected": 1}), "unknown fields"),
        (lambda d: d.update({"schema": "wrong"}), "config.schema"),
        (lambda d: d.update({"status": "READY"}), "config.status"),
    ],
)
def test_root_contract_is_closed(document, mutation, match):
    mutation(document)
    _assert_rejected(document, match)


def test_fleet_count_must_match_roster(document):
    document["fleet"]["expected_count"] = 4
    _assert_rejected(document, "does not match")


def test_fleet_names_are_unique_case_insensitively(document):
    document["fleet"]["vehicles"][1]["name"] = "ALPHA"
    _assert_rejected(document, "duplicate")


@pytest.mark.parametrize("bad_count", [True, 0, -1, 2.5])
def test_fleet_count_is_a_positive_integer(document, bad_count):
    document["fleet"]["expected_count"] = bad_count
    _assert_rejected(document, "positive integer")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("side_length_m", 0, "greater than zero"),
        ("side_length_m", True, "must be a number"),
        ("side_length_m", float("inf"), "must be finite"),
        ("edge_clearance_m", -0.1, "non-negative"),
        ("edge_clearance_m", 5.0, "no usable launch area"),
        ("minimum_separation_m", 13.0, "usable-square diagonal"),
        ("formation_generator", "fixed_positions", "must be one of"),
    ],
)
def test_launch_area_rejects_unsafe_values(document, field, value, match):
    document["launch_area"][field] = value
    if field == "minimum_separation_m":
        document["limits"][field] = value
    _assert_rejected(document, match)


@pytest.mark.parametrize("port", [0, -1, 65536, True, 41451.0])
def test_endpoint_port_is_valid(document, port):
    document["endpoint"]["port"] = port
    _assert_rejected(document, "positive integer|at most")


def test_geofence_axes_are_ordered(document):
    document["limits"]["geofence"]["min"]["x"] = 20.0
    _assert_rejected(document, "strictly below")


def test_launch_square_must_lie_inside_horizontal_geofence(document):
    document["launch_area"]["center_ned_m"]["x"] = 19.0
    _assert_rejected(document, "horizontal geofence")


def test_altitude_band_is_ordered_and_inside_geofence(document):
    document["limits"]["altitude_band_ned_m"] = {"min_z": -11.0, "max_z": -2.0}
    _assert_rejected(document, "inside the geofence")


@pytest.mark.parametrize("stage", ["connect", "cleanup", "reset"])
def test_every_required_stage_timeout_must_exist(document, stage):
    document["limits"]["stage_timeouts_seconds"].pop(stage)
    _assert_rejected(document, "missing required fields")


def test_unknown_stage_timeout_is_rejected(document):
    document["limits"]["stage_timeouts_seconds"]["surprise"] = 1.0
    _assert_rejected(document, "unknown fields")


def test_rpc_timeout_cannot_exceed_connect_timeout(document):
    document["limits"]["rpc_timeout_seconds"] = 11.0
    _assert_rejected(document, "RPC timeout")


def test_spawn_clearance_must_preserve_collision_and_position_margin(document):
    document["launch_area"]["takeoff_corridor_start_clearance_m"] = 1.01
    _assert_rejected(document, "spawn-position tolerance")


def test_spawn_clearance_must_exceed_collision_clearance(document):
    document["launch_area"]["takeoff_corridor_start_clearance_m"] = 1.0
    _assert_rejected(document, "takeoff corridor start clearance")


def test_verification_sampling_must_fit_verify_timeout(document):
    document["limits"]["verification_sample_count"] = 5
    document["limits"]["verification_sample_interval_seconds"] = 3.0
    _assert_rejected(document, "verification sample window")


def test_support_contact_penetration_limit_is_nonnegative(document):
    document["limits"]["support_contact_max_penetration_m"] = -0.01
    _assert_rejected(document, "non-negative")


def test_runtime_and_launch_separation_values_must_match(document):
    document["limits"]["minimum_separation_m"] = 2.1
    _assert_rejected(document, "minimum-separation")


def test_frames_must_match_across_contract_sections(document):
    document["world"]["coordinate_frame"] = "ENU_METRES"
    _assert_rejected(document, "must be one of")


@pytest.mark.parametrize("order_key", ["takeoff_order", "landing_order"])
def test_lifecycle_orders_are_exact_roster_permutations(document, order_key):
    document["lifecycle"][order_key][-1] = "outsider"
    _assert_rejected(document, "exact fleet-roster permutation")


def test_altitude_offsets_cover_the_exact_roster(document):
    document["lifecycle"]["takeoff_altitude_offsets_m"].pop()
    _assert_rejected(document, "exact fleet-roster permutation")


def test_cleanup_timeout_has_one_authoritative_value(document):
    document["lifecycle"]["cleanup_timeout_seconds"] = 29.0
    _assert_rejected(document, "cleanup timeout")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("takeoff_mode", "manual"),
        ("abort_action", "ignore"),
        ("landing_mode", "manual"),
        ("disarm_policy", "never"),
        ("api_control_release_policy", "never"),
        ("reset_policy", "none"),
    ],
)
def test_lifecycle_policies_are_bounded(document, field, value):
    document["lifecycle"][field] = value
    _assert_rejected(document, "must be one of")


def test_sensor_profile_requires_navigation_and_clearance_sensors(document):
    sensors = document["sensors"]["profiles"][0]["sensors"]
    document["sensors"]["profiles"][0]["sensors"] = [
        sensor for sensor in sensors if sensor["type"] not in {"gnss", "depth_camera"}
    ]
    _assert_rejected(document, "missing required sensor types")


def test_sensor_profile_requires_depth_or_lidar(document):
    sensors = document["sensors"]["profiles"][0]["sensors"]
    document["sensors"]["profiles"][0]["sensors"] = [
        sensor for sensor in sensors if sensor["type"] != "depth_camera"
    ]
    _assert_rejected(document, "depth_camera or lidar")


def test_sensor_names_are_unique_case_insensitively(document):
    sensors = document["sensors"]["profiles"][0]["sensors"]
    sensors[1]["name"] = sensors[0]["name"].upper()
    _assert_rejected(document, "duplicate")


def test_camera_settings_are_strict(document):
    settings = document["sensors"]["profiles"][0]["sensors"][0]["settings"]
    settings["quality"] = "high"
    _assert_rejected(document, "unknown fields")


def test_non_camera_settings_must_be_empty(document):
    document["sensors"]["profiles"][0]["sensors"][2]["settings"] = {"bias": 0.0}
    _assert_rejected(document, "must be empty")


def test_every_vehicle_has_exactly_one_known_sensor_profile(document):
    document["sensors"]["vehicle_profiles"].pop()
    _assert_rejected(document, "exact fleet-roster permutation")


def test_unknown_sensor_profile_assignment_is_rejected(document):
    document["sensors"]["vehicle_profiles"][0]["profile"] = "unknown"
    _assert_rejected(document, "unknown profile")


@pytest.mark.parametrize(
    "bad_path",
    [
        "D:/private/settings.json",
        "../outside/settings.json",
        "sim\\settings.json",
        "sim//settings.json",
    ],
)
def test_repository_paths_must_be_portable_and_relative(document, bad_path):
    document["world"]["generated_settings_path"] = bad_path
    _assert_rejected(document, "repository-relative|portable")


def test_evidence_requires_create_once_semantics(document):
    document["evidence"]["create_once"] = False
    _assert_rejected(document, "must be true")


def test_evidence_store_is_an_explicit_uri(document):
    document["evidence"]["large_artifact_store_uri"] = "local folder"
    _assert_rejected(document, "explicit URI")


def test_load_config_rejects_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not valid UTF-8 JSON"):
        load_config(path)


def test_load_config_rejects_non_utf8(tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigurationError, match="not valid UTF-8 JSON"):
        load_config(path)


def test_validation_does_not_mutate_input(document):
    original = copy.deepcopy(document)
    validate_config(document)
    assert document == original
