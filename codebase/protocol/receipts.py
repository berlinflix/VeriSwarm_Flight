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
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Optional, Sequence

import nacl.exceptions
import nacl.signing

from perception.claim import PerceptionClaim


_HEX_64_RE = re.compile(r"[0-9a-f]{64}\Z")
_HEX_32_RE = re.compile(r"[0-9a-f]{32}\Z")
_HEX_128_RE = re.compile(r"[0-9a-f]{128}\Z")
_DRONE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_MISSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
ACTION_DIM = 3
PROTOCOL_VERSION = 3
DEFAULT_ACTION_FRAME = "BODY_FLU_NORMALIZED_VELOCITY"
SUPPORTED_ACTION_FRAMES = frozenset({DEFAULT_ACTION_FRAME})
UNMEASURED_RUNTIME_HASH = hashlib.sha256(b"unmeasured-runtime").hexdigest()
DEFAULT_VALID_FOR_NS = 2_000_000_000
MAX_SERIALIZED_RECEIPT_BYTES = 16 * 1024
DEFAULT_REPLAY_CACHE_SIZE = 4096


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
    Windows). Platform entropy quality remains an operating-system and boot-time
    deployment assumption.
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
    protocol_version: int = PROTOCOL_VERSION
    mission_id: str = "simulation"
    mission_epoch: int = 0
    sequence: int = 0
    runtime_hash: str = UNMEASURED_RUNTIME_HASH
    action_frame: str = DEFAULT_ACTION_FRAME
    valid_for_ns: int = DEFAULT_VALID_FOR_NS
    pose_enu: Sequence[float] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    pose_timestamp_ns: int = 0
    pose_uncertainty_m: float = 0.0
    #: What the detector OBSERVED, as distinct from the command in `output`.
    #: Embedded here rather than sent alongside so that signing the receipt
    #: binds the observation to this exact frame, model, runtime and round --
    #: a claim lifted from one receipt cannot be replayed into another.
    perception: PerceptionClaim = PerceptionClaim()

    def __post_init__(self) -> None:
        if not _DRONE_ID_RE.fullmatch(self.drone_id):
            raise ValueError(
                "drone_id must be 1-64 ASCII letters/digits/._- and start "
                "with a letter or digit"
            )
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol_version: {self.protocol_version}")
        if not _MISSION_ID_RE.fullmatch(self.mission_id):
            raise ValueError("mission_id must be 1-128 safe ASCII characters")
        if self.mission_epoch < 0 or self.sequence < 0:
            raise ValueError("mission_epoch and sequence must be non-negative")
        if not _HEX_64_RE.fullmatch(self.input_hash):
            raise ValueError("input_hash must be 64 lowercase hex chars (SHA-256)")
        if not _HEX_64_RE.fullmatch(self.model_hash):
            raise ValueError("model_hash must be 64 lowercase hex chars (SHA-256)")
        if not _HEX_32_RE.fullmatch(self.nonce):
            raise ValueError("nonce must be 32 lowercase hex chars (16 bytes)")
        if not _HEX_64_RE.fullmatch(self.runtime_hash):
            raise ValueError("runtime_hash must be 64 lowercase hex chars (SHA-256)")
        if self.action_frame not in SUPPORTED_ACTION_FRAMES:
            raise ValueError(f"unsupported action_frame: {self.action_frame!r}")
        if not (0 < self.valid_for_ns <= DEFAULT_VALID_FOR_NS):
            raise ValueError(
                f"valid_for_ns must be in [1, {DEFAULT_VALID_FOR_NS}]"
            )
        pose = tuple(float(v) for v in self.pose_enu)
        if len(pose) != 6 or not all(math.isfinite(v) for v in pose):
            raise ValueError("pose_enu must contain six finite x,y,z,yaw,pitch,roll values")
        if pose[2] < 0.0:
            raise ValueError("pose altitude must be non-negative")
        if self.pose_timestamp_ns < 0:
            raise ValueError("pose_timestamp_ns must be non-negative")
        if not math.isfinite(self.pose_uncertainty_m) or self.pose_uncertainty_m < 0.0:
            raise ValueError("pose_uncertainty_m must be finite and non-negative")
        object.__setattr__(self, "pose_enu", pose)

        output = tuple(float(x) for x in self.output)
        if len(output) != ACTION_DIM:
            raise ValueError(f"output must contain exactly {ACTION_DIM} action values")
        if not all(math.isfinite(x) for x in output):
            raise ValueError("output values must be finite")
        if not all(-1.0 <= x <= 1.0 for x in output):
            raise ValueError("output values must be in [-1, 1]")
        object.__setattr__(self, "output", output)

        if not isinstance(self.perception, PerceptionClaim):
            raise ValueError("perception must be a PerceptionClaim")

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
            allow_nan=False,
        ).encode("utf-8")


