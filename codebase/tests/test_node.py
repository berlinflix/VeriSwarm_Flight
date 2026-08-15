"""
Live distributed path test: start real AttestationService gRPC servers on
loopback (ephemeral ports), originate over the wire, and check the three
canonical decisions. Exercises server.py + client.py + proto_bridge end to end.
"""

from __future__ import annotations

import pytest

from node import common
from node import server as node_server
from node.client import Originator
from protocol.peer_consensus import ConsensusOutcome


def _formation(ids):
    # originator centred, peers 3 m apart at 14 m -> all co-visible
    return {nid: (0.0, 3.0 * i, 14.0, 0.0) for i, nid in enumerate(ids)}


def _start_peers(manifest, peer_ids):
    servers = []
    for pid in peer_ids:
        manifest["nodes"][pid]["port"] = 0  # OS-assigned; serve() writes it back
        servers.append(node_server.serve(pid, manifest))
    return servers


def _stop(servers):
    for s in servers:
        s.stop(grace=None)


def _run(observations, output, model_hash):
    ids = ["alpha", "bravo", "charlie"]
    man = common.generate_manifest(ids, poses=_formation(ids), observations=observations)
    servers = _start_peers(man, ids[1:])
    try:
        orig = Originator(man, "alpha", ["bravo", "charlie"])
        assert orig.wait_ready(timeout=15), "peer servers never became ready"
        outcome = orig.originate(output=output, model_hash=model_hash)
        orig.close()
        return outcome
    finally:
        _stop(servers)


def test_distributed_honest_accepts():
    obs = {nid: [0.1, 0.0, 0.0] for nid in ("alpha", "bravo", "charlie")}
    r = _run(obs, output=(0.1, 0.0, 0.0), model_hash=common.APPROVED_MODEL)
    assert r.outcome is ConsensusOutcome.ACCEPTED
    assert r.ack_count == 2


def test_distributed_model_swap_rejects():
    obs = {nid: [0.1, 0.0, 0.0] for nid in ("alpha", "bravo", "charlie")}
    r = _run(obs, output=(0.1, 0.0, 0.0), model_hash=common.MALICIOUS_MODEL)
    assert r.outcome is ConsensusOutcome.REJECTED
    assert r.dispute_count == 2


def test_distributed_adversarial_patch_rejects():
    obs = {"alpha": [1.0, 0.0, 0.0], "bravo": [0.0, 0.0, 1.0], "charlie": [0.0, 0.0, 1.0]}
    r = _run(obs, output=(1.0, 0.0, 0.0), model_hash=common.APPROVED_MODEL)
    assert r.outcome is ConsensusOutcome.REJECTED
    assert r.dispute_count == 2
