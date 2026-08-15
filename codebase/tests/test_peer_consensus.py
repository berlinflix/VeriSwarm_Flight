"""
Security-property tests for `protocol/peer_consensus.py`.

Each test corresponds to a claim in the paper. Mapping table:

    test_honest_swarm_accepts                -> §6 Exp 1 (baseline)
    test_model_swap_rejected_by_swarm        -> §6 Exp 2 (model-swap detection) [STAR]
    test_adversarial_patch_rejected          -> §6 Exp 3 (adversarial perception) [STAR]
    test_below_quorum_returns_no_quorum      -> §6 Exp 4 (network drops)
    test_byzantine_minority_cannot_force_*   -> §3 Threat model: <1/3 Byzantine
    test_vote_signatures_verified            -> §3 Threat model: forged vote resistance
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from protocol.receipts import (  # noqa: E402
    ReceiptSigner,
    ReceiptVerifier,
    SignedReceipt,
    build_receipt,
    sha256_hex,
)
from protocol.peer_consensus import (  # noqa: E402
    ConsensusEngine,
    ConsensusOutcome,
    PeerVerifier,
    ReputationStore,
    SignedVote,
    Vote,
    VoteVerifier,
    outputs_agree,
    receipt_digest,
)
from protocol.geometry import Pose  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


APPROVED_MODEL_HASH = sha256_hex(b"yolov8n-weights-v1")
MALICIOUS_MODEL_HASH = sha256_hex(b"yolov8n-backdoored")


def _build_swarm(drone_ids: list[str]):
    """Return (signers_by_id, peer_keys, receipt_verifier, vote_verifier)."""
    signers = {did: ReceiptSigner() for did in drone_ids}
    peer_keys = {did: s.public_key_hex for did, s in signers.items()}
    receipt_verifier = ReceiptVerifier(
        peer_keys=peer_keys, approved_models={APPROVED_MODEL_HASH}
    )
    vote_verifier = VoteVerifier(peer_keys=peer_keys)
    return signers, peer_keys, receipt_verifier, vote_verifier


def _alpha_receipt(signer: ReceiptSigner, *, output=(0.1, -0.3, 0.5), model_hash=None):
    receipt = build_receipt(
        drone_id="alpha",
        input_bytes=b"\x00" * 128,
        model_hash=model_hash or APPROVED_MODEL_HASH,
        output=output,
    )
    return signer.sign(receipt)


# ---------------------------------------------------------------------------
# Threshold arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "k, expected_ack, expected_dispute",
    [
        (2, 2, 1),  # 3-drone swarm: must be unanimous to accept
        (4, 3, 2),  # 5-drone swarm
        (6, 5, 3),  # 7-drone swarm
        (3, 3, 2),  # 4-drone swarm
        (10, 7, 4),  # 11-drone swarm
    ],
)
def test_thresholds(k, expected_ack, expected_dispute):
    """Thresholds match the >2/3 / >1/3 PBFT-style rule."""
    eng = ConsensusEngine(num_peers=k)
    assert eng.ack_threshold == expected_ack
    assert eng.dispute_threshold == expected_dispute
    # Internal consistency: cannot simultaneously ACCEPT and REJECT.
    assert eng.ack_threshold + eng.dispute_threshold > k


def test_rejects_zero_peers():
    with pytest.raises(ValueError):
        ConsensusEngine(num_peers=0)


# ---------------------------------------------------------------------------
# Phase 2 (peer verifier) — vote_on() decision rules
# ---------------------------------------------------------------------------


def test_peer_acks_honest_receipt():
    signers, _, rv, _ = _build_swarm(["alpha", "bravo"])
    pv = PeerVerifier(
        my_drone_id="bravo", signer=signers["bravo"], receipt_verifier=rv
    )

    signed = _alpha_receipt(signers["alpha"], output=(0.1, 0.0, 0.0))
    vote = pv.vote_on(signed, my_observation=(0.12, -0.01, 0.0))

    assert vote.vote.decision is Vote.ACK
    assert vote.vote.voter_id == "bravo"
    assert vote.vote.target_receipt_hash == receipt_digest(signed.receipt)


def test_peer_disputes_bad_signature():
    signers, _, rv, _ = _build_swarm(["alpha", "bravo"])
    pv = PeerVerifier("bravo", signers["bravo"], rv)

    signed = _alpha_receipt(signers["alpha"])
    # Tamper after signing: flip a hex digit in the signature.
    forged = SignedVote  # noqa  -- just a marker so reader sees the trick below
    tampered_sig = ("0" * 128) if signed.signature[0] != "0" else ("f" * 128)
    tampered = SignedReceipt(receipt=signed.receipt, signature=tampered_sig)

    vote = pv.vote_on(tampered)
    assert vote.vote.decision is Vote.DISPUTE
    assert vote.vote.reason == "bad_signature"


def test_peer_disputes_unapproved_model():
    signers, _, rv, _ = _build_swarm(["alpha", "bravo"])
    pv = PeerVerifier("bravo", signers["bravo"], rv)

    signed = _alpha_receipt(signers["alpha"], model_hash=MALICIOUS_MODEL_HASH)
    vote = pv.vote_on(signed)
    assert vote.vote.decision is Vote.DISPUTE
    assert vote.vote.reason.startswith("model_hash_not_approved")


def test_peer_disputes_semantic_disagreement():
    """Adversarial-patch scenario: Alpha says forward, Bravo sees obstacle."""
    signers, _, rv, _ = _build_swarm(["alpha", "bravo"])
    pv = PeerVerifier("bravo", signers["bravo"], rv, agreement_threshold=0.5)

    signed = _alpha_receipt(signers["alpha"], output=(1.0, 0.0, 0.0))
    vote = pv.vote_on(signed, my_observation=(-1.0, 0.5, 0.0))
    assert vote.vote.decision is Vote.DISPUTE
    assert vote.vote.reason == "semantic_disagreement"


def test_peer_acks_when_no_observation_available():
    """If Bravo can't see what Alpha saw, it falls back to crypto-only ACK."""
    signers, _, rv, _ = _build_swarm(["alpha", "bravo"])
    pv = PeerVerifier("bravo", signers["bravo"], rv)
    signed = _alpha_receipt(signers["alpha"])
    vote = pv.vote_on(signed, my_observation=None)
    assert vote.vote.decision is Vote.ACK


