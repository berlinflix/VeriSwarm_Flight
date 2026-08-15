"""
VeriSwarm Protocol — Phase 2 & 3: peer verification and Byzantine consensus.

This module turns a single drone's cryptographically-signed Receipt into a
swarm-wide decision. The pipeline is:

    drone Alpha produces SignedReceipt
        |
        v
    each peer drone runs PeerVerifier.vote_on(signed_receipt)
        -> emits SignedVote (ACK | DISPUTE), signed with peer's key
        |
        v
    ConsensusEngine.tally(list[SignedVote]) -> ConsensusResult
        -> ACCEPTED | REJECTED | NO_QUORUM

Two layers of checking happen at the peer:

  (a) Cryptographic: signature valid + model_hash on the allowlist.
      Delegated to `ReceiptVerifier` (see `receipts.py`).

  (b) Semantic: does the claimed `output` agree with what this peer
      observed? Implemented here as `outputs_agree(...)`. The threshold
      is configurable per-mission.

The BFT thresholds enforced by `ConsensusEngine`:

    let k = number of peers voting (excludes the receipt's originator)

    ACCEPT  iff  ack_count     >=  floor(2k/3) + 1
    REJECT  iff  dispute_count >=  floor(k/3)  + 1
    else NO_QUORUM (votes still arriving / timeouts)

These thresholds satisfy `ack_threshold + dispute_threshold > k`, so
ACCEPT and REJECT cannot both fire on the same vote bag — the protocol
is internally consistent.

References
----------
Castro & Liskov, "Practical Byzantine Fault Tolerance", OSDI 1999.
Yin et al., "HotStuff: BFT Consensus in the Lens of Blockchain", PODC 2019.
"""

from __future__ import annotations

import enum
import hashlib
import math
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Mapping, Optional, Sequence

import json
import nacl.exceptions
import nacl.signing

from .receipts import (
    Receipt,
    ReceiptSigner,
    ReceiptVerifier,
    SignedReceipt,
    VerificationResult,
    sha256_hex,
)
from .geometry import DEFAULT_HFOV, DEFAULT_VFOV, Pose, covisibility


# ---------------------------------------------------------------------------
# Vote and consensus types
# ---------------------------------------------------------------------------


class Vote(enum.Enum):
    """A peer's verdict on a SignedReceipt."""

    ACK = "ACK"
    DISPUTE = "DISPUTE"


