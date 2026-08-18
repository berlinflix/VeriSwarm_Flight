from __future__ import annotations

import pytest

from perception.depth_check import check_free_space
from perception.claim import PerceptionClaim
from perception.safety_supervisor import HOLD_ACTION, SafetySupervisor
from protocol.peer_consensus import (
    ConsensusOutcome,
    ConsensusResult,
    receipt_digest,
)
from protocol.receipts import build_receipt, sha256_hex


MODEL = sha256_hex(b"supervisor-test-model")


def _receipt(action=(0.5, 0.0, 0.0), *, timestamp_ns=1_000, valid_for_ns=100):
    return build_receipt(
        "alpha",
        b"frame",
        MODEL,
        action,
        timestamp_ns=timestamp_ns,
        valid_for_ns=valid_for_ns,
        perception=PerceptionClaim.from_detections([]),
    )


def _consensus(receipt, outcome=ConsensusOutcome.ACCEPTED, semantic_acks=2):
    ack_count = 2 if outcome is ConsensusOutcome.ACCEPTED else 0
    dispute_count = 1 if outcome is ConsensusOutcome.REJECTED else 0
    semantic_acks = semantic_acks if outcome is ConsensusOutcome.ACCEPTED else 0
    return ConsensusResult(
        outcome=outcome,
        ack_count=ack_count,
        dispute_count=dispute_count,
        missing_count=2 - ack_count - dispute_count,
        ack_threshold=2,
        dispute_threshold=1,
        reason="test",
        semantic_ack_count=semantic_acks,
        target_receipt_hash=receipt_digest(receipt),
    )


def _authorize(
    action=(0.5, 0.0, 0.0),
    *,
    receipt=None,
    outcome=ConsensusOutcome.ACCEPTED,
    semantic_acks=2,
    **overrides,
):
    evidence = receipt or _receipt(action)
    if "forward_clearance" in overrides:
        forward_clearance = overrides.pop("forward_clearance")
    else:
        try:
            forward_clearance = check_free_space(action, 60.0)
        except (TypeError, ValueError, OverflowError):
            forward_clearance = None
    args = {
        "forward_clearance": forward_clearance,
        "perception_healthy": True,
        "state_estimate_healthy": True,
        "geofence_clear": True,
        "autopilot_guard_healthy": True,
        "evidence_receipt": evidence,
        "now_ns": 1_000,
    }
    args.update(overrides)
    return SafetySupervisor().authorize(
        action,
        _consensus(evidence, outcome, semantic_acks),
        **args,
    )


def test_releases_forward_only_with_all_positive_evidence():
    result = _authorize()
    assert result.allowed
    assert result.action == (0.5, 0.0, 0.0)


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"perception_healthy": False}, "perception_unhealthy"),
        ({"state_estimate_healthy": False}, "state_estimate_unhealthy"),
        ({"geofence_clear": False}, "geofence_blocked"),
        ({"autopilot_guard_healthy": False}, "autopilot_guard_unhealthy"),
        ({"operator_abort": True}, "operator_abort"),
        ({"forward_clearance": None}, "forward_clearance_unproven"),
        ({"forward_clearance": check_free_space((1.0, 0.0, 0.0), 2.0)},
         "forward_clearance_unproven"),
    ],
)
def test_any_missing_safety_evidence_holds(overrides, reason):
    result = _authorize(**overrides)
    assert not result.allowed
    assert result.action == HOLD_ACTION
    assert result.reason == reason


def test_crypto_only_accept_does_not_authorize_motion():
    result = _authorize(semantic_acks=0)
    assert not result.allowed
    assert result.reason == "semantic_quorum_missing"


def test_partial_semantic_evidence_cannot_complete_command_quorum():
    result = _authorize(semantic_acks=1)
    assert not result.allowed
    assert result.reason == "semantic_quorum_missing"


@pytest.mark.parametrize("action", [(float("nan"), 0.0, 0.0), (1.1, 0.0, 0.0)])
def test_invalid_actions_fail_closed(action):
    result = _authorize(action, receipt=_receipt(), forward_clearance=None)
    assert not result.allowed
    assert result.action == HOLD_ACTION
    assert result.reason == "invalid_action"


def test_truthy_non_boolean_health_does_not_count_as_evidence():
    result = _authorize(perception_healthy="yes")
    assert not result.allowed
    assert result.reason == "invalid_health_evidence"


@pytest.mark.parametrize(
    "timestamp,valid_for,now,reason",
    [
        (1_000, 100, 1_101, "command_expired"),
        (1_000, 100, 999, "command_expired"),
    ],
)
def test_command_validity_is_rechecked_at_release(timestamp, valid_for, now, reason):
    result = _authorize(
        receipt=_receipt(timestamp_ns=timestamp, valid_for_ns=valid_for),
        now_ns=now,
    )
    assert not result.allowed
    assert result.reason == reason


def test_internally_inconsistent_consensus_is_rejected_at_construction():
    with pytest.raises(ValueError, match="below ACK threshold"):
        ConsensusResult(
            outcome=ConsensusOutcome.ACCEPTED,
            ack_count=1,
            dispute_count=0,
            missing_count=1,
            ack_threshold=2,
            dispute_threshold=1,
            reason="corrupt",
            semantic_ack_count=1,
        )


def test_nonaccepted_consensus_holds():
    result = _authorize(outcome=ConsensusOutcome.NO_QUORUM)
    assert not result.allowed
    assert result.action == HOLD_ACTION


def test_lateral_or_vertical_motion_needs_all_axis_clearance():
    blocked = _authorize(action=(0.0, 0.4, 0.2), forward_clearance=None)
    assert not blocked.allowed
    assert blocked.reason == "all_axis_clearance_unproven"
    allowed = _authorize(
        action=(0.0, 0.4, 0.2),
        forward_clearance=None,
        all_axis_clearance_confirmed=True,
    )
    assert allowed.allowed


def test_missing_evidence_receipt_fails_closed():
    receipt = _receipt()
    result = _authorize(receipt=receipt, evidence_receipt=None)
    assert not result.allowed
    assert result.reason == "evidence_receipt_missing"


def test_consensus_for_another_receipt_cannot_release_command():
    receipt = _receipt()
    other = _receipt(timestamp_ns=1_001)
    result = SafetySupervisor().authorize(
        receipt.output,
        _consensus(other),
        forward_clearance=check_free_space(receipt.output, 60.0),
        perception_healthy=True,
        state_estimate_healthy=True,
        geofence_clear=True,
        autopilot_guard_healthy=True,
        evidence_receipt=receipt,
        now_ns=receipt.timestamp_ns,
    )
    assert not result.allowed
    assert result.reason == "consensus_receipt_mismatch"


def test_receipt_cannot_release_a_different_command():
    receipt = _receipt((0.5, 0.0, 0.0))
    result = _authorize((0.4, 0.0, 0.0), receipt=receipt)
    assert not result.allowed
    assert result.reason == "evidence_command_mismatch"
