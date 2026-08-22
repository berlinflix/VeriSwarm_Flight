"""Read-only inventory for a manifest-driven FactoryCity disaster level."""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
from pathlib import Path

import unreal


WORLD_ENV = "VERISWARM_DISASTER_WORLD"
OUTPUT_ENV = "VERISWARM_DISASTER_INVENTORY_OUTPUT"
POINT_A_LABEL = "Point_A"
ORIGIN_LABEL = "AirSimOrigin_Point_A"
ROAD_TERMS = ("street", "road", "asphalt", "way")
FLOOR_TERMS = ("floor", "landscape", "ground")


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


def _vector(value: object) -> dict[str, float]:
    return {"x": float(value.x), "y": float(value.y), "z": float(value.z)}


def _actor_record(actor: object) -> dict[str, object]:
    origin, extent = actor.get_actor_bounds(False)
    components = []
    for component in actor.get_components_by_class(unreal.PrimitiveComponent):
        record = {
            "name": component.get_name(),
            "class": component.get_class().get_name(),
        }
        for method_name in (
            "get_collision_enabled",
            "get_collision_profile_name",
            "get_collision_object_type",
        ):
            method = getattr(component, method_name, None)
            if callable(method):
                try:
                    record[method_name] = str(method())
                except Exception as exc:
                    record[method_name] = f"ERROR:{type(exc).__name__}"
        components.append(record)
    return {
        "label": actor.get_actor_label(),
        "name": actor.get_name(),
        "class": actor.get_class().get_name(),
        "location_unreal_cm": _vector(actor.get_actor_location()),
        "bounds_origin_unreal_cm": _vector(origin),
        "bounds_extent_unreal_cm": _vector(extent),
        "tags": sorted(str(tag) for tag in actor.tags),
        "primitive_components": components,
    }


def _matches_terms(actor: object, terms: tuple[str, ...]) -> bool:
    haystack = f"{actor.get_actor_label()} {actor.get_name()}".casefold()
    return any(term in haystack for term in terms)


def _exact_actor(actors: list[object], label: str) -> object:
    matches = [actor for actor in actors if actor.get_actor_label() == label]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {label!r} actor, found {len(matches)}")
    return matches[0]


def _main() -> None:
    world_path = _required_environment(WORLD_ENV)
    output = Path(_required_environment(OUTPUT_ENV))
    world = unreal.EditorLoadingAndSavingUtils.load_map(world_path)
    if world is None:
        raise RuntimeError(f"failed to load disaster world {world_path}")

    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = list(subsystem.get_all_level_actors())
    class_counts = collections.Counter(actor.get_class().get_name() for actor in actors)
    disaster_actors = [
        actor for actor in actors if any(str(tag) == "VS_Disaster" for tag in actor.tags)
    ]
    roads = [
        _actor_record(actor)
        for actor in actors
        if actor not in disaster_actors and _matches_terms(actor, ROAD_TERMS)
    ]
    floors = [_actor_record(actor) for actor in actors if _matches_terms(actor, FLOOR_TERMS)]

    result = {
        "schema": "veriswarm.factorycity.disaster_inventory.v1",
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "world": world_path,
        "actor_count": len(actors),
        "class_counts": dict(sorted(class_counts.items())),
        "point_a": _actor_record(_exact_actor(actors, POINT_A_LABEL)),
        "airsim_origin": _actor_record(_exact_actor(actors, ORIGIN_LABEL)),
        "roads": roads,
        "floors": floors,
        "existing_disaster_actors": [_actor_record(actor) for actor in disaster_actors],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    unreal.log(f"Disaster world inventory written to {output}")


_main()