class ConsensusOutcome(enum.Enum):
    """The aggregated decision after tallying peer votes."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NO_QUORUM = "NO_QUORUM"


@dataclass(frozen=True)
class PeerVote:
    """
    A single peer's vote on a target receipt.

    The vote itself is signed (see `SignedVote`) so that disputes are
    cryptographically attributable. A Byzantine drone cannot forge
    DISPUTE votes from honest peers, and an honest drone's DISPUTE
    cannot be repudiated.
    """

    voter_id: str
    target_receipt_hash: str  # SHA-256 of Receipt.canonical()
    decision: Vote
    reason: str
    timestamp_ns: int

    def canonical(self) -> bytes:
        payload = {
            "voter_id": self.voter_id,
            "target_receipt_hash": self.target_receipt_hash,
            "decision": self.decision.value,
            "reason": self.reason,
            "timestamp_ns": self.timestamp_ns,
        }
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")


@dataclass(frozen=True)
class SignedVote:
    """A PeerVote with an Ed25519 signature from the voter."""

    vote: PeerVote
    signature: str  # hex-encoded Ed25519 signature

    def serialize(self) -> bytes:
        payload = {
            "vote": {
                "voter_id": self.vote.voter_id,
                "target_receipt_hash": self.vote.target_receipt_hash,
                "decision": self.vote.decision.value,
                "reason": self.vote.reason,
                "timestamp_ns": self.vote.timestamp_ns,
            },
            "signature": self.signature,
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")

    @classmethod
    def deserialize(cls, wire: bytes) -> "SignedVote":
        obj = json.loads(wire)
        v = obj["vote"]
        return cls(
            vote=PeerVote(
                voter_id=v["voter_id"],
                target_receipt_hash=v["target_receipt_hash"],
                decision=Vote(v["decision"]),
                reason=v["reason"],
                timestamp_ns=v["timestamp_ns"],
            ),
            signature=obj["signature"],
        )


@dataclass(frozen=True)
class ConsensusResult:
    """Outcome of tallying a set of SignedVotes."""

    outcome: ConsensusOutcome
    ack_count: int
    dispute_count: int
    missing_count: int
    ack_threshold: int
    dispute_threshold: int
    reason: str
    ack_voter_ids: frozenset = frozenset()
    dispute_voter_ids: frozenset = frozenset()


# ---------------------------------------------------------------------------
# Semantic agreement check (catches adversarial-perception attacks)
# ---------------------------------------------------------------------------


def outputs_agree(
    claimed: Sequence[float],
    observed: Sequence[float],
    threshold: float = 0.5,
) -> bool:
    """
    Return True iff two action vectors agree within an L2 threshold.

    Two drones in the same swarm with overlapping fields of view should
    produce action vectors that are *close* (not byte-identical — they
    have different viewpoints, slightly different IMU readings). When
    one drone is fooled by an adversarial patch, its action vector
    diverges from its peers'.

    The threshold is mission-configured. In our experiments we set it
    to 0.5 in normalised action space (output range [-1, 1]).
    """
    if len(claimed) != len(observed):
        return False
    sq = sum((a - b) ** 2 for a, b in zip(claimed, observed))
    return math.sqrt(sq) < threshold


def receipt_digest(receipt: Receipt) -> str:
    """SHA-256 of a receipt's canonical bytes. Used as the vote target."""
    return sha256_hex(receipt.canonical())


# ---------------------------------------------------------------------------
# Peer verifier — turns a SignedReceipt into a SignedVote
# ---------------------------------------------------------------------------


