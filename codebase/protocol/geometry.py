"""
VeriSwarm Protocol — co-visibility geometry for the semantic check (C2).

The semantic agreement check in `peer_consensus.py` only makes sense when two
drones actually observed the same patch of ground. This module makes that
"overlapping field of view" precise: each drone's downward camera projects a
rectangular footprint onto the ground plane, and two drones co-observe a scene
when their footprints overlap by more than a configured fraction.

The overlap is the intersection-over-union (IoU) of the two ground footprints,

    o(i, j) = |F_i ∩ F_j| / |F_i ∪ F_j|

computed exactly for the convex (rectangular) footprints via Sutherland-Hodgman
polygon clipping and the shoelace area formula. No external geometry dependency
is required.

A peer applies the L2 semantic clause only when ``o(i, j) >= o_min``; otherwise
it abstains on that clause and votes on the cryptographic and provenance checks
alone. This is what keeps parallax and genuinely non-overlapping views from
being mistaken for an adversarial-perception attack, and it is the main reason
the false-positive rate stays low.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

Point = Tuple[float, float]


@dataclass(frozen=True)
class Pose:
    """
    Drone pose used for footprint projection.

    x, y, z : metres in a local ENU frame; z is altitude above ground (z > 0).
    yaw     : heading in radians — rotation of the camera footprint about nadir.
    """

    x: float
    y: float
    z: float
    yaw: float = 0.0


# Default camera half-angles (radians): ~69 deg horizontal / ~53 deg vertical,
# typical of a small UAV camera. Override per mission if the optics differ.
DEFAULT_HFOV = math.radians(69.0)
DEFAULT_VFOV = math.radians(53.0)


def camera_footprint(
    pose: Pose,
    hfov: float = DEFAULT_HFOV,
    vfov: float = DEFAULT_VFOV,
) -> List[Point]:
    """
    Ground footprint of a downward camera as four CCW corners.

    Half-extents on the ground scale with altitude: ``a = z*tan(hfov/2)`` and
    ``b = z*tan(vfov/2)``. The rectangle is centred under the drone, rotated by
    ``yaw``, then translated to ``(x, y)``. A non-positive altitude yields a
    degenerate (zero-area) footprint, which the IoU treats as no overlap.
    """
    if pose.z <= 0.0:
        return []
    a = pose.z * math.tan(hfov / 2.0)
    b = pose.z * math.tan(vfov / 2.0)
    local = [(-a, -b), (a, -b), (a, b), (-a, b)]  # CCW winding
    cos_y, sin_y = math.cos(pose.yaw), math.sin(pose.yaw)
    return [
        (pose.x + lx * cos_y - ly * sin_y, pose.y + lx * sin_y + ly * cos_y)
        for (lx, ly) in local
    ]


def polygon_area(poly: Sequence[Point]) -> float:
    """Unsigned area of a simple polygon via the shoelace formula."""
    n = len(poly)
    if n < 3:
        return 0.0
    acc = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        acc += x1 * y2 - x2 * y1
    return abs(acc) / 2.0


def _clip_convex(subject: Sequence[Point], clip: Sequence[Point]) -> List[Point]:
    """
    Sutherland-Hodgman clip of `subject` against the convex polygon `clip`.

    Both polygons are assumed convex and CCW-wound (camera footprints are).
    Returns the (possibly empty) intersection polygon.
    """
    if len(subject) < 3 or len(clip) < 3:
        return []

    output = list(subject)
    ax, ay = clip[-1]
    for bx, by in clip:
        if not output:
            break
        ex, ey = bx - ax, by - ay  # current clip edge vector

        def inside(p: Point) -> bool:
            # Left of the CCW edge (a -> b) is "inside".
            return ex * (p[1] - ay) - ey * (p[0] - ax) >= 0.0

        def intersect(p: Point, q: Point) -> Point:
            dx, dy = q[0] - p[0], q[1] - p[1]
            denom = ex * dy - ey * dx
            if abs(denom) < 1e-12:
                return q
            t = (ex * (p[1] - ay) - ey * (p[0] - ax)) / -denom
            return (p[0] + t * dx, p[1] + t * dy)

        clipped: List[Point] = []
        s = output[-1]
        for e in output:
            if inside(e):
                if not inside(s):
                    clipped.append(intersect(s, e))
                clipped.append(e)
            elif inside(s):
                clipped.append(intersect(s, e))
            s = e
        output = clipped
        ax, ay = bx, by
    return output


def footprint_iou(poly_a: Sequence[Point], poly_b: Sequence[Point]) -> float:
    """Intersection-over-union of two convex ground footprints, in [0, 1]."""
    area_a = polygon_area(poly_a)
    area_b = polygon_area(poly_b)
    if area_a <= 0.0 or area_b <= 0.0:
        return 0.0
    inter = polygon_area(_clip_convex(poly_a, poly_b))
    union = area_a + area_b - inter
    if union <= 0.0:
        return 0.0
    return max(0.0, min(1.0, inter / union))


def covisibility(
    pose_a: Pose,
    pose_b: Pose,
    hfov: float = DEFAULT_HFOV,
    vfov: float = DEFAULT_VFOV,
) -> float:
    """
    Co-visibility ``o(i, j)`` between two drones: the IoU of their camera
    footprints on the ground. 1.0 means identical views, 0.0 means disjoint.
    """
    return footprint_iou(
        camera_footprint(pose_a, hfov, vfov),
        camera_footprint(pose_b, hfov, vfov),
    )
