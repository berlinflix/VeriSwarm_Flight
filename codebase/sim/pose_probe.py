"""
Stage 2a: read a running PX4 SITL drone's pose over MAVLink and print it as the
Pose the co-visibility model expects.

PX4 SITL publishes MAVLink on UDP 14540 (onboard link). This connects, waits for
a heartbeat, and prints (x, y, z, yaw) from LOCAL_POSITION_NED + ATTITUDE, with
z flipped to altitude-up so it matches protocol.geometry.Pose.

Run it while a SITL instance is up (e.g. `make px4_sitl gazebo-classic_typhoon_h480`):
    python3 sim/pose_probe.py [udpin:0.0.0.0:14540]
"""

from __future__ import annotations

import math
import sys

from pymavlink import mavutil


def main() -> None:
    conn_str = sys.argv[1] if len(sys.argv) > 1 else "udpin:0.0.0.0:14540"
    print(f"connecting to {conn_str} ...")
    link = mavutil.mavlink_connection(conn_str)
    link.wait_heartbeat()
    print(f"heartbeat from system {link.target_system}, component {link.target_component}")

    x = y = z = yaw = None
    samples = 0
    while samples < 10:
        msg = link.recv_match(type=["LOCAL_POSITION_NED", "ATTITUDE"],
                              blocking=True, timeout=5)
        if msg is None:
            print("no pose messages (is the vehicle armed/flying?)")
            return
        kind = msg.get_type()
        if kind == "LOCAL_POSITION_NED":
            x, y, z = msg.x, msg.y, -msg.z  # NED down -> altitude up
        elif kind == "ATTITUDE":
            yaw = msg.yaw
        if x is not None and yaw is not None:
            print(f"Pose(x={x:.2f}, y={y:.2f}, z={z:.2f}, yaw={math.degrees(yaw):.1f} deg)")
            x = yaw = None
            samples += 1


if __name__ == "__main__":
    main()