# ---------------------------------------------------------------------------
# Phase 3 (consensus engine) — paper experiments
# ---------------------------------------------------------------------------


def test_honest_swarm_accepts():
    """§6 Experiment 1: 3 honest drones, normal mission -> ACCEPT."""
    signers, _, rv, vv = _build_swarm(["alpha", "bravo", "charlie"])
    eng = ConsensusEngine(num_peers=2)

    signed = _alpha_receipt(signers["alpha"], output=(0.1, 0.0, 0.0))
    votes = [
        PeerVerifier("bravo", signers["bravo"], rv).vote_on(
            signed, my_observation=(0.11, 0.0, 0.0)
        ),
        PeerVerifier("charlie", signers["charlie"], rv).vote_on(
            signed, my_observation=(0.09, 0.01, 0.0)
        ),
    ]

    result = eng.tally(signed.receipt, votes, vv,
                       expected_voters={"alpha", "bravo", "charlie"})
    assert result.outcome is ConsensusOutcome.ACCEPTED
    assert result.ack_count == 2
    assert result.dispute_count == 0


def test_model_swap_rejected_by_swarm():
    """§6 Experiment 2 [STAR]: Alpha runs a malicious model -> REJECT."""
    signers, _, rv, vv = _build_swarm(["alpha", "bravo", "charlie"])
    eng = ConsensusEngine(num_peers=2)

    signed = _alpha_receipt(signers["alpha"], model_hash=MALICIOUS_MODEL_HASH)
    votes = [
        PeerVerifier("bravo", signers["bravo"], rv).vote_on(signed),
        PeerVerifier("charlie", signers["charlie"], rv).vote_on(signed),
    ]

    result = eng.tally(signed.receipt, votes, vv,
                       expected_voters={"alpha", "bravo", "charlie"})
    assert result.outcome is ConsensusOutcome.REJECTED
    assert result.dispute_count == 2


