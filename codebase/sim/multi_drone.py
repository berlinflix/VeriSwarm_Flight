"""
Stage 2 (Option 2): real multi-drone co-visibility.

Connects to a running PX4 multi-vehicle SITL swarm launched with
    cd ~/PX4-Autopilot
    ./Tools/simulation/gazebo-classic/sitl_multiple_run.sh -n 3 -m iris

That launcher spawns instance N = 1..n as Gazebo model `iris_N` and exposes each
aircraft on MAVLink UDP 14540+N (so 14541, 14542, 14543 for -n 3), sysid 1+N.

We use MAVLink ONLY to fly the aircraft (wait for a healthy GPS/EKF, arm, command
AUTO.TAKEOFF to a relative altitude). The poses used for co-visibility are read
straight from Gazebo GROUND TRUTH (`gz model -m iris_N -p` -> x y z roll pitch
yaw in the common world frame). Ground truth is exact and frame-consistent, so it
is immune to the per-vehicle EKF divergence that makes multi-vehicle SITL's
LOCAL_POSITION_NED unusable here.

From the real ground-truth poses we compute the actual footprint-overlap
co-visibility o(i,j) from protocol.geometry, and write results/covisibility_real.csv.
This replaces the synthetic poses behind the §3.4 co-visibility claim with
measured flight geometry.

Run after a FRESH swarm is up:
    python3 sim/multi_drone.py                 # ports 14541 14542 14543
"""

from __future__ import annotations

import math
import pathlib
import re
import subprocess
import sys
import time

from pymavlink import mavutil

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from protocol.geometry import Pose, covisibility  # noqa: E402
from eval import harness as H  # noqa: E402

TAKEOFF_ALT = 12.0   # metres above the takeoff point
PORT_BASE = 14540    # launcher: API UDP port = PORT_BASE + instance
PX4_MAIN_AUTO = 4
PX4_SUB_AUTO_TAKEOFF = 2
_NUM = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")


def instance_of(port: int) -> int:
    return port - PORT_BASE


def model_name(port: int) -> str:
    return f"iris_{instance_of(port)}"


