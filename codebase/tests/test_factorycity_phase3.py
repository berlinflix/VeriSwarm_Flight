import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sim.cosys.factorycity.config import load_config, validate_config
from sim.cosys.factorycity.launch import generate_launch_plan, render_cosys_settings
from sim.cosys.factorycity.phase3 import (
    CLEARANCE_RESULT_SCHEMA_ID,
    Phase3ContractError,
    build_cosys_base_settings,
    load_clearance_result,
    load_runtime_profile,
    render_clearance_request,
    validate_runtime_profile,
)
from sim.cosys.factorycity.tools import verify_phase3_rpc


FACTORYCITY_DIR = (
    Path(__file__).resolve().parents[1] / "sim" / "cosys" / "factorycity"
)
CONFIG_PATH = FACTORYCITY_DIR / "factorycity_five_drone.template.json"
RUNTIME_PATH = FACTORYCITY_DIR / "factorycity_cosys_runtime.template.json"
WORLD_HASH = "2c56c0b11991719d88258d4aedefecf7a0314db3bf9a65a0f650b66372cd59eb"
PROBE_ADAPTER_PATH = FACTORYCITY_DIR / "tools" / "unreal_clearance_probe.py"
SETTINGS_SWITCH_PATH = FACTORYCITY_DIR / "tools" / "switch_cosys_settings.ps1"


@pytest.fixture
def config_loaded():
    return load_config(CONFIG_PATH)


@pytest.fixture
def runtime_loaded():
    return load_runtime_profile(RUNTIME_PATH)


@pytest.fixture
def runtime_document():
    return json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))


def _clearance_request(config_loaded, runtime_loaded):
    config, config_hash = config_loaded
    profile, profile_hash = runtime_loaded
    return render_clearance_request(
        config,
        config_hash,
        profile,
        profile_hash,
        WORLD_HASH,
        hashlib.sha256(PROBE_ADAPTER_PATH.read_bytes()).hexdigest(),
    )


def _clearance_result(request_bytes, *, ground_z=-1.0):
    request = json.loads(request_bytes)
    return {
        "schema": CLEARANCE_RESULT_SCHEMA_ID,
        "status": "PASS",
        "scene_validated": True,
        "captured_at_utc": "2026-08-21T00:00:00+00:00",
        "provider_id": request["provider_id"],
        "configuration_sha256": request["configuration_sha256"],
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        "world": {
            **request["world"],
            "loaded_world_name": "test-world",
            "observed_world_to_meters": request["coordinate_transform"][
                "world_to_meters"
            ],
        },
        "coordinate_transform": request["coordinate_transform"],
        "probes": [
            {
                **candidate,
                "ground_clear": True,
                "vertical_corridor_clear": True,
                "ground_z_ned_m": ground_z,
                "evidence_id": f"test/{candidate['candidate_id']}",
                "reason": "",
                "ground_hit": {},
                "corridor_hit": None,
            }
            for candidate in request["candidates"]
        ],
    }


def _write_result(tmp_path, result):
    path = tmp_path / "clearance-result.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    return path


def _generated_settings(tmp_path, config_loaded, runtime_loaded):
    config, config_hash = config_loaded
    profile, _ = runtime_loaded
    request = _clearance_request(config_loaded, runtime_loaded)
    result_path = _write_result(tmp_path, _clearance_result(request))
    provider, _, _ = load_clearance_result(
        result_path, config, config_hash, request
    )
    plan = generate_launch_plan(config, provider)
    content = render_cosys_settings(
        config, plan, build_cosys_base_settings(config, profile)
    )
    path = tmp_path / "settings.json"
    path.write_bytes(content)
    return path, json.loads(content)


def _vector(values):
    return SimpleNamespace(
        x_val=values["x"], y_val=values["y"], z_val=values["z"]
    )


