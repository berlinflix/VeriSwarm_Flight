"""Fail-closed authorization between AI consensus and flight-control output.

Consensus is evidence, not an actuator command. This small supervisor is the
mandatory choke point: it releases a normalized velocity request only when the
receipt obtained semantic quorum and independent vehicle-health and clearance
checks are positive. Every unknown condition maps to HOLD.

The returned action is still a request to the autopilot, not a motor command.
The autopilot must independently enforce attitude, speed, geofence, collision,
battery, and return/landing constraints.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Sequence, Tuple

from protocol.peer_consensus import (
    ConsensusOutcome,
    ConsensusResult,
    receipt_digest,
)
from protocol.receipts import Receipt

from .depth_check import FreeSpaceCheck

Action = Tuple[float, float, float]
HOLD_ACTION: Action = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Authorization:
    """Auditable result of the final software authorization gate."""

    allowed: bool
    action: Action
    reason: str


class SafetySupervisor:
    """Release only commands supported by independent, current evidence."""

    def __init__(self, min_semantic_acks: int = 1):
        if min_semantic_acks < 1:
            raise ValueError("min_semantic_acks must be >= 1")
        self.min_semantic_acks = int(min_semantic_acks)

    @staticmethod
    def _action(requested: Sequence[float]) -> Action:
        action = tuple(float(v) for v in requested)
        if len(action) != 3:
            raise ValueError("requested action must have three components")
        if not all(math.isfinite(v) and -1.0 <= v <= 1.0 for v in action):
            raise ValueError("requested action values must be finite and in [-1, 1]")
        return action  # type: ignore[return-value]

    def authorize(
        self,
        requested: Sequence[float],
        consensus: ConsensusResult,
        *,
        forward_clearance: FreeSpaceCheck | None,
        perception_healthy: bool,
        state_estimate_healthy: bool,
        geofence_clear: bool,
        autopilot_guard_healthy: bool,
        all_axis_clearance_confirmed: bool = False,
        operator_abort: bool = False,
        evidence_receipt: Receipt | None,
        now_ns: int | None = None,
    ) -> Authorization:
        try:
            action = self._action(requested)
        except (TypeError, ValueError, OverflowError):
            return Authorization(False, HOLD_ACTION, "invalid_action")

        if not isinstance(consensus, ConsensusResult):
            return Authorization(False, HOLD_ACTION, "invalid_consensus")

        # Bind the local consensus result to the exact signed receipt it tallied,
        # then bind the released command to that receipt's exact output. Claude's
        # earlier pair of caller-supplied CommandBinding objects compared only to
        # each other, was optional, and contained no command digest; two matching
        # fabricated objects therefore proved nothing.
        if not isinstance(evidence_receipt, Receipt):
            return Authorization(False, HOLD_ACTION, "evidence_receipt_missing")
        if not consensus.target_receipt_hash or not (
            consensus.target_receipt_hash == receipt_digest(evidence_receipt)
        ):
            return Authorization(False, HOLD_ACTION, "consensus_receipt_mismatch")
        if action != tuple(evidence_receipt.output):
            return Authorization(False, HOLD_ACTION, "evidence_command_mismatch")
        if not evidence_receipt.perception.measured:
            return Authorization(False, HOLD_ACTION, "perception_claim_missing")

        checked_now_ns = time.time_ns() if now_ns is None else now_ns
        if type(checked_now_ns) is not int:
            return Authorization(False, HOLD_ACTION, "command_freshness_unproven")
        command_age_ns = checked_now_ns - evidence_receipt.timestamp_ns
        if command_age_ns < 0 or command_age_ns > evidence_receipt.valid_for_ns:
            return Authorization(False, HOLD_ACTION, "command_expired")

        health_flags = (
            perception_healthy,
            state_estimate_healthy,
            geofence_clear,
            autopilot_guard_healthy,
            all_axis_clearance_confirmed,
            operator_abort,
        )
        if not all(type(flag) is bool for flag in health_flags):
            return Authorization(False, HOLD_ACTION, "invalid_health_evidence")

        if operator_abort:
            return Authorization(False, HOLD_ACTION, "operator_abort")
        if not autopilot_guard_healthy:
            return Authorization(False, HOLD_ACTION, "autopilot_guard_unhealthy")
        if not state_estimate_healthy:
            return Authorization(False, HOLD_ACTION, "state_estimate_unhealthy")
        if not perception_healthy:
            return Authorization(False, HOLD_ACTION, "perception_unhealthy")
        if not geofence_clear:
            return Authorization(False, HOLD_ACTION, "geofence_blocked")
        if consensus.outcome is not ConsensusOutcome.ACCEPTED:
            return Authorization(False, HOLD_ACTION, f"consensus_{consensus.outcome.value.lower()}")
        # Every ACK needed for the consensus safety threshold must also carry
        # semantic evidence. Requiring only one semantic ACK would let the one
        # Byzantine peer in a five-node formation provide the sole scene check
        # while two crypto-only ACKs complete the command quorum.
        semantic_threshold = max(self.min_semantic_acks, consensus.ack_threshold)
        if consensus.semantic_ack_count < semantic_threshold:
            return Authorization(False, HOLD_ACTION, "semantic_quorum_missing")

        # A boresighted range measurement only supports positive-forward motion.
        # Any reverse/lateral/vertical request needs an independent all-axis
        # collision layer (e.g. a validated depth/lidar occupancy envelope).
        if action[0] > 0.0:
            if not isinstance(forward_clearance, FreeSpaceCheck) or not (
                forward_clearance.safe_to_proceed
            ):
                return Authorization(False, HOLD_ACTION, "forward_clearance_unproven")
        if (action[0] < 0.0 or action[1] != 0.0 or action[2] != 0.0) and not (
            all_axis_clearance_confirmed
        ):
            return Authorization(False, HOLD_ACTION, "all_axis_clearance_unproven")

        return Authorization(True, action, "authorized")
