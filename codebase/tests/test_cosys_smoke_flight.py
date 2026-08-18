import json
import math
import time
from types import SimpleNamespace

import pytest
import sim.cosys_smoke_flight as smoke_module

from sim.cosys_smoke_flight import (
    CONFIG_SCHEMA,
    ConfigurationError,
    _flight_contract_sha256,
    _write_create_once,
    main,
    run_smoke,
    validate_config,
)


def route_config():
    return {
        "schema": CONFIG_SCHEMA,
        "runtime": {
            "qualification_configuration": "Q-C",
            "environment_family": "CityEnviron",
            "world_id": "/Game/Main_CityEnvironment",
            "unreal_engine_version": "5.8.1",
            "cosys_airsim_version": "3.4.1",
            "client_import": "cosysairsim",
            "client_version": "3.4.1",
            "client_artifact_sha256": "a" * 64,
            "settings_path": "D:/CityEnviron/settings.json",
            "settings_sha256": "b" * 64,
        },
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
        "geofence": {
            "min": {"x": -100.0, "y": -100.0, "z": -20.0},
            "max": {"x": 100.0, "y": 100.0, "z": 1.0},
        },
        "touchdown": {
            "expected_ground_object": "Ground",
            "stationary_velocity_tolerance_mps": 0.05,
            "stationary_angular_velocity_tolerance_rps": 0.05,
            "max_landing_linear_speed_mps": 1.0,
            "max_landing_angular_speed_rps": 0.5,
            "max_landing_roll_pitch_deg": 10.0,
            "max_penetration_depth_m": 0.05,
            "landing_zone_radius_m": 0.5,
            "landing_dwell_seconds": 2.0,
            "landed_state_deviation": {
                "id": "QB-LANDED-STATE-001",
                "approved": False,
                "approved_by": None,
                "approval_reference": None,
            },
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
            "land": 2.5,
            "cleanup": 2.5,
            "reset": 0.2,
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
    def __init__(
        self,
        *,
        roster=None,
        move_delay=0.0,
        lose_control_on_route_move=False,
        nonfinite_telemetry=False,
        startup_ground_contact=False,
        in_flight_collision=False,
        stale_landed_after_touchdown=False,
        touchdown_delay_checks=0,
        landing_linear_speed=0.0,
        landing_angular_speed=0.0,
        landing_roll_deg=0.0,
        penetration_depth=0.0,
        landing_offset_x=0.0,
        disarm_result=True,
        reset_position=None,
        reset_velocity=None,
        reset_angular_velocity=None,
        reset_landed_state=0,
        reset_api_control=False,
        post_contact_linear_speed_sequence=None,
        collision_during_route_poll=False,
    ):
        self.roster = roster or ["alpha", "bravo", "charlie"]
        self.move_delay = move_delay
        self.lose_control_on_route_move = lose_control_on_route_move
        self.nonfinite_telemetry = nonfinite_telemetry
        self.in_flight_collision = in_flight_collision
        self.stale_landed_after_touchdown = stale_landed_after_touchdown
        self.touchdown_delay_checks = touchdown_delay_checks
        self.landing_linear_speed = landing_linear_speed
        self.landing_angular_speed = landing_angular_speed
        self.landing_roll_deg = landing_roll_deg
        self.penetration_depth = penetration_depth
        self.landing_offset_x = landing_offset_x
        self.disarm_result = disarm_result
        self.reset_position = reset_position
        self.reset_velocity = reset_velocity
        self.reset_angular_velocity = reset_angular_velocity
        self.reset_landed_state = reset_landed_state
        self.reset_api_control = reset_api_control
        self.post_contact_linear_speed_sequence = list(
            post_contact_linear_speed_sequence or []
        )
        self.collision_during_route_poll = collision_during_route_poll
        self.route_future_active = False
        self.touchdown_pending = None
        self.landing_started = False
        self.move_call_count = 0
        self.position = [0.0, 0.0, 0.0]
        self.velocity = [0.0, 0.0, 0.0]
        self.angular_velocity = [0.0, 0.0, 0.0]
        self.api_control = False
        self.armed = False
        self.landed_state = 0
        self.collided = startup_ground_contact
        self.collision_object = "Ground" if startup_ground_contact else ""
        self.collision_timestamp = 1.0 if startup_ground_contact else 0.0
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
        if not armed and self.disarm_result is not True:
            return self.disarm_result
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
        self.move_call_count += 1
        if self.move_call_count >= 2:
            self.route_future_active = True
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
            if self.lose_control_on_route_move and self.move_call_count >= 2:
                self.api_control = False
            if self.in_flight_collision and self.move_call_count >= 2:
                self.collided = True
                self.collision_object = "Building"
                self.collision_timestamp += 1.0
            self.route_future_active = False

        return FakeFuture(arrive, self.move_delay)

    def landAsync(self, timeout_sec=60, vehicle_name=""):
        self.calls.append(("landAsync", timeout_sec, vehicle_name))

        def begin_touchdown():
            self.landing_started = True
            self.velocity = [self.landing_linear_speed, 0.0, 0.0]
            self.angular_velocity = [self.landing_angular_speed, 0.0, 0.0]
            self.position[0] += self.landing_offset_x
            self.touchdown_pending = self.touchdown_delay_checks
            if self.touchdown_pending == 0:
                self._complete_touchdown()

        return FakeFuture(begin_touchdown)

    def _complete_touchdown(self):
        self.calls.append("ground_contact")
        self.collided = True
        self.collision_object = "Ground"
        self.collision_timestamp += 1.0
        self.velocity = [0.0, 0.0, 0.0]
        self.angular_velocity = [0.0, 0.0, 0.0]
        self.landed_state = 1 if self.stale_landed_after_touchdown else 0
        self.touchdown_pending = None

    def getMultirotorState(self, vehicle_name=""):
        vector = lambda values: SimpleNamespace(
            x_val=values[0], y_val=values[1], z_val=values[2]
        )
        position = list(self.position)
        velocity = list(self.velocity)
        if (
            self.landing_started
            and self.touchdown_pending is None
            and self.collided
            and self.collision_object == "Ground"
            and self.post_contact_linear_speed_sequence
        ):
            velocity[0] = self.post_contact_linear_speed_sequence.pop(0)
        if self.nonfinite_telemetry:
            position[0] = float("nan")
        return SimpleNamespace(
            timestamp=1,
            landed_state=self.landed_state,
            kinematics_estimated=SimpleNamespace(
                position=vector(position),
                linear_velocity=vector(velocity),
                angular_velocity=vector(self.angular_velocity),
                orientation=SimpleNamespace(
                    w_val=math.cos(math.radians(self.landing_roll_deg) / 2.0),
                    x_val=math.sin(math.radians(self.landing_roll_deg) / 2.0),
                    y_val=0.0,
                    z_val=0.0,
                ),
            ),
        )

    def reset(self):
        self.calls.append("reset")
        self.position = list(self.reset_position or [0.0, 0.0, 0.0])
        self.velocity = list(self.reset_velocity or [0.0, 0.0, 0.0])
        self.angular_velocity = list(
            self.reset_angular_velocity or [0.0, 0.0, 0.0]
        )
        self.landed_state = self.reset_landed_state
        self.api_control = self.reset_api_control
        self.armed = False
        self.collided = True
        self.collision_object = "Ground"
        self.collision_timestamp += 1.0

    def simGetCollisionInfo(self, vehicle_name=""):
        self.calls.append("simGetCollisionInfo")
        if self.collision_during_route_poll and self.route_future_active:
            self.collided = True
            self.collision_object = "Building"
            self.collision_timestamp += 1.0
            self.collision_during_route_poll = False
        if self.touchdown_pending is not None:
            if self.touchdown_pending <= 0:
                self._complete_touchdown()
            else:
                self.touchdown_pending -= 1
        return SimpleNamespace(
            has_collided=self.collided,
            object_name=(self.collision_object or "test-wall") if self.collided else "",
            object_id=7 if self.collided else -1,
            time_stamp=self.collision_timestamp,
            penetration_depth=self.penetration_depth,
            normal=SimpleNamespace(x_val=0.0, y_val=0.0, z_val=-1.0),
            impact_point=SimpleNamespace(
                x_val=self.position[0],
                y_val=self.position[1],
                z_val=self.position[2],
            ),
            position=SimpleNamespace(
                x_val=self.position[0],
                y_val=self.position[1],
                z_val=self.position[2],
            ),
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


def test_legacy_q_a_runtime_is_refused_by_default_client():
    raw = route_config()
    raw["runtime"]["qualification_configuration"] = "Q-A"

    with pytest.raises(ConfigurationError, match="legacy AirSim 1.8"):
        validate_config(raw)


def test_route_outside_geofence_is_refused():
    raw = route_config()
    raw["route"]["b"]["x"] = 101.0

    with pytest.raises(ConfigurationError, match="outside the configured geofence"):
        validate_config(raw)


def test_unapproved_deviation_cannot_claim_suyash_approval_fields():
    raw = route_config()
    raw["touchdown"]["landed_state_deviation"]["approved_by"] = "Suyash"

    with pytest.raises(ConfigurationError, match="approval fields null"):
        validate_config(raw)


def test_touchdown_dwell_is_frozen_at_two_seconds():
    raw = route_config()
    raw["touchdown"]["landing_dwell_seconds"] = 1.99

    with pytest.raises(ConfigurationError, match="exactly 2.0"):
        validate_config(raw)


def test_flight_contract_hash_ignores_create_once_output_path():
    first = route_config()
    second = route_config()
    second["evidence"]["output_path"] = "cold_run_2.json"

    assert _flight_contract_sha256(validate_config(first)) == _flight_contract_sha256(
        validate_config(second)
    )


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
    assert result["disarm_return"] is True
    assert result["api_control_released"] is True
    assert result["api_control_enabled_after_release"] is False
    assert result["reset_confirmed"] is True
    assert result["reset_state"]["position_error_m"] == 0.0
    assert result["reset_state"]["api_control_enabled"] is False
    assert result["touchdown_dwell"]["completed_seconds"] >= 2.0
    assert client.armed is False
    assert client.api_control is False
    transitions = [row["state"] for row in result["transitions"]]
    assert transitions.index("MOVE_A_TO_B") < transitions.index("FINAL_HOVER")
    assert transitions.index("FINAL_HOVER") < transitions.index("LAND")
    assert transitions.index("RELEASE_API") < transitions.index("RESET_VERIFY")
    release_index = client.calls.index(("enableApiControl", False, "alpha"))
    assert release_index < client.calls.index("reset")


def test_expected_startup_ground_contact_is_baselined_not_counted():
    config = validate_config(route_config())
    client = FakeClient(startup_ground_contact=True)

    result = run_smoke(
        config,
        "2" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is True
    assert result["initial_collision"]["object_name"] == "Ground"
    assert result["collision_count"] == 0
    assert set(result["initial_collision"]) >= {
        "has_collided",
        "object_name",
        "object_id",
        "timestamp",
        "penetration_depth_m",
        "normal",
        "impact_point",
        "position",
    }
    assert [row["phase"] for row in result["ground_contacts"]] == [
        "startup",
        "touchdown",
    ]


def test_startup_ground_contact_must_be_stationary():
    config = validate_config(route_config())
    client = FakeClient(startup_ground_contact=True)
    client.velocity = [0.1, 0.0, 0.0]

    result = run_smoke(
        config,
        "2" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert any("startup Ground contact is not stationary" in error for error in result["errors"])
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


def test_new_in_flight_collision_timestamp_fails():
    config = validate_config(route_config())
    client = FakeClient(startup_ground_contact=True, in_flight_collision=True)

    result = run_smoke(
        config,
        "3" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert result["collision_count"] >= 1
    assert any("non-ground contact during move" in error for error in result["errors"])


def test_collision_is_detected_while_transit_future_is_still_running():
    config = validate_config(route_config())
    client = FakeClient(
        move_delay=0.1,
        collision_during_route_poll=True,
    )

    result = run_smoke(
        config,
        "4" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert result["collision_count"] >= 1
    assert any(
        "non-ground contact during move" in error for error in result["errors"]
    )


def test_early_returning_land_future_waits_for_current_ground_and_two_second_dwell():
    config = validate_config(route_config())
    client = FakeClient(touchdown_delay_checks=2)

    result = run_smoke(
        config,
        "4" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is True
    assert result["landing_confirmed"] is True
    assert result["land_async_join"]["phase"] == "land_async_join"
    assert result["land_async_join"]["state"]["landed_state"] == 1
    assert result["land_async_join"]["collision"]["has_collided"] is False
    assert result["ground_contacts"][-1]["phase"] == "touchdown"
    assert (
        result["land_async_join"]["collision"]["timestamp"]
        < result["ground_contacts"][-1]["timestamp"]
    )
    assert result["touchdown_dwell"]["contact_event_timestamp"] is not None
    assert result["touchdown_dwell"]["completed_seconds"] >= 2.0
    assert client.calls.index("ground_contact") < client.calls.index(
        ("armDisarm", False, "alpha")
    )


def test_touchdown_dwell_resets_after_stationary_threshold_violation():
    config = validate_config(route_config())
    client = FakeClient(
        post_contact_linear_speed_sequence=[0.0, 0.0, 0.0, 0.2, 0.0]
    )

    result = run_smoke(
        config,
        "7" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is True
    assert result["touchdown_dwell"]["reset_count"] >= 1
    assert any(
        "stationary linear speed" in violation
        for sample in result["touchdown_threshold_violations"]
        for violation in sample["violations"]
    )


@pytest.mark.parametrize(
    ("client_kwargs", "expected_error"),
    [
        (
            {"landing_linear_speed": 1.1, "touchdown_delay_checks": 2},
            "linear speed",
        ),
        (
            {"landing_angular_speed": 0.6, "touchdown_delay_checks": 2},
            "angular speed",
        ),
        (
            {"landing_roll_deg": 11.0, "touchdown_delay_checks": 2},
            "roll/pitch",
        ),
        (
            {"penetration_depth": 0.06, "touchdown_delay_checks": 2},
            "penetration depth",
        ),
        (
            {"landing_offset_x": 0.6, "touchdown_delay_checks": 2},
            "landing-zone error",
        ),
    ],
)
def test_landing_envelope_violation_fails_capture(client_kwargs, expected_error):
    config = validate_config(route_config())
    client = FakeClient(**client_kwargs)

    result = run_smoke(
        config,
        "8" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert any(expected_error in error for error in result["errors"])


def test_stale_landed_state_requires_explicit_deviation_approval():
    config = validate_config(route_config())
    client = FakeClient(stale_landed_after_touchdown=True)

    result = run_smoke(
        config,
        "5" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert any("requires Suyash approval" in error for error in result["errors"])
    assert (
        result["touchdown_policy"]["landed_state_deviation"]["applied"]
        is False
    )


def test_approved_stale_landed_state_deviation_is_recorded_when_applied():
    raw = route_config()
    raw["runtime"]["qualification_configuration"] = "Q-B"
    raw["runtime"]["environment_family"] = "Blocks"
    raw["vehicle"]["name"] = "Drone1"
    deviation = raw["touchdown"]["landed_state_deviation"]
    deviation["approved"] = True
    deviation["approved_by"] = "Suyash"
    deviation["approval_reference"] = "written-QB-LANDED-STATE-001"
    config = validate_config(raw)
    client = FakeClient(
        roster=["Drone1"], stale_landed_after_touchdown=True
    )

    result = run_smoke(
        config,
        "6" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is True
    recorded = result["touchdown_policy"]["landed_state_deviation"]
    assert recorded["approved"] is True
    assert recorded["applied"] is True
    assert recorded["approval_reference"] == "written-QB-LANDED-STATE-001"


def test_landed_state_deviation_cannot_apply_outside_q_b_simpleflight():
    raw = route_config()
    deviation = raw["touchdown"]["landed_state_deviation"]
    deviation["approved"] = True
    deviation["approved_by"] = "Suyash"
    deviation["approval_reference"] = "QB-LANDED-STATE-001-COMPARISON-01"

    with pytest.raises(ConfigurationError, match="only to Q-B SimpleFlight"):
        validate_config(raw)


def test_disarm_must_return_true_and_failed_capture_still_releases_api():
    config = validate_config(route_config())
    client = FakeClient(disarm_result=False)

    result = run_smoke(
        config,
        "9" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert result["disarm_confirmed"] is False
    assert result["api_control_released"] is True
    assert result["reset_confirmed"] is False
    assert any("disarm did not return true" in error for error in result["errors"])


@pytest.mark.parametrize(
    "client_kwargs",
    [
        {"reset_position": [1.0, 0.0, 0.0]},
        {"reset_velocity": [0.1, 0.0, 0.0]},
        {"reset_angular_velocity": [0.1, 0.0, 0.0]},
        {"reset_landed_state": 1},
    ],
)
def test_reset_must_restore_a_stationary_landed_and_api_off(client_kwargs):
    raw = route_config()
    raw["timeouts_seconds"]["reset"] = 0.05
    config = validate_config(raw)
    client = FakeClient(**client_kwargs)

    result = run_smoke(
        config,
        "0" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert result["api_control_released"] is True
    assert result["reset_attempted"] is True
    assert result["reset_confirmed"] is False
    assert any("reset did not restore" in error for error in result["errors"])


def test_reset_fails_if_api_control_reenables():
    config = validate_config(route_config())
    client = FakeClient(reset_api_control=True)

    result = run_smoke(
        config,
        "a" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert result["reset_confirmed"] is False
    assert any(
        "API control became enabled during reset verification" in error
        for error in result["errors"]
    )


class ApiReleaseRefusalClient(FakeClient):
    def enableApiControl(self, enabled, vehicle_name=""):
        self.calls.append(("enableApiControl", enabled, vehicle_name))
        if enabled:
            self.api_control = True


def test_api_control_off_must_be_observed_before_reset():
    config = validate_config(route_config())
    client = ApiReleaseRefusalClient()

    result = run_smoke(
        config,
        "b" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert result["api_control_released"] is False
    assert result["reset_attempted"] is False
    assert any("API control remained enabled" in error for error in result["errors"])


def test_nonfinite_live_telemetry_fails_before_api_control():
    config = validate_config(route_config())
    client = FakeClient(nonfinite_telemetry=True)

    result = run_smoke(
        config,
        "e" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert "non-finite position vector" in result["errors"][0]
    assert client.api_control is False
    assert client.armed is False


def test_in_flight_api_control_loss_fails_and_cleans_up():
    config = validate_config(route_config())
    client = FakeClient(lose_control_on_route_move=True)

    result = run_smoke(
        config,
        "f" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert any("API control lost during move" in error for error in result["errors"])
    assert result["abort_attempted"] is True
    assert result["cleanup_complete"] is True
    assert client.armed is False
    assert client.api_control is False


class CleanupFailClient(FakeClient):
    def __init__(self):
        super().__init__(lose_control_on_route_move=True)

    def landAsync(self, timeout_sec=60, vehicle_name=""):
        raise RuntimeError("cleanup land unavailable")

    def armDisarm(self, armed, vehicle_name=""):
        if not armed:
            raise RuntimeError("cleanup disarm unavailable")
        return super().armDisarm(armed, vehicle_name)

    def enableApiControl(self, enabled, vehicle_name=""):
        if not enabled:
            raise RuntimeError("cleanup API release unavailable")
        return super().enableApiControl(enabled, vehicle_name)


def test_cleanup_failure_is_retained_and_cannot_pass():
    config = validate_config(route_config())
    client = CleanupFailClient()

    result = run_smoke(
        config,
        "1" * 64,
        client_factory=lambda _config: client,
        landed_state_value=0,
    )

    assert result["pass"] is False
    assert result["abort_attempted"] is True
    assert result["cleanup_complete"] is False
    assert any("cleanup land unavailable" in error for error in result["errors"])
    assert any("cleanup disarm unavailable" in error for error in result["errors"])
    assert any("cleanup API release unavailable" in error for error in result["errors"])


def test_main_returns_nonzero_and_preserves_json_for_failed_capture(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "route.json"
    output_path = tmp_path / "failed.json"
    config_path.write_text(json.dumps(route_config()), encoding="utf-8")
    client = FakeClient(roster=["bravo"])
    monkeypatch.setattr(
        smoke_module,
        "_live_client_factory",
        lambda _config: (client, 0),
    )

    exit_code = main(
        ["--config", str(config_path), "--out", str(output_path)]
    )

    assert exit_code != 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["pass"] is False
    assert result["process_result"] == "FAIL"


def test_evidence_output_is_create_once(tmp_path):
    output = tmp_path / "airsim_smoke.json"
    _write_create_once(output, {"pass": False})

    assert json.loads(output.read_text(encoding="utf-8")) == {"pass": False}
    with pytest.raises(ConfigurationError, match="refusing to overwrite"):
        _write_create_once(output, {"pass": True})