class _ReadOnlyClient:
    def __init__(self, settings, *, collided_vehicle=None, running_settings=None):
        self.settings = settings
        self.collided_vehicle = collided_vehicle
        self.running_settings = running_settings or settings
        self.calls = []

    def _record(self, name):
        self.calls.append(name)

    def getSettingsString(self):
        self._record("getSettingsString")
        return json.dumps(self.running_settings)

    def listVehicles(self):
        self._record("listVehicles")
        return list(self.settings["Vehicles"])

    def getServerVersion(self):
        self._record("getServerVersion")
        return 4

    def getMinRequiredClientVersion(self):
        self._record("getMinRequiredClientVersion")
        return 1

    def getClientVersion(self):
        self._record("getClientVersion")
        return 4

    def simGetObjectPose(self, name, ned=True):
        self._record("simGetObjectPose")
        assert ned is True
        vehicle = self.settings["Vehicles"][name]
        return SimpleNamespace(
            position=_vector(
                {"x": vehicle["X"], "y": vehicle["Y"], "z": vehicle["Z"]}
            )
        )

    def simGetVehiclePose(self, vehicle_name):
        self._record("simGetVehiclePose")
        return SimpleNamespace(position=_vector({"x": 0.0, "y": 0.0, "z": 0.0}))

    def getMultirotorState(self, vehicle_name):
        self._record("getMultirotorState")
        return SimpleNamespace(landed_state=0)

    def simGetCollisionInfo(self, vehicle_name):
        self._record("simGetCollisionInfo")
        collided = vehicle_name == self.collided_vehicle
        return SimpleNamespace(
            has_collided=collided,
            object_name="Landscape_1" if collided else "",
            normal=SimpleNamespace(x_val=0.0, y_val=0.0, z_val=-1.0),
            penetration_depth=0.1 if collided else 0.0,
            time_stamp=1 if collided else 0,
        )

    def isApiControlEnabled(self, vehicle_name):
        self._record("isApiControlEnabled")
        return False


def _rpc_args(settings_path):
    return SimpleNamespace(
        config=CONFIG_PATH,
        runtime_profile=RUNTIME_PATH,
        settings=settings_path,
    )


def test_runtime_profile_loads_exact_protocol_values(runtime_loaded):
    profile, digest = runtime_loaded

    assert profile.settings_version == 2.0
    assert profile.status == "ACCEPTED_RUNTIME_VALIDATED"
    assert profile.image_codes == {"depth_camera": 2, "rgb_camera": 0}
    assert profile.sensor_codes == {
        "barometer": 1,
        "gnss": 3,
        "imu": 2,
        "magnetometer": 4,
    }
    assert len(digest) == 64


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda item: item.update({"unknown": True}), "unknown fields"),
        (
            lambda item: item["runtime"].update({"clock_speed": 0}),
            "positive finite",
        ),
        (
            lambda item: item["coordinate_transform"][
                "ned_to_unreal_axis_sign"
            ].update({"z": 0}),
            "axis signs",
        ),
        (
            lambda item: item["clearance_probe"].update(
                {"minimum_ground_normal_z": 1.1}
            ),
            "in \\(0, 1\\]",
        ),
    ],
)
def test_runtime_profile_rejects_incomplete_or_unsafe_values(
    runtime_document, mutation, match
):
    mutation(runtime_document)
    with pytest.raises(Phase3ContractError, match=match):
        validate_runtime_profile(runtime_document)


def test_base_settings_are_derived_from_config_and_runtime(
    config_loaded, runtime_loaded
):
    config, _ = config_loaded
    profile, _ = runtime_loaded

    settings = build_cosys_base_settings(config, profile)

    assert list(settings["Vehicles"]) == list(config.fleet.names)
    assert settings["LocalHostIp"] == config.endpoint.host
    assert settings["ApiServerPort"] == config.endpoint.port
    alpha = settings["Vehicles"][config.fleet.names[0]]
    assert set(alpha["Cameras"]) == {"front_rgb", "front_depth"}
    assert set(alpha["Sensors"]) == {"imu", "barometer", "magnetometer", "gnss"}
    assert alpha["Sensors"]["barometer"]["UpdateFrequency"] == 50.0
    assert "UpdateFrequency" not in alpha["Sensors"]["imu"]
    assert alpha["Cameras"]["front_depth"]["CaptureSettings"][0] == {
        "ImageType": 2,
        "Width": 640,
        "Height": 360,
        "FOV_Degrees": 90.0,
    }


