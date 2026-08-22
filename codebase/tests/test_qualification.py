"""End-to-end proof of the qualification peer + runner over real gRPC.

Three software nodes in one process exercise the exact code path Alpha/Bravo/
Charlie run on the LAN — the qualification snapshot provider, ``node.server.serve``,
the ``Originator`` round, and the tally — minus only the OP-TEE signer and the
physical network. If Q-CLEAN reaches two semantic ACKs and Q-MODEL-SWAP is
rejected on provenance here, the launchers are correct; the LAN rehearsal is
then about cables, clocks and hardware, not protocol logic.
"""

from __future__ import annotations

import threading

import pytest

from node.client import Originator
from node.common import APPROVED_RUNTIME, generate_manifest
from node.server import serve
from perception.claim import PerceptionClaim
from protocol.geometry import Pose
from protocol.peer_consensus import ConsensusOutcome
from tools.qualification_peer import snapshot_provider_from_fixture

APPROVED = "a" * 64
TAMPERED = "b" * 64

POSES = {
    "alpha": [0.0, 0.0, 14.0, 0.0, 0.0],
    "bravo": [4.0, 0.0, 14.0, 0.0, 0.0],
    "charlie": [-4.0, 0.0, 14.0, 0.0, 0.0],
}


def _empty_scene_fixture(node_id: str) -> dict:
    claim = PerceptionClaim(measured=True, detections_present=False)
    return {
        "node_id": node_id,
        "action": [0.0, 0.0, 0.0],
        "claim": {
            "measured": claim.measured,
            "detections_present": claim.detections_present,
            "detection_count": claim.detection_count,
            "class_ids": list(claim.class_ids),
            "occupancy": claim.occupancy,
            "max_confidence": claim.max_confidence,
            "bearing": claim.bearing,
        },
        "pose_enu": POSES[node_id] + [0.0],
    }


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, event: dict) -> None:
        with self._lock:
            self.events.append(event)

    def vote_reasons(self) -> dict[str, str]:
        return {
            e["node"]: e["reason"]
            for e in self.events
            if e.get("type") == "vote"
        }


@pytest.fixture()
def swarm():
    manifest = generate_manifest(
        ["alpha", "bravo", "charlie"],
        base_port=0,  # ephemeral; serve() writes the bound port back
        host="127.0.0.1",
        poses=POSES,
        approved_models=[APPROVED],
        approved_runtimes=[APPROVED_RUNTIME],
        o_min=0.1,
        phi_min=0.0,
        mode="simulation",
        mission_id="internal-qualifier-2026-08-19",
        mission_epoch=1,
    )
    for nid in manifest["nodes"]:
        manifest["nodes"][nid]["port"] = 0

    sinks = {nid: _Sink() for nid in ("bravo", "charlie")}
    servers = {}
    for nid in ("bravo", "charlie"):
        servers[nid] = serve(
            nid, manifest, on_event=sinks[nid],
            snapshot_provider=snapshot_provider_from_fixture(_empty_scene_fixture(nid)),
        )
    origin = Originator(manifest, "alpha", ["bravo", "charlie"])
    assert origin.wait_ready(timeout=10.0)
    try:
        yield origin, sinks
    finally:
        origin.close()
        for nid, srv in servers.items():
            srv.servicer.close()
            srv.stop(grace=1.0)


def test_q_clean_reaches_two_semantic_acks(swarm):
    origin, sinks = swarm
    claim = PerceptionClaim(measured=True, detections_present=False)
    out = origin.originate(
        output=[0.0, 0.0, 0.0], model_hash=APPROVED,
        input_bytes=b"veriswarm-qualifier:Q-CLEAN",
        pose=Pose(0.0, 0.0, 14.0), perception=claim,
    )
    assert out.outcome is ConsensusOutcome.ACCEPTED
    assert out.ack_count == 2
    assert out.consensus.semantic_ack_count == 2
    assert out.dispute_count == 0
    reasons = {**sinks["bravo"].vote_reasons(), **sinks["charlie"].vote_reasons()}
    assert reasons.get("bravo") == "ok"
    assert reasons.get("charlie") == "ok"