class PeerVerifier:
    """
    Runs cryptographic + semantic verification on a peer's SignedReceipt
    and emits a SignedVote.

    Cryptographic verification is delegated to `ReceiptVerifier`.
    Semantic verification (`outputs_agree`) is applied when the local
    drone has its own observation for the *same* logical timestep
    (i.e. overlapping field of view).
    """

    def __init__(
        self,
        my_drone_id: str,
        signer: ReceiptSigner,
        receipt_verifier: ReceiptVerifier,
        agreement_threshold: float = 0.5,
        o_min: float = 0.1,
        hfov: float = DEFAULT_HFOV,
        vfov: float = DEFAULT_VFOV,
        m_min: int = 15,
    ):
        self.my_drone_id = my_drone_id
        self._signer = signer
        self._receipt_verifier = receipt_verifier
        self._agreement_threshold = agreement_threshold
        self._o_min = o_min
        self._hfov = hfov
        self._vfov = vfov
        self._m_min = m_min

    def _co_visible(
        self,
        my_pose: Optional[Pose],
        originator_pose: Optional[Pose],
        my_frame,
        originator_frame,
    ) -> bool:
        """
        Decide whether this peer co-observed the originator's scene.

        Primary test is geometric: footprint overlap o(i, j) >= o_min from the
        two poses. When that test fails *and* both camera frames are available,
        fall back to a pose-independent, image-based check — the frames are
        co-visible if they share at least ``m_min`` RANSAC-consistent ORB
        matches. The fallback is what preserves the semantic layer when a pose
        is untrustworthy (GPS spoofing, EKF drift): a spoofed pose can hide a
        genuine co-view from the geometric test, but it cannot make two frames
        of the same scene stop matching.
        """
        if my_pose is not None and originator_pose is not None:
            overlap = covisibility(my_pose, originator_pose, self._hfov, self._vfov)
            if overlap >= self._o_min:
                return True

        if my_frame is not None and originator_frame is not None:
            # Lazy import so pure-consensus contexts never need OpenCV.
            from .covis_features import covisible_by_features

            return covisible_by_features(my_frame, originator_frame, m_min=self._m_min)

        return False

    def vote_on(
        self,
        signed_receipt: SignedReceipt,
        my_observation: Optional[Sequence[float]] = None,
        my_pose: Optional[Pose] = None,
        originator_pose: Optional[Pose] = None,
        my_frame=None,
        originator_frame=None,
    ) -> SignedVote:
        """
        Produce a signed vote on the given receipt.

        Decision rules (in order):
          1. Crypto fails -> DISPUTE (bad_signature | unknown_drone_id)
          2. Model hash not approved -> DISPUTE (model_hash_not_approved)
          3. Semantic disagreement (if `my_observation` provided and this peer
             co-observed the scene) -> DISPUTE (semantic_disagreement)
          4. otherwise -> ACK

        Co-visibility (step 3's gate) is decided by :meth:`_co_visible`:
        geometric footprint overlap first, then an image-feature fallback when
        both ``my_frame`` and ``originator_frame`` are supplied.
        """
        crypto_result: VerificationResult = self._receipt_verifier.verify(signed_receipt)

        if not crypto_result.ok:
            return self._build_signed_vote(
                signed_receipt.receipt,
                decision=Vote.DISPUTE,
                reason=crypto_result.reason,
            )

        # Semantic check (C2): only applied when this peer actually co-observed
        # the originator's scene. When poses (and optionally frames) are given,
        # gate on co-visibility; without any geometry, fall back to the legacy
        # behaviour of applying the check whenever a local observation exists.
        semantic_applicable = my_observation is not None
        geometry_supplied = (my_pose is not None and originator_pose is not None) or (
            my_frame is not None and originator_frame is not None
        )
        if semantic_applicable and geometry_supplied:
            if not self._co_visible(
                my_pose, originator_pose, my_frame, originator_frame
            ):
                # Not co-visible: abstain on the semantic clause and vote on the
                # cryptographic and provenance evidence alone.
                return self._build_signed_vote(
                    signed_receipt.receipt,
                    decision=Vote.ACK,
                    reason="ok_no_covisibility",
                )

        if semantic_applicable and not outputs_agree(
            signed_receipt.receipt.output,
            my_observation,
            threshold=self._agreement_threshold,
        ):
            return self._build_signed_vote(
                signed_receipt.receipt,
                decision=Vote.DISPUTE,
                reason="semantic_disagreement",
            )

        return self._build_signed_vote(
            signed_receipt.receipt,
            decision=Vote.ACK,
            reason="ok",
        )

    def _build_signed_vote(
        self, target_receipt: Receipt, *, decision: Vote, reason: str
    ) -> SignedVote:
        vote = PeerVote(
            voter_id=self.my_drone_id,
            target_receipt_hash=receipt_digest(target_receipt),
            decision=decision,
            reason=reason,
            timestamp_ns=time.time_ns(),
        )
        sig = self._signer._key.sign(vote.canonical()).signature
        return SignedVote(vote=vote, signature=sig.hex())


# ---------------------------------------------------------------------------
# Vote verification (a node checks votes it receives from peers)
# ---------------------------------------------------------------------------


class VoteVerifier:
    """Verifies that an incoming SignedVote was signed by the claimed voter."""

    def __init__(self, peer_keys: Mapping[str, str]):
        self._peer_keys = {
            did: nacl.signing.VerifyKey(bytes.fromhex(hex_key))
            for did, hex_key in peer_keys.items()
        }

    def verify(self, signed_vote: SignedVote) -> bool:
        verify_key = self._peer_keys.get(signed_vote.vote.voter_id)
        if verify_key is None:
            return False
        try:
            verify_key.verify(
                signed_vote.vote.canonical(),
                bytes.fromhex(signed_vote.signature),
            )
            return True
        except (nacl.exceptions.BadSignatureError, ValueError):
            return False


