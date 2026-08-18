from __future__ import annotations

from types import SimpleNamespace
from dataclasses import replace

import pytest

from node.events import EventLog, read_events, verify_event_chain
from node.frame_source import StaticSource
from node.mission import HealthState, MissionRunner
from perception.claim import PerceptionClaim, PerceptionResult
from protocol.geometry import Pose
from protocol.peer_consensus import ConsensusOutcome, ConsensusResult, receipt_digest
from protocol.receipts import build_receipt, sha256_hex

np = pytest.importorskip("numpy")


def _consensus(semantic_acks=2, outcome=ConsensusOutcome.ACCEPTED):
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


class FakeOriginator:
    def __init__(self, consensus):
        self.consensus = consensus
        self.manifest = {"nodes": {"alpha": {"backend": "simulation"}}}
        self.calls = []

    def originate(self, action, model_hash, **kwargs):
        self.calls.append((action, model_hash, kwargs))
        receipt = build_receipt(
            "alpha",
            kwargs["input_bytes"],
            model_hash,
            action,
            perception=kwargs["perception"],
        )
        return SimpleNamespace(
            consensus=replace(
                self.consensus,
                target_receipt_hash=receipt_digest(receipt),
            ),
            sign_ms=0.1,
            consensus_ms=1.2,
            receipt=receipt,
            completed_at_ns=receipt.timestamp_ns,
        )


def _health(**changes):
    values = {
        "perception_healthy": True,
        "state_estimate_healthy": True,
        "geofence_clear": True,
        "autopilot_guard_healthy": True,
    }
    values.update(changes)
    return HealthState(**values)


def _runner(tmp_path, *, depth=60.0, consensus=None, health=None):
    commands = []
    originator = FakeOriginator(consensus or _consensus())
    runner = MissionRunner(
        node_id="alpha",
        source=StaticSource(
            np.zeros((8, 8, 3), dtype=np.uint8),
            fixed_pose=Pose(0.0, 0.0, 14.0),
            depth=depth,
        ),
        inference=lambda _frame: PerceptionResult(
            (0.5, 0.0, 0.0),
            PerceptionClaim.from_detections([]),
        ),
        originator=originator,
        model_hash=sha256_hex(b"model"),
        health_provider=lambda: health or _health(),
        command_sink=lambda action, auth: commands.append((action, auth)),
        events=EventLog(tmp_path / "mission.jsonl"),
    )
    return runner, originator, commands


def test_integrated_step_releases_a_fully_supported_command(tmp_path):
    runner, originator, commands = _runner(tmp_path)
    step = runner.step()
    assert step.authorization.allowed
    assert commands[-1][0] == (0.5, 0.0, 0.0)
    assert originator.calls[0][2]["input_bytes"].startswith(b"VSFRAME1")
    assert verify_event_chain(tmp_path / "mission.jsonl") == (True, "ok")


@pytest.mark.parametrize(
    "depth,consensus,health,reason",
    [
        (None, None, None, "forward_clearance_unproven"),
        (2.0, None, None, "forward_clearance_unproven"),
        (60.0, _consensus(semantic_acks=0), None, "semantic_quorum_missing"),
        (60.0, _consensus(outcome=ConsensusOutcome.NO_QUORUM), None,
         "consensus_no_quorum"),
        (60.0, None, _health(state_estimate_healthy=False),
         "state_estimate_unhealthy"),
    ],
)
def test_integrated_step_holds_on_any_missing_evidence(
    tmp_path, depth, consensus, health, reason
):
    runner, _, commands = _runner(
        tmp_path, depth=depth, consensus=consensus, health=health
    )
    step = runner.step()
    assert not step.authorization.allowed
    assert step.authorization.reason == reason
    assert commands[-1][0] == (0.0, 0.0, 0.0)
    assert any(e["type"] == "safe_action" for e in read_events(tmp_path / "mission.jsonl"))


@pytest.mark.parametrize("bad_action", [(float("nan"), 0.0, 0.0), (2.0, 0.0, 0.0)])
def test_invalid_inference_action_holds_before_attestation(tmp_path, bad_action):
    runner, originator, commands = _runner(tmp_path)
    runner.inference = lambda _frame: PerceptionResult(
        bad_action,
        PerceptionClaim.from_detections([]),
    )
    step = runner.step()
    assert not step.authorization.allowed
    assert step.authorization.reason == "perception_failure"
    assert commands[-1][0] == (0.0, 0.0, 0.0)
    assert originator.calls == []


def test_action_only_inference_is_rejected_before_attestation(tmp_path):
    runner, originator, commands = _runner(tmp_path)
    runner.inference = lambda _frame: (0.5, 0.0, 0.0)
    step = runner.step()
    assert not step.authorization.allowed
    assert step.authorization.reason == "perception_failure"
    assert originator.calls == []
    assert commands[-1][0] == (0.0, 0.0, 0.0)


def test_frame_source_exception_holds(tmp_path):
    runner, _, commands = _runner(tmp_path)
    runner.source.read = lambda: (_ for _ in ()).throw(RuntimeError("camera lost"))
    step = runner.step()
    assert not step.authorization.allowed
    assert step.authorization.reason == "frame_source_failure"
    assert commands[-1][0] == (0.0, 0.0, 0.0)


def test_attestation_exception_holds(tmp_path):
    runner, originator, commands = _runner(tmp_path)
    originator.originate = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("transport failed")
    )
    step = runner.step()
    assert not step.authorization.allowed
    assert step.authorization.reason == "attestation_failure"
    assert commands[-1][0] == (0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "changes",
    [
        {"perception_healthy": "yes"},
        {"current_speed_mps": float("nan")},
        {"pose_uncertainty_m": -1.0},
    ],
)
def test_health_state_rejects_ambiguous_or_invalid_telemetry(changes):
    with pytest.raises(ValueError):
        _health(**changes)
