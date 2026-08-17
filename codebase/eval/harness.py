"""
Shared evaluation utilities for the VeriSwarm experiment runner.

Everything in here produces *real* measurements by exercising the actual
protocol code in `protocol/` — there are no hard-coded result numbers. Timing
uses `time.perf_counter_ns`; outcomes come from the real `ConsensusEngine`,
`PeerVerifier`, reputation store, and co-visibility geometry. Synthetic inputs
(action vectors for the threshold sweeps) are drawn from a *seeded* RNG and the
generating parameters are written into the CSVs, so every run is reproducible
and the methodology is transparent.

Hardware-only measurements (OP-TEE signing latency, Jetson energy) are NOT
simulated. When the OP-TEE backend is absent, the relevant runs are skipped and
recorded as PENDING in the manifest; they must be taken on the Alpha node.
"""

from __future__ import annotations

import csv
import math
import random
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from protocol.receipts import (
    ReceiptSigner,
    ReceiptVerifier,
    build_receipt,
    sha256_hex,
)
from protocol.peer_consensus import (
    ConsensusEngine,
    ConsensusOutcome,
    PeerVerifier,
    ReputationStore,
    SignedVote,
    Vote,
    VoteVerifier,
    outputs_agree,
)
from protocol.geometry import Pose, covisibility

# ---------------------------------------------------------------------------
# Paths and fixed model identities
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"

APPROVED_MODEL = sha256_hex(b"yolov8n-weights-v1")
MALICIOUS_MODEL = sha256_hex(b"yolov8n-backdoored")

# A fixed seed makes every sweep reproducible; it is recorded in the manifest.
DEFAULT_SEED = 20260616


def ensure_results_dir() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


# ---------------------------------------------------------------------------
# Statistics + CSV/log IO
# ---------------------------------------------------------------------------


@dataclass
class Stat:
    """Mean / sample-std summary of a list of samples, plus the raw count."""

    mean: float
    std: float
    n: int

    @classmethod
    def of(cls, samples: Sequence[float]) -> "Stat":
        xs = list(samples)
        if not xs:
            return cls(0.0, 0.0, 0)
        mean = statistics.fmean(xs)
        std = statistics.stdev(xs) if len(xs) > 1 else 0.0
        return cls(mean, std, len(xs))


def write_csv(name: str, rows: List[Dict[str, object]]) -> Path:
    """Overwrite results/<name> with `rows` (idempotent per run group)."""
    ensure_results_dir()
    path = RESULTS_DIR / name
    if not rows:
        path.write_text("")  # empty marker so the file exists
        return path
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


class RunLogger:
    """Append-only, human-readable per-round log: results/run_<ts>.log."""

    def __init__(self, tag: str = "") -> None:
        ensure_results_dir()
        ts = time.strftime("%Y%m%d_%H%M%S")
        suffix = f"_{tag}" if tag else ""
        self.path = RESULTS_DIR / f"run_{ts}{suffix}.log"
        self._fh = self.path.open("a")
        self.line(f"# VeriSwarm eval run {ts}")

    def line(self, text: str) -> None:
        self._fh.write(text + "\n")
        self._fh.flush()

    def round(self, **fields: object) -> None:
        """One structured round record, e.g. round(round_id=3, outcome='ACCEPTED', ...)."""
        kv = " ".join(f"{k}={v}" for k, v in fields.items())
        self.line(kv)

    def close(self) -> None:
        self._fh.close()


# ---------------------------------------------------------------------------
# Swarm + scenario construction
# ---------------------------------------------------------------------------

DRONE_NAMES = [
    "alpha", "bravo", "charlie", "delta", "echo",
    "foxtrot", "golf", "hotel", "india", "juliet", "kilo",
]


def swarm_ids(n: int) -> List[str]:
    if n > len(DRONE_NAMES):
        return [f"drone{i}" for i in range(n)]
    return DRONE_NAMES[:n]


@dataclass
class Swarm:
    """A constructed swarm with all the keys and verifiers wired up."""

    ids: List[str]
    signers: Dict[str, ReceiptSigner]
    peer_keys: Dict[str, str]
    receipt_verifier: ReceiptVerifier
    vote_verifier: VoteVerifier

    @property
    def n(self) -> int:
        return len(self.ids)

    @property
    def k(self) -> int:
        return len(self.ids) - 1  # peers voting on one originator


def build_swarm(n: int, approved=(APPROVED_MODEL,)) -> Swarm:
    ids = swarm_ids(n)
    signers = {d: ReceiptSigner() for d in ids}
    peer_keys = {d: s.public_key_hex for d, s in signers.items()}
    rv = ReceiptVerifier(peer_keys=peer_keys, approved_models=set(approved))
    vv = VoteVerifier(peer_keys=peer_keys)
    return Swarm(ids, signers, peer_keys, rv, vv)


def alpha_receipt(swarm: Swarm, *, output=(0.1, 0.0, 0.0), model_hash=None):
    """A signed receipt from the originator ('alpha')."""
    receipt = build_receipt(
        drone_id=swarm.ids[0],
        input_bytes=b"\x00" * 128,
        model_hash=model_hash or APPROVED_MODEL,
        output=tuple(output),
    )
    return swarm.signers[swarm.ids[0]].sign(receipt)


