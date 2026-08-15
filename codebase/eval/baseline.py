"""
Unattested baseline for the head-to-head comparison (R-BL, Table 4.14).

This is a deliberately simple stand-in for prior collaborative-consistency
schemes that have no hardware root of trust: peers compare their observed action
against the claimed action and a plain majority decides. Crucially it has

  * no signatures      -> votes are unauthenticated and can be injected/forged,
  * no provenance check -> a swapped (but well-formed) model is invisible,
  * no reputation       -> colluding voters are counted at face value.

Running the *same* scenarios through this baseline and through VeriSwarm is what
makes Table 4.14 a real measurement rather than an assertion: each pillar of
VeriSwarm is switched off here, so the difference is attributable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from protocol.peer_consensus import outputs_agree


@dataclass
class PlainVote:
    """An unauthenticated vote — note the absence of any signature field."""

    voter_id: str
    decision: str  # "ACK" | "DISPUTE"


class MajorityBaseline:
    """Plain majority vote over naive semantic agreement. No crypto, no trust."""

    def __init__(self, agreement_threshold: float = 0.5) -> None:
        self._threshold = agreement_threshold

    def naive_vote(
        self,
        voter_id: str,
        claimed: Sequence[float],
        observed: Optional[Sequence[float]],
    ) -> PlainVote:
        # With no observation the baseline simply trusts the claim.
        if observed is None:
            return PlainVote(voter_id, "ACK")
        agree = outputs_agree(claimed, observed, threshold=self._threshold)
        return PlainVote(voter_id, "ACK" if agree else "DISPUTE")

    def decide(self, votes: List[PlainVote]) -> str:
        """Plain majority. Ties resolve to ACCEPTED (the optimistic default)."""
        ack = sum(1 for v in votes if v.decision == "ACK")
        dispute = sum(1 for v in votes if v.decision == "DISPUTE")
        return "ACCEPTED" if ack >= dispute else "REJECTED"
