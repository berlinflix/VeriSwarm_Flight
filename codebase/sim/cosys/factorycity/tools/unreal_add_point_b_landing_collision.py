"""Add one invisible, collision-enabled landing pad beneath Point_B."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path

import unreal


CONFIG_ENV = "VERISWARM_POINT_B_COLLISION_CONFIG"
OUTPUT_ENV = "VERISWARM_POINT_B_COLLISION_OUTPUT"
TARGET_MAP_ENV = "VERISWARM_DISASTER_MAP_FILE"


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _vector(value: object) -> dict[str, float]:
    return {"x": float(value.x), "y": float(value.y), "z": float(value.z)}


def _exact_actor(actors: list[object], label: str) -> object:
    matches = [actor for actor in actors if actor.get_actor_label() == label]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {label!r} actor, found {len(matches)}")
    return matches[0]


def _main() -> None:
    config_path = Path(_required_environment(CONFIG_ENV))
    output_path = Path(_required_environment(OUTPUT_ENV))
    target_map = Path(_required_environment(TARGET_MAP_ENV))
    if output_path.exists():
        raise RuntimeError(f"refusing to overwrite result: {output_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != "veriswarm.factorycity.point_b_landing_collision.v1":
        raise RuntimeError("unsupported Point_B collision config schema")
    size = tuple(float(value) for value in config["platform_size_cm"])
    if len(size) != 3 or not all(math.isfinite(value) and value > 0.0 for value in size):
        raise RuntimeError("platform_size_cm must contain three positive finite values")
    if str(config["collision_profile"]) != "BlockAll":
        raise RuntimeError("Point_B landing collision must use BlockAll")
    world = unreal.EditorLoadingAndSavingUtils.load_map(str(config["world"]))
    if world is None:
        raise RuntimeError(f"failed to load {config['world']}")
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = list(subsystem.get_all_level_actors())
    collision_label = str(config["collision_actor_label"])
    if any(actor.get_actor_label() == collision_label for actor in actors):
        raise RuntimeError(f"refusing to replace existing actor {collision_label!r}")
    point_b = _exact_actor(actors, str(config["point_b_actor"]))
    support = _exact_actor(actors, str(config["support_actor"]))
    point = point_b.get_actor_location()
    support_origin, support_extent = support.get_actor_bounds(False)
    support_top = float(support_origin.z + support_extent.z)
    marker_gap = float(point.z) - support_top
    maximum_gap = float(config["maximum_marker_above_surface_cm"])
    if not 0.0 <= marker_gap <= maximum_gap:
        raise RuntimeError(
            f"Point_B surface gap {marker_gap:.3f} cm is outside [0, {maximum_gap}]"
        )
    margin = float(config["minimum_support_edge_margin_cm"])
    half_x, half_y = size[0] / 2.0, size[1] / 2.0
    if not (
        support_origin.x - support_extent.x + margin <= point.x - half_x
        and point.x + half_x <= support_origin.x + support_extent.x - margin
        and support_origin.y - support_extent.y + margin <= point.y - half_y
        and point.y + half_y <= support_origin.y + support_extent.y - margin
    ):
        raise RuntimeError("configured landing platform exceeds support actor bounds")
    asset = unreal.load_asset(str(config["asset"]))
    if not isinstance(asset, unreal.StaticMesh):
        raise RuntimeError(f"landing collision asset is not a StaticMesh: {config['asset']}")
    location = unreal.Vector(point.x, point.y, support_top - size[2] / 2.0)
    actor = subsystem.spawn_actor_from_class(
        unreal.StaticMeshActor,
        location,
        unreal.Rotator(roll=0.0, pitch=0.0, yaw=0.0),
        transient=False,
    )
    if actor is None:
        raise RuntimeError("failed to spawn Point_B landing collision actor")
    actor.set_actor_label(collision_label, mark_dirty=True)
    actor.tags = ["VS_LandingSafety", "VS_PointB", "VS_Development"]
    actor.set_actor_scale3d(unreal.Vector(size[0] / 100.0, size[1] / 100.0, size[2] / 100.0))
    component = actor.get_component_by_class(unreal.StaticMeshComponent)
    if component is None:
        raise RuntimeError("landing collision actor has no StaticMeshComponent")
    component.set_static_mesh(asset)
    component.set_collision_profile_name("BlockAll")
    component.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
    component.set_visibility(False, True)
    actor.set_actor_hidden_in_game(bool(config["hidden_in_game"]))
    level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level_subsystem.save_current_level():
        raise RuntimeError("failed to save FactoryCity_Disaster")
    result = {
        "schema": "veriswarm.factorycity.point_b_landing_collision_result.v1",
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "PASS",
        "development_only": bool(config["development_only"]),
        "world": config["world"],
        "point_b_actor": point_b.get_actor_label(),
        "support_actor": support.get_actor_label(),
        "collision_actor": actor.get_actor_label(),
        "location_unreal_cm": _vector(location),
        "platform_size_cm": list(size),
        "support_top_world_z_cm": support_top,
        "point_b_marker_gap_cm": marker_gap,
        "collision_profile": "BlockAll",
        "hidden_in_game": bool(config["hidden_in_game"]),
        "config_sha256": _sha256(config_path),
        "map_sha256_after": _sha256(target_map),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    unreal.log(f"Point_B landing collision added: {output_path}")


_main()
