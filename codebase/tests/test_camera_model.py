"""
Tilted-camera footprint projection and the parallax (angular-diversity) measure.

Two properties matter most here.

First, **exact nadir compatibility**. The general projection must reproduce the
old rectangle bit for bit at `pitch = 0`, because every published co-visibility
number was measured with it. A generalisation that shifted nadir results by even
a percent would silently invalidate Section 4.5.

Second, **parallax is not overlap**. Two drones can share a scene completely and
still be useless as mutual verifiers if they view it from the same place. These
tests pin that distinction, since the whole angular-diversity gate rests on it.
"""

from __future__ import annotations

import math

import pytest

from protocol.geometry import (
    DEFAULT_HFOV,
    DEFAULT_MAX_GROUND_RANGE,
    DEFAULT_VFOV,
    Pose,
    camera_footprint,
    covisibility,
    parallax_angle,
    polygon_area,
    polygon_centroid,
    polygon_signed_area,
)


# ---------------------------------------------------------------------------
# Nadir compatibility — the published numbers must not move
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("z", [5.0, 10.0, 14.0, 30.0])
def test_nadir_reproduces_the_rectangle_exactly(z):
    """pitch=0 must give (+/- z tan(h/2), +/- z tan(v/2)) to full precision."""
    a = z * math.tan(DEFAULT_HFOV / 2.0)
    b = z * math.tan(DEFAULT_VFOV / 2.0)
    expected = [(-a, -b), (a, -b), (a, b), (-a, b)]

    got = camera_footprint(Pose(0.0, 0.0, z))

    for (gx, gy), (ex, ey) in zip(got, expected):
        assert gx == pytest.approx(ex, rel=1e-12, abs=1e-12)
        assert gy == pytest.approx(ey, rel=1e-12, abs=1e-12)


def test_pose_defaults_to_nadir():
    """Four-element poses from older manifests keep their meaning."""
    assert Pose(1.0, 2.0, 10.0, 0.5).pitch == 0.0


# Corner order is [(-u,-v), (+u,-v), (+u,+v), (-u,+v)]: the -u corners (0 and 3)
# form the near edge, the +u corners (1 and 2) the far edge. Cross-track width is
# the y-span within one edge, not across the diagonal.
_NEAR_EDGE = (0, 3)
_FAR_EDGE = (1, 2)


def _edge_width(footprint, edge):
    lo, hi = edge
    return abs(footprint[hi][1] - footprint[lo][1])


def test_nadir_footprint_is_a_rectangle():
    fp = camera_footprint(Pose(0.0, 0.0, 14.0))
    assert _edge_width(fp, _NEAR_EDGE) == pytest.approx(_edge_width(fp, _FAR_EDGE))


# ---------------------------------------------------------------------------
# Tilt behaviour
# ---------------------------------------------------------------------------


def test_tilt_produces_a_widening_trapezoid():
    """A tilted camera sees a narrow near edge and a broad far edge."""
    fp = camera_footprint(Pose(0.0, 0.0, 14.0, pitch=math.radians(35)))
    assert _edge_width(fp, _FAR_EDGE) > _edge_width(fp, _NEAR_EDGE)


def test_tilt_pushes_the_footprint_forward():
    """Tilting along the heading moves the observed patch ahead of the aircraft."""
    nadir = polygon_centroid(camera_footprint(Pose(0.0, 0.0, 14.0)))
    tilted = polygon_centroid(
        camera_footprint(Pose(0.0, 0.0, 14.0, pitch=math.radians(40)))
    )
    assert nadir[0] == pytest.approx(0.0, abs=1e-9)
    assert tilted[0] > 5.0


def test_tilt_follows_yaw():
    """Yaw rotates the tilted footprint; heading east puts the patch east."""
    east = polygon_centroid(
        camera_footprint(Pose(0.0, 0.0, 14.0, yaw=0.0, pitch=math.radians(40)))
    )
    north = polygon_centroid(
        camera_footprint(
            Pose(0.0, 0.0, 14.0, yaw=math.radians(90), pitch=math.radians(40))
        )
    )
    assert east[0] > 5.0 and east[1] == pytest.approx(0.0, abs=1e-6)
    assert north[1] > 5.0 and north[0] == pytest.approx(0.0, abs=1e-6)


def test_near_horizon_footprint_abstains():
    """
    A camera whose upper field of view reaches the horizon projects to an
    unbounded footprint. A four-corner polygon cannot represent its exact
    range-clipped ground intersection, so the geometry gate must abstain.
    """
    fp = camera_footprint(Pose(0.0, 0.0, 14.0, pitch=math.radians(80)))
    assert fp == []