# ---------------------------------------------------------------------------
# Reputation weighting (C1) — votes weighted by historical reliability
# ---------------------------------------------------------------------------


@dataclass
class ReputationStore:
    """
    Per-drone reliability weights for reputation-weighted consensus.

    Each drone i carries r_i in [r_min, 1], initialised to 1.0. After every
    consensus round, a voter that agreed with the outcome gains a little weight
    and one that dissented loses more (asymmetric, so a persistent liar decays
    fast):

        r_i <- clip( r_i + alpha*[agreed] - beta*[dissented], r_min, 1 )

    The weights only ever *tighten* an ACCEPT (which additionally needs two
    thirds of the participating reputation mass) or *add* a REJECT trigger; they
    never relax the integer PBFT thresholds, so the Byzantine bound is preserved
    as a safety floor. See `ConsensusEngine.tally`.
    """

    alpha: float = 0.05
    beta: float = 0.2
    r_min: float = 0.1
    _weights: dict = field(default_factory=dict)

    def weight(self, drone_id: str) -> float:
        return self._weights.get(drone_id, 1.0)

    def total(self, drone_ids: Iterable[str]) -> float:
        return sum(self.weight(d) for d in drone_ids)

    def update(
        self,
        ack_ids: Iterable[str],
        dispute_ids: Iterable[str],
        outcome: "ConsensusOutcome",
    ) -> None:
        """Apply the post-round reputation update for one consensus outcome."""
        if outcome is ConsensusOutcome.ACCEPTED:
            agreed, dissented = ack_ids, dispute_ids
        elif outcome is ConsensusOutcome.REJECTED:
            agreed, dissented = dispute_ids, ack_ids
        else:
            return  # NO_QUORUM carries no information; leave weights untouched.
        for d in agreed:
            self._weights[d] = min(1.0, self.weight(d) + self.alpha)
        for d in dissented:
            self._weights[d] = max(self.r_min, self.weight(d) - self.beta)


# ---------------------------------------------------------------------------
# Consensus engine — BFT tally with >2/3 ACK threshold
# ---------------------------------------------------------------------------


def quorum_thresholds(num_peers: int) -> tuple[int, int]:
    """
    Return ``(ack_threshold, dispute_threshold)`` for ``num_peers`` voting peers.

        ack_threshold      = floor(2k / 3) + 1
        dispute_threshold  = floor(k  / 3) + 1

    These satisfy ``ack_threshold + dispute_threshold > k``, so ACCEPT and
    REJECT can never both fire on the same vote bag. Exposed as a free function
    because Module 4 (isolation) shrinks the active peer set at runtime and must
    recompute the quorum from the same rule the engine uses.
    """
    if num_peers < 1:
        raise ValueError("num_peers must be >= 1")
    return (2 * num_peers) // 3 + 1, num_peers // 3 + 1