# ---------------------------------------------------------------------------
# One consensus round (timed) — the workhorse for the scenario runs
# ---------------------------------------------------------------------------


@dataclass
class RoundResult:
    outcome: ConsensusOutcome
    ack_count: int
    dispute_count: int
    missing_count: int
    decision_ms: float
    decision_ms_std: float
    ack_ids: frozenset
    dispute_ids: frozenset


def run_round(
    swarm: Swarm,
    signed_receipt,
    votes: Sequence[SignedVote],
    *,
    reputation: Optional[ReputationStore] = None,
    repeats: int = 1,
) -> RoundResult:
    """
    Tally `votes` on `signed_receipt`, timing the decision over `repeats`
    identical tallies (the tally is pure, so repeating it only measures cost).
    Returns the outcome plus the mean decision latency in milliseconds.
    """
    eng = ConsensusEngine(num_peers=swarm.k)
    roster = set(swarm.ids)
    latencies_ns: List[int] = []
    result = None
    for _ in range(max(1, repeats)):
        t0 = time.perf_counter_ns()
        result = eng.tally(
            signed_receipt.receipt, votes, swarm.vote_verifier,
            expected_voters=roster, reputation=reputation,
        )
        latencies_ns.append(time.perf_counter_ns() - t0)
    assert result is not None
    decision_samples_ms = [sample / 1e6 for sample in latencies_ns]
    return RoundResult(
        outcome=result.outcome,
        ack_count=result.ack_count,
        dispute_count=result.dispute_count,
        missing_count=result.missing_count,
        decision_ms=statistics.fmean(decision_samples_ms),
        decision_ms_std=(
            statistics.stdev(decision_samples_ms)
            if len(decision_samples_ms) > 1
            else 0.0
        ),
        ack_ids=result.ack_voter_ids,
        dispute_ids=result.dispute_voter_ids,
    )


def honest_votes(swarm: Swarm, signed, *, observation=(0.1, 0.0, 0.0)) -> List[SignedVote]:
    """Every peer co-observes and agrees -> all ACK."""
    votes = []
    for d in swarm.ids[1:]:
        pv = PeerVerifier(d, swarm.signers[d], swarm.receipt_verifier)
        votes.append(pv.vote_on(signed, my_observation=observation))
    return votes


def provenance_votes(swarm: Swarm, signed) -> List[SignedVote]:
    """Originator ran an unapproved model -> peers DISPUTE on provenance."""
    votes = []
    for d in swarm.ids[1:]:
        pv = PeerVerifier(d, swarm.signers[d], swarm.receipt_verifier)
        votes.append(pv.vote_on(signed))
    return votes


def patch_votes(swarm: Swarm, signed, *, observation=(0.0, 0.0, 1.0)) -> List[SignedVote]:
    """Originator fooled by a patch; co-observing peers see a conflicting scene."""
    votes = []
    for d in swarm.ids[1:]:
        pv = PeerVerifier(d, swarm.signers[d], swarm.receipt_verifier)
        votes.append(pv.vote_on(signed, my_observation=observation))
    return votes


# ---------------------------------------------------------------------------
# Synthetic action-vector model (for the threshold/stealth sweeps)
# ---------------------------------------------------------------------------
#
# The semantic check compares a claimed action vector against a peer's observed
# action vector via their L2 distance. To characterise the detector we model:
#   honest peer:  observed = claimed + N(0, sigma_honest) per component
#   attacked:     the claimed vector is pushed off by an adversarial patch, so
#                 observed - claimed has L2 magnitude ~ attack_l2.
# Both are drawn from a seeded RNG; the parameters are logged with the results.

ACTION_DIM = 3


def honest_pair(rng: random.Random, sigma_honest: float) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    claimed = tuple(rng.uniform(-1.0, 1.0) for _ in range(ACTION_DIM))
    observed = tuple(c + rng.gauss(0.0, sigma_honest) for c in claimed)
    return claimed, observed


def attack_pair(rng: random.Random, attack_l2: float) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    claimed = tuple(rng.uniform(-1.0, 1.0) for _ in range(ACTION_DIM))
    # A random unit direction scaled to the desired L2 magnitude.
    direction = [rng.gauss(0.0, 1.0) for _ in range(ACTION_DIM)]
    norm = math.sqrt(sum(x * x for x in direction)) or 1.0
    observed = tuple(c + attack_l2 * d / norm for c, d in zip(claimed, direction))
    return claimed, observed


def l2(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ---------------------------------------------------------------------------
# OP-TEE / Jetson hardware detection
# ---------------------------------------------------------------------------


def optee_available() -> bool:
    """
    True only if a real OP-TEE signing backend is importable AND a TEE device is
    present. Today the Trusted Application is not yet built, so this returns
    False and the hardware runs (R2 sign latency, R-EN energy) are recorded as
    PENDING rather than fabricated.
    """
    try:
        from signing.optee_backend import OPTEEReceiptSigner  # noqa: F401
    except Exception:
        return False
    return Path("/dev/tee0").exists()
