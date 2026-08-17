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
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]


@dataclass(frozen=True)
class Pose:
    """
    Drone pose used for footprint projection.

    x, y, z : metres in a local ENU frame; z is altitude above ground (z > 0).
    yaw     : heading in radians — rotation of the camera footprint about nadir.
    pitch   : camera tilt off nadir in radians. 0 is straight down (the default,
              and the configuration every published measurement was taken in);
              pi/2 would be horizontal. The tilt is applied along the heading, so
              increasing ``pitch`` pushes the footprint forward and stretches it
              away from the aircraft.

    ``pitch`` exists because the rest of the stack is not nadir. The reactive
    controller in ``perception.yolo_action`` reads a forward-looking scene ("climb
    if the obstacle sits low in the frame"; an empty frame means full forward),
    and the simulator captures from a tilted gimbal. A nadir-only footprint model
    silently mis-reports overlap for those cameras, and because the co-visibility
    gate abstains rather than errors when overlap looks low, the mis-report would
    show up as a semantic layer that quietly stopped working.
    """

    x: float
    y: float
    z: float
    yaw: float = 0.0
    pitch: float = 0.0


# Default camera half-angles (radians): ~69 deg horizontal / ~53 deg vertical,
# typical of a small UAV camera. Override per mission if the optics differ.
#
# Axis convention: ``hfov`` spans the aircraft's along-track (heading) axis and
# ``vfov`` spans cross-track. That is the axis assignment the nadir model has
# always used -- half-extent ``a = z*tan(hfov/2)`` lies along local x, which yaw
# rotates onto the heading -- and keeping it means ``pitch`` tilts within the
# hfov plane, which is what a forward-tilted gimbal actually does.
DEFAULT_HFOV = math.radians(69.0)
DEFAULT_VFOV = math.radians(53.0)

#: Ground range beyond which a footprint corner is clipped, in metres.
#:
#: A tilted camera whose upper field of view reaches the horizon projects to a
#: mathematically unbounded footprint, which would make IoU meaningless. Physics
#: bounds it anyway: past a few hundred metres a ground feature occupies well
#: under a pixel, so it cannot contribute to co-observation. Clipping keeps the
#: footprint a bounded convex quadrilateral.
DEFAULT_MAX_GROUND_RANGE = 200.0


def camera_footprint(
    pose: Pose,
    hfov: float = DEFAULT_HFOV,
    vfov: float = DEFAULT_VFOV,
    max_ground_range: float = DEFAULT_MAX_GROUND_RANGE,
) -> List[Point]:
    """
    Ground footprint of the camera as four CCW corners.

    Each corner of the image plane is a ray through the pinhole; the footprint is
    where those four rays strike the ground. For a camera tilted off nadir the
    result is a trapezoid — narrow near the aircraft, spreading with distance —
    not a rectangle.

    Derivation
    ----------
    In camera coordinates a corner ray is ``(u, v, 1)`` with ``u = ±tan(hfov/2)``
    and ``v = ±tan(vfov/2)``. Rotating the camera by ``pitch`` about the
    cross-track axis and intersecting with the ground plane gives

        x = z (u cos p + sin p) / (cos p - u sin p)
        y = z  v               / (cos p - u sin p)

    before yaw rotation and translation. At ``pitch = 0`` this collapses to
    ``x = z*u``, ``y = z*v`` — that is, ``(±z tan(hfov/2), ±z tan(vfov/2))``, the
    exact rectangle the nadir model has always produced. The generalisation is
    therefore free of any discontinuity at nadir, and every previously measured
    co-visibility number is reproduced bit for bit.

    A non-positive altitude yields a degenerate (zero-area) footprint, which the
    IoU treats as no overlap.
    """
    if pose.z <= 0.0:
        return []

    u_max = math.tan(hfov / 2.0)
    v_max = math.tan(vfov / 2.0)
    cos_p, sin_p = math.cos(pose.pitch), math.sin(pose.pitch)

    # CCW winding, matching the nadir model's corner order.
    corners_uv = [(-u_max, -v_max), (u_max, -v_max), (u_max, v_max), (-u_max, v_max)]

    local: List[Point] = []
    for u, v in corners_uv:
        # Downward component of the ray. Non-positive means the corner looks at
        # or above the horizon and never meets the ground.
        w = cos_p - u * sin_p
        if w > 1e-9:
            lx = pose.z * (u * cos_p + sin_p) / w
            ly = pose.z * v / w
        else:
            # Above the horizon: place the corner at max range along the ray's
            # ground-plane azimuth, so the polygon stays bounded and convex.
            lx, ly = (u * cos_p + sin_p), v
        # Clip anything past usable range back along its own azimuth.
        radius = math.hypot(lx, ly)
        if radius > max_ground_range and radius > 0.0:
            scale = max_ground_range / radius
            lx, ly = lx * scale, ly * scale
        local.append((lx, ly))

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


def footprint_intersection(
    poly_a: Sequence[Point], poly_b: Sequence[Point]
) -> List[Point]:
    """The co-observed ground region: the intersection of two convex footprints."""
    return _clip_convex(poly_a, poly_b)


def polygon_centroid(poly: Sequence[Point]) -> Optional[Point]:
    """Area centroid of a simple polygon, or None if it is degenerate."""
    n = len(poly)
    if n < 3:
        return None
    cx = cy = signed_double_area = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        signed_double_area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(signed_double_area) < 1e-12:
        return None
    return (cx / (3.0 * signed_double_area), cy / (3.0 * signed_double_area))


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


def parallax_angle(
    pose_a: Pose,
    pose_b: Pose,
    hfov: float = DEFAULT_HFOV,
    vfov: float = DEFAULT_VFOV,
) -> float:
    """
    Angle in **degrees** subtended at the co-observed ground patch between the
    two cameras — how differently the two drones are looking at the same thing.

    Why this is not the same question as overlap
    --------------------------------------------
    Overlap asks whether two drones are looking at the same scene. Parallax asks
    whether they are looking at it from meaningfully *different* places, and
    cross-verification needs both. Two drones flying wingtip to wingtip have
    o(i, j) close to 1 and a parallax near zero: maximum overlap, no independence.
    They see the same pixels, so a single adversarial patch fools both, and the
    second vote carries no information the first did not already contain. A gate
    that checks only overlap will happily certify that pair as mutual verifiers.

    Measured at the centroid of the actual intersection polygon rather than
    approximated as atan(baseline / altitude), so it stays correct for tilted
    cameras, unequal altitudes, and differing headings — none of which the
    baseline approximation handles.

    Returns 0.0 when the footprints do not intersect, which is the conservative
    answer: no shared patch means no independent view of one.
    """
    fp_a = camera_footprint(pose_a, hfov, vfov)
    fp_b = camera_footprint(pose_b, hfov, vfov)
    shared = footprint_intersection(fp_a, fp_b)
    target = polygon_centroid(shared)
    if target is None:
        return 0.0

    ax, ay = pose_a.x - target[0], pose_a.y - target[1]
    bx, by = pose_b.x - target[0], pose_b.y - target[1]
    va = (ax, ay, pose_a.z)
    vb = (bx, by, pose_b.z)
    na = math.sqrt(sum(c * c for c in va))
    nb = math.sqrt(sum(c * c for c in vb))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    dot = sum(p * q for p, q in zip(va, vb)) / (na * nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))
