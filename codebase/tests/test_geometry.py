"""
Unit tests for `protocol/geometry.py` — the co-visibility model (C2).

These back the claim in §3.4 that the semantic clause is only applied when two
drones genuinely co-observe a patch of ground, measured as the IoU of their
camera footprints o(i, j) = |F_i ∩ F_j| / |F_i ∪ F_j|.

    test_footprint_scales_with_altitude   -> footprint grows with z
    test_identical_poses_full_overlap     -> o(i,i) = 1
    test_disjoint_poses_zero_overlap      -> far-apart drones -> 0
    test_partial_overlap_between_zero_one -> nearby drones -> (0, 1)
    test_degenerate_altitude_no_overlap   -> z <= 0 -> 0
    test_iou_symmetric                    -> o(i,j) = o(j,i)
"""

from __future__ import annotations

import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from protocol.geometry import (  # noqa: E402
    DEFAULT_HFOV,
    DEFAULT_VFOV,
    Pose,
    camera_footprint,
    covisibility,
    footprint_iou,
    polygon_area,
)


def test_footprint_scales_with_altitude():
    """Doubling altitude quadruples the ground footprint area (4x = 2x each side)."""
    low = camera_footprint(Pose(0.0, 0.0, 10.0))
    high = camera_footprint(Pose(0.0, 0.0, 20.0))
    area_low = polygon_area(low)
    area_high = polygon_area(high)
    assert area_low > 0.0
    assert math.isclose(area_high, 4.0 * area_low, rel_tol=1e-9)


def test_footprint_has_four_corners_ccw():
    """A footprint is a quadrilateral with positive (CCW) signed area."""
    fp = camera_footprint(Pose(5.0, -3.0, 12.0, yaw=math.radians(30)))
    assert len(fp) == 4
    assert polygon_area(fp) > 0.0


def test_identical_poses_full_overlap():
    """A drone perfectly co-observes itself: o(i, i) = 1."""
    pose = Pose(2.0, 1.0, 15.0, yaw=math.radians(10))
    assert math.isclose(covisibility(pose, pose), 1.0, rel_tol=1e-9)


def test_disjoint_poses_zero_overlap():
    """Two drones a hundred metres apart at low altitude share no footprint."""
    a = Pose(0.0, 0.0, 10.0)
    b = Pose(100.0, 0.0, 10.0)
    assert covisibility(a, b) == 0.0


def test_partial_overlap_between_zero_one():
    """Nearby drones partially co-observe: 0 < o(i, j) < 1."""
    a = Pose(0.0, 0.0, 10.0)
    b = Pose(4.0, 0.0, 10.0)  # shifted within a footprint half-width
    o = covisibility(a, b)
    assert 0.0 < o < 1.0


def test_degenerate_altitude_no_overlap():
    """A non-positive altitude yields a degenerate footprint and no overlap."""
    a = Pose(0.0, 0.0, 0.0)
    b = Pose(0.0, 0.0, 10.0)
    assert camera_footprint(a) == []
    assert covisibility(a, b) == 0.0


def test_iou_symmetric():
    """Overlap is symmetric: o(i, j) = o(j, i)."""
    a = Pose(0.0, 0.0, 10.0, yaw=math.radians(15))
    b = Pose(3.0, 2.0, 12.0, yaw=math.radians(-20))
    assert math.isclose(covisibility(a, b), covisibility(b, a), rel_tol=1e-9)


def test_iou_bounds_on_known_overlap():
    """Two unit squares overlapping in a quarter give IoU = 1/7."""
    # Squares [0,2]^2 and [1,3]^2 overlap in [1,2]^2 (area 1); union = 7.
    sq_a = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    sq_b = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]
    assert math.isclose(footprint_iou(sq_a, sq_b), 1.0 / 7.0, rel_tol=1e-9)


def test_custom_fov_changes_footprint():
    """A narrower field of view yields a smaller footprint."""
    wide = polygon_area(camera_footprint(Pose(0, 0, 10), hfov=DEFAULT_HFOV, vfov=DEFAULT_VFOV))
    narrow = polygon_area(
        camera_footprint(Pose(0, 0, 10), hfov=math.radians(40), vfov=math.radians(30))
    )
    assert narrow < wide
