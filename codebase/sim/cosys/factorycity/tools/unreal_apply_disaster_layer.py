"""Apply an additive, manifest-driven disaster layer to a derived Unreal level."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path

import unreal


CONFIG_ENV = "VERISWARM_DISASTER_LAYER_CONFIG"
OUTPUT_ENV = "VERISWARM_DISASTER_LAYER_OUTPUT"
TARGET_MAP_ENV = "VERISWARM_DISASTER_MAP_FILE"
ROOT_TAG = "VS_Disaster"


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _triple(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise RuntimeError(f"{field} must be a three-number array")
    numbers = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in numbers):
        raise RuntimeError(f"{field} contains a non-finite value")
    return numbers


def _load_config(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = (
        "schema",
        "scenario_id",
        "development_only",
        "world",
        "point_a_actor",
        "point_a_exclusion_radius_cm",
        "actors",
    )
    missing = [key for key in required if key not in data]
    if missing:
        raise RuntimeError(f"disaster config missing fields: {missing}")
    if data["schema"] != "veriswarm.factorycity.disaster_layer.v1":
        raise RuntimeError(f"unsupported disaster config schema {data['schema']!r}")
    if not isinstance(data["development_only"], bool):
        raise RuntimeError("development_only must be boolean")
    if not isinstance(data["actors"], list) or not data["actors"]:
        raise RuntimeError("actors must be a non-empty array")
    ids = [str(item.get("id", "")) for item in data["actors"]]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise RuntimeError("every disaster actor id must be non-empty and unique")
    for index, item in enumerate(data["actors"]):
        for key in (
            "kind",
            "anchor_actor",
            "asset",
            "offset_cm",
            "rotation_rpy_deg",
            "scale",
            "collision_profile",
        ):
            if key not in item:
                raise RuntimeError(f"actors[{index}] missing {key}")
        _triple(item["offset_cm"], f"actors[{index}].offset_cm")
        _triple(item["rotation_rpy_deg"], f"actors[{index}].rotation_rpy_deg")
        scale = _triple(item["scale"], f"actors[{index}].scale")
        if any(value <= 0.0 for value in scale):
            raise RuntimeError(f"actors[{index}].scale must be positive")
        if item["collision_profile"] not in ("BlockAll", "NoCollision"):
            raise RuntimeError(
                f"actors[{index}].collision_profile must be BlockAll or NoCollision"
            )
    return data


def _actors_by_label(actors: list[object]) -> dict[str, object]:
    result: dict[str, object] = {}
    duplicates: set[str] = set()
    for actor in actors:
        label = actor.get_actor_label()
        if label in result:
            duplicates.add(label)
        result[label] = actor
    for label in duplicates:
        result.pop(label, None)
    return result


def _vector(value: object) -> dict[str, float]:
    return {"x": float(value.x), "y": float(value.y), "z": float(value.z)}


def _main() -> None:
    config_path = Path(_required_environment(CONFIG_ENV))
    output_path = Path(_required_environment(OUTPUT_ENV))
    target_map = Path(_required_environment(TARGET_MAP_ENV))
    if not config_path.is_file():
        raise RuntimeError(f"disaster config does not exist: {config_path}")
    if not target_map.is_file():
        raise RuntimeError(f"disaster map does not exist: {target_map}")
    config = _load_config(config_path)

    world_path = str(config["world"])
    world = unreal.EditorLoadingAndSavingUtils.load_map(world_path)
    if world is None:
        raise RuntimeError(f"failed to load disaster world {world_path}")
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors_before = list(subsystem.get_all_level_actors())
    existing_vs = [
        actor.get_actor_label()
        for actor in actors_before
        if ROOT_TAG in {str(tag) for tag in actor.tags}
    ]
    if existing_vs:
        raise RuntimeError(
            "refusing to layer over existing VS_Disaster actors: "
            + ", ".join(sorted(existing_vs))
        )
    labels = _actors_by_label(actors_before)
    point_a_label = str(config["point_a_actor"])
    if point_a_label not in labels:
        raise RuntimeError(f"unique point-A actor not found: {point_a_label}")
    point_a = labels[point_a_label].get_actor_location()
    exclusion_radius = float(config["point_a_exclusion_radius_cm"])
    if not math.isfinite(exclusion_radius) or exclusion_radius <= 0.0:
        raise RuntimeError("point_a_exclusion_radius_cm must be positive and finite")

    created: list[dict[str, object]] = []
    for spec in config["actors"]:
        anchor_label = str(spec["anchor_actor"])
        if anchor_label not in labels:
            raise RuntimeError(f"unique anchor actor not found: {anchor_label}")
        anchor = labels[anchor_label]
        anchor_location = anchor.get_actor_location()
        offset = _triple(spec["offset_cm"], f"{spec['id']}.offset_cm")
        location = unreal.Vector(
            anchor_location.x + offset[0],
            anchor_location.y + offset[1],
            anchor_location.z + offset[2],
        )
        horizontal_distance = math.hypot(location.x - point_a.x, location.y - point_a.y)
        if horizontal_distance < exclusion_radius:
            raise RuntimeError(
                f"{spec['id']} violates Point_A exclusion radius: "
                f"{horizontal_distance:.1f} < {exclusion_radius:.1f} cm"
            )
        asset = unreal.load_asset(str(spec["asset"]))
        if not isinstance(asset, unreal.StaticMesh):
            raise RuntimeError(f"{spec['id']} asset is not a StaticMesh: {spec['asset']}")
        rotation = _triple(
            spec["rotation_rpy_deg"], f"{spec['id']}.rotation_rpy_deg"
        )
        actor = subsystem.spawn_actor_from_class(
            unreal.StaticMeshActor,
            location,
            unreal.Rotator(roll=rotation[0], pitch=rotation[1], yaw=rotation[2]),
            transient=False,
        )
        if actor is None:
            raise RuntimeError(f"failed to spawn {spec['id']}")
        label = f"VS_{spec['kind']}_{spec['id']}"
        actor.set_actor_label(label, mark_dirty=True)
        actor.tags = [
            ROOT_TAG,
            f"VS_Scenario_{config['scenario_id']}",
            f"VS_Kind_{spec['kind']}",
            "VS_Development" if config["development_only"] else "VS_Frozen",
        ]
        scale = _triple(spec["scale"], f"{spec['id']}.scale")
        actor.set_actor_scale3d(unreal.Vector(*scale))
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        if component is None:
            raise RuntimeError(f"spawned actor {spec['id']} has no StaticMeshComponent")
        component.set_static_mesh(asset)
        component.set_collision_profile_name(str(spec["collision_profile"]))
        if spec["collision_profile"] == "BlockAll":
            component.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
        else:
            component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        material_path = spec.get("material")
        if material_path:
            material = unreal.load_asset(str(material_path))
            if material is None:
                raise RuntimeError(
                    f"failed to load material for {spec['id']}: {material_path}"
                )
            component.set_material(0, material)
        created.append(
            {
                "id": spec["id"],
                "label": label,
                "kind": spec["kind"],
                "anchor_actor": anchor_label,
                "location_unreal_cm": _vector(location),
                "distance_from_point_a_cm": horizontal_distance,
                "collision_profile": spec["collision_profile"],
                "asset": spec["asset"],
            }
        )

    level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level_subsystem.save_current_level():
        raise RuntimeError(f"failed to save disaster world {world_path}")
    if not target_map.is_file():
        raise RuntimeError(f"disaster map disappeared after save: {target_map}")
    result = {
        "schema": "veriswarm.factorycity.disaster_layer_result.v1",
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "PASS",
        "world": world_path,
        "scenario_id": config["scenario_id"],
        "development_only": config["development_only"],
        "config_file": str(config_path),
        "config_sha256": _sha256(config_path),
        "target_map": str(target_map),
        "target_map_development_sha256": _sha256(target_map),
        "actor_count_before": len(actors_before),
        "actor_count_after": len(list(subsystem.get_all_level_actors())),
        "point_a": _vector(point_a),
        "point_a_exclusion_radius_cm": exclusion_radius,
        "created": created,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    unreal.log(f"Disaster layer result written to {output_path}")


_main()
