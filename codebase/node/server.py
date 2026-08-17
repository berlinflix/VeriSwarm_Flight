"""
AttestationService gRPC server — one runs on each peer drone.

On SubmitReceipt it runs the real `PeerVerifier` (crypto + provenance + the
co-visibility-gated semantic check) and returns this drone's SignedVote. The
originator's signed current pose and a peer's atomic local perception snapshot
feed the C2 gate. Static manifest values remain simulation scaffolding only.

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
import math
from collections import OrderedDict
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
from protocol.geometry import Pose
from . import common


MAX_TRACKED_RECEIPTS = 4096


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
        observation_provider: Optional[Callable[[], object]] = None,
        pose_provider: Optional[Callable[[], Optional[Pose]]] = None,
        snapshot_provider: Optional[Callable[[], object]] = None,
    ):
        self.my_id = my_id
        self.manifest = manifest
        entry = manifest["nodes"][my_id]
        self.signer = common.signer_for(entry)
        self.receipt_verifier = common.receipt_verifier_for(manifest)
        self.vote_verifier = common.vote_verifier_for(manifest)
        self.peer_verifier = PeerVerifier(
            my_id, self.signer, self.receipt_verifier,
            agreement_threshold=float(manifest.get("agreement_threshold", 0.5)),
            o_min=float(manifest.get("o_min", 0.1)),
            phi_min=float(manifest.get("phi_min", 0.0)),
            hfov=math.radians(float(manifest.get("camera_hfov_deg", 69.0))),
            vfov=math.radians(float(manifest.get("camera_vfov_deg", 53.0))),
            on_covis=self._emit_covis,
        )
        self.my_pose = common.pose_of(entry)
        self.observation = entry.get("observation")
        self._observation_provider = observation_provider
        self._pose_provider = pose_provider
        self._snapshot_provider = snapshot_provider
        self.poses = {nid: common.pose_of(e) for nid, e in manifest["nodes"].items()}

        self.roster = set(manifest["nodes"])
        self._on_event = on_event
        self._broadcast = broadcast_votes

        self._vote_cache: Dict[str, object] = {}    # digest -> our own SignedVote
        self._receipts: Dict[str, object] = {}      # digest -> Receipt under vote
        self._votes: OrderedDict[str, Dict[str, object]] = OrderedDict()
        self._verdicts: Dict[str, ConsensusOutcome] = {}  # digest -> last announced
        self._equivocation_evidence: Dict[str, Dict[str, tuple]] = {}
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

    def _trim_locked(self) -> None:
        """Bound attacker-controlled protocol state while holding ``_lock``."""
        while len(self._votes) > MAX_TRACKED_RECEIPTS:
            digest, _ = self._votes.popitem(last=False)
            self._vote_cache.pop(digest, None)
            self._receipts.pop(digest, None)
            self._verdicts.pop(digest, None)
            self._equivocation_evidence.pop(digest, None)

    def SubmitReceipt(self, request, context):
        try:
            signed = signed_receipt_from_pb(request)
        except (KeyError, TypeError, ValueError) as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"invalid_receipt:{exc}")
        digest = receipt_digest(signed.receipt)
        with self._lock:
            cached = self._vote_cache.get(digest)  # idempotent: one receipt, one vote
        if cached is not None:
            return signed_vote_to_pb(cached)

        if self._snapshot_provider is not None:
            try:
                snapshot = self._snapshot_provider()
                captured_ns = int(snapshot.captured_ns)
                max_skew_ns = int(
                    self.manifest.get("max_observation_skew_ns", 250_000_000)
                )
                if abs(signed.receipt.timestamp_ns - captured_ns) > max_skew_ns:
                    raise ValueError("local perception snapshot is not time-aligned")
                my_observation = snapshot.action
                my_pose = snapshot.pose
            except Exception:
                # A missing, malformed, or temporally mismatched snapshot is an
                # abstention. Never inherit the preceding observation.
                my_observation = None
                my_pose = None
        else:
            try:
                my_observation = (
                    self._observation_provider()
                    if self._observation_provider is not None
                    else self.observation
                )
            except Exception:
                my_observation = None
            try:
                my_pose = (
                    self._pose_provider()
                    if self._pose_provider is not None
                    else self.my_pose
                )
            except Exception:
                my_pose = None

        receipt_pose = signed.receipt.pose_enu
        pose_age_ns = abs(
            signed.receipt.timestamp_ns - signed.receipt.pose_timestamp_ns
        )
        max_pose_age_ns = int(self.manifest.get("max_pose_age_ns", 200_000_000))
        max_pose_uncertainty = float(
            self.manifest.get("max_pose_uncertainty_m", 2.0)
        )
        if (
            receipt_pose[2] > 0.0
            and pose_age_ns <= max_pose_age_ns
            and signed.receipt.pose_uncertainty_m <= max_pose_uncertainty
        ):
            originator_pose = Pose(*receipt_pose)
        else:
            originator_pose = (
                self.poses.get(signed.receipt.drone_id)
                if self.manifest.get("mode", "simulation") == "simulation"
                else None
            )
        vote = self.peer_verifier.vote_on(
            signed,
            my_observation=my_observation,
            my_pose=my_pose,
            originator_pose=originator_pose,
        )
        with self._lock:
            self._vote_cache[digest] = vote
            self._receipts[digest] = signed.receipt
            self._votes.setdefault(digest, {})[self.my_id] = vote
            self._votes.move_to_end(digest)
            self._trim_locked()

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
        try:
            sv = signed_vote_from_pb(request)
        except (KeyError, TypeError, ValueError) as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"invalid_vote:{exc}")
        ok = self.vote_verifier.verify(sv)
        reason = "ok" if ok else "bad_signature"
        if ok:
            digest = sv.vote.target_receipt_hash
            with self._lock:
                evidence = self._equivocation_evidence.setdefault(digest, {})
                votes = self._votes.setdefault(digest, {})
                previous = votes.get(sv.vote.voter_id)
                if sv.vote.voter_id in evidence:
                    ok = False
                    reason = "equivocation"
                elif previous is not None and previous.vote != sv.vote:
                    # Preserve any two distinct signed statements as attributable
                    # evidence and count neither. Same-decision votes with
                    # different semantic reasons are security-distinct too.
                    evidence[sv.vote.voter_id] = (previous, sv)
                    votes.pop(sv.vote.voter_id, None)
                    ok = False
                    reason = "equivocation"
                # Exact retries are idempotent and retain the first vote.
                elif previous is None:
                    votes[sv.vote.voter_id] = sv
                self._votes.move_to_end(digest)
                self._trim_locked()
            if reason == "equivocation":
                self._emit(
                    type="equivocation",
                    voter=sv.vote.voter_id,
                    digest=digest[:16],
                )
            self._try_tally(digest)
        return pb.PushVoteAck(accepted=ok, reason=reason)

    def Ping(self, request, context):
        return pb.PingResponse(this_drone_id=self.my_id, timestamp_ns=time.time_ns())

    # -- broadcast + independent tally ---------------------------------------

    def _stub_for(self, peer_id: str):
        with self._lock:
            stub = self._stubs.get(peer_id)
            if stub is None:
                channel = common.channel_for(self.manifest, self.my_id, peer_id)
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
    observation_provider: Optional[Callable[[], object]] = None,
    pose_provider: Optional[Callable[[], Optional[Pose]]] = None,
    snapshot_provider: Optional[Callable[[], object]] = None,
):
    """Start (non-blocking) the AttestationService for `my_id`. Returns the
    grpc server; the manifest entry's port is updated to the actually-bound port
    (so callers in the same process can dial it even when port 0 was requested).

    The servicer is attached to the returned server as `.servicer` so callers can
    read `local_verdict()` and close its broadcast channels."""
    common.validate_manifest(manifest, local_node_id=my_id)
    if manifest.get("mode", "simulation") == "production" and (
        snapshot_provider is None
    ):
        raise ValueError(
            "production server requires an atomic synchronized snapshot_provider"
        )
    entry = manifest["nodes"][my_id]
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=common.GRPC_OPTIONS,
    )
    servicer = AttestationServicer(
        my_id,
        manifest,
        on_event=on_event,
        broadcast_votes=broadcast_votes,
        observation_provider=observation_provider,
        pose_provider=pose_provider,
        snapshot_provider=snapshot_provider,
    )
    pb_grpc.add_AttestationServiceServicer_to_server(servicer, server)
    bound = common.add_server_listener(server, manifest, my_id)
    if bound == 0:
        raise RuntimeError(f"failed to bind AttestationService on port {entry['port']}")
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

    manifest = common.load_manifest(args.manifest, local_node_id=args.id)
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
