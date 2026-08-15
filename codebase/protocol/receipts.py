"""
VeriSwarm Protocol — Phase 1: Receipt construction, signing, and verification.

A Receipt is a cryptographically-signed attestation that a specific drone,
running a specific (hash-verified) AI model, observed a specific input and
produced a specific output at a specific time.

Signed receipts are broadcast to peer drones who verify:
  (a) the Ed25519 signature is valid under the sender's known public key,
  (b) the model_hash matches an approved allowlist,
  (c) the output is consistent with peers' own observations
      (cross-drone output comparison — implemented in `peer_consensus.py`,
       which uses the Receipt produced here as input).

This module implements (a) and (b). Cross-drone semantic comparison is
intentionally out of scope: receipts.py guarantees *cryptographic*
integrity; semantic agreement is a separate layer.

References
----------
Castro & Liskov, "Practical Byzantine Fault Tolerance", OSDI 1999.
Tramer & Boneh, "Slalom: Fast, Verifiable and Private Execution of Neural
    Networks in Trusted Hardware", ICLR 2019.
Bernstein et al., "High-speed high-security signatures", CHES 2011 (Ed25519).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Optional, Sequence

import nacl.exceptions
import nacl.signing


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    """SHA-256 of `data`, returned as a 64-character hex string."""
    return hashlib.sha256(data).hexdigest()


def fresh_nonce(num_bytes: int = 16) -> str:
    """
    Cryptographically secure random nonce, hex-encoded.

    16 bytes (128 bits) of entropy is sufficient to make collision
    probability negligible for any practical drone-mission duration.
    Source: the OS CSPRNG (`/dev/urandom` on Linux, BCryptGenRandom on
    Windows). On Jetson, this is additionally seeded by the hardware
    secure element when available.
    """
    return os.urandom(num_bytes).hex()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Receipt:
    """
    A cryptographic attestation of one inference step.

    The receipt is the *contents* being signed. It is immutable
    (`frozen=True`) so that the signature cannot silently de-sync from
    the data it covers.

    Canonical serialization
    -----------------------
    For Ed25519 to be deterministic across signer and verifier, both
    must produce *byte-identical* serializations of the receipt. We use
    JSON with sorted keys, no whitespace, ASCII-only escaping. This
    yields a single canonical form per receipt and is independently
    auditable (humans can read the signed bytes).

    Fields
    ------
    drone_id:
        Identifier of the producing drone (e.g. ``"alpha"``). Maps to a
        public key via the verifier's peer registry.
    timestamp_ns:
        Unix epoch in **nanoseconds** as an integer. Integer avoids
        float-precision drift across the wire and survives JSON
        round-trips losslessly.
    input_hash:
        Hex-encoded SHA-256 of the canonical input bytes — typically a
        captured camera frame in a known encoding (e.g. raw RGB888
        bytes, fixed resolution).
    model_hash:
        Hex-encoded SHA-256 of the model weight bytes. Computed once at
        boot from the on-disk weight file and never recomputed during
        flight (the in-memory model is immutable).
    output:
        The action / decision vector emitted by the model. We store it
        as a tuple for hashability and immutability.
    nonce:
        Hex-encoded 16-byte random nonce ensuring receipt uniqueness
        even when all other fields collide. This guarantees *uniqueness*,
        not replay resistance -- rejecting a re-sent receipt is the job of
        the freshness window in `ReceiptVerifier`.
    """

    drone_id: str
    timestamp_ns: int
    input_hash: str
    model_hash: str
    output: Sequence[float]
    nonce: str

    def __post_init__(self) -> None:
        # Lightweight invariants. We do not validate cryptographic
        # correctness here — that lives in the verifier.
        if not self.drone_id:
            raise ValueError("drone_id must be non-empty")
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        if len(self.input_hash) != 64:
            raise ValueError("input_hash must be 64 hex chars (SHA-256)")
        if len(self.model_hash) != 64:
            raise ValueError("model_hash must be 64 hex chars (SHA-256)")
        if len(self.nonce) != 32:
            raise ValueError("nonce must be 32 hex chars (16 bytes)")
        # Coerce output into an immutable tuple for hashability.
        object.__setattr__(self, "output", tuple(float(x) for x in self.output))

    def canonical(self) -> bytes:
        """
        Canonical byte representation. **This is the byte string signed
        by `ReceiptSigner` and verified by `ReceiptVerifier`.**

        Any change to the serialization format is a hard protocol break
        — bump the protocol version if you ever touch this method.
        """
        return json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")


@dataclass(frozen=True)
class SignedReceipt:
    """A Receipt paired with its Ed25519 signature (hex-encoded)."""

    receipt: Receipt
    signature: str  # 128 hex chars = 64 bytes Ed25519 signature

    def serialize(self) -> bytes:
        """Wire format — what gets sent over gRPC to peers."""
        return json.dumps(
            {"receipt": asdict(self.receipt), "signature": self.signature},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")

    @classmethod
    def deserialize(cls, wire: bytes) -> "SignedReceipt":
        obj = json.loads(wire)
        return cls(
            receipt=Receipt(**obj["receipt"]),
            signature=obj["signature"],
        )


# ---------------------------------------------------------------------------
# Construction helper
# ---------------------------------------------------------------------------


def build_receipt(
    drone_id: str,
    input_bytes: bytes,
    model_hash: str,
    output: Iterable[float],
    timestamp_ns: Optional[int] = None,
) -> Receipt:
    """
    Construct a Receipt from raw inference inputs.

    Hashes `input_bytes`, generates a fresh nonce, stamps the current
    nanosecond timestamp if none is supplied, and packages everything
    into an immutable Receipt.

    Note: `model_hash` is **not** recomputed here — it is supplied by
    the caller, who is expected to have computed it once at boot and
    cached it. This keeps the per-frame hot path tight.
    """
    return Receipt(
        drone_id=drone_id,
        timestamp_ns=timestamp_ns if timestamp_ns is not None else time.time_ns(),
        input_hash=sha256_hex(input_bytes),
        model_hash=model_hash,
        output=tuple(output),
        nonce=fresh_nonce(),
    )


# ---------------------------------------------------------------------------
# Signing — software backend (Bravo, Charlie)
# ---------------------------------------------------------------------------


class ReceiptSigner:
    """
    Software-backed Ed25519 signer.

    The private key is generated (or supplied) and held in process
    memory. Suitable for the software-attested peer nodes (Bravo on the
    laptop, Charlie on the lab GPU server).

    For Alpha (hardware-attested on Jetson), this class is replaced by
    `OPTEEReceiptSigner` (see `signing/optee_backend.py`), which
    delegates the `sign()` call to the OP-TEE Trusted Application via
    `tee-supplicant`. The keypair never leaves the secure element.
    """

    def __init__(self, signing_key: Optional[nacl.signing.SigningKey] = None):
        self._key = signing_key or nacl.signing.SigningKey.generate()

    @property
    def public_key_bytes(self) -> bytes:
        return bytes(self._key.verify_key)

    @property
    def public_key_hex(self) -> str:
        return self.public_key_bytes.hex()

    def sign(self, receipt: Receipt) -> SignedReceipt:
        sig = self._key.sign(receipt.canonical()).signature
        return SignedReceipt(receipt=receipt, signature=sig.hex())


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


#: Receipts whose timestamp differs from the verifier's clock by more than
#: this are rejected as stale. This is what makes replay of a previously
#: accepted receipt fail: a captured receipt is by definition old.
#:
#: The window must exceed the residual clock offset between drones (the
#: AttestationService's Ping call coarsely aligns clocks) while staying far
#: below the horizon over which a scene changes. At a 10-30 Hz inference
#: rate, 2 s is 20-60 cycles.
DEFAULT_MAX_AGE_NS: int = 2_000_000_000  # 2 seconds


@dataclass(frozen=True)
class VerificationResult:
    """
    Result of verifying a SignedReceipt.

    `ok` is True only if signature is valid AND model_hash is approved.
    `reason` is a short machine-friendly string for logging and metrics
    (used in eval/experiment_2_model_swap_detection.py).
    """

    ok: bool
    reason: str


class ReceiptVerifier:
    """
    Verifies signed receipts against:
      0. a freshness window on the receipt's own timestamp,
      1. a registry of known peer public keys (`drone_id -> hex key`),
      2. an allowlist of approved model hashes.

    The registries are configured at mission start. In a production
    deployment they would be loaded from a ground-station-signed
    manifest; for the simulator we load them from a YAML config.

    The freshness window is what rejects a replayed receipt. The nonce
    alone cannot: it makes each receipt unique, but a verifier that keeps
    no state will happily re-verify a captured receipt, whose signature is
    still perfectly valid. Checking the timestamp needs no stored state and
    runs before any curve arithmetic, so a flood of replays is cheap to
    shed. Pass `max_age_ns=0` to disable the check (offline analysis of
    recorded receipts).
    """

    def __init__(
        self,
        peer_keys: Mapping[str, str],
        approved_models: Iterable[str],
        max_age_ns: int = DEFAULT_MAX_AGE_NS,
    ):
        self._peer_keys: dict[str, nacl.signing.VerifyKey] = {
            did: nacl.signing.VerifyKey(bytes.fromhex(hex_key))
            for did, hex_key in peer_keys.items()
        }
        self._approved_models: frozenset[str] = frozenset(approved_models)
        self._max_age_ns: int = int(max_age_ns)

    def verify(
        self,
        signed: SignedReceipt,
        *,
        now_ns: Optional[int] = None,
    ) -> VerificationResult:
        """
        Verify `signed`. `now_ns` overrides the wall clock, which lets the
        freshness check be tested without sleeping.
        """
        # Step 0: Freshness. Cheapest check, so it runs first: replayed and
        # far-future receipts are shed before spending an Ed25519 verify on
        # them. Symmetric, so a drone with a badly skewed clock is caught in
        # both directions rather than being trusted indefinitely.
        if self._max_age_ns > 0:
            now = time.time_ns() if now_ns is None else now_ns
            if abs(now - signed.receipt.timestamp_ns) > self._max_age_ns:
                return VerificationResult(ok=False, reason="stale_receipt")

        # Step 1: Identify the claimed sender.
        verify_key = self._peer_keys.get(signed.receipt.drone_id)
        if verify_key is None:
            return VerificationResult(
                ok=False,
                reason=f"unknown_drone_id:{signed.receipt.drone_id}",
            )

        # Step 2: Cryptographic check — Ed25519 signature.
        try:
            verify_key.verify(
                signed.receipt.canonical(),
                bytes.fromhex(signed.signature),
            )
        except (nacl.exceptions.BadSignatureError, ValueError):
            return VerificationResult(ok=False, reason="bad_signature")

        # Step 3: Policy check — model_hash on the allowlist.
        if signed.receipt.model_hash not in self._approved_models:
            return VerificationResult(
                ok=False,
                reason=f"model_hash_not_approved:{signed.receipt.model_hash[:16]}",
            )

        return VerificationResult(ok=True, reason="ok")
