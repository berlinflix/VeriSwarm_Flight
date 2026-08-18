"""
Semantically-unverified accepts, and the visibility of the co-visibility gate.

The gate abstains rather than disputes when two drones did not share a view.
That is deliberate and it is what keeps the false-positive rate low (Table 4.9),
but it has a sharp edge: if *no* peer is co-visible, every peer ACKs on
cryptographic and provenance evidence alone and the receipt reaches ACCEPTED
without anyone having checked what the originator claims to have seen. An
adversarial patch is invisible to both layers that did vote.

These tests pin the three things that make that case survivable:
  * the tally reports how many peers actually exercised the semantic layer,
  * an accept nobody cross-checked degrades instead of executing at full trust,
  * the gate reports the numbers behind its decision, so a dead semantic layer
    is readable rather than showing up as an unexplained all-green console.
"""

from __future__ import annotations

import pytest

from node import common
from perception.claim import PerceptionClaim
from protocol.geometry import Pose
from protocol.peer_consensus import (
    REASON_NO_COVISIBILITY,
    REASON_NO_OBSERVATION,
    REASON_OK,
    ConsensusEngine,
    ConsensusOutcome,
    PeerVerifier,
    SafeAction,
    Vote,
    VoteVerifier,
    fallback_action,
)
from protocol.receipts import ReceiptSigner, ReceiptVerifier, build_receipt, sha256_hex

APPROVED = sha256_hex(b"yolov8n-weights-v1")

# Alpha is fooled by a patch and reports a clear path; the world contains an
# obstacle, so an honest co-observer would command a climb.
PATCHED_ACTION = (1.0, 0.0, 0.0)
TRUE_ACTION = (0.0, 0.0, 1.0)
EMPTY_CLAIM = PerceptionClaim.from_detections([])
OBJECT_CLAIM = PerceptionClaim(
    measured=True,
    detections_present=True,
    detection_count=1,
    class_ids=(5,),
    occupancy=0.36,
    max_confidence=0.9,
)


def _swarm(ids):
    signers = {d: ReceiptSigner() for d in ids}
    peer_keys = {d: s.public_key_hex for d, s in signers.items()}
    return (
        signers,
        ReceiptVerifier(peer_keys=peer_keys, approved_models={APPROVED}),
        VoteVerifier(peer_keys=peer_keys),
    )


def _receipt(signers, action=PATCHED_ACTION):
    claim = OBJECT_CLAIM if action == TRUE_ACTION else EMPTY_CLAIM
    receipt = build_receipt(
        drone_id="alpha",
        input_bytes=b"frame",
        model_hash=APPROVED,
        output=action,
        perception=claim,
    )
    return receipt, signers["alpha"].sign(receipt)


# ---------------------------------------------------------------------------
# The tally distinguishes a cross-checked ACK from an abstention
# ---------------------------------------------------------------------------


def test_far_peers_ack_but_do_not_count_as_semantic_verification():
    """
    The exact failure this guards: a patched drone claims 'clear ahead', no peer
    shares its view, and the swarm ACCEPTS. The accept is legitimate on the
    evidence available — but `semantic_ack_count` is 0, which is what tells the
    caller nobody checked the claim.
    """
    ids = ["alpha", "bravo", "charlie"]
    signers, receipt_verifier, vote_verifier = _swarm(ids)
    receipt, signed = _receipt(signers)

    alpha_pose = Pose(0.0, 0.0, 14.0)
    far = {"bravo": Pose(200.0, 0.0, 14.0), "charlie": Pose(-200.0, 0.0, 14.0)}

    votes = []
    for peer, pose in far.items():
        verifier = PeerVerifier(peer, signers[peer], receipt_verifier, o_min=0.1)
        votes.append(verifier.vote_on(
            signed, my_observation=TRUE_ACTION,
            my_pose=pose, originator_pose=alpha_pose,
            my_claim=OBJECT_CLAIM,
        ))

    assert all(v.vote.decision is Vote.ACK for v in votes)
    assert all(v.vote.reason == REASON_NO_COVISIBILITY for v in votes)

    result = ConsensusEngine(num_peers=2).tally(
        receipt, votes, vote_verifier, expected_voters=set(ids)
    )

    assert result.outcome is ConsensusOutcome.ACCEPTED
    assert result.ack_count == 2
    assert result.semantic_ack_count == 0, "abstentions are not verification"


def test_covisible_peers_produce_semantic_acks():
    """The same swarm, in a formation that actually overlaps."""
    ids = ["alpha", "bravo", "charlie"]
    signers, receipt_verifier, vote_verifier = _swarm(ids)
    receipt, signed = _receipt(signers, action=TRUE_ACTION)

    alpha_pose = Pose(0.0, 0.0, 14.0)
    near = {"bravo": Pose(3.0, 0.0, 14.0), "charlie": Pose(-3.0, 0.0, 14.0)}

    votes = [
        PeerVerifier(p, signers[p], receipt_verifier, o_min=0.1).vote_on(
            signed,
            my_observation=TRUE_ACTION,
            my_pose=pose,
            originator_pose=alpha_pose,
            my_claim=OBJECT_CLAIM,
        )
        for p, pose in near.items()
    ]

    assert all(v.vote.reason == REASON_OK for v in votes)

    result = ConsensusEngine(num_peers=2).tally(
        receipt, votes, vote_verifier, expected_voters=set(ids)
    )
    assert result.outcome is ConsensusOutcome.ACCEPTED
    assert result.semantic_ack_count == 2


