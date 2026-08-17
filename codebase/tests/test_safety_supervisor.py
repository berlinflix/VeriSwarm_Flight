from __future__ import annotations

import pytest

from perception.depth_check import check_free_space
from perception.safety_supervisor import HOLD_ACTION, SafetySupervisor
from protocol.peer_consensus import ConsensusOutcome, ConsensusResult


def _consensus(outcome=ConsensusOutcome.ACCEPTED, semantic_acks=2):
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
    )


def _authorize(action=(0.5, 0.0, 0.0), **overrides):
    args = {
        "forward_clearance": check_free_space(action, 60.0),
        "perception_healthy": True,
        "state_estimate_healthy": True,
        "geofence_clear": True,
        "autopilot_guard_healthy": True,
        "command_timestamp_ns": 1_000,
        "command_valid_for_ns": 100,
        "now_ns": 1_000,
    }
    args.update(overrides)
    return SafetySupervisor().authorize(action, _consensus(), **args)


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
    supervisor = SafetySupervisor()
    result = supervisor.authorize(
        (0.5, 0.0, 0.0),
        _consensus(semantic_acks=0),
        forward_clearance=check_free_space((0.5, 0.0, 0.0), 60.0),
        perception_healthy=True,
        state_estimate_healthy=True,
        geofence_clear=True,
        autopilot_guard_healthy=True,
        command_timestamp_ns=1_000,
        command_valid_for_ns=100,
        now_ns=1_000,
    )
    assert not result.allowed
    assert result.reason == "semantic_quorum_missing"


def test_partial_semantic_evidence_cannot_complete_command_quorum():
    result = SafetySupervisor().authorize(
        (0.5, 0.0, 0.0),
        _consensus(semantic_acks=1),
        forward_clearance=check_free_space((0.5, 0.0, 0.0), 60.0),
        perception_healthy=True,
        state_estimate_healthy=True,
        geofence_clear=True,
        autopilot_guard_healthy=True,
        command_timestamp_ns=1_000,
        command_valid_for_ns=100,
        now_ns=1_000,
    )
    assert not result.allowed
    assert result.reason == "semantic_quorum_missing"


@pytest.mark.parametrize("action", [(float("nan"), 0.0, 0.0), (1.1, 0.0, 0.0)])
def test_invalid_actions_fail_closed(action):
    result = SafetySupervisor().authorize(
        action,
        _consensus(),
        forward_clearance=None,
        perception_healthy=True,
        state_estimate_healthy=True,
        geofence_clear=True,
        autopilot_guard_healthy=True,
        command_timestamp_ns=1_000,
        command_valid_for_ns=100,
        now_ns=1_000,
    )
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
        (None, 100, 1_000, "command_freshness_unproven"),
        (1_000, None, 1_000, "command_freshness_unproven"),
        (1_000, 100, 1_101, "command_expired"),
        (1_000, 100, 999, "command_expired"),
    ],
)
def test_command_validity_is_rechecked_at_release(timestamp, valid_for, now, reason):
    result = _authorize(
        command_timestamp_ns=timestamp,
        command_valid_for_ns=valid_for,
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
    supervisor = SafetySupervisor()
    result = supervisor.authorize(
        (0.5, 0.0, 0.0),
        _consensus(ConsensusOutcome.NO_QUORUM),
        forward_clearance=check_free_space((0.5, 0.0, 0.0), 60.0),
        perception_healthy=True,
        state_estimate_healthy=True,
        geofence_clear=True,
        autopilot_guard_healthy=True,
        command_timestamp_ns=1_000,
        command_valid_for_ns=100,
        now_ns=1_000,
    )
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
