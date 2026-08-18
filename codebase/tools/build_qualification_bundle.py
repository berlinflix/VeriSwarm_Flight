"""Build the internal-qualifier evidence bundle (H0 #3-#6).

One command produces the entire distributable bundle described in
``SUYASH_TO_SAMIK_QUALIFICATION_HANDOFF.md`` section 4: the public manifest,
three node-scoped manifests, the frozen detector-derived fixtures, the two panel
cases, the fixed contract, and ``PUBLIC_SHA256SUMS``.

Why a dedicated tool rather than hand-editing JSON
--------------------------------------------------
Three trust-critical invariants are easy to get wrong by hand and fatal on
stage, so they are asserted in code, every run:

1. **Alpha carries no private seed.** Alpha signs through OP-TEE on the Jetson.
   Its manifest entry has ``backend: "optee"`` and the pinned public key, and
   **no** ``seed`` field anywhere. ``generate_manifest`` mints a software seed
   for every node, so Alpha's is stripped and the absence is asserted. This is
   what makes "Alpha's key is not a fleet-wide shared signer" a checked fact.
2. **No peer's seed leaks into another node's file.** Bravo's scoped manifest
   contains Bravo's seed and no other; Charlie likewise; the public manifest
   contains none. Asserted per scoped file.
3. **The approved model hash is the real weight file's hash**, computed here
   from ``--model`` at build time, never a synthetic placeholder. The clean
   receipt carries this hash; the tampered hash is recorded in the model-swap
   case as the attack input and never added to the allowlist.

The bundle is a deployment artifact. Its manifests reference the frozen switch
addresses (``192.168.50.10/.12/.13``) and ports (Alpha ``51000``, Bravo
``51001``, Charlie ``51003``); the port map is set explicitly because Charlie is
``51003`` and not the ``base_port + index`` default, which would silently place
it on ``51002`` and surface on stage as a missing peer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Dict

from node.common import (
    APPROVED_RUNTIME,
    generate_manifest,
    manifest_for_node,
    save_manifest,
    validate_manifest,
)
from perception.claim import PerceptionClaim, claims_agree
from protocol.receipts import (
    DEFAULT_ACTION_FRAME,
    DEFAULT_VALID_FOR_NS,
    PROTOCOL_VERSION,
    sha256_hex,
)

# --- Frozen qualification contract (matches the handoff, do not drift) --------

BUNDLE_ID_DEFAULT = "IHQ-20260819-001"
MISSION_ID = "internal-qualifier-2026-08-19"
MISSION_EPOCH = 1
ROSTER = ("alpha", "bravo", "charlie")
ALPHA_PUBKEY_DEFAULT = (
    "20c7be09ee691367468080617cbe07d12ce11c847f5b27cc7bc2e942f152c0f7"
)

# Identity -> (host, port). Ports are explicit; Charlie is 51003, not 51002.
TOPOLOGY: Dict[str, tuple[str, int]] = {
    "alpha": ("192.168.50.10", 51000),
    "bravo": ("192.168.50.12", 51001),
    "charlie": ("192.168.50.13", 51003),
}

# Nadir poses [x, y, z, yaw, pitch] at a shared 14 m altitude. Small horizontal
# baselines so the three ground footprints overlap well above o_min; phi_min is
# 0 in simulation mode, so even a small parallax is treated as co-visible and the
# semantic clause applies (reason "ok"), which is what yields two semantic ACKs.
POSES: Dict[str, list[float]] = {
    "alpha": [0.0, 0.0, 14.0, 0.0, 0.0],
    "bravo": [4.0, 0.0, 14.0, 0.0, 0.0],
    "charlie": [-4.0, 0.0, 14.0, 0.0, 0.0],
}

ACK_THRESHOLD = 2
DISPUTE_THRESHOLD = 1
CLEAN_ACTION = [0.0, 0.0, 0.0]  # measured empty scene -> hold; matches handoff


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_pubkey(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("Alpha public key must be 64 lowercase hex characters")
    return value


def _clean_claim() -> PerceptionClaim:
    """The frozen clean observation: a measured, empty nadir scene.

    All three nodes attest the same empty-scene claim, so ``claims_agree`` returns
    True and each peer votes ACK/ok. This is a valid detector-derived result (a
    clear overhead frame yields no COCO detections); the live three-camera scene
    is the separate webcam beat.
    """
    return PerceptionClaim(measured=True, detections_present=False)


def _claim_to_json(claim: PerceptionClaim) -> dict:
    return {
        "measured": claim.measured,
        "detections_present": claim.detections_present,
        "detection_count": claim.detection_count,
        "class_ids": list(claim.class_ids),
        "occupancy": claim.occupancy,
        "max_confidence": claim.max_confidence,
        "bearing": claim.bearing,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def build_manifest(model_hash: str, alpha_pubkey: str) -> dict:
    """Three-node simulation manifest with Alpha bound to OP-TEE (no seed)."""
    manifest = generate_manifest(
        list(ROSTER),
        poses=POSES,
        approved_models=[model_hash],
        approved_runtimes=[APPROVED_RUNTIME],
        o_min=0.1,
        phi_min=0.0,  # simulation: small parallax counts as co-visible
        mode="simulation",
        mission_id=MISSION_ID,
        mission_epoch=MISSION_EPOCH,
    )
    manifest["ack_threshold"] = ACK_THRESHOLD
    manifest["dispute_threshold"] = DISPUTE_THRESHOLD

    for node_id, (host, port) in TOPOLOGY.items():
        entry = manifest["nodes"][node_id]
        entry["host"] = host
        entry["port"] = port

    # Alpha signs through OP-TEE: pin the public key, drop the software seed.
    alpha = manifest["nodes"]["alpha"]
    alpha["backend"] = "optee"
    alpha["pubkey"] = alpha_pubkey
    alpha.pop("seed", None)

    validate_manifest(manifest)
    return manifest


def _assert_seed_isolation(manifest: dict) -> None:
    """Alpha must never carry a seed; verify before anything is written."""
    if "seed" in manifest["nodes"]["alpha"]:
        raise AssertionError("alpha carries a private seed; OP-TEE identity violated")


def _public_manifest(manifest: dict) -> dict:
    public = json.loads(json.dumps(manifest))
    for entry in public["nodes"].values():
        entry.pop("seed", None)
    return public


def _assert_scope(scoped: dict, owner: str) -> None:
    for node_id, entry in scoped["nodes"].items():
        has_seed = "seed" in entry
        if node_id == owner and owner != "alpha" and not has_seed:
            raise AssertionError(f"{owner} scoped manifest is missing its own seed")
        if node_id != owner and has_seed:
            raise AssertionError(
                f"{owner} scoped manifest leaks {node_id}'s private seed"
            )
    if owner == "alpha" and any("seed" in e for e in scoped["nodes"].values()):
        raise AssertionError("alpha scoped manifest carries a seed")


def build_bundle(
    *, out_root: Path, bundle_id: str, model_path: Path, tampered_path: Path,
    alpha_pubkey: str,
) -> dict:
    alpha_pubkey = _validate_pubkey(alpha_pubkey)
    model_hash = _sha256_file(model_path)
    tampered_hash = _sha256_file(tampered_path)
    if tampered_hash == model_hash:
        raise ValueError("tampered model hash equals the approved hash")

    root = out_root / bundle_id
    if root.exists():
        raise FileExistsError(f"bundle already exists (never overwrite): {root}")

    manifest = build_manifest(model_hash, alpha_pubkey)
    _assert_seed_isolation(manifest)

    # --- manifests -----------------------------------------------------------
    public = _public_manifest(manifest)
    (root / "public").mkdir(parents=True, exist_ok=True)
    save_manifest(public, root / "public" / "manifest.public.json")

    scoped_hashes: Dict[str, str] = {}
    for node_id in ROSTER:
        scoped = manifest_for_node(manifest, node_id)
        _assert_scope(scoped, node_id)
        path = root / f"{node_id}-private" / f"{node_id}.manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        save_manifest(scoped, path)
        scoped_hashes[node_id] = _sha256_file(path)

    public_manifest_sha = _sha256_file(root / "public" / "manifest.public.json")

    # --- fixtures: one atomic measured snapshot per node ---------------------
    clean_claim = _clean_claim()
    if claims_agree(clean_claim, clean_claim) is not True:
        raise AssertionError("clean claim does not agree with itself")
    for node_id in ROSTER:
        snapshot = {
            "node_id": node_id,
            "action": CLEAN_ACTION,
            "claim": _claim_to_json(clean_claim),
            "pose_enu": POSES[node_id] + [0.0],  # [x,y,z,yaw,pitch,uncertainty_m]
            "note": "detector-derived replay fixture; empty nadir scene",
        }
        _write_json(root / "fixtures" / node_id / "snapshot.json", snapshot)

    # --- panel cases ---------------------------------------------------------
    mission = {
        "mission_id": MISSION_ID,
        "mission_epoch": MISSION_EPOCH,
        "action_frame": DEFAULT_ACTION_FRAME,
        "valid_for_ns": DEFAULT_VALID_FOR_NS,
        "sequence": 1,
    }
    _write_json(root / "public" / "clean-case.json", {
        "case_id": "Q-CLEAN",
        "originator": "alpha",
        "model_hash": model_hash,
        "runtime_hash": APPROVED_RUNTIME,
        "action": CLEAN_ACTION,
        "claim": _claim_to_json(clean_claim),
        **mission,
        "expected": {"outcome": "ACCEPTED", "acks": 2, "disputes": 0,
                     "semantic_acks": 2, "decision": "EXECUTE_IF_HEALTH_OK"},
    })
    _write_json(root / "public" / "model-swap-case.json", {
        "case_id": "Q-MODEL-SWAP",
        "originator": "alpha",
        "model_hash": tampered_hash,  # attack input; NOT in the allowlist
        "runtime_hash": APPROVED_RUNTIME,
        "action": CLEAN_ACTION,
        "claim": _claim_to_json(clean_claim),
        **mission,
        "expected": {"outcome": "REJECTED", "disputes": 2,
                     "reason_prefix": f"model_hash_not_approved:{tampered_hash[:16]}",
                     "decision": "HOLD"},
    })

    # --- fixed contract + schemas + operator sheets --------------------------
    _write_json(root / "public" / "contract.json", {
        "bundle_id": bundle_id,
        "protocol_version": PROTOCOL_VERSION,
        "mission_id": MISSION_ID,
        "mission_epoch": MISSION_EPOCH,
        "roster": list(ROSTER),
        "topology": {n: {"host": h, "port": p} for n, (h, p) in TOPOLOGY.items()},
        "ack_threshold": ACK_THRESHOLD,
        "dispute_threshold": DISPUTE_THRESHOLD,
        "action_frame": DEFAULT_ACTION_FRAME,
        "valid_for_ns": DEFAULT_VALID_FOR_NS,
        "approved_model_sha256": model_hash,
        "tampered_model_sha256": tampered_hash,
        "alpha_pubkey": alpha_pubkey,
        "public_manifest_sha256": public_manifest_sha,
        "scoped_manifest_sha256": scoped_hashes,
        "hold_action": [0.0, 0.0, 0.0],
        "transport": "insecure gRPC on isolated wired LAN; signatures verified, link not mTLS",
        "created_ns": time.time_ns(),
    })
    _write_json(root / "public" / "output-schema.json", {
        "schema": "veriswarm.internal_qualifier.v1",
        "required": ["schema", "bundle_id", "run_id", "case_id", "created_ns",
                     "commit", "public_manifest_sha256", "expected", "actual",
                     "receipt", "authorization", "peer_votes", "pass"],
    })
    (root / "public" / "network-preflight-template.txt").write_text(
        "VeriSwarm internal-qualifier network/clock preflight\n"
        f"bundle: {bundle_id}\n\n"
        "machine   | ip              | iface | ping alpha | ping p1 | port | clock offset | initials/time\n"
        "alpha     | 192.168.50.10   |       |            |         | 51000|              |\n"
        "pratik-p1 | 192.168.50.11   |       |            |         | 41451|              |\n"
        "bravo     | 192.168.50.12   |       |            |         | 51001|              |\n"
        "charlie   | 192.168.50.13   |       |            |         | 51003|              |\n"
        "abhijan   | 192.168.50.14   |       |            |         |  n/a |              |\n\n"
        "Wi-Fi disabled: [ ]   firewall wired-only: [ ]   one time source verified: [ ]\n"
    )
    (root / "public" / "go-no-go.md").write_text(
        f"# GO / NO-GO - {bundle_id}\n\n"
        "GO only after two cold full rehearsals and a fresh verified OP-TEE preflight.\n\n"
        "- [ ] OP-TEE preflight: actual_pubkey == expected_pubkey, signature_verified\n"
        "- [ ] Q-CLEAN: acks=2, semantic_acks=2, ACCEPTED\n"
        "- [ ] Q-MODEL-SWAP: disputes=2, REJECTED, HOLD\n"
        "- [ ] Q-PEER-TIMEOUT: NO_QUORUM -> HOLD\n"
        "- [ ] network/clock/firewall sheet complete for all five machines\n"
        "- [ ] CoSys A->B: zero collisions, landed and disarmed\n"
        "- [ ] two cold runs from reset without editing source\n\n"
        "Suyash alone calls final GO. Any owner may call NO-GO for their subsystem.\n"
    )

    # --- evidence drop points ------------------------------------------------
    for sub in ("optee", "protocol", "network"):
        (root / "evidence" / sub).mkdir(parents=True, exist_ok=True)
        (root / "evidence" / sub / ".gitkeep").write_text("")

    # --- PUBLIC_SHA256SUMS over the public tree ------------------------------
    public_dir = root / "public"
    lines = []
    for path in sorted(public_dir.rglob("*")):
        if path.is_file():
            lines.append(f"{_sha256_file(path)}  {path.relative_to(root).as_posix()}")
    (root / "PUBLIC_SHA256SUMS").write_text("\n".join(lines) + "\n")

    return {
        "root": str(root),
        "model_hash": model_hash,
        "tampered_hash": tampered_hash,
        "public_manifest_sha256": public_manifest_sha,
        "scoped_manifest_sha256": scoped_hashes,
        "alpha_pubkey": alpha_pubkey,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="Build the internal-qualifier bundle")
    parser.add_argument("--model", required=True, help="path to the approved yolov8n.pt")
    parser.add_argument("--tampered", required=True, help="path to yolov8n_tampered.pt")
    parser.add_argument("--alpha-pubkey", default=ALPHA_PUBKEY_DEFAULT,
                        help="pinned Alpha OP-TEE Ed25519 public key (64 hex)")
    parser.add_argument("--out", default="results/internal_qualifier",
                        help="bundle output root")
    parser.add_argument("--bundle-id", default=BUNDLE_ID_DEFAULT)
    args = parser.parse_args()

    summary = build_bundle(
        out_root=Path(args.out),
        bundle_id=args.bundle_id,
        model_path=Path(args.model).expanduser().resolve(strict=True),
        tampered_path=Path(args.tampered).expanduser().resolve(strict=True),
        alpha_pubkey=args.alpha_pubkey,
    )
    print("bundle built:", summary["root"])
    print("  approved model :", summary["model_hash"])
    print("  tampered model :", summary["tampered_hash"])
    print("  alpha pubkey   :", summary["alpha_pubkey"])
    print("  public manifest:", summary["public_manifest_sha256"])
    for node_id, digest in summary["scoped_manifest_sha256"].items():
        print(f"  {node_id:<7} manifest:", digest)


if __name__ == "__main__":
    _main()
