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
from perception.claim import PerceptionClaim, claims_agree
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
def test_action_space_alone_still_has_the_blind_band(size):
    """
    The defect that motivated `PerceptionClaim`, kept as a standing witness.

    Comparing control outputs, a fooled drone emits (0,0,0) and an honest peer
    facing a moderate obstacle emits something very near it — the avoidance
    action passes through the origin as threat rises. At 0.5-0.6 frame occupancy
    they agree and the patch is NOT caught.

    This is not fixable by tuning theta. `(0,0,0)` genuinely means both "I see
    nothing" and "I see a moderate threat and chose to slow", so the information
    needed to separate them was destroyed before the comparison ran. The fix was
    to stop comparing decisions and start comparing observations — see the test
    below, which covers the same geometry through `claims_agree`.
    """
    peer = detections_to_action([_obstacle(size)])
    fooled = detections_to_action([])

    assert math.dist(fooled, peer) < THETA
    assert outputs_agree(fooled, peer, threshold=THETA)


@pytest.mark.parametrize("size", [0.3, 0.4, 0.5, 0.6, 0.7, 0.9])
def test_perception_claims_close_the_blind_band(size):
    """
    The fix, over the whole range including the band the action comparison
    misses. `detections_present` differs, so the dispute does not depend on
    obstacle size, range, or controller gains at all.
    """
    peer_claim = PerceptionClaim.from_detections([_obstacle(size)])
    fooled_claim = PerceptionClaim.from_detections([])

    assert claims_agree(fooled_claim, peer_claim) is False


def test_claims_do_not_dispute_honest_peers_that_agree():
    """The band must not close by disputing everything."""
    dets = [_obstacle(0.6)]
    assert claims_agree(
        PerceptionClaim.from_detections(dets),
        PerceptionClaim.from_detections(dets),
    ) is True


def test_unmeasured_claim_draws_no_conclusion():
    """
    Absence of evidence is not agreement. Collapsing None to True here would
    reintroduce the original blind band one layer up.
    """
    assert claims_agree(
        PerceptionClaim.unmeasured(),
        PerceptionClaim.from_detections([_obstacle(0.6)]),
    ) is None


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