def test_q_model_swap_rejected_on_provenance(swarm):
    origin, sinks = swarm
    claim = PerceptionClaim(measured=True, detections_present=False)
    out = origin.originate(
        output=[0.0, 0.0, 0.0], model_hash=TAMPERED,
        input_bytes=b"veriswarm-qualifier:Q-MODEL-SWAP",
        pose=Pose(0.0, 0.0, 14.0), perception=claim,
    )
    assert out.outcome is ConsensusOutcome.REJECTED
    assert out.dispute_count == 2
    reasons = {**sinks["bravo"].vote_reasons(), **sinks["charlie"].vote_reasons()}
    assert reasons.get("bravo", "").startswith("model_hash_not_approved")
    assert reasons.get("charlie", "").startswith("model_hash_not_approved")


# --- degraded-quorum scenarios -------------------------------------------
# One witness is enough to REFUSE a command; authorising one still needs the
# full quorum. These lock that asymmetry in: a change that let a single ACK
# authorise motion, or that stopped a lone peer from rejecting an unapproved
# model, would break here rather than on stage.

def _serve_subset(manifest, live, sinks):
    return {
        nid: serve(nid, manifest, on_event=sinks[nid],
                   snapshot_provider=snapshot_provider_from_fixture(
                       _empty_scene_fixture(nid)))
        for nid in live
    }


def _degraded_swarm(live):
    manifest = generate_manifest(
        ["alpha", "bravo", "charlie"], base_port=0, host="127.0.0.1", poses=POSES,
        approved_models=[APPROVED], approved_runtimes=[APPROVED_RUNTIME],
        o_min=0.1, phi_min=0.0, mode="simulation",
        mission_id="internal-qualifier-2026-08-19", mission_epoch=1)
    for nid in manifest["nodes"]:
        manifest["nodes"][nid]["port"] = 0
    sinks = {nid: _Sink() for nid in ("bravo", "charlie")}
    servers = _serve_subset(manifest, live, sinks)
    for nid in ("bravo", "charlie"):
        if nid not in live and manifest["nodes"][nid]["port"] == 0:
            manifest["nodes"][nid]["port"] = 59700 + len(nid)  # nothing listening
    return manifest, servers


@pytest.mark.parametrize(
    "live,model_hash,outcome,acks,disputes,missing",
    [
        # one peer down: a lone ACK cannot authorise, a lone DISPUTE can reject
        (("charlie",), APPROVED, ConsensusOutcome.NO_QUORUM, 1, 0, 1),
        (("charlie",), TAMPERED, ConsensusOutcome.REJECTED, 0, 1, 1),
        # no peers: nothing verified, so nothing authorised AND nothing claimed caught
        ((), APPROVED, ConsensusOutcome.NO_QUORUM, 0, 0, 2),
        ((), TAMPERED, ConsensusOutcome.NO_QUORUM, 0, 0, 2),
    ],
)
def test_degraded_quorum_outcomes(live, model_hash, outcome, acks, disputes, missing):
    manifest, servers = _degraded_swarm(live)
    origin = Originator(manifest, "alpha", ["bravo", "charlie"], rpc_timeout_s=1.0)
    try:
        assert origin.wait_ready(timeout=5.0, minimum=len(live))
        out = origin.originate(
            output=[0.0, 0.0, 0.0], model_hash=model_hash,
            input_bytes=b"degraded", pose=Pose(0.0, 0.0, 14.0),
            perception=PerceptionClaim(measured=True, detections_present=False))
        assert out.outcome is outcome
        assert out.ack_count == acks
        assert out.dispute_count == disputes
        assert out.missing_count == missing
    finally:
        origin.close()
        for srv in servers.values():
            srv.servicer.close()
            srv.stop(grace=1.0)


def test_wait_ready_minimum_tolerates_absent_peers():
    """A degraded run must not abort in the readiness gate before it starts."""
    manifest, servers = _degraded_swarm(("charlie",))
    origin = Originator(manifest, "alpha", ["bravo", "charlie"], rpc_timeout_s=1.0)
    try:
        assert origin.wait_ready(timeout=4.0, minimum=1) is True
        assert origin.wait_ready(timeout=0.0, minimum=0) is True
        assert origin.wait_ready(timeout=2.0) is False  # default still requires all
    finally:
        origin.close()
        for srv in servers.values():
            srv.servicer.close()
            srv.stop(grace=1.0)