class ConsensusEngine:
    """
    Tallies a set of votes targeting a single Receipt and produces a
    `ConsensusResult`.

    The originator of the receipt is *not* one of the voters: a drone
    does not vote on its own receipt. Therefore for a swarm of N drones
    voting on Alpha's receipt, the engine expects up to N-1 votes.

    Thresholds for k peers:
        ack_threshold      = floor(2k / 3) + 1
        dispute_threshold  = floor(k  / 3) + 1

    These satisfy:
        ack_threshold + dispute_threshold > k

    so ACCEPT and REJECT cannot both be true on the same vote bag.

    Worked thresholds:
        k=2 (3-drone swarm)  -> ACCEPT >=2, REJECT >=1
        k=4 (5-drone swarm)  -> ACCEPT >=3, REJECT >=2
        k=6 (7-drone swarm)  -> ACCEPT >=5, REJECT >=3
    """

    def __init__(self, num_peers: int):
        if num_peers < 1:
            raise ValueError("num_peers must be >= 1")
        self.num_peers = num_peers
        self.ack_threshold, self.dispute_threshold = quorum_thresholds(num_peers)

        # Internal-consistency sanity check on the thresholds.
        assert self.ack_threshold + self.dispute_threshold > num_peers, (
            "threshold misconfiguration would allow simultaneous ACCEPT and REJECT"
        )

    def tally(
        self,
        target_receipt: Receipt,
        signed_votes: Iterable[SignedVote],
        vote_verifier: VoteVerifier,
        expected_voters: Optional[Iterable[str]] = None,
        reputation: Optional["ReputationStore"] = None,
    ) -> ConsensusResult:
        """
        Aggregate `signed_votes` and return a ConsensusResult.

        `signed_votes`:
            collected SignedVote objects, possibly from a subset of
            peers (network drops, timeouts).
        `vote_verifier`:
            used to check that each SignedVote's signature is valid.
            Invalid signatures are silently dropped.
        `expected_voters`:
            optional roster of peer ids. If provided, votes from any id
            outside this roster are dropped, and missing voters are
            counted toward `missing_count`.
        """
        target_hash = receipt_digest(target_receipt)
        roster = set(expected_voters) if expected_voters is not None else None

        # Drop self-votes — a drone never votes on its own receipt.
        originator = target_receipt.drone_id

        ack_voters: set[str] = set()
        dispute_voters: set[str] = set()

        for sv in signed_votes:
            # Wrong target — vote isn't for this receipt.
            if sv.vote.target_receipt_hash != target_hash:
                continue
            # Voter outside the expected roster.
            if roster is not None and sv.vote.voter_id not in roster:
                continue
            # Originator's self-vote is ignored.
            if sv.vote.voter_id == originator:
                continue
            # Cryptographic check.
            if not vote_verifier.verify(sv):
                continue
            # Deduplicate by voter — first vote per peer wins.
            if sv.vote.voter_id in ack_voters or sv.vote.voter_id in dispute_voters:
                continue

            if sv.vote.decision is Vote.ACK:
                ack_voters.add(sv.vote.voter_id)
            elif sv.vote.decision is Vote.DISPUTE:
                dispute_voters.add(sv.vote.voter_id)

        ack_count = len(ack_voters)
        dispute_count = len(dispute_voters)

        if roster is not None:
            expected_peers = (roster - {originator})
            missing_count = max(
                0, len(expected_peers) - ack_count - dispute_count
            )
        else:
            missing_count = max(
                0, self.num_peers - ack_count - dispute_count
            )

        # Decision. Without a reputation store this is the plain count-based
        # PBFT rule. With one (C1), an ACCEPT additionally needs two thirds of
        # the participating reputation mass on the ACK side, and a REJECT may
        # also be triggered by a one-third dispute mass — so reputation only
        # ever tightens an ACCEPT or adds a REJECT, never weakening the integer
        # safety floor that gives the Byzantine bound.
        if reputation is not None:
            w_ack = reputation.total(ack_voters)
            w_dispute = reputation.total(dispute_voters)
            w_part = w_ack + w_dispute
            ack_ok = ack_count >= self.ack_threshold and (
                w_part <= 0.0 or w_ack >= (2.0 / 3.0) * w_part
            )
            reject_ok = dispute_count >= self.dispute_threshold or (
                w_part > 0.0 and w_dispute > (1.0 / 3.0) * w_part
            )
        else:
            ack_ok = ack_count >= self.ack_threshold
            reject_ok = dispute_count >= self.dispute_threshold

        common = dict(
            ack_count=ack_count,
            dispute_count=dispute_count,
            missing_count=missing_count,
            ack_threshold=self.ack_threshold,
            dispute_threshold=self.dispute_threshold,
            ack_voter_ids=frozenset(ack_voters),
            dispute_voter_ids=frozenset(dispute_voters),
        )

        if ack_ok:
            return ConsensusResult(
                outcome=ConsensusOutcome.ACCEPTED,
                reason=f"ack_quorum_reached:{ack_count}>={self.ack_threshold}",
                **common,
            )

        if reject_ok:
            return ConsensusResult(
                outcome=ConsensusOutcome.REJECTED,
                reason=f"dispute_quorum_reached:{dispute_count}>={self.dispute_threshold}",
                **common,
            )

        return ConsensusResult(
            outcome=ConsensusOutcome.NO_QUORUM,
            reason=(
                f"insufficient_votes:ack={ack_count}<{self.ack_threshold},"
                f"dispute={dispute_count}<{self.dispute_threshold}"
            ),
            **common,
        )