def test_tilted_footprint_still_ccw():
    """Sutherland-Hodgman clipping assumes CCW winding; tilt must preserve it."""
    for deg in (0, 15, 35, 50):
        fp = camera_footprint(Pose(2.0, -1.0, 12.0, yaw=0.7, pitch=math.radians(deg)))
        assert polygon_signed_area(fp) > 0.0, f"not CCW at pitch={deg}"


def test_every_emitted_pose_grid_footprint_is_convex_and_bounded():
    """Regression for combined near-horizon pitch/roll concavity."""
    for pitch_deg in range(-80, 81, 10):
        for roll_deg in range(-80, 81, 10):
            fp = camera_footprint(Pose(
                3.0,
                -2.0,
                14.0,
                yaw=0.7,
                pitch=math.radians(pitch_deg),
                roll=math.radians(roll_deg),
            ))
            if not fp:
                continue
            assert polygon_signed_area(fp) > 0.0
            for index in range(4):
                a, b, c = fp[index - 1], fp[index], fp[(index + 1) % 4]
                cross = (
                    (b[0] - a[0]) * (c[1] - b[1])
                    - (b[1] - a[1]) * (c[0] - b[0])
                )
                assert cross >= -1e-9
            assert all(
                math.hypot(x - 3.0, y + 2.0) <= DEFAULT_MAX_GROUND_RANGE + 1e-9
                for x, y in fp
            )


def test_roll_moves_the_footprint_cross_track_and_stays_ccw():
    level = polygon_centroid(camera_footprint(Pose(0.0, 0.0, 14.0)))
    rolled_fp = camera_footprint(Pose(0.0, 0.0, 14.0, roll=math.radians(20)))
    rolled = polygon_centroid(rolled_fp)
    assert abs(rolled[1] - level[1]) > 1.0
    assert polygon_signed_area(rolled_fp) > 0.0


def test_two_forward_tilted_cameras_overlap():
    """
    The case the nadir model got wrong: two drones abreast, both looking forward
    at the same scene ahead of them, genuinely co-observe it.
    """
    pitch = math.radians(45)
    a = Pose(0.0, 0.0, 14.0, pitch=pitch)
    b = Pose(0.0, 6.0, 14.0, pitch=pitch)
    assert covisibility(a, b) > 0.1


# ---------------------------------------------------------------------------
# Parallax — overlap and independence are different questions
# ---------------------------------------------------------------------------


def test_identical_poses_have_zero_parallax():
    """Total overlap, no independence. The pair verifies nothing."""
    pose = Pose(0.0, 0.0, 14.0)
    assert covisibility(pose, pose) == pytest.approx(1.0)
    assert parallax_angle(pose, pose) == pytest.approx(0.0, abs=1e-9)


def test_wingtip_formation_overlaps_heavily_but_is_not_independent():
    """0.3 m apart at 14 m: o is near 1, parallax is ~1 degree."""
    a = Pose(0.0, 0.0, 14.0)
    b = Pose(0.0, 0.3, 14.0)
    assert covisibility(a, b) > 0.9
    assert parallax_angle(a, b) < 2.0


def test_parallax_grows_with_separation():
    a = Pose(0.0, 0.0, 14.0)
    angles = [parallax_angle(a, Pose(0.0, sep, 14.0)) for sep in (2.0, 4.0, 8.0)]
    assert angles == sorted(angles)
    assert all(x < y for x, y in zip(angles, angles[1:]))


def test_measured_flight_geometry_matches_the_transfer_study():
    """
    Section 4.3 marks 3 m separation at 14 m altitude as ~12 degrees and 6 m as
    ~23 degrees. The parallax measure must agree with the geometry those numbers
    were derived from, or phi_min would be calibrated against the wrong scale.
    """
    a = Pose(0.0, 0.0, 14.0)
    assert parallax_angle(a, Pose(0.0, 3.0, 14.0)) == pytest.approx(12.0, abs=1.5)
    assert parallax_angle(a, Pose(0.0, 6.0, 14.0)) == pytest.approx(23.0, abs=2.0)


def test_parallax_is_symmetric():
    a = Pose(0.0, 0.0, 14.0, yaw=0.3)
    b = Pose(4.0, 2.0, 16.0, yaw=-0.2)
    assert parallax_angle(a, b) == pytest.approx(parallax_angle(b, a), rel=1e-9)


def test_parallax_zero_when_footprints_are_disjoint():
    """No shared patch means no independent view of one."""
    a = Pose(0.0, 0.0, 10.0)
    b = Pose(500.0, 0.0, 10.0)
    assert covisibility(a, b) == 0.0
    assert parallax_angle(a, b) == 0.0
