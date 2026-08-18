"""
The angular-diversity gate: overlap alone does not make a peer a verifier.

A peer flying beside the originator sees the same pixels. Its footprint overlaps
almost perfectly, so the geometric gate certifies it as a co-observer — and its
vote carries no information the originator's own inference did not already
contain, because whatever adversarial patch fooled one camera fools the other
from the same angle. Counting that ACK as verification is worse than having no
peer at all: the swarm believes it cross-checked something it did not.

`phi_min` closes that. It defaults to 0 (off), which these tests also pin: the
published measurements were flown at 3 m / 14 m, where parallax is ~12 degrees,
so switching the gate on by default would retroactively disable the semantic
layer for every one of them.
"""

from __future__ import annotations

import math

import pytest

from node import common
from perception.claim import PerceptionClaim
from protocol.geometry import Pose
from protocol.peer_consensus import (
    REASON_NO_COVISIBILITY,
    REASON_OK,
    PeerVerifier,
    Vote,
)
from protocol.receipts import ReceiptSigner, ReceiptVerifier, build_receipt, sha256_hex

APPROVED = sha256_hex(b"yolov8n-weights-v1")
ACTION = (0.1, 0.0, 0.0)
EMPTY_CLAIM = PerceptionClaim.from_detections([])

#: Section 4.3 measured patch suppression falling away between 12 and 23 degrees.
PHI_MIN = 23.0


def _pair():
    signers = {d: ReceiptSigner() for d in ("alpha", "bravo")}
    peer_keys = {d: s.public_key_hex for d, s in signers.items()}
    verifier = ReceiptVerifier(peer_keys=peer_keys, approved_models={APPROVED})
    receipt = build_receipt(
        drone_id="alpha",
        input_bytes=b"frame",
        model_hash=APPROVED,
        output=ACTION,
        perception=EMPTY_CLAIM,
    )
    return signers, verifier, signers["alpha"].sign(receipt)


def _vote(bravo_pose, phi_min, alpha_pose=Pose(0.0, 0.0, 14.0)):
    signers, verifier, signed = _pair()
    peer = PeerVerifier(
        "bravo", signers["bravo"], verifier, o_min=0.1, phi_min=phi_min
    )
    vote = peer.vote_on(
        signed,
        my_observation=ACTION,
        my_pose=bravo_pose,
        originator_pose=alpha_pose,
        my_claim=EMPTY_CLAIM,
    )
    return vote, peer.last_covis


# ---------------------------------------------------------------------------
# The gate off by default
# ---------------------------------------------------------------------------


def test_gate_is_off_by_default():
    """
    Enabling this silently would switch off the semantic layer for the 3 m / 14 m
    formation behind every published result. It is mission policy, opt-in.
    """
    peer = PeerVerifier("bravo", ReceiptSigner(), ReceiptVerifier({}, set()))
    assert peer._phi_min == 0.0


def test_published_formation_still_verifies_with_the_gate_off():
    """3 m at 14 m is ~12 degrees — below phi_min, but the gate is not engaged."""
    vote, diag = _vote(Pose(0.0, 3.0, 14.0), phi_min=0.0)
    assert vote.vote.decision is Vote.ACK
    assert vote.vote.reason == REASON_OK
    assert diag.covisible and diag.method == "geometric"


# ---------------------------------------------------------------------------
# The gate on
# ---------------------------------------------------------------------------


def test_redundant_wingtip_peer_abstains():
    """
    The case the gate exists for: near-total overlap, near-zero independence.
    The peer must abstain rather than cast a vote that proves nothing.
    """
    vote, diag = _vote(Pose(0.0, 0.3, 14.0), phi_min=PHI_MIN)

    assert vote.vote.decision is Vote.ACK
    assert vote.vote.reason == REASON_NO_COVISIBILITY
    assert diag.covisible is False
    assert diag.method == "low_parallax"
    assert diag.iou > 0.9, "it genuinely does overlap — that is the point"
    assert diag.parallax_deg < PHI_MIN
    assert "redundant viewpoint" in diag.describe()


def test_well_separated_peer_verifies():
    """6 m at 14 m is ~23 degrees — the disparity Section 4.3 measured to."""
    vote, diag = _vote(Pose(0.0, 6.5, 14.0), phi_min=PHI_MIN)

    assert vote.vote.reason == REASON_OK
    assert diag.covisible and diag.method == "geometric"
    assert diag.parallax_deg >= PHI_MIN


def test_abstention_does_not_count_as_semantic_verification():
    """A low-parallax abstention must not inflate semantic_ack_count."""
    from protocol.peer_consensus import ConsensusEngine, VoteVerifier

    signers = {d: ReceiptSigner() for d in ("alpha", "bravo", "charlie")}
    peer_keys = {d: s.public_key_hex for d, s in signers.items()}
    verifier = ReceiptVerifier(peer_keys=peer_keys, approved_models={APPROVED})
    receipt = build_receipt(
        drone_id="alpha",
        input_bytes=b"frame",
        model_hash=APPROVED,
        output=ACTION,
        perception=EMPTY_CLAIM,
    )
    signed = signers["alpha"].sign(receipt)

    alpha = Pose(0.0, 0.0, 14.0)
    votes = [
        PeerVerifier(p, signers[p], verifier, o_min=0.1, phi_min=PHI_MIN).vote_on(
            signed,
            my_observation=ACTION,
            my_pose=pose,
            originator_pose=alpha,
            my_claim=EMPTY_CLAIM,
        )
        for p, pose in (("bravo", Pose(0.0, 0.3, 14.0)),
                        ("charlie", Pose(0.0, -0.3, 14.0)))
    ]

    result = ConsensusEngine(num_peers=2).tally(
        receipt, votes, VoteVerifier(peer_keys),
        expected_voters={"alpha", "bravo", "charlie"},
    )
    assert result.ack_count == 2
    assert result.semantic_ack_count == 0


