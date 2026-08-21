"""Collect a small, machine-readable inventory from a loaded Unreal map.

This script runs inside Unreal's Python environment. The request and output locations are
provided through environment variables so the portable source contains no workstation or
scenario paths. It never saves the map.
"""

from __future__ import annotations

import collections
import datetime
import hashlib
import json
import os
import traceback

import unreal


REQUEST_ENV = "VERISWARM_UNREAL_INVENTORY_REQUEST"
OUTPUT_ENV = "VERISWARM_UNREAL_INVENTORY_OUTPUT"


def _vector(value):
    return {"x": value.x, "y": value.y, "z": value.z}


def _rotation(value):
    return {"pitch": value.pitch, "roll": value.roll, "yaw": value.yaw}


def _class_name(actor):
    return actor.get_class().get_name()


def _actor_record(actor):
    return {
        "class": _class_name(actor),
        "label": actor.get_actor_label(),
        "name": actor.get_name(),
        "location_unreal_units": _vector(actor.get_actor_location()),
        "rotation_degrees": _rotation(actor.get_actor_rotation()),
        "tags": sorted(str(tag) for tag in actor.tags),
    }


def _candidate_origin_actor(actor):
    class_name = _class_name(actor).lower()
    label = actor.get_actor_label().lower()
    return any(
        token in class_name or token in label
        for token in ("playerstart", "camera", "spectator", "pawn")
    )


def _load_request():
    request_path = os.environ.get(REQUEST_ENV)
    if not request_path:
        raise RuntimeError(f"missing environment variable {REQUEST_ENV}")
    with open(request_path, "rb") as stream:
        request_bytes = stream.read()
    request = json.loads(request_bytes.decode("utf-8"))
    if request.get("schema") != "veriswarm.factorycity.fleet_config.v1":
        raise RuntimeError("inventory input must be a FactoryCity fleet configuration")
    world_section = request.get("world")
    if not isinstance(world_section, dict):
        raise RuntimeError("inventory input is missing its world section")
    world_id = world_section.get("world_id")
    if not isinstance(world_id, str) or not world_id.startswith("/Game/"):
        raise RuntimeError("world_id must be a /Game/ object path")
    return request, request_bytes, world_id


def _write_output(payload):
    output_path = os.environ.get(OUTPUT_ENV)
    if not output_path:
        raise RuntimeError(f"missing environment variable {OUTPUT_ENV}")
    rendered = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    with open(output_path, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(rendered)
        stream.write("\n")


def main():
    request = None
    request_bytes = None
    try:
        request, request_bytes, world_id = _load_request()
        level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if not level_subsystem.load_level(world_id):
            raise RuntimeError(f"Unreal could not load map {world_id!r}")

        unreal_editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        world = unreal_editor.get_editor_world()
        if world is None:
            raise RuntimeError("loaded map did not expose an editor world")
        actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        actors = list(actor_subsystem.get_all_level_actors())
        classes = collections.Counter(_class_name(actor) for actor in actors)
        origin_candidates = [
            _actor_record(actor) for actor in actors if _candidate_origin_actor(actor)
        ]
        world_settings = world.get_world_settings()
        output = {
            "schema": "veriswarm.factorycity.unreal_inventory_result.v1",
            "status": "PASS",
            "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
            "world_id": world_id,
            "loaded_world_name": world.get_name(),
            "actor_count": len(actors),
            "actor_class_counts": dict(sorted(classes.items())),
            "origin_candidate_actors": origin_candidates,
            "world_settings": {
                "class": world_settings.get_class().get_name(),
                "default_game_mode": str(
                    world_settings.get_editor_property("default_game_mode")
                ),
                "world_to_meters": world_settings.get_editor_property(
                    "world_to_meters"
                ),
            },
        }
    except Exception as exc:
        output = {
            "schema": "veriswarm.factorycity.unreal_inventory_result.v1",
            "status": "FAIL",
            "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "request_sha256": (
                hashlib.sha256(request_bytes).hexdigest()
                if request_bytes is not None
                else None
            ),
            "world_id": (
                request.get("world", {}).get("world_id")
                if isinstance(request, dict)
                and isinstance(request.get("world"), dict)
                else None
            ),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    _write_output(output)
    if output["status"] != "PASS":
        raise RuntimeError(output["error"])


main()
