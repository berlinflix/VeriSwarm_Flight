"""Run hash-bound FactoryCity ground and corridor traces inside Unreal Editor.

The host produces the request from validated configuration. This Unreal-side adapter only
applies the supplied NED transform and trace contract, loads the requested map, and writes
create-once raw evidence. It never saves or modifies the map.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import traceback

import unreal


REQUEST_ENV = "VERISWARM_UNREAL_CLEARANCE_REQUEST"
OUTPUT_ENV = "VERISWARM_UNREAL_CLEARANCE_OUTPUT"
REQUEST_SCHEMA = "veriswarm.factorycity.clearance_request.v1"
RESULT_SCHEMA = "veriswarm.factorycity.clearance_result.v1"


def _load_request():
    path = os.environ.get(REQUEST_ENV)
    if not path:
        raise RuntimeError(f"missing environment variable {REQUEST_ENV}")
    with open(path, "rb") as stream:
        content = stream.read()
    request = json.loads(content.decode("utf-8"))
    if request.get("schema") != REQUEST_SCHEMA:
        raise RuntimeError("unsupported clearance request schema")
    with open(__file__, "rb") as stream:
        adapter_hash = hashlib.sha256(stream.read()).hexdigest()
    if request.get("probe_adapter_sha256") != adapter_hash:
        raise RuntimeError("clearance request does not authorize this probe adapter")
    return request, content


def _write_create_once(payload):
    path = os.environ.get(OUTPUT_ENV)
    if not path:
        raise RuntimeError(f"missing environment variable {OUTPUT_ENV}")
    rendered = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    with open(path, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(rendered)
        stream.write("\n")


def _vector_record(value):
    return {"x": value.x, "y": value.y, "z": value.z}


def _hit_record(hit_occurred, hit):
    if not hit_occurred:
        return None
    if len(hit) != 18:
        raise RuntimeError("unexpected Unreal BreakHitResult output shape")
    actor = hit[9]
    component = hit[10]
    return {
        "actor_class": actor.get_class().get_name() if actor is not None else None,
        "actor_label": actor.get_actor_label() if actor is not None else None,
        "actor_name": actor.get_name() if actor is not None else None,
        "blocking_hit": bool(hit[0]),
        "component_name": component.get_name() if component is not None else None,
        "distance_unreal_units": hit[3],
        "impact_normal": _vector_record(hit[7]),
        "impact_point_unreal_units": _vector_record(hit[5]),
        "start_penetrating": bool(hit[1]),
    }


def _trace_result(value):
    """Normalize Unreal versions that return either HitResult or (bool, HitResult)."""

    if value is None:
        return False, None
    reported_hit = None
    if isinstance(value, tuple):
        if len(value) != 2:
            raise RuntimeError("unexpected Unreal trace tuple shape")
        reported_hit, value = bool(value[0]), value[1]
    broken = value.to_tuple()
    if len(broken) != 18:
        raise RuntimeError("unexpected Unreal BreakHitResult output shape")
    blocking_hit = bool(broken[0])
    if reported_hit is not None and reported_hit != blocking_hit:
        raise RuntimeError("Unreal trace and HitResult blocking flags disagree")
    return blocking_hit, broken


def _unreal_position(origin, signs, scale, x_ned, y_ned, z_ned):
    return unreal.Vector(
        origin["x"] + (signs["x"] * x_ned * scale),
        origin["y"] + (signs["y"] * y_ned * scale),
        origin["z"] + (signs["z"] * z_ned * scale),
    )


def _ned_z(origin, signs, scale, unreal_z):
    return ((unreal_z - origin["z"]) / scale) / signs["z"]


def _trace_candidate(world, request_hash, request, candidate, trace_channel):
    transform = request["coordinate_transform"]
    contract = request["probe_contract"]
    origin = transform["origin_unreal_units"]
    signs = transform["ned_to_unreal_axis_sign"]
    scale = transform["world_to_meters"]
    x_ned = candidate["x_ned_m"]
    y_ned = candidate["y_ned_m"]
    ground_start = _unreal_position(
        origin,
        signs,
        scale,
        x_ned,
        y_ned,
        contract["ground_trace_start_z_ned_m"],
    )
    ground_end = _unreal_position(
        origin,
        signs,
        scale,
        x_ned,
        y_ned,
        contract["ground_trace_end_z_ned_m"],
    )
    ground_hit_occurred, ground_hit = _trace_result(
        unreal.SystemLibrary.line_trace_single(
            world,
            ground_start,
            ground_end,
            trace_channel,
            contract["trace_complex"],
            [],
            unreal.DrawDebugTrace.NONE,
            True,
        )
    )
    ground_record = _hit_record(ground_hit_occurred, ground_hit)
    reasons = []
    ground_z_ned = None
    ground_clear = bool(ground_hit_occurred)
    if not ground_clear:
        reasons.append("no_ground_hit")
    else:
        normal_z = ground_record["impact_normal"]["z"]
        if normal_z < contract["minimum_ground_normal_z"]:
            ground_clear = False
            reasons.append("ground_normal_below_minimum")
        ground_z_ned = _ned_z(
            origin,
            signs,
            scale,
            ground_record["impact_point_unreal_units"]["z"],
        )

    corridor_hit_occurred = False
    corridor_hit = None
    corridor_clear = False
    if ground_clear:
        start_z_ned = (
            ground_z_ned
            - contract["takeoff_corridor_start_clearance_m"]
        )
        corridor_start = _unreal_position(
            origin, signs, scale, x_ned, y_ned, start_z_ned
        )
        corridor_end = _unreal_position(
            origin,
            signs,
            scale,
            x_ned,
            y_ned,
            contract["corridor_top_z_ned_m"],
        )
        corridor_hit_occurred, corridor_hit = _trace_result(
            unreal.SystemLibrary.sphere_trace_single(
                world,
                corridor_start,
                corridor_end,
                contract["required_clearance_m"] * scale,
                trace_channel,
                contract["trace_complex"],
                [],
                unreal.DrawDebugTrace.NONE,
                True,
            )
        )
        corridor_clear = not bool(corridor_hit_occurred)
        if not corridor_clear:
            reasons.append("vertical_corridor_blocked")
    else:
        reasons.append("corridor_not_evaluated_without_clear_ground")

    return {
        "candidate_id": candidate["candidate_id"],
        "row": candidate["row"],
        "column": candidate["column"],
        "x_ned_m": x_ned,
        "y_ned_m": y_ned,
        "ground_clear": ground_clear,
        "vertical_corridor_clear": corridor_clear,
        "ground_z_ned_m": ground_z_ned,
        "evidence_id": f"unreal-trace/{request_hash}/{candidate['candidate_id']}",
        "reason": ";".join(reasons),
        "ground_hit": ground_record,
        "corridor_hit": _hit_record(corridor_hit_occurred, corridor_hit),
    }


def main():
    request = None
    request_bytes = None
    try:
        request, request_bytes = _load_request()
        request_hash = hashlib.sha256(request_bytes).hexdigest()
        world_id = request["world"]["world_id"]
        level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if not level_subsystem.load_level(world_id):
            raise RuntimeError(f"Unreal could not load map {world_id!r}")
        editor_subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        world = editor_subsystem.get_editor_world()
        if world is None or world.get_name() != world_id.rsplit("/", 1)[-1].split(".")[0]:
            raise RuntimeError("loaded Unreal world identity does not match the request")
        observed_scale = world.get_world_settings().get_editor_property("world_to_meters")
        requested_scale = request["coordinate_transform"]["world_to_meters"]
        if not math.isclose(observed_scale, requested_scale, rel_tol=0.0, abs_tol=1e-9):
            raise RuntimeError(
                f"world scale mismatch: requested {requested_scale}, observed {observed_scale}"
            )
        channel_name = request["probe_contract"]["trace_channel"]
        try:
            trace_channel = getattr(unreal.TraceTypeQuery, channel_name)
        except AttributeError as exc:
            raise RuntimeError(f"unsupported Unreal trace channel {channel_name!r}") from exc
        probes = [
            _trace_candidate(world, request_hash, request, item, trace_channel)
            for item in request["candidates"]
        ]
        output = {
            "schema": RESULT_SCHEMA,
            "status": "PASS",
            "scene_validated": True,
            "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "provider_id": request["provider_id"],
            "configuration_sha256": request["configuration_sha256"],
            "request_sha256": request_hash,
            "world": {
                **request["world"],
                "loaded_world_name": world.get_name(),
                "observed_world_to_meters": observed_scale,
            },
            "coordinate_transform": request["coordinate_transform"],
            "probes": probes,
        }
    except Exception as exc:
        output = {
            "schema": RESULT_SCHEMA,
            "status": "FAIL",
            "scene_validated": False,
            "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "provider_id": request.get("provider_id") if isinstance(request, dict) else None,
            "configuration_sha256": (
                request.get("configuration_sha256") if isinstance(request, dict) else None
            ),
            "request_sha256": (
                hashlib.sha256(request_bytes).hexdigest()
                if request_bytes is not None
                else None
            ),
            "world": request.get("world") if isinstance(request, dict) else None,
            "coordinate_transform": (
                request.get("coordinate_transform")
                if isinstance(request, dict)
                else None
            ),
            "probes": [],
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    _write_create_once(output)
    if output["status"] != "PASS":
        raise RuntimeError(output["error"])


main()
