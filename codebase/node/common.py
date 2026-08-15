"""
Manifest handling and node construction for the live distributed runtime.

A *manifest* is the deployment roster every node is given at mission start (in a
real system, a ground-station-signed file). It lists, per drone:
  host, port            - where its AttestationService listens
  pubkey                - Ed25519 public key (so peers can verify it)
  seed                  - Ed25519 seed for a software signer (omitted for a
                          hardware/OP-TEE node, whose key lives in the secure
                          element)
  backend               - "software" | "optee"
  pose                  - [x, y, z, yaw] world pose, for the co-visibility gate
  observation           - the action this drone perceives for the current scene
                          (the experiment's stand-in for live per-drone perception)

Building signers and verifiers from a manifest reuses the exact `protocol/`
classes, so the distributed path and the in-process path are cryptographically
identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import nacl.signing

from protocol.receipts import (
    DEFAULT_MAX_AGE_NS,
    ReceiptSigner,
    ReceiptVerifier,
    sha256_hex,
)
from protocol.peer_consensus import VoteVerifier
from protocol.geometry import Pose

# Model identities match eval/harness.py so distributed results line up with the
# in-process ones.
APPROVED_MODEL = sha256_hex(b"yolov8n-weights-v1")
MALICIOUS_MODEL = sha256_hex(b"yolov8n-backdoored")


def generate_manifest(
    ids: Sequence[str],
    *,
    base_port: int = 51000,
    host: str = "127.0.0.1",
    poses: Optional[Mapping[str, Sequence[float]]] = None,
    observations: Optional[Mapping[str, Sequence[float]]] = None,
    approved_models: Iterable[str] = (APPROVED_MODEL,),
    o_min: float = 0.1,
) -> dict:
    """Build a manifest with a fresh software keypair per node."""
    nodes: Dict[str, dict] = {}
    for i, nid in enumerate(ids):
        sk = nacl.signing.SigningKey.generate()
        nodes[nid] = {
            "host": host,
            "port": base_port + i,
            "pubkey": bytes(sk.verify_key).hex(),
            "seed": bytes(sk).hex(),
            "backend": "software",
            "pose": list(poses[nid]) if poses and nid in poses else None,
            "observation": (
                list(observations[nid]) if observations and nid in observations else None
            ),
        }
    return {"approved_models": list(approved_models), "o_min": o_min, "nodes": nodes}


def save_manifest(manifest: dict, path: str | Path) -> Path:
    p = Path(path)
    p.write_text(json.dumps(manifest, indent=2))
    return p


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def peer_keys_of(manifest: dict) -> Dict[str, str]:
    return {nid: e["pubkey"] for nid, e in manifest["nodes"].items()}


def signer_for(entry: dict):
    """Build the signer for one node from its manifest entry."""
    if entry.get("backend") == "optee":
        from signing.optee_backend import OPTEEReceiptSigner

        return OPTEEReceiptSigner()
    sk = nacl.signing.SigningKey(bytes.fromhex(entry["seed"]))
    return ReceiptSigner(signing_key=sk)


def receipt_verifier_for(manifest: dict) -> ReceiptVerifier:
    return ReceiptVerifier(
        peer_keys=peer_keys_of(manifest),
        approved_models=set(manifest["approved_models"]),
        max_age_ns=int(manifest.get("max_receipt_age_ns", DEFAULT_MAX_AGE_NS)),
    )


def vote_verifier_for(manifest: dict) -> VoteVerifier:
    return VoteVerifier(peer_keys=peer_keys_of(manifest))


def pose_of(entry: dict) -> Optional[Pose]:
    p = entry.get("pose")
    if not p:
        return None
    x, y, z, yaw = (list(p) + [0.0, 0.0, 0.0, 0.0])[:4]
    return Pose(x, y, z, yaw)


def address_of(manifest: dict, node_id: str) -> str:
    e = manifest["nodes"][node_id]
    return f"{e['host']}:{e['port']}"
