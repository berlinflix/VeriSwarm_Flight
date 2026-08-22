"""Build disaster-world CoSys settings and placement metadata from verified inputs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _triple(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise RuntimeError(f"{field} must be a three-number array")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise RuntimeError(f"{field} contains a non-finite value")
    return result


def _write_new_json(path: Path, value: object) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-settings", type=Path, required=True)
    parser.add_argument("--base-placement", type=Path, required=True)
    parser.add_argument("--camera-profile", type=Path, required=True)
    parser.add_argument("--disaster-world", required=True)
    parser.add_argument("--disaster-map", type=Path, required=True)
    parser.add_argument("--output-settings", type=Path, required=True)
    parser.add_argument("--output-placement", type=Path, required=True)
    args = parser.parse_args()

    for path in (
        args.base_settings,
        args.base_placement,
        args.camera_profile,
        args.disaster_map,
    ):
        if not path.is_file():
            raise RuntimeError(f"required input does not exist: {path}")
    settings = json.loads(args.base_settings.read_text(encoding="utf-8"))
    placement = json.loads(args.base_placement.read_text(encoding="utf-8"))
    profile = json.loads(args.camera_profile.read_text(encoding="utf-8"))
    if profile.get("schema") != "veriswarm.factorycity.camera_profile.v1":
        raise RuntimeError("unsupported camera profile schema")
    vehicle_names = tuple(sorted(settings.get("Vehicles", {})))
    placement_names = tuple(sorted(item["name"] for item in placement["vehicles"]))
    if vehicle_names != placement_names:
        raise RuntimeError("base settings and placement rosters differ")
    if not vehicle_names:
        raise RuntimeError("base settings roster is empty")
    camera_specs = profile.get("cameras")
    if not isinstance(camera_specs, dict) or not camera_specs:
        raise RuntimeError("camera profile cameras must be a non-empty object")

    settings["ViewMode"] = str(profile["view_mode"])
    for vehicle_name in vehicle_names:
        rendered: dict[str, object] = {}
        for camera_name, spec in camera_specs.items():
            position = _triple(spec["position_ned_m"], f"{camera_name}.position_ned_m")
            rotation = _triple(
                spec["rotation_rpy_deg"], f"{camera_name}.rotation_rpy_deg"
            )
            capture = spec["capture"]
            width = int(capture["width"])
            height = int(capture["height"])
            fov = float(capture["fov_degrees"])
            image_type = int(capture["image_type"])
            if width <= 0 or height <= 0 or not 0.0 < fov < 180.0:
                raise RuntimeError(f"invalid capture geometry for {camera_name}")
            rendered[str(camera_name)] = {
                "X": position[0],
                "Y": position[1],
                "Z": position[2],
                "Roll": rotation[0],
                "Pitch": rotation[1],
                "Yaw": rotation[2],
                "CaptureSettings": [
                    {
                        "ImageType": image_type,
                        "Width": width,
                        "Height": height,
                        "FOV_Degrees": fov,
                    }
                ],
            }
        settings["Vehicles"][vehicle_name]["Cameras"] = rendered

    _write_new_json(args.output_settings, settings)
    settings_hash = _sha256(args.output_settings)
    disaster_placement = copy.deepcopy(placement)
    disaster_placement["schema"] = "veriswarm.factorycity.disaster_placement.v1"
    disaster_placement["status"] = "AWAITING_LIVE_RUNTIME_VALIDATION"
    disaster_placement["scope"] = "FACTORYCITY_DISASTER_POINT_A"
    disaster_placement["project"]["world"] = args.disaster_world
    disaster_placement["project"]["map_path"] = str(args.disaster_map)
    disaster_placement["project"]["map_development_sha256"] = _sha256(
        args.disaster_map
    )
    disaster_placement["settings_file"] = str(args.output_settings)
    disaster_placement["settings_sha256"] = settings_hash
    disaster_placement["camera_profile"] = {
        "file": str(args.camera_profile),
        "sha256": _sha256(args.camera_profile),
        "view_mode": settings["ViewMode"],
        "cameras": tuple(sorted(camera_specs)),
    }
    _write_new_json(args.output_placement, disaster_placement)
    print(
        json.dumps(
            {
                "status": "PASS",
                "settings": str(args.output_settings),
                "settings_sha256": settings_hash,
                "placement": str(args.output_placement),
            }
        )
    )


if __name__ == "__main__":
    main()
