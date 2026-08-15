"""
AttestationService gRPC server — one runs on each peer drone.

On SubmitReceipt it runs the real `PeerVerifier` (crypto + provenance + the
co-visibility-gated semantic check) and returns this drone's SignedVote. The
originator's pose is looked up from the manifest by the receipt's drone_id, so
the C2 gate engages exactly as in the in-process protocol. PushVote folds a
peer-broadcast vote into a local buffer; Ping is a liveness probe.

Run one node:
    python -m node.server --manifest manifest.json --id bravo
"""

from __future__ import annotations

import threading
import time
from concurrent import futures

import grpc

from protocol import attestation_pb2 as pb
from protocol import attestation_pb2_grpc as pb_grpc
from protocol.proto_bridge import (
    signed_receipt_from_pb,
    signed_vote_from_pb,
    signed_vote_to_pb,
)
from protocol.peer_consensus import PeerVerifier, receipt_digest
from . import common


class AttestationServicer(pb_grpc.AttestationServiceServicer):
    def __init__(self, my_id: str, manifest: dict):
        self.my_id = my_id
        self.manifest = manifest
        entry = manifest["nodes"][my_id]
        self.signer = common.signer_for(entry)
        self.receipt_verifier = common.receipt_verifier_for(manifest)
        self.vote_verifier = common.vote_verifier_for(manifest)
        self.peer_verifier = PeerVerifier(
            my_id, self.signer, self.receipt_verifier,
            o_min=float(manifest.get("o_min", 0.1)),
        )
        self.my_pose = common.pose_of(entry)
        self.observation = entry.get("observation")
        self.poses = {nid: common.pose_of(e) for nid, e in manifest["nodes"].items()}
        self._vote_cache: dict[str, object] = {}
        self._pushed_votes: list = []
        self._lock = threading.Lock()

    def SubmitReceipt(self, request, context):
        signed = signed_receipt_from_pb(request)
        digest = receipt_digest(signed.receipt)
        with self._lock:
            cached = self._vote_cache.get(digest)  # idempotent: one receipt, one vote
        if cached is not None:
            return signed_vote_to_pb(cached)

        originator_pose = self.poses.get(signed.receipt.drone_id)
        vote = self.peer_verifier.vote_on(
            signed,
            my_observation=self.observation,
            my_pose=self.my_pose,
            originator_pose=originator_pose,
        )
        with self._lock:
            self._vote_cache[digest] = vote
        return signed_vote_to_pb(vote)

    def PushVote(self, request, context):
        sv = signed_vote_from_pb(request)
        ok = self.vote_verifier.verify(sv)
        if ok:
            with self._lock:
                self._pushed_votes.append(sv)
        return pb.PushVoteAck(accepted=ok, reason="ok" if ok else "bad_signature")

    def Ping(self, request, context):
        return pb.PingResponse(this_drone_id=self.my_id, timestamp_ns=time.time_ns())


def serve(my_id: str, manifest: dict, *, max_workers: int = 8):
    """Start (non-blocking) the AttestationService for `my_id`. Returns the
    grpc server; the manifest entry's port is updated to the actually-bound port
    (so callers in the same process can dial it even when port 0 was requested)."""
    entry = manifest["nodes"][my_id]
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb_grpc.add_AttestationServiceServicer_to_server(
        AttestationServicer(my_id, manifest), server
    )
    bound = server.add_insecure_port(f"0.0.0.0:{entry['port']}")
    entry["port"] = bound
    server.start()
    return server


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Run one VeriSwarm attestation node.")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--id", required=True)
    args = ap.parse_args()

    manifest = common.load_manifest(args.manifest)
    server = serve(args.id, manifest)
    entry = manifest["nodes"][args.id]
    print(f"[{args.id}] AttestationService on {entry['host']}:{entry['port']}", flush=True)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(grace=1.0)


if __name__ == "__main__":
    _main()