def test_adversarial_patch_rejected():
    """§6 Experiment 3 [STAR]: Alpha is fooled by a patch, peers disagree."""
    signers, _, rv, vv = _build_swarm(["alpha", "bravo", "charlie"])
    eng = ConsensusEngine(num_peers=2)

    # Alpha sees patch -> outputs 'descend, clear'
    signed = _alpha_receipt(signers["alpha"], output=(0.0, 0.0, -1.0))
    # Bravo and Charlie see the real obstacle and want to climb/avoid.
    votes = [
        PeerVerifier("bravo", signers["bravo"], rv).vote_on(
            signed, my_observation=(0.0, 0.0, 1.0)
        ),
        PeerVerifier("charlie", signers["charlie"], rv).vote_on(
            signed, my_observation=(0.0, 0.0, 0.9)
        ),
    ]
    result = eng.tally(signed.receipt, votes, vv,
                       expected_voters={"alpha", "bravo", "charlie"})

    assert result.outcome is ConsensusOutcome.REJECTED
    assert all(v.vote.reason == "semantic_disagreement" for v in votes)


def test_below_quorum_returns_no_quorum():
    """§6 Experiment 4: network drops -> NO_QUORUM, neither accept nor reject."""
    signers, _, rv, vv = _build_swarm(["alpha", "bravo", "charlie", "delta", "echo"])
    eng = ConsensusEngine(num_peers=4)  # 5-drone swarm, k=4

    signed = _alpha_receipt(signers["alpha"], output=(0.1, 0.0, 0.0))
    # Only ONE vote arrives — below both thresholds (ack=3, dispute=2).
    votes = [
        PeerVerifier("bravo", signers["bravo"], rv).vote_on(signed,
                                                            my_observation=(0.1, 0, 0)),
    ]
    result = eng.tally(signed.receipt, votes, vv,
                       expected_voters={"alpha", "bravo", "charlie", "delta", "echo"})
    assert result.outcome is ConsensusOutcome.NO_QUORUM
    assert result.ack_count == 1
    assert result.missing_count == 3


def test_byzantine_minority_cannot_force_acceptance():
    """
    §3 Threat model: in a 5-drone swarm with 1 Byzantine drone falsely
    ACKing a malicious receipt, the consensus still REJECTs because honest
    peers outnumber the attacker.
    """
    signers, _, rv, vv = _build_swarm(
        ["alpha", "bravo", "charlie", "delta", "echo"]
    )
    eng = ConsensusEngine(num_peers=4)  # k=4, ack=3, dispute=2

    # Alpha runs a malicious model.
    signed = _alpha_receipt(signers["alpha"], model_hash=MALICIOUS_MODEL_HASH)

    # 1 Byzantine drone (echo) ACKs falsely; 3 honest drones DISPUTE.
    byzantine_signer = signers["echo"]
    byzantine_pv = PeerVerifier(
        my_drone_id="echo",
        signer=byzantine_signer,
        # Echo's local view says malicious model is fine (it's compromised).
        receipt_verifier=ReceiptVerifier(
            peer_keys={"alpha": signers["alpha"].public_key_hex},
            approved_models={MALICIOUS_MODEL_HASH, APPROVED_MODEL_HASH},
        ),
    )

    votes = [
        PeerVerifier("bravo", signers["bravo"], rv).vote_on(signed),
        PeerVerifier("charlie", signers["charlie"], rv).vote_on(signed),
        PeerVerifier("delta", signers["delta"], rv).vote_on(signed),
        byzantine_pv.vote_on(signed),
    ]

    result = eng.tally(signed.receipt, votes, vv,
                       expected_voters={"alpha", "bravo", "charlie",
                                        "delta", "echo"})
    assert result.outcome is ConsensusOutcome.REJECTED
    assert result.dispute_count == 3
    assert result.ack_count == 1