def test_peer_without_perception_is_not_semantic_verification():
    """A peer with no observation ACKs on crypto alone and says so."""
    ids = ["alpha", "bravo"]
    signers, receipt_verifier, _ = _swarm(ids)
    _, signed = _receipt(signers)

    vote = PeerVerifier("bravo", signers["bravo"], receipt_verifier).vote_on(signed)

    assert vote.vote.decision is Vote.ACK
    assert vote.vote.reason == REASON_NO_OBSERVATION


def test_one_covisible_peer_still_catches_the_patch():
    """A single genuine co-observer is enough to reject at N=3 (T_rej = 1)."""
    ids = ["alpha", "bravo", "charlie"]
    signers, receipt_verifier, vote_verifier = _swarm(ids)
    receipt, signed = _receipt(signers)

    alpha_pose = Pose(0.0, 0.0, 14.0)
    votes = [
        PeerVerifier("bravo", signers["bravo"], receipt_verifier, o_min=0.1).vote_on(
            signed, my_observation=TRUE_ACTION,
            my_pose=Pose(3.0, 0.0, 14.0), originator_pose=alpha_pose,
            my_claim=OBJECT_CLAIM,
        ),
        PeerVerifier("charlie", signers["charlie"], receipt_verifier, o_min=0.1).vote_on(
            signed, my_observation=TRUE_ACTION,
            my_pose=Pose(200.0, 0.0, 14.0), originator_pose=alpha_pose,
            my_claim=OBJECT_CLAIM,
        ),
    ]

    result = ConsensusEngine(num_peers=2).tally(
        receipt, votes, vote_verifier, expected_voters=set(ids)
    )
    assert result.outcome is ConsensusOutcome.REJECTED


# ---------------------------------------------------------------------------
# An unverified accept degrades rather than executing at full authority
# ---------------------------------------------------------------------------


def test_unverified_accept_holds():
    assert fallback_action(ConsensusOutcome.ACCEPTED, semantic_ack_count=0) is (
        SafeAction.DEFER
    )


def test_verified_accept_executes():
    assert fallback_action(ConsensusOutcome.ACCEPTED, semantic_ack_count=2) is (
        SafeAction.EXECUTE
    )


def test_omitted_semantic_count_fails_closed():
    assert fallback_action(ConsensusOutcome.ACCEPTED) is SafeAction.DEFER
    assert fallback_action(ConsensusOutcome.REJECTED) is SafeAction.SAFE_FALLBACK
    assert fallback_action(ConsensusOutcome.NO_QUORUM) is SafeAction.DEFER


def test_rejection_is_never_degraded_into_execution():
    assert fallback_action(ConsensusOutcome.REJECTED, semantic_ack_count=0) is (
        SafeAction.SAFE_FALLBACK
    )


# ---------------------------------------------------------------------------
# The gate reports its own reasoning
# ---------------------------------------------------------------------------


def test_covis_diagnostic_reports_the_measured_overlap():
    ids = ["alpha", "bravo"]
    signers, receipt_verifier, _ = _swarm(ids)
    _, signed = _receipt(signers)

    seen = []
    verifier = PeerVerifier(
        "bravo", signers["bravo"], receipt_verifier, o_min=0.1, on_covis=seen.append
    )
    verifier.vote_on(
        signed, my_observation=TRUE_ACTION,
        my_pose=Pose(200.0, 0.0, 14.0), originator_pose=Pose(0.0, 0.0, 14.0),
        my_claim=OBJECT_CLAIM,
    )

    assert len(seen) == 1
    diag = seen[0]
    assert diag.covisible is False
    assert diag.method == "none"      # no frames supplied, so no image fallback
    assert diag.iou == pytest.approx(0.0)
    assert "NOT co-visible" in diag.describe()
    assert verifier.last_covis is diag


def test_covis_diagnostic_reports_a_geometric_pass():
    ids = ["alpha", "bravo"]
    signers, receipt_verifier, _ = _swarm(ids)
    _, signed = _receipt(signers, action=TRUE_ACTION)

    verifier = PeerVerifier("bravo", signers["bravo"], receipt_verifier, o_min=0.1)
    verifier.vote_on(
        signed, my_observation=TRUE_ACTION,
        my_pose=Pose(3.0, 0.0, 14.0), originator_pose=Pose(0.0, 0.0, 14.0),
        my_claim=OBJECT_CLAIM,
    )

    diag = verifier.last_covis
    assert diag.covisible and diag.method == "geometric"
    assert diag.iou >= 0.1


# ---------------------------------------------------------------------------
# Preflight: a formation with a dead semantic layer must not launch quietly
# ---------------------------------------------------------------------------


def test_preflight_rejects_a_formation_with_no_covisible_peer():
    poses = {
        "alpha": (0.0, 0.0, 14.0, 0.0),
        "bravo": (300.0, 0.0, 14.0, 0.0),
        "charlie": (-300.0, 0.0, 14.0, 0.0),
    }
    manifest = common.generate_manifest(list(poses), poses=poses)

    with pytest.raises(RuntimeError, match="no peer is co-visible"):
        common.assert_covisible_formation(manifest, "alpha")


def test_preflight_passes_on_the_measured_flight_formation():
    """3 m spacing at 14 m — the geometry the transfer study was run at."""
    poses = {
        "alpha": (0.0, 0.0, 14.0, 0.0),
        "bravo": (0.0, 3.0, 14.0, 0.0),
        "charlie": (0.0, -3.0, 14.0, 0.0),
    }
    manifest = common.generate_manifest(list(poses), poses=poses)

    rows = common.assert_covisible_formation(manifest, "alpha")
    alpha_rows = [r for r in rows if "alpha" in (r["i"], r["j"])]

    assert all(r["co_visible"] for r in alpha_rows)
    # 3 m at 14 m subtends ~12 deg — the disparity Section 4.3 measured against.
    assert all(10.0 <= r["angle_deg"] <= 14.0 for r in alpha_rows)
