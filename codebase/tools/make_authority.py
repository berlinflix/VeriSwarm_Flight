"""
Mint a `mission_authority.json` — the allowlist of model hashes the swarm accepts.

    python -m tools.make_authority --out mission_authority.json yolov8n.pt

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
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

from node.common import file_model_hash


def build(weight_paths, issued_by: str) -> dict:
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
    return {
        "issued_by": issued_by,
        "issued_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "approved_models": models,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Mint the mission authority allowlist from known-good weights."
    )
    ap.add_argument("weights", nargs="+", help="weight files to approve")
    ap.add_argument("--out", default="mission_authority.json")
    ap.add_argument("--issued-by", default=f"mission-authority@{socket.gethostname()}")
    args = ap.parse_args()

    doc = build(args.weights, args.issued_by)
    Path(args.out).write_text(json.dumps(doc, indent=2))
    print(f"wrote {args.out}")
    for model in doc["approved_models"]:
        print(f"  {model['name']:24} {model['sha256']}")
    print("\nPoint the manifest at it:  manifest['authority'] = '<path>'")
    print("Keep it off the provisioning host — see the module docstring.")


if __name__ == "__main__":
    main()