def test_originator_self_vote_ignored():
    """A drone cannot ACK its own receipt to push it over the threshold."""
    signers, _, rv, vv = _build_swarm(["alpha", "bravo", "charlie"])
    eng = ConsensusEngine(num_peers=2)

    signed = _alpha_receipt(signers["alpha"], output=(0.0, 0.0, 0.0))

    # Alpha tries to vote ACK on its own receipt.
    alpha_pv = PeerVerifier("alpha", signers["alpha"], rv)
    self_vote = alpha_pv.vote_on(signed)

    # Only one real peer votes.
    bravo_vote = PeerVerifier("bravo", signers["bravo"], rv).vote_on(
        signed, my_observation=(0.0, 0.0, 0.0)
    )

    # With Alpha's self-vote dropped, only 1 ACK -- below the threshold of 2.
    result = eng.tally(
        signed.receipt, [self_vote, bravo_vote], vv,
        expected_voters={"alpha", "bravo", "charlie"},
    )
    assert result.outcome is ConsensusOutcome.NO_QUORUM


def test_duplicate_votes_collapsed():
    """A peer that retransmits is counted at most once."""
    signers, _, rv, vv = _build_swarm(["alpha", "bravo", "charlie"])
    eng = ConsensusEngine(num_peers=2)

    signed = _alpha_receipt(signers["alpha"], output=(0.0, 0.0, 0.0))
    bravo_pv = PeerVerifier("bravo", signers["bravo"], rv)
    v1 = bravo_pv.vote_on(signed, my_observation=(0.0, 0.0, 0.0))
    v2 = bravo_pv.vote_on(signed, my_observation=(0.0, 0.0, 0.0))

    # Both ACKs from the same voter -> counted as one.
    result = eng.tally(
        signed.receipt, [v1, v2], vv,
        expected_voters={"alpha", "bravo", "charlie"},
    )
    assert result.ack_count == 1
    # Below threshold of 2 -> NO_QUORUM.
    assert result.outcome is ConsensusOutcome.NO_QUORUM


def test_forged_vote_signature_dropped():
    """A vote whose signature doesn't verify is silently dropped from the tally."""
    signers, _, rv, vv = _build_swarm(["alpha", "bravo", "charlie"])
    eng = ConsensusEngine(num_peers=2)

    signed = _alpha_receipt(signers["alpha"], output=(0.0, 0.0, 0.0))
    bravo_real = PeerVerifier("bravo", signers["bravo"], rv).vote_on(
        signed, my_observation=(0.0, 0.0, 0.0)
    )

    # Build a forged "charlie" vote not signed by the real charlie key.
    rogue_signer = ReceiptSigner()
    rogue_pv = PeerVerifier(
        my_drone_id="charlie",  # claims to be charlie
        signer=rogue_signer,    # but signs with rogue key
        receipt_verifier=rv,
    )
    forged_vote = rogue_pv.vote_on(signed, my_observation=(0.0, 0.0, 0.0))

    result = eng.tally(
        signed.receipt, [bravo_real, forged_vote], vv,
        expected_voters={"alpha", "bravo", "charlie"},
    )
    # Forged Charlie vote dropped, only Bravo's ACK counted -> below quorum.
    assert result.ack_count == 1
    assert result.outcome is ConsensusOutcome.NO_QUORUM


