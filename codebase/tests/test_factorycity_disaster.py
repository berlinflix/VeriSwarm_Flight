from __future__ import annotations

import ast
import json
import math
from pathlib import Path


ROOT = Path(__file__).parents[1]
WORLD = ROOT / "sim" / "cosys" / "factorycity"
LAYER = WORLD / "factorycity_disaster.development.json"
CAMERAS = WORLD / "factorycity_disaster_camera_profile.json"
RISING_FLOOD = WORLD / "factorycity_rising_flood.development.json"
POINT_B_AUDIT = WORLD / "factorycity_point_b_audit.development.json"
POINT_B_COLLISION = WORLD / "factorycity_point_b_landing_collision.development.json"
AB_MISSION = WORLD / "factorycity_ab_mission.development.json"
JOINT_MOVEMENT = WORLD / "factorycity_joint_movement_contract.development.json"
TOOLS = WORLD / "tools"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_disaster_layer_is_explicitly_development_only() -> None:
    config = _json(LAYER)
    assert config["schema"] == "veriswarm.factorycity.disaster_layer.v2"
    assert config["development_only"] is True
    assert config["world"] == "/Game/VeriSwarm/FactoryCity_Disaster"
    assert config["point_a_exclusion_radius_cm"] > 0


def test_disaster_actor_ids_are_unique_and_transforms_are_complete() -> None:
    actors = _json(LAYER)["actors"]
    ids = [actor["id"] for actor in actors]
    assert len(ids) == len(set(ids))
    assert ids
    for actor in actors:
        assert actor["anchor_actor"]
        assert actor["asset"]
        assert len(actor["offset_cm"]) == 3
        assert len(actor["rotation_rpy_deg"]) == 3
        assert len(actor["scale"]) == 3
        assert all(value > 0 for value in actor["scale"])


def test_disaster_collision_policy_is_fail_closed() -> None:
    actors = _json(LAYER)["actors"]
    water = [actor for actor in actors if actor["kind"] == "water_or_flood"]
    physical = [actor for actor in actors if actor["kind"] != "water_or_flood"]
    assert water and physical
    assert all(actor["collision_profile"] == "NoCollision" for actor in water)
    assert all(actor["collision_profile"] == "BlockAll" for actor in physical)


def test_flood_surface_covers_landscape_and_is_runtime_movable() -> None:
    water = [
        actor
        for actor in _json(LAYER)["actors"]
        if actor["kind"] == "water_or_flood"
    ]
    assert len(water) == 1
    surface = water[0]
    assert surface["id"] == "global_flood_surface"
    assert surface["anchor_actor"] == "Landscape1"
    assert surface["placement_mode"] == "fit_anchor_xy_bounds"
    assert surface["bounds_margin_cm"] > 0
    assert surface["runtime_movable"] is True
    assert surface["allow_within_point_a_exclusion"] is True


def test_rising_flood_controller_has_safe_configured_clearance() -> None:
    config = _json(RISING_FLOOD)
    assert config["schema"] == "veriswarm.factorycity.rising_flood.v1"
    assert config["development_only"] is True
    assert len(config["vehicles"]) == 5
    assert len(set(config["vehicles"])) == 5
    flight = config["flight"]
    flood = config["flood"]
    assert flight["desired_clearance_above_water_m"] > flight[
        "minimum_clearance_above_water_m"
    ]
    assert flight["minimum_pairwise_separation_m"] > 0
    assert flood["rise_height_m"] > 0
    assert flood["rise_duration_seconds"] > flood["update_period_seconds"]
    assert flood["peak_hold_mode"] == "until_interrupted"
    assert flood["peak_keepalive_seconds"] > 0
    assert flood["peak_evidence_interval_seconds"] >= flood["peak_keepalive_seconds"]
    assert flood["restore_initial_level_before_landing"] is True
    assert all(0.0 <= value <= 1.0 for value in config["weather"].values())
    assert config["weather"]["Fog"] == 0.0
    assert config["weather"]["Dust"] == 0.0
    assert 0.0 < config["weather"]["Rain"] <= 0.10


def test_camera_profile_has_rgb_and_depth_planar() -> None:
    profile = _json(CAMERAS)
    assert profile["schema"] == "veriswarm.factorycity.camera_profile.v1"
    assert profile["view_mode"] == "SpringArmChase"
    cameras = profile["cameras"]
    assert cameras["front_rgb"]["capture"]["image_type"] == 0
    assert cameras["front_depth"]["capture"]["image_type"] == 1
    assert cameras["front_rgb"]["position_ned_m"][0] > 0
    assert cameras["front_depth"]["position_ned_m"][0] > 0
    assert cameras["front_rgb"]["capture"]["width"] > 0
    assert cameras["front_rgb"]["capture"]["height"] > 0


