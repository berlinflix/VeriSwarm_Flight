"""Read-only Point_B landing-site audit for FactoryCity_Disaster."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path

import unreal


CONFIG_ENV = "VERISWARM_POINT_B_AUDIT_CONFIG"
OUTPUT_ENV = "VERISWARM_POINT_B_AUDIT_OUTPUT"


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


def _vector(value: object) -> dict[str, float]:
    return {"x": float(value.x), "y": float(value.y), "z": float(value.z)}


def _rotation(value: object) -> dict[str, float]:
    return {
        "roll": float(value.roll),
        "pitch": float(value.pitch),
        "yaw": float(value.yaw),
    }


def _exact_actor(actors: list[object], label: str) -> object:
    matches = [actor for actor in actors if actor.get_actor_label() == label]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {label!r} actor, found {len(matches)}")
    return matches[0]


def _property(value: object, *names: str) -> object | None:
    for name in names:
        try:
            return value.get_editor_property(name)
        except Exception:
            candidate = getattr(value, name, None)
            if candidate is not None:
                return candidate
    return None


def _trace_surface(
    world: object,
    point_b: object,
    x: float,
    y: float,
    point_z: float,
    start_above_cm: float,
    end_below_cm: float,
    trace_complex: bool,
) -> dict[str, object]:
    result = unreal.SystemLibrary.line_trace_single(
        world,
        unreal.Vector(x, y, point_z + start_above_cm),
        unreal.Vector(x, y, point_z - end_below_cm),
        unreal.TraceTypeQuery.ECC_VISIBILITY,
        trace_complex,
        [point_b],
        unreal.DrawDebugTrace.NONE,
        True,
    )
    reported_hit = None
    if isinstance(result, tuple):
        if len(result) != 2:
            raise RuntimeError("unexpected Unreal line trace tuple shape")
        reported_hit, hit_result = bool(result[0]), result[1]
    else:
        # UE 5.8 returns a HitResult directly; older bindings returned a tuple.
        hit_result = result
    broken = hit_result.to_tuple()
    if len(broken) != 18:
        raise RuntimeError("unexpected Unreal BreakHitResult output shape")
    hit = bool(broken[0])
    if reported_hit is not None and reported_hit != hit:
        raise RuntimeError("Unreal trace and HitResult blocking flags disagree")
    record: dict[str, object] = {"hit": hit}
    if not hit:
        return record
    impact_point = broken[5]
    impact_normal = broken[7]
    hit_actor = broken[9]
    hit_component = broken[10]
    record.update(
        {
            "impact_point_unreal_cm": _vector(impact_point),
            "impact_normal": _vector(impact_normal),
            "actor_label": (
                hit_actor.get_actor_label() if hit_actor is not None else None
            ),
            "actor_name": hit_actor.get_name() if hit_actor is not None else None,
            "component_name": (
                hit_component.get_name() if hit_component is not None else None
            ),
        }
    )
    return record


def _support_candidates(
    actors: list[object], point_b: object, x: float, y: float, z: float
) -> list[dict[str, object]]:
    candidates = []
    for actor in actors:
        if actor == point_b:
            continue
        origin, extent = actor.get_actor_bounds(False)
        contains_xy = (
            float(origin.x - extent.x) <= x <= float(origin.x + extent.x)
            and float(origin.y - extent.y) <= y <= float(origin.y + extent.y)
        )
        near_z = float(origin.z - extent.z - 100.0) <= z <= float(
            origin.z + extent.z + 100.0
        )
        if not contains_xy or not near_z:
            continue
        components = []
        for component in actor.get_components_by_class(unreal.PrimitiveComponent):
            components.append(
                {
                    "name": component.get_name(),
                    "class": component.get_class().get_name(),
                    "collision_enabled": str(component.get_collision_enabled()),
                    "collision_profile": str(component.get_collision_profile_name()),
                    "visibility_response": str(
                        component.get_collision_response_to_channel(
                            unreal.CollisionChannel.ECC_VISIBILITY
                        )
                    ),
                    "world_static_response": str(
                        component.get_collision_response_to_channel(
                            unreal.CollisionChannel.ECC_WORLD_STATIC
                        )
                    ),
                    "world_dynamic_response": str(
                        component.get_collision_response_to_channel(
                            unreal.CollisionChannel.ECC_WORLD_DYNAMIC
                        )
                    ),
                }
            )
        candidates.append(
            {
                "label": actor.get_actor_label(),
                "name": actor.get_name(),
                "class": actor.get_class().get_name(),
                "bounds_origin_unreal_cm": _vector(origin),
                "bounds_extent_unreal_cm": _vector(extent),
                "primitive_components": components,
            }
        )
    return candidates


def _main() -> None:
    config_path = Path(_required_environment(CONFIG_ENV))
    output_path = Path(_required_environment(OUTPUT_ENV))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != "veriswarm.factorycity.point_b_audit.v1":
        raise RuntimeError("unsupported Point_B audit config schema")
    world_path = str(config["world"])
    world = unreal.EditorLoadingAndSavingUtils.load_map(world_path)
    if world is None:
        raise RuntimeError(f"failed to load {world_path}")
    actors = list(
        unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    )
    point_a = _exact_actor(actors, str(config["point_a_actor"]))
    point_b = _exact_actor(actors, str(config["point_b_actor"]))
    a = point_a.get_actor_location()
    b = point_b.get_actor_location()
    b_rotation = point_b.get_actor_rotation()
    validation = config["validation"]
    route_distance_m = math.hypot(float(b.x - a.x), float(b.y - a.y)) / 100.0
    flood_clearance_m = (
        float(b.z) - float(config["peak_flood_world_z_cm"])
    ) / 100.0
    start_above_cm = float(validation["trace_start_above_point_m"]) * 100.0
    end_below_cm = float(validation["trace_end_below_point_m"]) * 100.0

    probes = []
    for offset in config["footprint_probe_offsets_cm"]:
        dx, dy = (float(offset[0]), float(offset[1]))
        trace = _trace_surface(
            world,
            point_b,
            float(b.x) + dx,
            float(b.y) + dy,
            float(b.z),
            start_above_cm,
            end_below_cm,
            bool(validation["trace_complex"]),
        )
        probes.append({"offset_cm": [dx, dy], **trace})
    support_candidates = _support_candidates(
        actors, point_b, float(b.x), float(b.y), float(b.z)
    )

    failures: list[str] = []
    warnings: list[str] = []
    if not (
        float(validation["minimum_route_distance_m"])
        <= route_distance_m
        <= float(validation["maximum_route_distance_m"])
    ):
        failures.append("route_distance_outside_configured_range")
    if flood_clearance_m < float(validation["minimum_roof_above_peak_flood_m"]):
        failures.append("roof_below_minimum_peak_flood_clearance")
    if not all(bool(item["hit"]) for item in probes):
        failures.append("landing_footprint_has_missing_support")

    hits = [item for item in probes if item["hit"]]
    heights = [float(item["impact_point_unreal_cm"]["z"]) for item in hits]
    normals = [float(item["impact_normal"]["z"]) for item in hits]
    maximum_height_error_m: float | None = (
        max(abs(height - float(b.z)) for height in heights) / 100.0
        if heights
        else None
    )
    surface_variation_m: float | None = (
        (max(heights) - min(heights)) / 100.0 if heights else None
    )
    minimum_normal_z: float | None = min(normals) if normals else None
    if maximum_height_error_m is None or maximum_height_error_m > float(
        validation["maximum_surface_height_error_m"]
    ):
        failures.append("marker_not_on_landing_surface")
    if surface_variation_m is None or surface_variation_m > float(
        validation["maximum_surface_variation_m"]
    ):
        failures.append("landing_surface_not_level")
    if minimum_normal_z is None or minimum_normal_z < float(
        validation["minimum_surface_normal_z"]
    ):
        failures.append("landing_surface_too_steep")
    marker_tilt = max(abs(float(b_rotation.roll)), abs(float(b_rotation.pitch)))
    if marker_tilt > float(validation["maximum_marker_pitch_roll_deg"]):
        warnings.append("marker_pitch_roll_will_be_ignored_by_mission_controller")

    result = {
        "schema": "veriswarm.factorycity.point_b_audit_result.v1",
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "warnings": warnings,
        "development_only": bool(config["development_only"]),
        "world": world_path,
        "point_a": {
            "label": point_a.get_actor_label(),
            "location_unreal_cm": _vector(a),
        },
        "point_b": {
            "label": point_b.get_actor_label(),
            "location_unreal_cm": _vector(b),
            "rotation_unreal_deg": _rotation(b_rotation),
        },
        "route_horizontal_distance_m": route_distance_m,
        "roof_clearance_above_peak_flood_m": flood_clearance_m,
        "landing_footprint": {
            "formation_offsets_cm": config["formation_offsets_cm"],
            "probes": probes,
            "maximum_surface_height_error_m": maximum_height_error_m,
            "surface_variation_m": surface_variation_m,
            "minimum_surface_normal_z": minimum_normal_z,
            "support_candidates": support_candidates,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    unreal.log(f"Point_B audit {result['status']}: {output_path}")
    if failures:
        raise RuntimeError(f"Point_B audit failed: {failures}")


_main()
