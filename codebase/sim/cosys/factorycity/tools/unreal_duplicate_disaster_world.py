"""Create the derived FactoryCity disaster level without editing its source level.

Run inside Unreal Editor-Cmd. All machine paths and asset identifiers are supplied by
environment variables so the reusable tool contains no scenario coordinates.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import unreal


SOURCE_WORLD_ENV = "VERISWARM_SOURCE_WORLD"
TARGET_WORLD_ENV = "VERISWARM_DISASTER_WORLD"
SOURCE_MAP_ENV = "VERISWARM_SOURCE_MAP_FILE"
TARGET_MAP_ENV = "VERISWARM_DISASTER_MAP_FILE"
OUTPUT_ENV = "VERISWARM_DISASTER_DUPLICATE_OUTPUT"

REQUIRED_ACTOR_LABELS = ("Point_A", "AirSimOrigin_Point_A")


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _vector(value: object) -> dict[str, float]:
    return {"x": float(value.x), "y": float(value.y), "z": float(value.z)}


def _actor_by_label(actors: list[object], label: str) -> object:
    matches = [actor for actor in actors if actor.get_actor_label() == label]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {label!r} actor, found {len(matches)}")
    return matches[0]


def _actor_identity(actor: object) -> dict[str, object]:
    return {
        "label": actor.get_actor_label(),
        "name": actor.get_name(),
        "class": actor.get_class().get_name(),
        "location_unreal_cm": _vector(actor.get_actor_location()),
        "tags": sorted(str(tag) for tag in actor.tags),
    }


def _main() -> None:
    source_world = _required_environment(SOURCE_WORLD_ENV)
    target_world = _required_environment(TARGET_WORLD_ENV)
    source_map = Path(_required_environment(SOURCE_MAP_ENV))
    target_map = Path(_required_environment(TARGET_MAP_ENV))
    output = Path(_required_environment(OUTPUT_ENV))

    if source_world == target_world:
        raise RuntimeError("source and disaster world asset paths must differ")
    if source_map.resolve() == target_map.resolve():
        raise RuntimeError("source and disaster map files must differ")
    if not source_map.is_file():
        raise RuntimeError(f"source map does not exist: {source_map}")
    if target_map.exists():
        raise RuntimeError(f"refusing to overwrite existing disaster map: {target_map}")

    source_hash_before = _sha256(source_map)
    source_loaded = unreal.EditorLoadingAndSavingUtils.load_map(source_world)
    if source_loaded is None:
        raise RuntimeError(f"failed to load source world {source_world}")

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    source_actors = list(actor_subsystem.get_all_level_actors())
    source_required = {
        label: _actor_identity(_actor_by_label(source_actors, label))
        for label in REQUIRED_ACTOR_LABELS
    }

    target_directory = target_world.rsplit("/", 1)[0]
    if not unreal.EditorAssetLibrary.does_directory_exist(target_directory):
        if not unreal.EditorAssetLibrary.make_directory(target_directory):
            raise RuntimeError(f"failed to create target asset directory {target_directory}")
    if unreal.EditorAssetLibrary.does_asset_exist(target_world):
        raise RuntimeError(f"refusing to overwrite existing target asset {target_world}")
    duplicated_asset = unreal.EditorAssetLibrary.duplicate_asset(source_world, target_world)
    if not duplicated_asset:
        raise RuntimeError(f"failed to duplicate {source_world} as {target_world}")
    if not unreal.EditorAssetLibrary.save_loaded_asset(
        duplicated_asset, only_if_is_dirty=False
    ):
        raise RuntimeError(f"failed to save duplicated world asset {target_world}")
    # Do not load the duplicated UWorld in this process. The object returned by
    # duplicate_asset keeps the new world reachable and UE 5.8 can then abort with a
    # World Memory Leaks error while switching maps. A fresh commandlet performs the
    # authoritative load/inventory verification.
    del duplicated_asset
    if not target_map.is_file():
        raise RuntimeError(f"duplicated map file was not created: {target_map}")

    source_hash_after = _sha256(source_map)
    if source_hash_after != source_hash_before:
        raise RuntimeError("protected source map changed while creating the disaster copy")
    result = {
        "schema": "veriswarm.factorycity.disaster_duplicate.v1",
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": {
            "world": source_world,
            "map_file": str(source_map),
            "sha256_before": source_hash_before,
            "sha256_after": source_hash_after,
            "actor_count": len(source_actors),
            "required_actors": source_required,
        },
        "target": {
            "world": target_world,
            "map_file": str(target_map),
            "development_sha256": _sha256(target_map),
            "verification": "REQUIRES_FRESH_PROCESS_INVENTORY",
        },
        "status": "CREATED_AWAITING_SEPARATE_INVENTORY",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    unreal.log(f"FactoryCity disaster duplicate evidence written to {output}")


_main()
