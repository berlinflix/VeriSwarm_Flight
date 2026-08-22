from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
WORLD = ROOT / "sim" / "cosys" / "factorycity"
LAYER = WORLD / "factorycity_disaster.development.json"
CAMERAS = WORLD / "factorycity_disaster_camera_profile.json"
RISING_FLOOD = WORLD / "factorycity_rising_flood.development.json"
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
    assert flood["restore_initial_level_before_landing"] is True
    assert all(0.0 <= value <= 1.0 for value in config["weather"].values())


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
