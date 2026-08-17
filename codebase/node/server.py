"""
AttestationService gRPC server — one runs on each peer drone.

On SubmitReceipt it runs the real `PeerVerifier` (crypto + provenance + the
co-visibility-gated semantic check) and returns this drone's SignedVote. The
originator's pose is looked up from the manifest by the receipt's drone_id, so
the C2 gate engages exactly as in the in-process protocol.

Independent tallies
-------------------
A vote is also *broadcast* to every other peer via PushVote, and each node folds
the votes it receives into its own `ConsensusEngine` tally. Every node therefore
reaches the verdict from the signed votes it holds itself, rather than being told
the verdict by the drone whose receipt is under scrutiny.

This closes an obvious question: if only the originator tallies, what stops a
compromised originator from collecting three DISPUTEs and announcing ACCEPTED?
Nothing does — its peers would never know. With the broadcast, they all hold the
same signed votes and compute the same outcome, and the originator's claim is
just one of N answers that must agree. The votes were always signed and
attributable; what was missing was anyone else adding them up.

The tally remains deterministic given the same vote set, so agreement is the
expected case and divergence is itself a signal (a node that saw a different
vote set, or is lying about what it saw).

Run one node:
    python -m node.server --manifest manifest.json --id bravo
"""

from __future__ import annotations

import threading
import time
from concurrent import futures
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

import grpc

from protocol import attestation_pb2 as pb
from protocol import attestation_pb2_grpc as pb_grpc
from protocol.proto_bridge import (
    signed_receipt_from_pb,
    signed_vote_from_pb,
    signed_vote_to_pb,
)
from protocol.peer_consensus import (
    ConsensusEngine,
    ConsensusOutcome,
    PeerVerifier,
    receipt_digest,
)
from . import common