def test_unreal_python_tools_parse_without_importing_unreal() -> None:
    paths = sorted(TOOLS.glob("*.py"))
    assert paths
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_rising_flood_uses_shared_world_frame_for_separation() -> None:
    source = (TOOLS / "run_rising_flood_swarm.py").read_text(encoding="utf-8")
    assert "simGetObjectPose(vehicle, ned=True)" in source
    assert "pose = client.simGetVehiclePose(vehicle_name=vehicle)" not in source
    assert "transition_started = time.monotonic()" in source
    assert "scheduled_step_end = transition_started + duration_seconds * fraction" in source
    assert 'value["timestamp"] > collision_baseline[name]' in source


def test_point_b_audit_contract_covers_five_drone_landing_footprint() -> None:
    config = _json(POINT_B_AUDIT)
    assert config["schema"] == "veriswarm.factorycity.point_b_audit.v1"
    assert config["development_only"] is True
    assert config["world"] == "/Game/VeriSwarm/FactoryCity_Disaster"
    assert config["point_a_actor"] == "Point_A"
    assert config["point_b_actor"] == "Point_B"
    assert set(config["formation_offsets_cm"]) == {
        "alpha",
        "bravo",
        "charlie",
        "delta",
        "echo",
    }
    assert len(config["footprint_probe_offsets_cm"]) >= 9
    validation = config["validation"]
    assert validation["minimum_route_distance_m"] > 0
    assert validation["maximum_route_distance_m"] > validation[
        "minimum_route_distance_m"
    ]
    assert validation["minimum_roof_above_peak_flood_m"] > 0
    assert validation["trace_complex"] is False
    assert 0.0 < validation["minimum_surface_normal_z"] <= 1.0


def test_point_b_audit_is_read_only_and_config_driven() -> None:
    source = (TOOLS / "unreal_point_b_audit.py").read_text(encoding="utf-8")
    assert "line_trace_single" in source
    assert "save_current_level" not in source
    assert "save_map" not in source
    assert "set_actor_" not in source
    assert 'config["footprint_probe_offsets_cm"]' in source


def test_point_b_collision_contract_is_invisible_physical_and_bounded() -> None:
    config = _json(POINT_B_COLLISION)
    assert config["schema"] == "veriswarm.factorycity.point_b_landing_collision.v1"
    assert config["development_only"] is True
    assert config["point_b_actor"] == "Point_B"
    assert config["support_actor"]
    assert config["collision_profile"] == "BlockAll"
    assert config["hidden_in_game"] is True
    assert len(config["platform_size_cm"]) == 3
    assert config["platform_size_cm"][0] >= 500.0
    assert config["platform_size_cm"][1] >= 500.0
    assert config["platform_size_cm"][2] > 0.0
    assert config["minimum_support_edge_margin_cm"] > 0.0


def test_point_b_collision_builder_is_fail_closed() -> None:
    source = (TOOLS / "unreal_add_point_b_landing_collision.py").read_text(
        encoding="utf-8"
    )
    assert "refusing to replace existing actor" in source
    assert "QUERY_AND_PHYSICS" in source
    assert 'set_collision_profile_name("BlockAll")' in source
    assert "set_visibility(False" in source
    assert "save_current_level" in source


def test_ab_mission_is_five_drone_straight_flood_safe_and_map_bound() -> None:
    config = _json(AB_MISSION)
    assert config["schema"] == "veriswarm.factorycity.ab_mission.v1"
    assert config["development_only"] is True
    assert len(config["source_map"]["sha256"]) == 64
    assert len(config["vehicles"]) == 5
    assert set(config["vehicles"]) == set(config["formation_offsets_ned_m"])
    route = config["route"]
    assert len(route["target_delta_ned_m"]) == 2
    assert math.isclose(
        math.hypot(*route["target_delta_ned_m"]),
        route["horizontal_distance_m"],
        abs_tol=1e-6,
    )
    assert route["cruise_z_ned_m"] == -10.0
    assert route["control_step_seconds"] > 0.0
    assert config["flood"]["manage_water_to_peak"] is True
    assert config["flood"]["leave_water_at_peak_after_mission"] is True
    assert config["collision_policy"]["monitor_from_state"] == "EN_ROUTE"
    assert config["collision_policy"]["ignore_takeoff_and_landing_collisions"] is True
    assert config["landing"]["collision_actor"] == "VS_PointB_LandingCollision"


