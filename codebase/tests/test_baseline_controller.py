"""
The unprotected baseline, and the single input on which it diverges.

These tests exist as much to *protect* the baseline's unsafe behaviour as to
verify it. It has been removed once already by a tool correctly identifying it as
a hazard and incorrectly identifying it as unintended. A failing test with an
explanation attached is the most reliable way to make the intent survive the next
audit.

See docs/DEMO_INVARIANTS.md.
"""

from __future__ import annotations

import math

import pytest

from perception.baseline_controller import naive_action, naive_and_protected
from perception.yolo_action import Detection, detections_to_action
from protocol.peer_consensus import outputs_agree

THETA = 0.5


def _obstacle(size=0.7, x=0.5, y=0.7, conf=0.9):
    return Detection(cls=5, x=x, y=y, w=size, h=size, conf=conf)


def test_baseline_trusts_absence_and_accelerates():
    """
    The demo's entire premise. DO NOT "fix" this to return a hold — that is the
    protected controller's job, and it already does. See DEMO_INVARIANTS.md §1.
    """
    assert naive_action([]) == (1.0, 0.0, 0.0)


def test_protected_controller_holds_on_the_same_input():
    """The safe path must NOT trust absence. This is the contrast."""
    assert detections_to_action([]) == (0.0, 0.0, 0.0)


def test_the_two_policies_agree_whenever_the_detector_sees_anything():
    """
    They differ on exactly one input — the empty set. Anywhere else the baseline
    is a competent avoider, which is what makes the comparison fair: Run A does
    not crash because it is a bad controller, it crashes because it was lied to.
    """
    for size in (0.2, 0.4, 0.6, 0.8):
        dets = [_obstacle(size)]
        assert naive_action(dets) == detections_to_action(dets)


def test_side_by_side_helper_shows_the_divergence():
    unprotected, protected = naive_and_protected([])
    assert unprotected == (1.0, 0.0, 0.0)
    assert protected == (0.0, 0.0, 0.0)

    same_a, same_b = naive_and_protected([_obstacle()])
    assert same_a == same_b


# ---------------------------------------------------------------------------
# The crash sequence, and why the protected path detects it more weakly
# ---------------------------------------------------------------------------


def test_run_a_patched_drone_diverges_far_enough_to_be_caught():
    """
    Run A geometry: a patched drone reports the assumed-clear action while its
    co-observing peers still see the obstacle. The divergence is large, which is
    what makes the semantic layer fire — and what makes the crash worth watching.
    """
    peer = detections_to_action([_obstacle(0.7)])
    fooled = naive_action([])
    delta = math.dist(fooled, peer)

    assert delta > THETA
    assert not outputs_agree(fooled, peer, threshold=THETA)


@pytest.mark.parametrize("size", [0.5, 0.6])
def test_protected_fallback_has_a_detection_blind_band(size):
    """
    Documents a REGRESSION, not a desired property.

    With the protected fallback the fooled drone emits (0,0,0), which is also what
    an honest peer emits at moderate threat — the avoidance action passes through
    the origin as threat rises. So around 0.5-0.6 frame occupancy the two agree and
    the patch is NOT caught.

    Root cause: (0,0,0) means both "I see nothing" and "I see a moderate threat and
    chose to slow". The semantic layer compares control outputs, and control
    outputs are a non-injective projection of what was perceived.

    When this is fixed — by attesting the perception claim rather than the control
    output — this test should start failing. That is the signal to invert it.
    """
    peer = detections_to_action([_obstacle(size)])
    fooled = detections_to_action([])

    assert math.dist(fooled, peer) < THETA
    assert outputs_agree(fooled, peer, threshold=THETA), (
        "blind band closed — invert this test and update DEMO_INVARIANTS.md"
    )


@pytest.mark.parametrize("size", [0.3, 0.4, 0.7, 0.8, 0.9])
def test_outside_the_blind_band_the_protected_path_still_catches_the_patch(size):
    """The band is bounded on both sides; small and large obstacles are caught."""
    peer = detections_to_action([_obstacle(size)])
    fooled = detections_to_action([])

    assert not outputs_agree(fooled, peer, threshold=THETA)


def test_baseline_cannot_reach_flight_control():
    """
    Structural guarantee: the unsafe controller has no import path to the node
    runtime, so it cannot be wired to an actuator by accident.
    """
    import pathlib

    source = pathlib.Path(
        __file__
    ).resolve().parent.parent / "perception" / "baseline_controller.py"
    text = source.read_text(encoding="utf-8")

    assert "import node" not in text
    assert "from node" not in text
    assert "safety_supervisor" not in text