def test_base_settings_follow_changed_roster_without_source_changes(
    runtime_loaded,
):
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    names = ("one", "two", "three")
    raw["fleet"]["expected_count"] = len(names)
    raw["fleet"]["vehicles"] = [
        {"name": name, "type": f"type-{index}", "role": "member"}
        for index, name in enumerate(names)
    ]
    raw["lifecycle"]["takeoff_order"] = list(names)
    raw["lifecycle"]["landing_order"] = list(reversed(names))
    raw["lifecycle"]["takeoff_altitude_offsets_m"] = [
        {"vehicle_name": name, "offset_m": 0.0} for name in names
    ]
    raw["sensors"]["vehicle_profiles"] = [
        {"vehicle_name": name, "profile": "standard-navigation-v1"}
        for name in names
    ]
    config = validate_config(raw)
    profile, _ = runtime_loaded

    settings = build_cosys_base_settings(config, profile)

    assert tuple(settings["Vehicles"]) == names
    assert tuple(
        item["VehicleType"] for item in settings["Vehicles"].values()
    ) == ("type-0", "type-1", "type-2")


def test_clearance_request_is_deterministic_and_complete(
    config_loaded, runtime_loaded
):
    config, _ = config_loaded
    first = _clearance_request(config_loaded, runtime_loaded)
    second = _clearance_request(config_loaded, runtime_loaded)
    request = json.loads(first)

    assert first == second
    assert len(request["candidates"]) == 36
    assert request["world"]["world_id"] == config.world.world_id
    assert request["probe_adapter_sha256"] == hashlib.sha256(
        PROBE_ADAPTER_PATH.read_bytes()
    ).hexdigest()
    assert request["probe_contract"]["required_clearance_m"] == (
        config.limits.collision_clearance_m
    )
    assert request["probe_contract"]["takeoff_corridor_start_clearance_m"] == (
        config.launch_area.takeoff_corridor_start_clearance_m
    )
    assert all(
        -4.5 <= item["x_ned_m"] <= 4.5
        and -4.5 <= item["y_ned_m"] <= 4.5
        for item in request["candidates"]
    )


def test_live_result_replays_through_launch_and_settings(
    tmp_path, config_loaded, runtime_loaded
):
    config, config_hash = config_loaded
    profile, _ = runtime_loaded
    request = _clearance_request(config_loaded, runtime_loaded)
    result_path = _write_result(tmp_path, _clearance_result(request))

    provider, evidence, digest = load_clearance_result(
        result_path, config, config_hash, request
    )
    plan = generate_launch_plan(config, provider)
    settings = json.loads(
        render_cosys_settings(config, plan, build_cosys_base_settings(config, profile))
    )

    assert provider.scene_validated is True
    assert evidence["scene_validated"] is True
    assert len(digest) == 64
    assert len(plan.positions) == config.fleet.expected_count
    assert set(settings["Vehicles"]) == set(config.fleet.names)
    assert all(
        {"X", "Y", "Z"} <= set(settings["Vehicles"][name])
        for name in config.fleet.names
    )


def test_rpc_verifier_repeats_only_read_only_checks(
    tmp_path, config_loaded, runtime_loaded, monkeypatch
):
    settings_path, settings = _generated_settings(
        tmp_path, config_loaded, runtime_loaded
    )
    client = _ReadOnlyClient(settings)
    monkeypatch.setattr(verify_phase3_rpc, "_connect", lambda _config: client)
    monkeypatch.setattr(verify_phase3_rpc.time, "sleep", lambda _seconds: None)

    result = verify_phase3_rpc.verify(_rpc_args(settings_path))

    config, _ = config_loaded
    assert result["status"] == "PASS"
    assert len(result["samples"]) == config.limits.verification_sample_count
    assert result["checks"]["running_settings_match"] is True
    assert set(client.calls) <= {
        "getSettingsString",
        "listVehicles",
        "getServerVersion",
        "getMinRequiredClientVersion",
        "getClientVersion",
        "simGetObjectPose",
        "simGetVehiclePose",
        "getMultirotorState",
        "simGetCollisionInfo",
        "isApiControlEnabled",
    }


