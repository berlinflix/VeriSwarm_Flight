"""
Mint a `mission_authority.json` — the allowlist of model hashes the swarm accepts.

    # generate an authority keypair once, keep the private half off every drone
    python -m tools.make_authority --new-key

    # mint a signed allowlist from known-good weights
    python -m tools.make_authority --sign-key <hex_seed> --out mission_authority.json yolov8n.pt

This runs on the *mission authority's* machine, not on the provisioning host and
not on any drone. That is the entire point. The authority hashes weights it has
reviewed and signed off on; the provisioner later flashes weights onto aircraft.
Because the two are separate, a compromised provisioner can deliver a trojaned
model but cannot add its hash to the list the model will be checked against, so
the tampered weights fail provenance at every peer that receives a receipt from
the aircraft carrying them.

Collapsing this step into provisioning would silently delete that property: the
attacker would hash whatever it flashed and bless it. If you find yourself
generating the authority file from the same script that writes weights to a
node, stop — the provenance layer is no longer doing anything.

The file pins hashes, never paths, for the same reason: a path is resolved later,
on a machine an attacker may have reached, against a file it may have replaced.

Signing it closes the remaining hole. Unsigned, the allowlist is an ordinary file
and anyone with write access to a drone appends their own hash. Signed, tampering
is detected by a key that never touches the fleet.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

import nacl.signing

from node.common import authority_payload_bytes, file_model_hash, runtime_bundle_hash


def build_payload(weight_paths, issued_by: str, runtime_paths=()) -> dict:
    models = []
    for raw in weight_paths:
        path = Path(raw)
        if not path.exists():
            raise SystemExit(f"no such weight file: {path}")
        models.append({
            "name": path.name,
            "sha256": file_model_hash(path),
            "size_bytes": path.stat().st_size,
        })
    payload = {
        "issued_by": issued_by,
        "issued_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "approved_models": models,
    }
    runtime_paths = list(runtime_paths)
    if runtime_paths:
        payload["approved_runtimes"] = [{
            "name": "runtime-bundle-v1",
            "sha256": runtime_bundle_hash(runtime_paths),
            "artifacts": sorted(Path(raw).name for raw in runtime_paths),
        }]
    return payload


def sign_payload(payload: dict, seed_hex: str) -> dict:
    key = nacl.signing.SigningKey(bytes.fromhex(seed_hex))
    signature = key.sign(authority_payload_bytes(payload)).signature
    return {"payload": payload, "signature": signature.hex()}


def _new_key() -> None:
    key = nacl.signing.SigningKey.generate()
    print("authority keypair generated\n")
    print(f"  private seed (KEEP OFF EVERY DRONE): {bytes(key).hex()}")
    print(f"  public key   (pin on each drone)   : {bytes(key.verify_key).hex()}\n")
    print("Mint with   --sign-key <private seed>")
    print("Verify with VERISWARM_AUTHORITY_PUBKEY=<public key>")
    print("\nIn deployment the public key belongs in OP-TEE secure storage, not "
          "in the manifest — the manifest is written by the provisioner, so a key "
          "read from it could be swapped by whoever forged the allowlist.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Mint the mission authority allowlist from known-good weights."
    )
    ap.add_argument("weights", nargs="*", help="weight files to approve")
    ap.add_argument("--out", default="mission_authority.json")
    ap.add_argument("--issued-by", default=f"mission-authority@{socket.gethostname()}")
    ap.add_argument("--sign-key", default=None,
                    help="hex Ed25519 seed; omit to write an UNSIGNED file (dev only)")
    ap.add_argument("--sign-key-file", default=None,
                    help="file containing the hex seed (preferred; avoids process listings)")
    ap.add_argument("--runtime", action="append", default=[],
                    help="reviewed runtime/controller artifact; repeat for the bundle")
    ap.add_argument("--new-key", action="store_true",
                    help="generate an authority keypair and exit")
    args = ap.parse_args()

    if args.new_key:
        _new_key()
        return
    if not args.weights:
        ap.error("give at least one weight file, or use --new-key")
    if args.sign_key and args.sign_key_file:
        ap.error("use only one of --sign-key or --sign-key-file")

    seed = args.sign_key
    if args.sign_key_file:
        seed = Path(args.sign_key_file).read_text(encoding="ascii").strip()
    payload = build_payload(args.weights, args.issued_by, args.runtime)
    doc = sign_payload(payload, seed) if seed else payload
    Path(args.out).write_text(json.dumps(doc, indent=2))

    print(f"wrote {args.out}  [{'signed' if seed else 'UNSIGNED'}]")
    for model in payload["approved_models"]:
        print(f"  {model['name']:24} {model['sha256']}")
    if not seed:
        print("\nWARNING: unsigned. Anyone who can write this file can add their "
              "own model hash and the provenance layer stops catching anything. "
              "Use --sign-key before flying.")
    print("\nPoint the manifest at it:  manifest['authority'] = '<path>'")
    print("Keep it off the provisioning host — see the module docstring.")


if __name__ == "__main__":
    main()