# ---------------------------------------------------------------------------
# Module 4 — Consensus-Triggered Isolation and Safe-Fallback (CTI-SF)
# ---------------------------------------------------------------------------


class SafeAction(enum.Enum):
    """The fail-safe action Module 4 takes on a consensus outcome."""

    EXECUTE = "EXECUTE"              # ACCEPTED: run the action, log the receipt
    SAFE_FALLBACK = "SAFE_FALLBACK"  # REJECTED: hover / return-to-launch
    DEFER = "DEFER"                  # NO_QUORUM: re-request votes, cautious hold


def fallback_action(outcome: ConsensusOutcome) -> SafeAction:
    """
    Map a consensus outcome to its fail-safe action (Algorithm 4, lines 4-7).

    An ACCEPTED action is executed; a REJECTED one triggers a safe fallback
    (hover or return-to-launch); a NO_QUORUM result is deferred until the
    missing votes arrive or a cautious fallback fires on timeout.
    """
    if outcome is ConsensusOutcome.ACCEPTED:
        return SafeAction.EXECUTE
    if outcome is ConsensusOutcome.REJECTED:
        return SafeAction.SAFE_FALLBACK
    return SafeAction.DEFER


@dataclass(frozen=True)
class IsolationEvent:
    """
    A logged + announced record that a drone was isolated from the swarm.

    Emitted the moment a drone's rejection fraction crosses the threshold. It
    carries enough context to audit the decision after the fact and to let every
    node reconstruct the active-set change.
    """

    drone_id: str
    round_index: int
    rejected_in_window: int
    window: int
    rho: float
    threshold: float
    roster_before: frozenset
    roster_after: frozenset

    def as_dict(self) -> dict:
        return {
            "drone_id": self.drone_id,
            "round_index": self.round_index,
            "rejected_in_window": self.rejected_in_window,
            "window": self.window,
            "rho": round(self.rho, 4),
            "threshold": self.threshold,
            "n_before": len(self.roster_before),
            "n_after": len(self.roster_after),
        }


@dataclass
class IsolationPolicy:
    """CTI-SF parameters: sliding-window length W and rejection fraction alpha."""

    window: int = 10   # W
    alpha: float = 0.5  # isolate when rho(i) > alpha


