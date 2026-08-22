from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
WORLD = ROOT / "sim" / "cosys" / "factorycity"
LAYER = WORLD / "factorycity_disaster.development.json"
CAMERAS = WORLD / "factorycity_disaster_camera_profile.json"
TOOLS = WORLD / "tools"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_disaster_layer_is_explicitly_development_only() -> None:
    config = _json(LAYER)
    assert config["schema"] == "veriswarm.factorycity.disaster_layer.v1"
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
