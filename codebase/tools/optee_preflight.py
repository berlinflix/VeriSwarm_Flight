"""Fail-closed OP-TEE/manifest binding check for the Alpha handover.

Run this on the Jetson after ``covis_live`` has stopped and before the Alpha
originator starts.  It signs a fresh, canonical protocol receipt through the
real OP-TEE Client Application, verifies the result against the public key
pinned outside the Jetson's runtime manifest, and writes non-overwriting JSON
evidence.

This proves use of the expected hardware-protected signing key.  It does not
prove that inference, model loading, or pose estimation ran inside the TEE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import time
from pathlib import Path
from typing import Any

import nacl.exceptions
import nacl.signing

from protocol.receipts import build_receipt, sha256_hex
from signing.optee_backend import OPTEEReceiptSigner


_PREFLIGHT_MODEL_HASH = sha256_hex(b"veriswarm-optee-preflight-no-model-v1")
_CHALLENGE_DOMAIN = b"VERISWARM_OPTEE_PREFLIGHT_V1\x00"
_DEFAULT_TA_PATH = Path(
    "/lib/optee_armtz/7e9a4c10-3b62-4d8e-a1f5-9c2b6d04e7a3.ta"
)


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
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("expected public key must be 64 lowercase hex characters")
    return value


def create_preflight_evidence(
    *,
    signer: Any,
    expected_pubkey: str,
    ca_path: Path,
    ta_path: Path | None = None,
    mission_id: str,
    mission_epoch: int,
    node_id: str = "alpha",
    sequence: int = 0,
    challenge: bytes | None = None,
) -> dict:
    """Create and locally verify one fresh canonical receipt-signing proof."""
    expected = _validate_pubkey(expected_pubkey)
    actual = _validate_pubkey(signer.public_key_hex)
    if not secrets.compare_digest(actual, expected):
        raise RuntimeError("OP-TEE public key does not match the pinned Alpha key")
    if mission_epoch < 0 or sequence < 0:
        raise ValueError("mission_epoch and sequence must be non-negative")

    fresh = secrets.token_bytes(32) if challenge is None else bytes(challenge)
    if len(fresh) < 16:
        raise ValueError("preflight challenge must contain at least 128 bits")
    receipt = build_receipt(
        drone_id=node_id,
        input_bytes=_CHALLENGE_DOMAIN + fresh,
        model_hash=_PREFLIGHT_MODEL_HASH,
        output=(0.0, 0.0, 0.0),
        mission_id=mission_id,
        mission_epoch=mission_epoch,
        sequence=sequence,
        runtime_hash=_sha256_file(ca_path),
    )

    started = time.perf_counter_ns()
    signed = signer.sign(receipt)
    sign_ms = (time.perf_counter_ns() - started) / 1e6
    try:
        nacl.signing.VerifyKey(bytes.fromhex(expected)).verify(
            receipt.canonical(), bytes.fromhex(signed.signature)
        )
    except (ValueError, nacl.exceptions.BadSignatureError) as exc:
        raise RuntimeError("OP-TEE preflight signature verification failed") from exc

    evidence = {
        "schema": "veriswarm.optee_preflight.v1",
        "created_ns": time.time_ns(),
        "host": platform.node(),
        "node_id": node_id,
        "backend": "optee",
        "tee_device": "/dev/tee0",
        "ca_path": str(ca_path),
        "ca_sha256": _sha256_file(ca_path),
        "expected_pubkey": expected,
        "actual_pubkey": actual,
        "challenge_hex": fresh.hex(),
        "canonical_receipt_sha256": sha256_hex(receipt.canonical()),
        "receipt": json.loads(receipt.canonical()),
        "signature": signed.signature,
        "signature_verified": True,
        "sign_ms": round(sign_ms, 6),
        "claim_boundary": (
            "OP-TEE protected the Alpha signing key and signed these canonical "
            "receipt bytes; inference and pose were not attested by this check"
        ),
    }
    if ta_path is not None:
        evidence["ta_path"] = str(ta_path)
        evidence["ta_sha256"] = _sha256_file(ta_path)
    return evidence


def write_evidence(path: Path, evidence: dict) -> None:
    """Write once so a later rehearsal cannot silently replace prior evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the pinned Alpha OP-TEE key and retain a signed receipt"
    )
    parser.add_argument(
        "--ca",
        default=os.environ.get("VERISWARM_OPTEE_CA"),
        help="path to veriswarm_optee_ca (or set VERISWARM_OPTEE_CA)",
    )
    parser.add_argument(
        "--ta",
        default=str(_DEFAULT_TA_PATH),
        help="installed VeriSwarm Trusted Application path",
    )
    parser.add_argument(
        "--expected-pubkey",
        default=os.environ.get("VERISWARM_ALPHA_PUBKEY"),
        help="pinned Alpha Ed25519 public key (or set VERISWARM_ALPHA_PUBKEY)",
    )
    parser.add_argument("--out", required=True, help="new JSON evidence path")
    parser.add_argument("--mission-id", required=True)
    parser.add_argument("--mission-epoch", required=True, type=int)
    parser.add_argument("--node-id", default="alpha")
    parser.add_argument("--sequence", default=0, type=int)
    parser.add_argument("--timeout-s", default=5.0, type=float)
    args = parser.parse_args()

    if not args.ca:
        parser.error("--ca or VERISWARM_OPTEE_CA is required")
    if not args.expected_pubkey:
        parser.error("--expected-pubkey or VERISWARM_ALPHA_PUBKEY is required")
    if args.timeout_s <= 0.0:
        parser.error("--timeout-s must be positive")
    ca_path = Path(args.ca).expanduser().resolve(strict=True)
    if not ca_path.is_file():
        parser.error("OP-TEE Client Application path is not a file")
    ta_path = Path(args.ta).expanduser().resolve(strict=True)
    if not ta_path.is_file():
        parser.error("OP-TEE Trusted Application path is not a file")

    signer = OPTEEReceiptSigner(ca_path=str(ca_path), timeout_s=args.timeout_s)
    evidence = create_preflight_evidence(
        signer=signer,
        expected_pubkey=args.expected_pubkey,
        ca_path=ca_path,
        ta_path=ta_path,
        mission_id=args.mission_id,
        mission_epoch=args.mission_epoch,
        node_id=args.node_id,
        sequence=args.sequence,
    )
    output = Path(args.out).expanduser().resolve()
    write_evidence(output, evidence)
    print(
        f"OP-TEE preflight PASS: node={args.node_id} "
        f"pubkey={evidence['actual_pubkey'][:16]}... "
        f"sign_ms={evidence['sign_ms']} evidence={output}"
    )


if __name__ == "__main__":
    _main()
