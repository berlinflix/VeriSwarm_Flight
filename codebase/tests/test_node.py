"""
Live distributed path test: start real AttestationService gRPC servers on
loopback (ephemeral ports), originate over the wire, and check the three
canonical decisions. Exercises server.py + client.py + proto_bridge end to end.

Also covers the vote broadcast: each peer pushes its signed vote to the other
peers and tallies independently, so the verdict does not rest on the word of the
drone whose receipt is under scrutiny.
"""

from __future__ import annotations

import time

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
        s.servicer.close()
        s.stop(grace=None)


def _run(observations, output, model_hash, servers_out=None):
    ids = ["alpha", "bravo", "charlie"]
    man = common.generate_manifest(ids, poses=_formation(ids), observations=observations)
    servers = _start_peers(man, ids[1:])
    try:
        orig = Originator(man, "alpha", ["bravo", "charlie"])
        assert orig.wait_ready(timeout=15), "peer servers never became ready"
        outcome = orig.originate(output=output, model_hash=model_hash)
        orig.close()
        if servers_out is not None:
            # Broadcast is fire-and-forget; give the pushes a moment to land.
            _await_verdicts(servers, timeout=5.0)
            servers_out.extend(servers)
        return outcome
    finally:
        _stop(servers)


def _await_verdicts(servers, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(s.servicer._verdicts for s in servers):
            return
        time.sleep(0.05)


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


# ---------------------------------------------------------------------------
# Vote broadcast: peers reach the verdict themselves
# ---------------------------------------------------------------------------


def test_peers_tally_independently_and_agree_with_the_originator():
    """
    Each peer holds both signed votes and computes REJECTED on its own. Nothing
    here is taken on the originator's word — which is the point, since the
    originator is the drone under scrutiny.
    """
    obs = {"alpha": [1.0, 0.0, 0.0], "bravo": [0.0, 0.0, 1.0], "charlie": [0.0, 0.0, 1.0]}
    servers = []
    r = _run(obs, output=(1.0, 0.0, 0.0), model_hash=common.APPROVED_MODEL,
             servers_out=servers)

    verdicts = [list(s.servicer._verdicts.values()) for s in servers]
    assert all(v for v in verdicts), "every peer must reach its own verdict"
    assert all(v[0] is ConsensusOutcome.REJECTED for v in verdicts)
    assert all(v[0] is r.outcome for v in verdicts)


def test_each_peer_collects_the_full_vote_set():
    """A peer sees its own vote plus the other peer's, pushed over PushVote."""
    obs = {nid: [0.1, 0.0, 0.0] for nid in ("alpha", "bravo", "charlie")}
    servers = []
    _run(obs, output=(0.1, 0.0, 0.0), model_hash=common.APPROVED_MODEL,
         servers_out=servers)

    for s in servers:
        by_digest = list(s.servicer._votes.values())
        assert by_digest, "peer recorded no votes"
        voters = set(by_digest[0])
        assert voters == {"bravo", "charlie"}, f"{s.servicer.my_id} saw {voters}"


def test_pushed_vote_with_a_bad_signature_is_refused():
    """PushVote is a real ingress now, so it has to reject forged votes."""
    from protocol.peer_consensus import PeerVote, SignedVote, Vote
    from protocol.proto_bridge import signed_vote_to_pb

    ids = ["alpha", "bravo", "charlie"]
    man = common.generate_manifest(ids, poses=_formation(ids))
    servers = _start_peers(man, ["bravo"])
    try:
        forged = SignedVote(
            vote=PeerVote(
                voter_id="charlie", target_receipt_hash="a" * 64,
                decision=Vote.ACK, reason="ok", timestamp_ns=1,
            ),
            signature="00" * 64,
        )
        ack = servers[0].servicer.PushVote(signed_vote_to_pb(forged), None)

        assert not ack.accepted
        assert ack.reason == "bad_signature"
        assert not servers[0].servicer._votes
    finally:
        _stop(servers)