def test_vote_against_wrong_target_ignored():
    """A vote whose target_receipt_hash mismatches is ignored."""
    signers, _, rv, vv = _build_swarm(["alpha", "bravo", "charlie"])
    eng = ConsensusEngine(num_peers=2)

    receipt_one = _alpha_receipt(signers["alpha"], output=(0.0, 0.0, 0.0))
    receipt_two = _alpha_receipt(signers["alpha"], output=(1.0, 0.0, 0.0))

    # Bravo votes on receipt_two but we tally for receipt_one.
    bravo_vote_on_two = PeerVerifier("bravo", signers["bravo"], rv).vote_on(
        receipt_two, my_observation=(1.0, 0.0, 0.0)
    )
    charlie_vote_on_one = PeerVerifier("charlie", signers["charlie"], rv).vote_on(
        receipt_one, my_observation=(0.0, 0.0, 0.0)
    )

    result = eng.tally(
        receipt_one.receipt,
        [bravo_vote_on_two, charlie_vote_on_one],
        vv,
        expected_voters={"alpha", "bravo", "charlie"},
    )
    # Bravo's vote-on-receipt-two doesn't count for receipt_one.
    assert result.ack_count == 1
    assert result.outcome is ConsensusOutcome.NO_QUORUM


def test_outputs_agree_distance_threshold():
    """L2 agreement check behaves as specified."""
    assert outputs_agree((0.0, 0.0), (0.1, 0.1), threshold=0.5)
    assert not outputs_agree((0.0, 0.0), (1.0, 1.0), threshold=0.5)
    # Mismatched dimensions -> disagreement.
    assert not outputs_agree((0.0,), (0.0, 0.0))


def test_signedvote_serialization_roundtrip():
    signers, _, rv, _ = _build_swarm(["alpha", "bravo"])
    pv = PeerVerifier("bravo", signers["bravo"], rv)
    signed = _alpha_receipt(signers["alpha"])
    sv = pv.vote_on(signed)

    wire = sv.serialize()
    restored = SignedVote.deserialize(wire)
    assert restored == sv


# ---------------------------------------------------------------------------
# C1 — Reputation-weighted consensus (§3.5, Table 4.12 ablation)
# ---------------------------------------------------------------------------


def test_reputation_default_weight_is_one():
    """An unseen drone starts at full trust."""
    rep = ReputationStore()
    assert rep.weight("alpha") == 1.0
    assert rep.total(["alpha", "bravo", "charlie"]) == 3.0


def test_reputation_update_rewards_agreement_penalizes_dissent():
    """On ACCEPT, ACK voters gain alpha; DISPUTE voters lose beta."""
    rep = ReputationStore(alpha=0.05, beta=0.2)
    rep.update(ack_ids={"bravo"}, dispute_ids={"echo"},
               outcome=ConsensusOutcome.ACCEPTED)
    assert rep.weight("bravo") == pytest.approx(1.0)  # already at the ceiling
    assert rep.weight("echo") == pytest.approx(0.8)    # dissented against ACCEPT


def test_reputation_update_symmetric_on_reject():
    """On REJECT, the DISPUTE side is the one that 'agreed' with the outcome."""
    rep = ReputationStore(alpha=0.05, beta=0.2)
    # Drop bravo below the ceiling first so its reward is observable.
    rep.update(ack_ids=set(), dispute_ids={"bravo"},
               outcome=ConsensusOutcome.ACCEPTED)
    assert rep.weight("bravo") == pytest.approx(0.8)
    # Now a REJECT outcome: the disputers were right, the acker was wrong.
    rep.update(ack_ids={"echo"}, dispute_ids={"bravo"},
               outcome=ConsensusOutcome.REJECTED)
    assert rep.weight("bravo") == pytest.approx(0.85)  # rewarded
    assert rep.weight("echo") == pytest.approx(0.8)    # penalized


def test_reputation_clipped_to_bounds():
    """Weight saturates at 1.0 and floors at r_min."""
    rep = ReputationStore(alpha=0.05, beta=0.2, r_min=0.1)
    # Reward many times -> capped at 1.0.
    for _ in range(50):
        rep.update(ack_ids={"bravo"}, dispute_ids=set(),
                   outcome=ConsensusOutcome.ACCEPTED)
    assert rep.weight("bravo") == pytest.approx(1.0)
    # Penalize many times -> floored at r_min, never zero or negative.
    for _ in range(50):
        rep.update(ack_ids=set(), dispute_ids={"echo"},
                   outcome=ConsensusOutcome.ACCEPTED)
    assert rep.weight("echo") == pytest.approx(0.1)


