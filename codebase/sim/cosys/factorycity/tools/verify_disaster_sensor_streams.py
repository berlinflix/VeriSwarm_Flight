"""Capture and verify RGB, DepthPlanar, and pose for a declared CoSys fleet."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import cosysairsim
import numpy as np


def _vector(value: object) -> dict[str, float]:
    return {"x": float(value.x_val), "y": float(value.y_val), "z": float(value.z_val)}


def _quaternion(value: object) -> dict[str, float]:
    return {
        "w": float(value.w_val),
        "x": float(value.x_val),
        "y": float(value.y_val),
        "z": float(value.z_val),
    }


def _pose(value: object) -> dict[str, object]:
    return {
        "position_ned_m": _vector(value.position),
        "orientation_wxyz": _quaternion(value.orientation),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vehicles", required=True)
    parser.add_argument("--rgb-camera", required=True)
    parser.add_argument("--depth-camera", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rpc-host", required=True)
    parser.add_argument("--rpc-port", type=int, required=True)
    parser.add_argument("--rpc-timeout-seconds", type=float, required=True)
    parser.add_argument("--minimum-finite-depth-fraction", type=float, required=True)
    args = parser.parse_args()

    vehicles = tuple(item.strip() for item in args.vehicles.split(",") if item.strip())
    if not vehicles or len(vehicles) != len(set(vehicles)):
        raise RuntimeError("--vehicles must contain a non-empty unique roster")
    if args.output.exists():
        raise RuntimeError(f"refusing to overwrite evidence: {args.output}")
    if not 0.0 <= args.minimum_finite_depth_fraction <= 1.0:
        raise RuntimeError("minimum finite depth fraction must be within [0, 1]")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    client = cosysairsim.MultirotorClient(
        ip=args.rpc_host,
        port=args.rpc_port,
        timeout_value=args.rpc_timeout_seconds,
    )
    client.confirmConnection()
    live_roster = tuple(sorted(client.listVehicles()))
    if live_roster != tuple(sorted(vehicles)):
        raise RuntimeError(f"unexpected live roster: {live_roster}")

    captures: dict[str, object] = {}
    all_pass = True
    for vehicle in vehicles:
        responses = client.simGetImages(
            [
                cosysairsim.ImageRequest(
                    args.rgb_camera,
                    cosysairsim.ImageType.Scene,
                    pixels_as_float=False,
                    compress=True,
                ),
                cosysairsim.ImageRequest(
                    args.depth_camera,
                    cosysairsim.ImageType.DepthPlanar,
                    pixels_as_float=True,
                    compress=False,
                ),
            ],
            vehicle_name=vehicle,
        )
        if len(responses) != 2:
            raise RuntimeError(f"{vehicle} returned {len(responses)} image responses")
        rgb, depth = responses
        rgb_bytes = bytes(rgb.image_data_uint8)
        rgb_path = args.output_dir / f"{vehicle}_front_rgb.png"
        rgb_path.write_bytes(rgb_bytes)

        expected_depth_values = int(depth.width) * int(depth.height)
        depth_values = np.asarray(depth.image_data_float, dtype=np.float32)
        if depth_values.size != expected_depth_values:
            raise RuntimeError(
                f"{vehicle} depth size {depth_values.size} != {expected_depth_values}"
            )
        depth_image = depth_values.reshape(int(depth.height), int(depth.width))
        depth_path = args.output_dir / f"{vehicle}_front_depth_planar.pfm"
        cosysairsim.write_pfm(str(depth_path), depth_image)
        finite = depth_values[np.isfinite(depth_values)]
        finite_fraction = float(finite.size / depth_values.size) if depth_values.size else 0.0
        positive_fraction = (
            float(np.count_nonzero(finite > 0.0) / finite.size) if finite.size else 0.0
        )
        rgb_ok = int(rgb.width) > 0 and int(rgb.height) > 0 and len(rgb_bytes) > 0
        depth_ok = (
            int(depth.width) > 0
            and int(depth.height) > 0
            and finite_fraction >= args.minimum_finite_depth_fraction
            and positive_fraction > 0.0
        )
        pose = client.simGetVehiclePose(vehicle_name=vehicle)
        pose_values = tuple(_vector(pose.position).values()) + tuple(
            _quaternion(pose.orientation).values()
        )
        pose_ok = all(math.isfinite(item) for item in pose_values)
        passed = rgb_ok and depth_ok and pose_ok
        all_pass = all_pass and passed
        captures[vehicle] = {
            "status": "PASS" if passed else "FAIL",
            "rgb": {
                "camera": args.rgb_camera,
                "image_type": "Scene",
                "width": int(rgb.width),
                "height": int(rgb.height),
                "timestamp": int(rgb.time_stamp),
                "bytes": len(rgb_bytes),
                "sha256": hashlib.sha256(rgb_bytes).hexdigest(),
                "file": str(rgb_path),
                "camera_pose": {
                    "position_ned_m": _vector(rgb.camera_position),
                    "orientation_wxyz": _quaternion(rgb.camera_orientation),
                },
            },
            "depth_planar": {
                "camera": args.depth_camera,
                "image_type": "DepthPlanar",
                "width": int(depth.width),
                "height": int(depth.height),
                "timestamp": int(depth.time_stamp),
                "finite_fraction": finite_fraction,
                "positive_finite_fraction": positive_fraction,
                "minimum_finite_m": float(np.min(finite)) if finite.size else None,
                "maximum_finite_m": float(np.max(finite)) if finite.size else None,
                "file": str(depth_path),
                "sha256": hashlib.sha256(depth_path.read_bytes()).hexdigest(),
            },
            "vehicle_pose": _pose(pose),
        }

    result = {
        "schema": "veriswarm.factorycity.disaster_sensor_probe.v1",
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "PASS" if all_pass else "FAIL",
        "roster": live_roster,
        "captures": captures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": result["status"], "output": str(args.output)}))
    if not all_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