class IsolationTracker:
    """
    Module 4 (CTI-SF): keeps a sliding window of each drone's recent consensus
    outcomes and isolates a drone from future rounds once the fraction of its
    receipts that the swarm rejected exceeds ``alpha`` (Algorithm 4, Section 3.6).

    ``rho(i) = rejected_in_window / W`` follows the algorithm exactly: the
    divisor is the window length ``W``, not the number of samples seen so far,
    so a drone must accumulate more than ``alpha * W`` rejections inside the
    window before it can be isolated. With the defaults (W = 10, alpha = 0.5)
    that is more than five rejections in its last ten receipts — one unlucky
    round can never remove an otherwise-honest drone, and old rejections age out
    of the window so a recovered drone is not punished forever.

    Isolation is a *deterministic function of the public consensus outcomes*, so
    every honest node that observes the same outcomes reaches the same active
    set independently; no separately trusted "isolation announcement" has to be
    believed. The two hooks make the event visible where a deployment wants it:
    ``on_log`` for the local structured log, ``on_announce`` for a swarm
    broadcast in the live gRPC layer. Both are optional; every event is also
    retained in :attr:`events`.

    When a drone is isolated it is dropped from the active roster, which shrinks
    the peer set every subsequent round tallies over; :meth:`quorum_for` then
    recomputes the ACCEPT/REJECT thresholds from the smaller set via the same
    :func:`quorum_thresholds` rule the engine uses.
    """

    def __init__(
        self,
        roster: Iterable[str],
        policy: Optional[IsolationPolicy] = None,
        on_log: Optional[Callable[["IsolationEvent"], None]] = None,
        on_announce: Optional[Callable[["IsolationEvent"], None]] = None,
    ) -> None:
        self.policy = policy or IsolationPolicy()
        if self.policy.window < 1:
            raise ValueError("window W must be >= 1")
        self._roster: set[str] = set(roster)
        self._isolated: set[str] = set()
        self._history: dict[str, deque] = {}
        self._on_log = on_log
        self._on_announce = on_announce
        self.events: list[IsolationEvent] = []
        self._round = 0

    # -- queries -----------------------------------------------------------
    @property
    def active_roster(self) -> frozenset:
        """Drones still trusted to originate receipts and cast votes."""
        return frozenset(self._roster)

    @property
    def isolated(self) -> frozenset:
        return frozenset(self._isolated)

    def is_isolated(self, drone_id: str) -> bool:
        return drone_id in self._isolated

    def active_voters(self, originator_id: str) -> frozenset:
        """Active peers that vote on ``originator_id``'s receipt (excludes self)."""
        return frozenset(self._roster - {originator_id})

    def quorum_for(self, originator_id: str) -> tuple[int, int]:
        """(ack_threshold, dispute_threshold) over the *current* active peers."""
        k = len(self.active_voters(originator_id))
        return quorum_thresholds(max(1, k))

    def rejection_rate(self, drone_id: str) -> float:
        """rho(i): rejected receipts in the window divided by the window length W."""
        hist = self._history.get(drone_id)
        if not hist:
            return 0.0
        rejected = sum(1 for o in hist if o is ConsensusOutcome.REJECTED)
        return rejected / self.policy.window

    # -- update ------------------------------------------------------------
    def record(
        self,
        originator_id: str,
        outcome: ConsensusOutcome,
        round_index: Optional[int] = None,
    ) -> Optional[IsolationEvent]:
        """
        Append ``originator_id``'s round outcome to its sliding window and
        isolate it when its rejection fraction now exceeds ``alpha``.

        Returns the :class:`IsolationEvent` if this call triggered isolation,
        otherwise ``None``. Recording for an already-isolated drone is a no-op:
        the event fires exactly once, and its ``on_log``/``on_announce`` hooks
        are each called exactly once.
        """
        self._round = round_index if round_index is not None else self._round + 1
        if originator_id in self._isolated:
            return None

        hist = self._history.setdefault(
            originator_id, deque(maxlen=self.policy.window)
        )
        hist.append(outcome)
        rejected = sum(1 for o in hist if o is ConsensusOutcome.REJECTED)
        rho = rejected / self.policy.window

        if rho <= self.policy.alpha:
            return None

        before = frozenset(self._roster)
        self._roster.discard(originator_id)
        self._isolated.add(originator_id)
        event = IsolationEvent(
            drone_id=originator_id,
            round_index=self._round,
            rejected_in_window=rejected,
            window=self.policy.window,
            rho=rho,
            threshold=self.policy.alpha,
            roster_before=before,
            roster_after=frozenset(self._roster),
        )
        self.events.append(event)
        if self._on_log is not None:
            self._on_log(event)
        if self._on_announce is not None:
            self._on_announce(event)
        return event