@dataclass(frozen=True)
class SignedReceipt:
    """A Receipt paired with its Ed25519 signature (hex-encoded)."""

    receipt: Receipt
    signature: str  # 128 hex chars = 64 bytes Ed25519 signature

    def __post_init__(self) -> None:
        if not _HEX_128_RE.fullmatch(self.signature):
            raise ValueError("signature must be 128 lowercase hex chars")

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
        if len(wire) > MAX_SERIALIZED_RECEIPT_BYTES:
            raise ValueError("serialized receipt exceeds size limit")
        obj = json.loads(wire)
        if not isinstance(obj, dict) or set(obj) != {"receipt", "signature"}:
            raise ValueError("serialized receipt has unexpected fields")
        fields = dict(obj["receipt"])
        # `asdict` flattens the nested claim to a plain dict on the way out, so
        # rebuild it on the way in. Constructing PerceptionClaim here also
        # re-runs its range checks, so a malformed claim from the wire is
        # rejected at parse time rather than reaching a comparison.
        claim = fields.get("perception")
        if isinstance(claim, dict):
            fields["perception"] = PerceptionClaim(**claim)
        return cls(
            receipt=Receipt(**fields),
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
    *,
    mission_id: str = "simulation",
    mission_epoch: int = 0,
    sequence: int = 0,
    runtime_hash: str = UNMEASURED_RUNTIME_HASH,
    action_frame: str = DEFAULT_ACTION_FRAME,
    valid_for_ns: int = DEFAULT_VALID_FOR_NS,
    pose_enu: Sequence[float] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    pose_timestamp_ns: Optional[int] = None,
    pose_uncertainty_m: float = 0.0,
    perception: Optional[PerceptionClaim] = None,
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
    stamp = timestamp_ns if timestamp_ns is not None else time.time_ns()
    return Receipt(
        drone_id=drone_id,
        timestamp_ns=stamp,
        input_hash=sha256_hex(input_bytes),
        model_hash=model_hash,
        output=tuple(output),
        nonce=fresh_nonce(),
        protocol_version=PROTOCOL_VERSION,
        mission_id=mission_id,
        mission_epoch=mission_epoch,
        sequence=sequence,
        runtime_hash=runtime_hash,
        action_frame=action_frame,
        valid_for_ns=valid_for_ns,
        pose_enu=tuple(pose_enu),
        pose_timestamp_ns=stamp if pose_timestamp_ns is None else pose_timestamp_ns,
        pose_uncertainty_m=pose_uncertainty_m,
        perception=perception if perception is not None else PerceptionClaim.unmeasured(),
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
    `tee-supplicant`. The private key remains in OP-TEE secure storage.
    """

    def __init__(self, signing_key: Optional[nacl.signing.SigningKey] = None):
        self._key = signing_key or nacl.signing.SigningKey.generate()

    @property
    def public_key_bytes(self) -> bytes:
        return bytes(self._key.verify_key)

    @property
    def public_key_hex(self) -> str:
        return self.public_key_bytes.hex()

    def sign_bytes(self, message: bytes) -> str:
        """Sign protocol bytes without exposing the private-key implementation."""
        return self._key.sign(message).signature.hex()

    def sign(self, receipt: Receipt) -> SignedReceipt:
        return SignedReceipt(
            receipt=receipt,
            signature=self.sign_bytes(receipt.canonical()),
        )


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
        replay_cache_size: int = DEFAULT_REPLAY_CACHE_SIZE,
        expected_mission_id: Optional[str] = None,
        expected_mission_epoch: Optional[int] = None,
        approved_runtimes: Optional[Iterable[str]] = None,
        enforce_sequence: bool = False,
    ):
        self._peer_keys: dict[str, nacl.signing.VerifyKey] = {
            did: nacl.signing.VerifyKey(bytes.fromhex(hex_key))
            for did, hex_key in peer_keys.items()
        }
        self._approved_models: frozenset[str] = frozenset(approved_models)
        self._max_age_ns: int = int(max_age_ns)
        self._expected_mission_id = expected_mission_id
        self._expected_mission_epoch = expected_mission_epoch
        self._approved_runtimes = (
            None if approved_runtimes is None else frozenset(approved_runtimes)
        )
        self._enforce_sequence = bool(enforce_sequence)
        if replay_cache_size < 1:
            raise ValueError("replay_cache_size must be >= 1")
        self._replay_cache_size = int(replay_cache_size)
        self._seen: dict[str, OrderedDict[str, None]] = {}
        self._seen_sequences: dict[str, dict[str, OrderedDict[int, None]]] = {}
        self._seen_lock = threading.Lock()

    def verify(
        self,
        signed: SignedReceipt,
        *,
        now_ns: Optional[int] = None,
        replay_scope: str = "default",
        consume: bool = True,
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
            accepted_age = min(self._max_age_ns, signed.receipt.valid_for_ns)
            if abs(now - signed.receipt.timestamp_ns) > accepted_age:
                return VerificationResult(ok=False, reason="stale_receipt")

        if (
            self._expected_mission_id is not None
            and signed.receipt.mission_id != self._expected_mission_id
        ):
            return VerificationResult(ok=False, reason="wrong_mission")
        if (
            self._expected_mission_epoch is not None
            and signed.receipt.mission_epoch != self._expected_mission_epoch
        ):
            return VerificationResult(ok=False, reason="wrong_mission_epoch")

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
        if (
            self._approved_runtimes is not None
            and signed.receipt.runtime_hash not in self._approved_runtimes
        ):
            return VerificationResult(
                ok=False,
                reason=f"runtime_hash_not_approved:{signed.receipt.runtime_hash[:16]}",
            )

        # Step 4: exact replay detection. Freshness alone only bounds how long a
        # captured packet remains useful; it does not stop repeated use inside
        # that window. The scope represents one receiving node so a verifier
        # object may safely be shared by in-process test peers without making
        # one peer's observation consume another peer's receipt.
        if consume:
            digest = sha256_hex(signed.receipt.canonical())
            with self._seen_lock:
                seen = self._seen.setdefault(replay_scope, OrderedDict())
                if digest in seen:
                    seen.move_to_end(digest)
                    return VerificationResult(ok=False, reason="replayed_receipt")
                seen[digest] = None
                while len(seen) > self._replay_cache_size:
                    seen.popitem(last=False)

                if self._enforce_sequence:
                    by_drone = self._seen_sequences.setdefault(replay_scope, {})
                    sequences = by_drone.setdefault(
                        signed.receipt.drone_id, OrderedDict()
                    )
                    if signed.receipt.sequence in sequences:
                        # Remove the digest inserted above: this invalid packet
                        # must not evict a later valid receipt from the cache.
                        seen.pop(digest, None)
                        return VerificationResult(
                            ok=False, reason="duplicate_sequence"
                        )
                    sequences[signed.receipt.sequence] = None
                    while len(sequences) > self._replay_cache_size:
                        sequences.popitem(last=False)

        return VerificationResult(ok=True, reason="ok")
