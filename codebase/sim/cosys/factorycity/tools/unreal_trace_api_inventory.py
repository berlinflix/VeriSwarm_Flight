"""Inventory the Unreal Python HitResult surface used by the clearance adapter."""

from __future__ import annotations

import json
import os

import unreal


REQUEST_ENV = "VERISWARM_UNREAL_CLEARANCE_REQUEST"
OUTPUT_ENV = "VERISWARM_UNREAL_TRACE_API_OUTPUT"


def _safe_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return {"type": type(value).__name__, "repr": repr(value), "str": str(value)}


def main():
    with open(os.environ[REQUEST_ENV], "r", encoding="utf-8") as stream:
        request = json.load(stream)
    world_id = request["world"]["world_id"]
    if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(world_id):
        raise RuntimeError("map load failed")
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    transform = request["coordinate_transform"]
    contract = request["probe_contract"]
    candidate = request["candidates"][0]
    origin = transform["origin_unreal_units"]
    signs = transform["ned_to_unreal_axis_sign"]
    scale = transform["world_to_meters"]

    def point(z_ned):
        return unreal.Vector(
            origin["x"] + signs["x"] * candidate["x_ned_m"] * scale,
            origin["y"] + signs["y"] * candidate["y_ned_m"] * scale,
            origin["z"] + signs["z"] * z_ned * scale,
        )

    hit = unreal.SystemLibrary.line_trace_single(
        world,
        point(contract["ground_trace_start_z_ned_m"]),
        point(contract["ground_trace_end_z_ned_m"]),
        getattr(unreal.TraceTypeQuery, contract["trace_channel"]),
        contract["trace_complex"],
        [],
        unreal.DrawDebugTrace.NONE,
        True,
    )
    attributes = {}
    for name in sorted(item for item in dir(hit) if not item.startswith("__")):
        try:
            attributes[name] = _safe_value(getattr(hit, name))
        except Exception as exc:
            attributes[name] = {"attribute_error": str(exc)}
    property_attempts = {}
    for name in (
        "BlockingHit",
        "bBlockingHit",
        "blocking_hit",
        "Distance",
        "distance",
        "ImpactPoint",
        "impact_point",
        "ImpactNormal",
        "impact_normal",
        "Actor",
        "actor",
        "Component",
        "component",
        "bStartPenetrating",
        "start_penetrating",
    ):
        try:
            property_attempts[name] = _safe_value(hit.get_editor_property(name))
        except Exception as exc:
            property_attempts[name] = {"property_error": str(exc)}
    method_results = {}
    for name in ("to_dict", "to_tuple", "export_text"):
        try:
            method_results[name] = _safe_value(getattr(hit, name)())
        except Exception as exc:
            method_results[name] = {"method_error": str(exc)}
    output = {
        "hit_type": type(hit).__name__,
        "hit_repr": repr(hit),
        "hit_str": str(hit),
        "attributes": attributes,
        "method_results": method_results,
        "property_attempts": property_attempts,
    }
    with open(os.environ[OUTPUT_ENV], "x", encoding="utf-8", newline="\n") as stream:
        json.dump(output, stream, allow_nan=False, indent=2, sort_keys=True)
        stream.write("\n")


main()