def test_reputation_no_quorum_leaves_weights_unchanged():
    """A NO_QUORUM round carries no signal, so no weights move."""
    rep = ReputationStore()
    rep.update(ack_ids={"bravo"}, dispute_ids={"echo"},
               outcome=ConsensusOutcome.NO_QUORUM)
    assert rep.weight("bravo") == 1.0
    assert rep.weight("echo") == 1.0


def test_uniform_reputation_matches_plain_accept():
    """With all weights at 1.0, a clear ACK majority still ACCEPTs."""
    signers, _, rv, vv = _build_swarm(["alpha", "bravo", "charlie"])
    eng = ConsensusEngine(num_peers=2)
    rep = ReputationStore()  # all weights default to 1.0

    signed = _alpha_receipt(signers["alpha"], output=(0.1, 0.0, 0.0))
    votes = [
        PeerVerifier("bravo", signers["bravo"], rv).vote_on(
            signed, my_observation=(0.1, 0.0, 0.0)),
        PeerVerifier("charlie", signers["charlie"], rv).vote_on(
            signed, my_observation=(0.1, 0.0, 0.0)),
    ]
    result = eng.tally(signed.receipt, votes, vv,
                       expected_voters={"alpha", "bravo", "charlie"},
                       reputation=rep)
    assert result.outcome is ConsensusOutcome.ACCEPTED
    # The tally now exposes who voted which way, for the post-round update.
    assert result.ack_voter_ids == frozenset({"bravo", "charlie"})


def test_reputation_blocks_low_trust_ack_majority():
    """
    Table 4.12 / colluding-voter defence: a bare integer ACK majority made up
    of drones with decayed reputation cannot push a receipt to ACCEPTED when a
    high-reputation peer disputes. The weighted ACK mass falls below 2/3.
    """
    swarm = ["alpha", "bravo", "charlie", "delta", "echo"]
    signers, _, rv, vv = _build_swarm(swarm)
    eng = ConsensusEngine(num_peers=4)  # k=4, ack=3, dispute=2

    signed = _alpha_receipt(signers["alpha"], output=(0.1, 0.0, 0.0))

    # 3 colluding low-trust ACKs vs 1 honest high-trust DISPUTE.
    rep = ReputationStore()
    for liar in ("bravo", "charlie", "delta"):
        rep._weights[liar] = 0.1
    rep._weights["echo"] = 1.0

    votes = [
        # bravo/charlie/delta ACK regardless of the scene (compromised).
        PeerVerifier("bravo", signers["bravo"], rv).vote_on(signed),
        PeerVerifier("charlie", signers["charlie"], rv).vote_on(signed),
        PeerVerifier("delta", signers["delta"], rv).vote_on(signed),
        # echo genuinely co-observed a conflicting scene.
        PeerVerifier("echo", signers["echo"], rv).vote_on(
            signed, my_observation=(-1.0, 0.0, 0.0)),
    ]
    result = eng.tally(signed.receipt, votes, vv,
                       expected_voters=set(swarm), reputation=rep)

    # Raw count would ACCEPT (3 >= 3); reputation weighting does not.
    assert result.ack_count == 3
    assert result.outcome is not ConsensusOutcome.ACCEPTED


def test_reputation_never_accepts_below_integer_floor():
    """
    Safety floor: reputation can only *tighten* an ACCEPT, never manufacture
    one. Even with maximal trust, two ACKs cannot accept when the floor is 3.
    """
    swarm = ["alpha", "bravo", "charlie", "delta", "echo"]
    signers, _, rv, vv = _build_swarm(swarm)
    eng = ConsensusEngine(num_peers=4)  # ack_threshold = 3

    signed = _alpha_receipt(signers["alpha"], output=(0.1, 0.0, 0.0))
    rep = ReputationStore()  # everyone at full trust

    votes = [
        PeerVerifier("bravo", signers["bravo"], rv).vote_on(
            signed, my_observation=(0.1, 0.0, 0.0)),
        PeerVerifier("charlie", signers["charlie"], rv).vote_on(
            signed, my_observation=(0.1, 0.0, 0.0)),
    ]
    result = eng.tally(signed.receipt, votes, vv,
                       expected_voters=set(swarm), reputation=rep)
    assert result.ack_count == 2
    assert result.outcome is not ConsensusOutcome.ACCEPTED