def test_rpc_verifier_fails_with_collision_diagnostics(
    tmp_path, config_loaded, runtime_loaded, monkeypatch
):
    settings_path, settings = _generated_settings(
        tmp_path, config_loaded, runtime_loaded
    )
    client = _ReadOnlyClient(settings, collided_vehicle="bravo")
    monkeypatch.setattr(verify_phase3_rpc, "_connect", lambda _config: client)

    with pytest.raises(
        verify_phase3_rpc.LiveVerificationError, match="unsafe collision"
    ) as failure:
        verify_phase3_rpc.verify(_rpc_args(settings_path))

    assert failure.value.diagnostics["vehicle"]["collision"] == {
        "has_collided": True,
        "object_name": "Landscape_1",
        "normal": {"x": 0.0, "y": 0.0, "z": -1.0},
        "penetration_depth_m": 0.1,
        "timestamp": 1,
    }


def test_rpc_verifier_binds_running_server_settings(
    tmp_path, config_loaded, runtime_loaded, monkeypatch
):
    settings_path, settings = _generated_settings(
        tmp_path, config_loaded, runtime_loaded
    )
    changed = copy.deepcopy(settings)
    changed["ClockSpeed"] = settings["ClockSpeed"] + 1
    client = _ReadOnlyClient(settings, running_settings=changed)
    monkeypatch.setattr(verify_phase3_rpc, "_connect", lambda _config: client)

    with pytest.raises(
        verify_phase3_rpc.LiveVerificationError, match="do not match"
    ):
        verify_phase3_rpc.verify(_rpc_args(settings_path))


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda result: result.update({"request_sha256": "0" * 64}),
            "request hash mismatch",
        ),
        (
            lambda result: result.update({"scene_validated": False}),
            "not scene-validated",
        ),
        (
            lambda result: result["probes"].pop(),
            "does not cover every",
        ),
        (
            lambda result: result["probes"].append(copy.deepcopy(result["probes"][0])),
            "unexpected or duplicate",
        ),
        (
            lambda result: result["probes"][0].update({"x_ned_m": 999}),
            "geometry mismatch",
        ),
    ],
)
def test_clearance_result_fails_closed_on_tampering(
    tmp_path, config_loaded, runtime_loaded, mutation, match
):
    config, config_hash = config_loaded
    request = _clearance_request(config_loaded, runtime_loaded)
    result = _clearance_result(request)
    mutation(result)
    result_path = _write_result(tmp_path, result)

    with pytest.raises(Phase3ContractError, match=match):
        load_clearance_result(result_path, config, config_hash, request)


def test_settings_switch_round_trip_is_hash_guarded_and_recorded(tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")
    active = tmp_path / "active.json"
    candidate = tmp_path / "candidate.json"
    backup = tmp_path / "backup.json"
    activate_record = tmp_path / "activate.json"
    restore_record = tmp_path / "restore.json"
    previous_bytes = b'{"previous":true}\n'
    candidate_bytes = b'{"candidate":true}\n'
    active.write_bytes(previous_bytes)
    candidate.write_bytes(candidate_bytes)
    common = [
        powershell,
        "-NoProfile",
        "-File",
        str(SETTINGS_SWITCH_PATH),
        "-CandidateSettingsPath",
        str(candidate),
        "-ActiveSettingsPath",
        str(active),
        "-BackupSettingsPath",
        str(backup),
        "-ExpectedCandidateSha256",
        hashlib.sha256(candidate_bytes).hexdigest(),
        "-ExpectedPreviousSha256",
        hashlib.sha256(previous_bytes).hexdigest(),
    ]

    subprocess.run(
        common
        + [
            "-Action",
            "Activate",
            "-OperationRecordPath",
            str(activate_record),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert active.read_bytes() == candidate_bytes
    assert backup.read_bytes() == previous_bytes
    assert json.loads(activate_record.read_text())["action"] == "Activate"

    subprocess.run(
        common
        + [
            "-Action",
            "Restore",
            "-OperationRecordPath",
            str(restore_record),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert active.read_bytes() == previous_bytes
    assert json.loads(restore_record.read_text())["action"] == "Restore"