# ---------------------------------------------------------------------------
# Preflight distinguishes "too far apart" from "too close together"
# ---------------------------------------------------------------------------


def test_preflight_rejects_a_formation_that_is_too_tight():
    poses = {
        "alpha": (0.0, 0.0, 14.0, 0.0),
        "bravo": (0.0, 0.3, 14.0, 0.0),
        "charlie": (0.0, -0.3, 14.0, 0.0),
    }
    manifest = common.generate_manifest(list(poses), poses=poses, phi_min=PHI_MIN)

    with pytest.raises(RuntimeError, match="too close"):
        common.assert_covisible_formation(manifest, "alpha")


#: Ring radius, metres, for N=5 at 14 m altitude with phi_min = 23 deg.
#:
#: The formation is pinned between two opposing constraints. Peers must be far
#: enough apart to be independent (parallax >= phi_min) and close enough to still
#: share ground (IoU >= o_min), and for N=5 those bounds leave R in [5.0, 6.0].
#: 5.5 sits in the middle, giving margin on both sides: worst-pair IoU 0.14
#: against a 0.10 floor, worst-pair parallax 26 deg against a 23 deg floor.
#:
#: Note the upper bound comes from the *narrow* axis of the footprint. At 14 m
#: the ground rectangle is 19.2 x 14.0 m, so the separation at which overlap
#: decays to o_min is 15.7 m across the wide (hfov) axis but only 11.4 m across
#: the narrow one. A ring mixes directions, so the narrow axis binds — which is
#: why the naive one-axis derivation over-estimates the usable radius.
RING_RADIUS_M = 5.5
RING_ALT_M = 14.0


def _ring(ids, radius=RING_RADIUS_M, alt=RING_ALT_M):
    n = len(ids)
    return {
        nid: (radius * math.cos(2 * math.pi * i / n),
              radius * math.sin(2 * math.pi * i / n), alt, 0.0)
        for i, nid in enumerate(ids)
    }


def test_ring_formation_makes_every_peer_a_verifier():
    """All 4 peers verify alpha — k=4, so f=1 and the N=5 Byzantine claim holds."""
    ids = ["alpha", "bravo", "charlie", "delta", "echo"]
    manifest = common.generate_manifest(ids, poses=_ring(ids), phi_min=PHI_MIN)

    rows = common.assert_covisible_formation(manifest, "alpha")
    alpha_rows = [r for r in rows if "alpha" in (r["i"], r["j"])]

    assert len(alpha_rows) == 4
    assert all(r["independent"] for r in alpha_rows), (
        f"every peer should verify alpha: {alpha_rows}"
    )


def test_ring_is_mutually_verifying_for_every_member():
    """Not just alpha: the property must be symmetric across the formation."""
    ids = ["alpha", "bravo", "charlie", "delta", "echo"]
    manifest = common.generate_manifest(ids, poses=_ring(ids), phi_min=PHI_MIN)

    rows = common.covisibility_report(manifest)
    assert all(r["independent"] for r in rows), f"not fully connected: {rows}"


def test_tilted_camera_ring_is_replanned_from_actual_pitch():
    """The 5.5 m nadir result must not be reused blindly for tilted cameras."""
    ids = ["alpha", "bravo", "charlie", "delta", "echo"]
    too_small = common.generate_manifest(
        ids,
        poses=common.ring_formation(ids, radius_m=5.5, camera_pitch_deg=35.0),
        phi_min=PHI_MIN,
    )
    assert not all(r["independent"] for r in common.covisibility_report(too_small))

    replanned = common.generate_manifest(
        ids,
        poses=common.ring_formation(ids, radius_m=10.0, camera_pitch_deg=35.0),
        phi_min=PHI_MIN,
    )
    assert all(r["independent"] for r in common.covisibility_report(replanned))


@pytest.mark.parametrize("radius,expect_ok", [
    (3.0, False),   # too tight — overlaps fully, parallax only 14 deg
    (4.5, False),   # still under phi_min at 21 deg
    (5.0, True),    # lower edge of the band
    (6.0, True),    # upper edge
    (6.5, False),   # opposite pairs have fallen below o_min
])
def test_feasible_radius_band(radius, expect_ok):
    """
    Pins the two-sided band. Both failure directions are real and they need
    opposite fixes, so a formation planner that only checks one bound will
    confidently fly a geometry where cross-verification does nothing.
    """
    ids = ["alpha", "bravo", "charlie", "delta", "echo"]
    manifest = common.generate_manifest(
        ids, poses=_ring(ids, radius=radius), phi_min=PHI_MIN
    )
    rows = common.covisibility_report(manifest)
    assert all(r["independent"] for r in rows) is expect_ok