# ---------------------------------------------------------------------------
# C2 — Co-visibility gate (§3.4): the semantic clause only fires when the
# voter actually co-observed the originator's scene.
# ---------------------------------------------------------------------------


def test_covisible_peer_disputes_conflicting_scene():
    """When footprints overlap, a conflicting observation -> DISPUTE."""
    signers, _, rv, _ = _build_swarm(["alpha", "bravo"])
    pv = PeerVerifier("bravo", signers["bravo"], rv, o_min=0.1)

    signed = _alpha_receipt(signers["alpha"], output=(1.0, 0.0, 0.0))
    my_pose = Pose(2.0, 0.0, 10.0)        # ~2 m from alpha, heavily overlapping
    alpha_pose = Pose(0.0, 0.0, 10.0)

    vote = pv.vote_on(signed, my_observation=(-1.0, 0.0, 0.0),
                      my_pose=my_pose, originator_pose=alpha_pose)
    assert vote.vote.decision is Vote.DISPUTE
    assert vote.vote.reason == "semantic_disagreement"


def test_non_covisible_peer_abstains_on_semantic_clause():
    """
    When footprints do not overlap, a conflicting observation must NOT cause a
    dispute — the peer never saw the same scene. This is what keeps the
    false-positive rate down (§4.5). It ACKs on the crypto/provenance evidence.
    """
    signers, _, rv, _ = _build_swarm(["alpha", "bravo"])
    pv = PeerVerifier("bravo", signers["bravo"], rv, o_min=0.1)

    signed = _alpha_receipt(signers["alpha"], output=(1.0, 0.0, 0.0))
    my_pose = Pose(100.0, 0.0, 10.0)      # 100 m away — disjoint footprint
    alpha_pose = Pose(0.0, 0.0, 10.0)

    vote = pv.vote_on(signed, my_observation=(-1.0, 0.0, 0.0),
                      my_pose=my_pose, originator_pose=alpha_pose)
    assert vote.vote.decision is Vote.ACK
    assert vote.vote.reason == "ok_no_covisibility"


def test_covisibility_gate_does_not_mask_crypto_failure():
    """The gate only governs the semantic clause; crypto failures still DISPUTE."""
    signers, _, rv, _ = _build_swarm(["alpha", "bravo"])
    pv = PeerVerifier("bravo", signers["bravo"], rv, o_min=0.1)

    # Unapproved model — a provenance failure, independent of co-visibility.
    signed = _alpha_receipt(signers["alpha"], model_hash=MALICIOUS_MODEL_HASH)
    far_pose = Pose(100.0, 0.0, 10.0)
    alpha_pose = Pose(0.0, 0.0, 10.0)

    vote = pv.vote_on(signed, my_observation=(-1.0, 0.0, 0.0),
                      my_pose=far_pose, originator_pose=alpha_pose)
    assert vote.vote.decision is Vote.DISPUTE
    assert vote.vote.reason.startswith("model_hash_not_approved")


def test_poses_absent_preserves_legacy_semantic_behavior():
    """Without poses, the semantic check applies whenever an observation exists."""
    signers, _, rv, _ = _build_swarm(["alpha", "bravo"])
    pv = PeerVerifier("bravo", signers["bravo"], rv)

    signed = _alpha_receipt(signers["alpha"], output=(1.0, 0.0, 0.0))
    vote = pv.vote_on(signed, my_observation=(-1.0, 0.0, 0.0))
    assert vote.vote.decision is Vote.DISPUTE
    assert vote.vote.reason == "semantic_disagreement"
