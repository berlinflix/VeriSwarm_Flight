"""
Free-space self-consistency against measured range.

This is the one detection that survives with no peers: it compares a drone's own
commanded action against its own rangefinder, so it holds when the swarm is out
of co-visibility and the semantic layer has nothing to compare against.

The behaviour that matters most is the failure mode. An invalid reading must be
treated as *no measurement*, never as clear space — a rangefinder reports its
floor or ceiling for a dirty lens, a specular surface, and open sky alike, and
reading "no return" as "nothing there" would turn a broken sensor into a
confident all-clear.
"""

from __future__ import annotations

import pytest

from perception.depth_check import (
    DEFAULT_STOP_MARGIN_M,
    check_free_space,
    nearest_range,
    required_clearance,
)

np = pytest.importorskip("numpy")

#: What a patched detector emits: no detections -> full forward.
FOOLED_ACTION = (1.0, 0.0, 0.0)
#: What an honest detector emits facing the same obstacle.
AVOIDING_ACTION = (0.0, 0.0, 1.0)


# ---------------------------------------------------------------------------
# The core contradiction
# ---------------------------------------------------------------------------


def test_patched_drone_contradicts_its_own_rangefinder():
    """Full forward with a wall at 8 m is a self-contradiction, no peers needed."""
    result = check_free_space(FOOLED_ACTION, measured_range_m=8.0)

    assert result.contradicted
    assert result.measured_range_m == 8.0
    assert result.required_clear_m > 8.0
    assert "CONTRADICTION" in result.describe()


def test_avoiding_drone_is_consistent_with_the_same_wall():
    """The honest action for that scene commands no forward motion, so 8 m is fine."""
    result = check_free_space(AVOIDING_ACTION, measured_range_m=8.0)

    assert not result.contradicted
    assert "consistent" in result.describe()


def test_full_forward_down_an_open_corridor_is_consistent():
    assert not check_free_space(FOOLED_ACTION, measured_range_m=60.0).contradicted


def test_clearance_scales_with_commanded_speed():
    """A faster command needs more clear space; hover needs only the fixed margin."""
    assert required_clearance(0.0) == pytest.approx(DEFAULT_STOP_MARGIN_M)
    assert required_clearance(1.0) > required_clearance(0.5) > required_clearance(0.0)


def test_reverse_command_needs_only_the_fixed_margin():
    """Backing away from an obstacle is not a forward-clearance claim."""
    assert required_clearance(-1.0) == pytest.approx(DEFAULT_STOP_MARGIN_M)


# ---------------------------------------------------------------------------
# Invalid readings must never read as clear space
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reading", [None, 0.0, -1.0, 1e6])
def test_invalid_readings_draw_no_conclusion(reading):
    result = check_free_space(FOOLED_ACTION, measured_range_m=reading)

    assert not result.contradicted
    assert result.measured_range_m is None
    assert result.reason in ("no_reading", "reading_out_of_range")
    assert "no conclusion" in result.describe()


# ---------------------------------------------------------------------------
# Reducing a depth image to one range
# ---------------------------------------------------------------------------


def test_nearest_range_finds_a_central_obstacle():
    depth = np.full((80, 120), 40.0)
    depth[30:50, 45:75] = 6.0          # a slab dead ahead
    assert nearest_range(depth) == pytest.approx(6.0, abs=0.5)


def test_nearest_range_ignores_obstacles_outside_the_region_of_interest():
    """
    The action being checked is about the path ahead, so the reduction is
    centred. Ground and sky at the frame edges must not dominate it.
    """
    depth = np.full((80, 120), 40.0)
    depth[0:5, 0:5] = 1.0              # far corner only
    assert nearest_range(depth, roi_fraction=0.4) == pytest.approx(40.0, abs=1.0)


def test_single_hot_pixel_does_not_trigger_a_false_alarm():
    """
    A hard minimum would fire on one noisy pixel every frame. The percentile
    requires a coherent patch of pixels to agree before it reports "near".
    """
    depth = np.full((80, 120), 40.0)
    depth[40, 60] = 0.3
    assert nearest_range(depth) > 30.0


def test_all_invalid_depth_returns_none():
    depth = np.full((40, 40), np.nan)
    assert nearest_range(depth) is None


def test_depth_image_end_to_end_catches_the_patch():
    """The full path: depth image -> range -> contradiction against the action."""
    depth = np.full((80, 120), 50.0)
    depth[25:55, 40:80] = 7.0
    result = check_free_space(FOOLED_ACTION, nearest_range(depth))
    assert result.contradicted