class AttestationServicer(pb_grpc.AttestationServiceServicer):
    """
    One drone's attestation endpoint.

    `on_event` receives structured dicts for anything worth showing an operator:
    the vote this node cast, the co-visibility decision behind it, and this
    node's own independent verdict. It is optional and defaults to nothing, so
    the server has no dependency on whatever consumes the events.
    """

    def __init__(
        self,
        my_id: str,
        manifest: dict,
        *,
        on_event: Optional[Callable[[dict], None]] = None,
        broadcast_votes: bool = True,
    ):
        self.my_id = my_id
        self.manifest = manifest
        entry = manifest["nodes"][my_id]
        self.signer = common.signer_for(entry)
        self.receipt_verifier = common.receipt_verifier_for(manifest)
        self.vote_verifier = common.vote_verifier_for(manifest)
        self.peer_verifier = PeerVerifier(
            my_id, self.signer, self.receipt_verifier,
            o_min=float(manifest.get("o_min", 0.1)),
            phi_min=float(manifest.get("phi_min", 0.0)),
            on_covis=self._emit_covis,
        )
        self.my_pose = common.pose_of(entry)
        self.observation = entry.get("observation")
        self.poses = {nid: common.pose_of(e) for nid, e in manifest["nodes"].items()}

        self.roster = set(manifest["nodes"])
        self._on_event = on_event
        self._broadcast = broadcast_votes

        self._vote_cache: Dict[str, object] = {}    # digest -> our own SignedVote
        self._receipts: Dict[str, object] = {}      # digest -> Receipt under vote
        self._votes: Dict[str, Dict[str, object]] = {}   # digest -> voter_id -> SignedVote
        self._verdicts: Dict[str, ConsensusOutcome] = {}  # digest -> last announced
        self._lock = threading.Lock()

        # Lazily-built stubs for vote broadcast. Built on demand because peers
        # come up in an arbitrary order and a channel to a dead peer is useless.
        self._stubs: Dict[str, object] = {}
        self._channels: Dict[str, object] = {}
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, len(self.roster) - 1),
            thread_name_prefix=f"vs-bcast-{my_id}",
        )

    # -- events -------------------------------------------------------------

    def _emit(self, **fields) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event({"t": time.time_ns() // 1_000_000, "node": self.my_id, **fields})
        except Exception:
            pass  # telemetry must never take down a voting node

    def _emit_covis(self, diag) -> None:
        self._emit(
            type="covisibility",
            covisible=diag.covisible,
            method=diag.method,
            iou=diag.iou,
            orb_inliers=diag.orb_inliers,
            o_min=diag.o_min,
            parallax_deg=diag.parallax_deg,
            phi_min=diag.phi_min,
            detail=diag.describe(),
        )

    # -- RPCs ---------------------------------------------------------------

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
            self._receipts[digest] = signed.receipt
            self._votes.setdefault(digest, {})[self.my_id] = vote

        self._emit(
            type="vote", target=signed.receipt.drone_id,
            decision=vote.vote.decision.value, reason=vote.vote.reason,
            digest=digest[:16],
        )

        if self._broadcast:
            # Fire and forget: the reply to this RPC is the originator's copy of
            # the vote, so the broadcast is purely for the *other* peers and must
            # not delay the response or fail the call.
            self._pool.submit(self._broadcast_vote, vote, signed.receipt.drone_id)

        self._try_tally(digest)
        return signed_vote_to_pb(vote)

    def PushVote(self, request, context):
        sv = signed_vote_from_pb(request)
        ok = self.vote_verifier.verify(sv)
        if ok:
            digest = sv.vote.target_receipt_hash
            with self._lock:
                self._votes.setdefault(digest, {})[sv.vote.voter_id] = sv
            self._try_tally(digest)
        return pb.PushVoteAck(accepted=ok, reason="ok" if ok else "bad_signature")

    def Ping(self, request, context):
        return pb.PingResponse(this_drone_id=self.my_id, timestamp_ns=time.time_ns())

    # -- broadcast + independent tally ---------------------------------------

    def _stub_for(self, peer_id: str):
        with self._lock:
            stub = self._stubs.get(peer_id)
            if stub is None:
                channel = grpc.insecure_channel(common.address_of(self.manifest, peer_id))
                self._channels[peer_id] = channel
                stub = pb_grpc.AttestationServiceStub(channel)
                self._stubs[peer_id] = stub
            return stub

    def _broadcast_vote(self, vote, originator_id: str) -> None:
        """Push our vote to every peer except ourselves and the originator.

        The originator is skipped because it already receives this vote as the
        SubmitReceipt reply; sending it twice would be redundant, and the tally
        deduplicates by voter anyway.
        """
        pb_vote = signed_vote_to_pb(vote)
        for peer_id in self.roster - {self.my_id, originator_id}:
            try:
                self._stub_for(peer_id).PushVote(pb_vote, timeout=2.0)
            except grpc.RpcError:
                pass  # a peer that is down is the drop case the tally handles

    def _try_tally(self, digest: str) -> None:
        """
        Tally the votes this node holds for `digest`, from its own copy of the
        receipt. Emits a verdict event the first time the outcome becomes
        decisive, and again if it ever changes as late votes arrive.
        """
        with self._lock:
            receipt = self._receipts.get(digest)
            votes = list(self._votes.get(digest, {}).values())
            already = self._verdicts.get(digest)
        if receipt is None or not votes:
            return  # votes for a receipt we have not seen yet; keep them buffered

        engine = ConsensusEngine(num_peers=max(1, len(self.roster) - 1))
        result = engine.tally(
            receipt, votes, self.vote_verifier, expected_voters=self.roster
        )
        if result.outcome is already:
            return
        with self._lock:
            self._verdicts[digest] = result.outcome
        self._emit(
            type="verdict", target=receipt.drone_id, digest=digest[:16],
            outcome=result.outcome.value,
            acks=result.ack_count, disputes=result.dispute_count,
            semantic_acks=result.semantic_ack_count,
            tallied_by=self.my_id,
        )

    def local_verdict(self, digest: str) -> Optional[ConsensusOutcome]:
        """This node's own verdict on a receipt, or None if it has not decided."""
        with self._lock:
            return self._verdicts.get(digest)

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        for channel in self._channels.values():
            channel.close()


def serve(
    my_id: str,
    manifest: dict,
    *,
    max_workers: int = 8,
    on_event: Optional[Callable[[dict], None]] = None,
    broadcast_votes: bool = True,
):
    """Start (non-blocking) the AttestationService for `my_id`. Returns the
    grpc server; the manifest entry's port is updated to the actually-bound port
    (so callers in the same process can dial it even when port 0 was requested).

    The servicer is attached to the returned server as `.servicer` so callers can
    read `local_verdict()` and close its broadcast channels."""
    entry = manifest["nodes"][my_id]
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    servicer = AttestationServicer(
        my_id, manifest, on_event=on_event, broadcast_votes=broadcast_votes
    )
    pb_grpc.add_AttestationServiceServicer_to_server(servicer, server)
    bound = server.add_insecure_port(f"0.0.0.0:{entry['port']}")
    entry["port"] = bound
    server.start()
    server.servicer = servicer
    return server


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Run one VeriSwarm attestation node.")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--no-broadcast", action="store_true",
                    help="do not push votes to peers (originator-only tally)")
    ap.add_argument("--verbose", action="store_true",
                    help="print vote / co-visibility / verdict events to stdout")
    args = ap.parse_args()

    manifest = common.load_manifest(args.manifest)
    on_event = (lambda e: print(f"[{args.id}] {e}", flush=True)) if args.verbose else None
    server = serve(args.id, manifest, on_event=on_event,
                   broadcast_votes=not args.no_broadcast)
    entry = manifest["nodes"][args.id]
    print(f"[{args.id}] AttestationService on {entry['host']}:{entry['port']}", flush=True)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(grace=1.0)


if __name__ == "__main__":
    _main()