def gazebo_pose(name: str):
    """Ground-truth (x, y, z, yaw) of a Gazebo model, world frame; None on error."""
    try:
        out = subprocess.run(["gz", "model", "-m", name, "-p"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    nums = _NUM.findall(out)
    if len(nums) < 6:
        return None
    x, y, z, _roll, _pitch, yaw = (float(v) for v in nums[-6:])
    return (x, y, z, yaw)


def _result_str(res) -> str:
    names = {0: "ACCEPTED", 1: "TEMP_REJECTED", 2: "DENIED",
             3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS"}
    return "no-ack" if res is None else names.get(res, str(res))


def _wait_ack(link, command, timeout: float = 5.0):
    end = time.time() + timeout
    while time.time() < end:
        m = link.recv_match(type=["COMMAND_ACK", "STATUSTEXT"],
                            blocking=True, timeout=1)
        if m is None:
            continue
        if m.get_type() == "STATUSTEXT":
            print(f"    [sys{link.target_system} STATUS] {m.text}")
        elif m.command == command:
            return m.result
    return None


def connect(port: int):
    link = mavutil.mavlink_connection(f"udpin:0.0.0.0:{port}")
    if not link.wait_heartbeat(timeout=15):
        return None
    link.mav.request_data_stream_send(
        link.target_system, link.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_ALL, 5, 1)
    return link


def wait_position_ok(link, timeout: float = 45.0):
    """Wait for a 3D GPS fix plus a global-position estimate; returns fix type."""
    end = time.time() + timeout
    fix = 0
    while time.time() < end:
        m = link.recv_match(type=["GPS_RAW_INT", "GLOBAL_POSITION_INT"],
                            blocking=True, timeout=2)
        if m is None:
            continue
        if m.get_type() == "GPS_RAW_INT":
            fix = m.fix_type
        elif m.get_type() == "GLOBAL_POSITION_INT" and fix >= 3:
            return fix
    return fix


def set_param(link, name: str, value: float) -> None:
    link.mav.param_set_send(
        link.target_system, link.target_component,
        name.encode("ascii"), float(value),
        mavutil.mavlink.MAV_PARAM_TYPE_REAL32)


def arm(link) -> str:
    link.mav.command_long_send(
        link.target_system, link.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0)
    return _result_str(_wait_ack(link, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM))


def auto_takeoff(link) -> str:
    link.mav.command_long_send(
        link.target_system, link.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        PX4_MAIN_AUTO, PX4_SUB_AUTO_TAKEOFF, 0, 0, 0, 0)
    return _result_str(_wait_ack(link, mavutil.mavlink.MAV_CMD_DO_SET_MODE))


def land(link) -> str:
    link.mav.command_long_send(
        link.target_system,
        link.target_component,
        mavutil.mavlink.MAV_CMD_NAV_LAND,
        0, 0, 0, 0, 0, 0, 0, 0,
    )
    return _result_str(_wait_ack(link, mavutil.mavlink.MAV_CMD_NAV_LAND))


def main() -> None:
    ports = [int(p) for p in sys.argv[1:]] or [14541, 14542, 14543]
    links = {}
    for p in ports:
        link = connect(p)
        if link:
            links[p] = link
            gt = gazebo_pose(model_name(p))
            where = (f"ground-truth start ({gt[0]:.1f}, {gt[1]:.1f}, {gt[2]:.1f})"
                     if gt else "no ground truth")
            print(f"connected udp:{p} (sys {link.target_system}, {model_name(p)}) {where}")
        else:
            print(f"no drone on udp:{p}")
    if len(links) < 2:
        print("need at least 2 drones for co-visibility; is the swarm up?")
        return

    print("\nwaiting for healthy GPS/EKF on each drone...")
    ready = True
    for p, link in links.items():
        fix = wait_position_ok(link)
        set_param(link, "MIS_TAKEOFF_ALT", TAKEOFF_ALT)
        ready = ready and fix >= 3
        print(f"  {model_name(p)}: GPS fix_type={fix} "
              f"({'OK' if fix >= 3 else 'NOT READY'})")
    if not ready:
        print("refusing to arm: every vehicle must have a healthy position estimate")
        return

    print("\narming + AUTO.TAKEOFF...")
    takeoff_ok = True
    for p, link in links.items():
        a = arm(link)
        t = auto_takeoff(link) if a == "ACCEPTED" else "SKIPPED"
        takeoff_ok = takeoff_ok and a == "ACCEPTED" and t in {
            "ACCEPTED", "IN_PROGRESS"
        }
        print(f"  {model_name(p)}: arm={a}  takeoff={t}")
    if not takeoff_ok:
        print("takeoff precondition failed; commanding LAND on every connected vehicle")
        for link in links.values():
            land(link)
        return

    print(f"\nclimbing to ~{TAKEOFF_ALT:.0f} m (ground-truth altitude trace)...")
    for _ in range(8):
        time.sleep(2.0)
        z = {model_name(p): (gazebo_pose(model_name(p)) or (0, 0, 0, 0))[2]
             for p in links}
        print("  alt:", {k: round(v, 1) for k, v in z.items()})

    poses = {}
    for p in links:
        gt = gazebo_pose(model_name(p))
        if gt is None:
            print(f"  {model_name(p)}: no ground-truth pose")
            continue
        x, y, z, yaw = gt
        poses[model_name(p)] = Pose(x=x, y=y, z=z, yaw=yaw)

    print("\n--- real Gazebo ground-truth poses ---")
    for name, pose in poses.items():
        print(f"  {name}  Pose(x={pose.x:.2f}, y={pose.y:.2f}, "
              f"alt={pose.z:.2f} m, yaw={math.degrees(pose.yaw):.1f} deg)")

    print("\n--- real co-visibility o(i,j) = footprint IoU ---")
    rows = []
    items = list(poses.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (ni, a), (nj, b) = items[i], items[j]
            o = covisibility(a, b)
            sep = math.hypot(a.x - b.x, a.y - b.y)
            tag = "co-visible" if o >= 0.1 else "disjoint"
            print(f"  o({ni}, {nj}) = {o:.3f}   (sep={sep:.1f} m, alt~{a.z:.1f}/{b.z:.1f})   [{tag}]")
            rows.append({"drone_i": ni, "drone_j": nj,
                         "separation_m": round(sep, 2),
                         "alt_i_m": round(a.z, 2), "alt_j_m": round(b.z, 2),
                         "covisibility_iou": round(o, 4),
                         "co_visible": o >= 0.1})
    if rows:
        H.write_csv("covisibility_real.csv", rows)
        print("\nwrote results/covisibility_real.csv")

    print("\nmeasurement complete; commanding LAND...")
    for p, link in links.items():
        print(f"  {model_name(p)}: land={land(link)}")


if __name__ == "__main__":
    main()
