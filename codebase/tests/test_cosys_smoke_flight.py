import json
import time
from types import SimpleNamespace

import pytest

from sim.cosys_smoke_flight import (
    CONFIG_SCHEMA,
    ConfigurationError,
    _write_create_once,
    run_smoke,
    validate_config,
)


def route_config():
    return {
        "schema": CONFIG_SCHEMA,
        "endpoint": {
            "host": "192.168.50.11",
            "port": 41451,
            "rpc_timeout_seconds": 1.0,
        },
        "vehicle": {"name": "alpha", "type": "SimpleFlight"},
        "frame": "NED_METRES",
        "route": {
            "a": {"x": 0.0, "y": 0.0, "z": 0.0},
            "takeoff_z_ned_m": -3.0,
            "b": {"x": 10.0, "y": 0.0, "z": -3.0},
        },
        "limits": {
            "start_tolerance_m": 0.25,
            "speed_mps": 2.0,
            "position_tolerance_m": 0.25,
            "hover_velocity_tolerance_mps": 0.1,
            "dwell_seconds": 0.001,
        },
        "timeouts_seconds": {
            "connect": 0.2,
            "vehicle_check": 0.2,
            "api_control": 0.2,
            "arm": 0.2,
            "takeoff": 0.2,
            "hover": 0.2,
            "move": 0.2,
            "arrival": 0.2,
            "land": 0.2,
            "cleanup": 0.2,
        },
        "evidence": {"output_path": "airsim_smoke.json"},
    }


class FakeFuture:
    def __init__(self, callback=None, delay=0.0):
        self.callback = callback
        self.delay = delay

    def join(self):
        if self.delay:
            time.sleep(self.delay)
        if self.callback:
            self.callback()


class FakeClient:
    def __init__(self, *, roster=None, move_delay=0.0):
        self.roster = roster or ["alpha", "bravo", "charlie"]
        self.move_delay = move_delay
        self.position = [0.0, 0.0, 0.0]
        self.velocity = [0.0, 0.0, 0.0]
        self.api_control = False
        self.armed = False
        self.landed_state = 0
        self.collided = False
        self.calls = []

    def ping(self):
        self.calls.append("ping")
        return True

    def listVehicles(self):
        self.calls.append("listVehicles")
        return list(self.roster)

    def enableApiControl(self, enabled, vehicle_name=""):
        self.calls.append(("enableApiControl", enabled, vehicle_name))
        self.api_control = enabled

    def isApiControlEnabled(self, vehicle_name=""):
        return self.api_control

    def armDisarm(self, armed, vehicle_name=""):
        self.calls.append(("armDisarm", armed, vehicle_name))
        self.armed = armed
        return True

    def takeoffAsync(self, timeout_sec=20, vehicle_name=""):
        self.calls.append(("takeoffAsync", timeout_sec, vehicle_name))
        return FakeFuture(lambda: setattr(self, "landed_state", 1))

    def hoverAsync(self, vehicle_name=""):
        self.calls.append(("hoverAsync", vehicle_name))
        return FakeFuture(lambda: setattr(self, "velocity", [0.0, 0.0, 0.0]))

    def moveToPositionAsync(
        self,
        x,
        y,
        z,
        velocity,
        timeout_sec,
        vehicle_name="",
    ):
        self.calls.append(
            (
                "moveToPositionAsync",
                x,
                y,
                z,
                velocity,
                timeout_sec,
                vehicle_name,
            )
        )

        def arrive():
            self.position = [x, y, z]
            self.velocity = [0.0, 0.0, 0.0]

        return FakeFuture(arrive, self.move_delay)

    def landAsync(self, timeout_sec=60, vehicle_name=""):
        self.calls.append(("landAsync", timeout_sec, vehicle_name))
        return FakeFuture(lambda: setattr(self, "landed_state", 0))

    def getMultirotorState(self, vehicle_name=""):
        vector = lambda values: SimpleNamespace(
            x_val=values[0], y_val=values[1], z_val=values[2]
        )
        return SimpleNamespace(
            timestamp=1,
            landed_state=self.landed_state,
            kinematics_estimated=SimpleNamespace(
                position=vector(self.position),
                linear_velocity=vector(self.velocity),
            ),
        )

    def simGetCollisionInfo(self, vehicle_name=""):
        return SimpleNamespace(
            has_collided=self.collided,
            object_name="test-wall" if self.collided else "",
            object_id=7 if self.collided else -1,
            time_stamp=1.0,
        )


def test_missing_route_value_is_refused_before_client_creation():
    raw = route_config()
    del raw["route"]["b"]

    with pytest.raises(ConfigurationError, match=r"config\.route\.b"):
        validate_config(raw)


def test_unknown_or_implicit_frame_is_refused():
    raw = route_config()
    raw["frame"] = "ENU"

    with pytest.raises(ConfigurationError, match="implicit frame conversion"):
        validate_config(raw)


def test_happy_path_records_landing_disarm_release_and_zero_collisions():
    config = validate_config(route_config())
    client = FakeClient()

    result = run_smoke(
        config,
        "a" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is True
    assert result["process_result"] == "PASS"
    assert result["position_error_m"] == 0.0
    assert result["collision_count"] == 0
    assert result["landing_confirmed"] is True
    assert result["disarm_confirmed"] is True
    assert result["api_control_released"] is True
    assert client.armed is False
    assert client.api_control is False


def test_missing_vehicle_fails_before_api_control():
    config = validate_config(route_config())
    client = FakeClient(roster=["bravo", "charlie"])

    result = run_smoke(
        config,
        "b" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert "absent from roster" in result["errors"][0]
    assert client.api_control is False
    assert client.armed is False


def test_move_timeout_attempts_land_disarm_and_api_release():
    raw = route_config()
    raw["timeouts_seconds"]["move"] = 0.01
    config = validate_config(raw)
    client = FakeClient(move_delay=0.1)

    result = run_smoke(
        config,
        "c" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert result["abort_attempted"] is True
    assert result["landing_confirmed"] is True
    assert result["disarm_confirmed"] is True
    assert result["api_control_released"] is True
    assert client.armed is False
    assert client.api_control is False
    assert any("exceeded" in error for error in result["errors"])


def test_collision_fails_and_enters_cleanup():
    config = validate_config(route_config())
    client = FakeClient()
    client.collided = True

    result = run_smoke(
        config,
        "d" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert result["collision_count"] == 1
    assert "collision detected" in result["errors"][0]
    assert client.api_control is False
    assert client.armed is False


def test_evidence_output_is_create_once(tmp_path):
    output = tmp_path / "airsim_smoke.json"
    _write_create_once(output, {"pass": False})

    assert json.loads(output.read_text(encoding="utf-8")) == {"pass": False}
    with pytest.raises(ConfigurationError, match="refusing to overwrite"):
        _write_create_once(output, {"pass": True})