def test_ab_controller_monitors_only_new_enroute_collisions_and_cleans_up() -> None:
    source = (TOOLS / "run_factorycity_ab_swarm.py").read_text(encoding="utf-8")
    assert "moveByVelocityZAsync" in source
    assert 'states[vehicle] = "EN_ROUTE"' in source
    assert 'sample["timestamp"] > collision_baseline[vehicle]' in source
    assert 'states[vehicle] = "DESTROYED_BY_COLLISION"' in source
    assert "target_z = min(" in source
    assert "_raise_water_to_peak(" in source
    assert "simSetObjectPose" in source
    assert "initial_clearance_z = max(" in source
    assert "route_started = time.monotonic()" in source
    assert "client.landAsync(" in source
    assert "cosysairsim.LandedState.Landed" in source
    assert "client.isApiControlEnabled" in source
    assert 'status = "PARTIAL_COLLISION"' in source
    assert "client.armDisarm(False" in source
    assert "client.enableApiControl(False" in source
    assert "finally:" in source


def test_joint_movement_contract_freezes_exact_endpoints_roster_and_cells() -> None:
    contract = _json(JOINT_MOVEMENT)
    assert contract["schema"] == "veriswarm.factorycity.joint_movement_contract.v1"
    assert contract["development_only"] is True
    assert contract["coordinate_frame"]["name"] == "NED"
    assert len(contract["map_binding"]["sha256"]) == 64
    assert contract["endpoints_ned_m"]["point_a_actor"] == [0.0, 0.0, 0.0]
    assert len(contract["endpoints_ned_m"]["point_b_actor"]) == 3
    roster = contract["vehicles"]["roster"]
    poses = contract["vehicles"]["initial_poses_ned_m"]
    assert len(roster) == 5
    assert len(set(roster)) == 5
    assert set(roster) == set(poses)
    assert all(len(pose) == 3 for pose in poses.values())
    cells = contract["search_cells"]["cells"]
    assert len(cells) == 10
    assert {cell["owner"] for cell in cells} == set(roster)
    assert math.isclose(cells[0]["start_fraction"], 0.0)
    assert math.isclose(cells[-1]["end_fraction"], 1.0)
    assert all(
        math.isclose(left["end_fraction"], right["start_fraction"])
        for left, right in zip(cells, cells[1:])
    )


def test_joint_movement_contract_is_fail_closed_and_preserves_observations() -> None:
    contract = _json(JOINT_MOVEMENT)
    policy = contract["authorization_policy"]
    assert policy["missing_stale_or_malformed_authorization"] == "HOLD"
    assert policy["ALLOW"]["pending_commands"].startswith("release")
    assert policy["HOLD"]["pending_commands"] == "do not dispatch"
    assert "hover" in policy["HOLD"]["in_flight"]
    assert "reassign" in policy["QUARANTINE"]["unfinished_cells"]
    assert policy["QUARANTINE"]["terminal_vehicle_state"] == "QUARANTINED"
    assert contract["search_cells"]["reassignment"][
        "preserve_positive_observations"
    ] is True


def test_joint_movement_contract_never_uses_truth_or_depth_for_avoidance() -> None:
    contract = _json(JOINT_MOVEMENT)
    truth = contract["evaluation_truth"]
    collision = contract["obstacle_and_collision_policy"]
    assert truth["controller_access"] == "FORBIDDEN"
    assert truth["survivor_locations"] == []
    assert collision["route_policy"] == (
        "do not deviate from the configured straight line"
    )
    assert collision["depth_use"] == (
        "telemetry_and_post_run_evaluation_only_no_avoidance"
    )
    assert collision["predeclared_route_obstacles_consumed_by_controller"] == []
    assert collision["collision_monitoring_starts"] == "EN_ROUTE"
    assert collision["takeoff_and_landing_penetration_terminal"] is False


def test_joint_movement_events_match_frozen_rescue_data_plane_surface() -> None:
    events = _json(JOINT_MOVEMENT)["events"]
    assert events["schema"] == "veriswarm.rescue.event.v1"
    assert events["authorization_source"] == "abhijan-security"
    assert events["vehicle_source_pattern"] == "<node>.telemetry"
    assert set(events["required_top_level_fields"]) == {
        "schema",
        "mission_id",
        "event_id",
        "source",
        "source_seq",
        "observed_at_ms",
        "kind",
        "payload",
    }
    assert {
        "mission_started",
        "assignment",
        "coverage",
        "vehicle_state",
        "authorization",
        "task_reassigned",
        "link_state",
        "mission_completed",
    } == set(events["required_kinds"])
    assert "position_ned" in events["required_payload_fields"]["vehicle_state"]
    assert "decision" in events["required_payload_fields"]["authorization"]
    assert events["vehicle_state_mapping"]["DESTROYED_BY_COLLISION"] == "FAILED"
    assert events["vehicle_state_mapping"]["QUARANTINE"] == "QUARANTINED"
