"""
Originator client: signs a receipt, broadcasts it to peer AttestationService
servers over gRPC, collects their signed votes, and tallies the real
ConsensusEngine decision. Timings separate the signing cost from the
network+verify+tally consensus cost.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Sequence

import grpc

from protocol import attestation_pb2_grpc as pb_grpc
from protocol.proto_bridge import signed_receipt_to_pb, signed_vote_from_pb
from protocol.peer_consensus import ConsensusEngine, ConsensusOutcome, SignedVote
from protocol.receipts import build_receipt
from . import common


@dataclass
class RoundOutcome:
    outcome: ConsensusOutcome
    ack_count: int
    dispute_count: int
    missing_count: int
    sign_ms: float       # build + sign the receipt
    consensus_ms: float  # broadcast + collect votes + tally (the distributed cost)
    total_ms: float
    reason: str


class Originator:
    """Drives one drone (the receipt originator) against its live peers."""

    def __init__(self, manifest: dict, originator_id: str, peer_ids: Sequence[str]):
        self.manifest = manifest
        self.originator_id = originator_id
        self.peer_ids = list(peer_ids)
        self.signer = common.signer_for(manifest["nodes"][originator_id])
        self.vote_verifier = common.vote_verifier_for(manifest)
        self.engine = ConsensusEngine(num_peers=len(self.peer_ids))
        self.roster = set(manifest["nodes"].keys())
        self._channels = {
            pid: grpc.insecure_channel(common.address_of(manifest, pid))
            for pid in self.peer_ids
        }
        self._stubs = {
            pid: pb_grpc.AttestationServiceStub(ch) for pid, ch in self._channels.items()
        }
        self._pool = ThreadPoolExecutor(max_workers=max(1, len(self.peer_ids)))

    def wait_ready(self, timeout: float = 20.0) -> bool:
        """Block until every peer answers Ping (or timeout)."""
        from protocol import attestation_pb2 as pb

        deadline = time.time() + timeout
        pending = set(self.peer_ids)
        while pending and time.time() < deadline:
            for pid in list(pending):
                try:
                    self._stubs[pid].Ping(
                        pb.PingRequest(from_drone_id=self.originator_id), timeout=2.0
                    )
                    pending.discard(pid)
                except grpc.RpcError:
                    time.sleep(0.2)
        return not pending

    def originate(self, output: Sequence[float], model_hash: str) -> RoundOutcome:
        t0 = time.perf_counter_ns()
        receipt = build_receipt(
            drone_id=self.originator_id,
            input_bytes=b"\x00" * 128,
            model_hash=model_hash,
            output=tuple(output),
        )
        signed = self.signer.sign(receipt)
        t1 = time.perf_counter_ns()

        pb_sr = signed_receipt_to_pb(signed)
        futs = {
            pid: self._pool.submit(self._stubs[pid].SubmitReceipt, pb_sr)
            for pid in self.peer_ids
        }
        votes: List[SignedVote] = []
        for pid, fut in futs.items():
            try:
                votes.append(signed_vote_from_pb(fut.result(timeout=15.0)))
            except grpc.RpcError:
                pass  # a missing vote is exactly the drop case the tally handles
        result = self.engine.tally(
            receipt, votes, self.vote_verifier, expected_voters=self.roster
        )
        t2 = time.perf_counter_ns()
        return RoundOutcome(
            outcome=result.outcome,
            ack_count=result.ack_count,
            dispute_count=result.dispute_count,
            missing_count=result.missing_count,
            sign_ms=(t1 - t0) / 1e6,
            consensus_ms=(t2 - t1) / 1e6,
            total_ms=(t2 - t0) / 1e6,
            reason=result.reason,
        )

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        for ch in self._channels.values():
            ch.close()
