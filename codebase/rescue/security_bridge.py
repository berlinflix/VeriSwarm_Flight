"""Map VeriSwarm consensus evidence into rescue-observation security labels.

This adapter never decides whether a person exists.  It only describes how strongly
the evidence behind a positive observation was checked, so the rescue plane can alert
responders while the security plane independently holds unsafe autonomous control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from protocol.peer_consensus import ConsensusOutcome, ConsensusResult


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SecurityAssessment:
    """Security label and auditable reasons for one positive observation."""

    state: str
    reasons: tuple[str, ...]


def assess_consensus_security(
    result: ConsensusResult,
    *,
    expected_receipt_hash: str,
    receipt_verified: bool,
    model_approved: bool,
    runtime_approved: bool,
    min_semantic_acks: int = 1,
) -> SecurityAssessment:
    """Fail closed when translating a consensus result into a view-security label.

    ``VERIFIED`` requires a verified receipt, approved model and runtime, an exact
    receipt-digest binding, ACCEPTED consensus, and enough semantic ACKs.  A rejected
    or integrity-invalid observation is ``DISPUTED``.  An otherwise valid observation
    with no semantic quorum remains ``UNVERIFIED``.

    A caller must still forward the positive person candidate for responder review.
    This result must never be used as a survivor-presence veto.
    """
    if not _SHA256_RE.fullmatch(expected_receipt_hash):
        raise ValueError("expected_receipt_hash must be lowercase SHA-256 hex")
    if type(receipt_verified) is not bool:
        raise TypeError("receipt_verified must be boolean")
    if type(model_approved) is not bool:
        raise TypeError("model_approved must be boolean")
    if type(runtime_approved) is not bool:
        raise TypeError("runtime_approved must be boolean")
    if type(min_semantic_acks) is not int or min_semantic_acks < 1:
        raise ValueError("min_semantic_acks must be a positive integer")

    integrity_failures: list[str] = []
    if not receipt_verified:
        integrity_failures.append("receipt_not_verified")
    if not model_approved:
        integrity_failures.append("model_not_approved")
    if not runtime_approved:
        integrity_failures.append("runtime_not_approved")
    if result.target_receipt_hash != expected_receipt_hash:
        integrity_failures.append("consensus_receipt_digest_mismatch")
    if integrity_failures:
        return SecurityAssessment("DISPUTED", tuple(integrity_failures))

    reasons: list[str] = []
    if result.equivocation_voter_ids:
        reasons.append("peer_equivocation_detected")
    if result.dispute_count:
        reasons.append("minority_dispute_present")

    if result.outcome is ConsensusOutcome.REJECTED:
        reasons.append("consensus_rejected")
        return SecurityAssessment("DISPUTED", tuple(sorted(set(reasons))))
    if result.outcome is ConsensusOutcome.NO_QUORUM:
        reasons.append("consensus_no_quorum")
        return SecurityAssessment("UNVERIFIED", tuple(sorted(set(reasons))))
    if result.semantic_ack_count < min_semantic_acks:
        reasons.append("semantic_cross_check_insufficient")
        return SecurityAssessment("UNVERIFIED", tuple(sorted(set(reasons))))
    return SecurityAssessment("VERIFIED", tuple(sorted(set(reasons))))
