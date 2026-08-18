"""Launch one verification peer (Bravo or Charlie) for the internal qualifier.

Why this exists and the plain ``node.server`` CLI does not suffice
------------------------------------------------------------------
``python -m node.server`` starts an attestation endpoint with **no measured
snapshot provider**. It can check a receipt's signature and model provenance,
but with nothing to compare the observation against, the semantic clause falls
back to ``ok_no_observation`` and ``semantic_acks`` stays zero. A clean case that
"passes" with zero semantic acknowledgements has proven nothing about the claim.

This launcher supplies a fresh, timestamped, atomic ``PerceptionSnapshot`` on
every request, replaying the frozen detector-derived fixture for this node. The
snapshot's ``captured_ns`` is stamped at call time, so it stays inside the
server's ``max_observation_skew_ns`` window against the incoming receipt — which
is exactly why the demo clocks must be synchronised: a skewed clock makes the
snapshot look stale and silently drops the peer to an abstention.

It never runs a detector and never touches an actuator. It is a deterministic
replay peer for the protocol slice; the live detector is the separate webcam
beat (``covis_live``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import signal
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from node.live_node import PerceptionSnapshot
from node.server import serve
from perception.claim import PerceptionClaim
from protocol.geometry import Pose


def _fixture_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def claim_from_dict(data: dict) -> PerceptionClaim:
    """Rebuild a PerceptionClaim from fixture JSON, re-running every validator."""
    return PerceptionClaim(
        measured=bool(data["measured"]),
        detections_present=bool(data["detections_present"]),
        detection_count=int(data["detection_count"]),
        class_ids=tuple(int(c) for c in data.get("class_ids", ())),
        occupancy=float(data["occupancy"]),
        max_confidence=float(data["max_confidence"]),
        bearing=float(data["bearing"]),
    )


def snapshot_provider_from_fixture(fixture: dict) -> Callable[[], PerceptionSnapshot]:
    """Return a provider yielding a fresh, time-stamped snapshot each call.

    The action, claim and pose are frozen (the replay); only ``captured_ns`` is
    live, so the server's time-alignment check sees a current observation.
    """
    claim = claim_from_dict(fixture["claim"])
    if not claim.measured:
        raise ValueError("qualification fixture must carry a measured claim")
    action = tuple(float(v) for v in fixture["action"])
    if len(action) != 3:
        raise ValueError("fixture action must be a 3-tuple")
    pose_vals = list(fixture["pose_enu"])
    pose = Pose(*pose_vals[:5]) if pose_vals else None
    frame_tag = "replay:" + hashlib.sha256(
        json.dumps(fixture, sort_keys=True).encode()
    ).hexdigest()

    def _provider() -> PerceptionSnapshot:
        return PerceptionSnapshot(
            frame=None,
            frame_hash=frame_tag,
            action=action,  # type: ignore[arg-type]
            claim=claim,
            pose=pose,
            depth=None,
            captured_ns=time.time_ns(),
        )

    return _provider


class _JsonlEvents:
    """Minimal schema-compliant JSONL event sink (seq, t, type, ...)."""

    def __init__(self, path: Optional[Path]):
        self._path = path
        self._seq = 0
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: dict) -> None:
        if self._path is None:
            return
        with self._lock:
            self._seq += 1
            record = {"seq": self._seq, "t": event.get("t", time.time_ns() // 1_000_000)}
            record.update(event)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")


def _load_manifest(path: Path, node_id: str) -> dict:
    # Deliberately not node.common.load_manifest: the scoped file legitimately
    # contains only this node's seed, which validate_manifest accepts, but we
    # want a clear error naming the node if the wrong scoped file is supplied.
    manifest = json.loads(path.read_text())
    if node_id not in manifest.get("nodes", {}):
        raise SystemExit(f"manifest has no node {node_id!r}: wrong scoped file?")
    if "seed" not in manifest["nodes"][node_id]:
        raise SystemExit(
            f"{node_id} scoped manifest carries no seed; cannot sign as {node_id}"
        )
    return manifest


def run_peer(
    *, manifest_path: Path, node_id: str, fixture_path: Path,
    events_path: Optional[Path], verbose: bool,
) -> None:
    manifest = _load_manifest(manifest_path, node_id)
    fixture = json.loads(fixture_path.read_text())
    provider = snapshot_provider_from_fixture(fixture)
    # Prove the provider yields a valid snapshot before we advertise READY.
    probe = provider()
    events = _JsonlEvents(events_path)

    server = serve(node_id, manifest, on_event=events, snapshot_provider=provider)
    port = manifest["nodes"][node_id]["port"]
    print(
        f"READY {node_id} on :{port} | fixture={fixture_path.name} "
        f"sha256={_fixture_sha256(fixture_path)[:16]} | "
        f"claim={probe.claim.describe()}",
        flush=True,
    )
    if verbose:
        print(
            f"      pubkey={manifest['nodes'][node_id]['pubkey'][:16]} "
            f"pose={probe.pose} action={probe.action}",
            flush=True,
        )

    stop = threading.Event()

    def _handle(signum, frame):  # noqa: ARG001
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    try:
        signal.signal(signal.SIGTERM, _handle)
    except (ValueError, AttributeError):
        pass  # SIGTERM not settable on some platforms/threads

    try:
        stop.wait()
    finally:
        print(f"stopping {node_id}...", flush=True)
        server.servicer.close()
        server.stop(grace=2.0).wait(timeout=5.0)
        print(f"{node_id} stopped; port {port} released", flush=True)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Run one qualification verification peer")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--id", required=True, choices=["bravo", "charlie", "alpha"])
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--events", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    run_peer(
        manifest_path=Path(args.manifest).expanduser().resolve(strict=True),
        node_id=args.id,
        fixture_path=Path(args.fixture).expanduser().resolve(strict=True),
        events_path=Path(args.events).expanduser().resolve() if args.events else None,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    _main()
