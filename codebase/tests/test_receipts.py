"""
Security-property tests for `protocol/receipts.py`.

Each test maps to a security claim made in the paper. When a reviewer
asks "how do you know your protocol catches X?", the answer is:
"experiment N in §6 plus the property test below".
"""

from __future__ import annotations

import json
import os
import time
import sys
import pathlib

# Make `protocol` importable when running pytest from repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest
import nacl.signing

from protocol.receipts import (  # noqa: E402
    DEFAULT_MAX_AGE_NS,
    Receipt,
    ReceiptSigner,
    ReceiptVerifier,
    SignedReceipt,
    build_receipt,
    fresh_nonce,
    sha256_hex,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


APPROVED_MODEL_BYTES = b"yolov8n-weights-v1-canonical"
APPROVED_MODEL_HASH = sha256_hex(APPROVED_MODEL_BYTES)


@pytest.fixture
def alpha_signer() -> ReceiptSigner:
    return ReceiptSigner()


@pytest.fixture
def bravo_signer() -> ReceiptSigner:
    return ReceiptSigner()


@pytest.fixture
def verifier(alpha_signer: ReceiptSigner, bravo_signer: ReceiptSigner) -> ReceiptVerifier:
    return ReceiptVerifier(
        peer_keys={
            "alpha": alpha_signer.public_key_hex,
            "bravo": bravo_signer.public_key_hex,
        },
        approved_models={APPROVED_MODEL_HASH},
    )


def _sample_receipt(drone_id: str = "alpha") -> Receipt:
    return build_receipt(
        drone_id=drone_id,
        input_bytes=b"\x00" * 128,  # stand-in for a camera frame
        model_hash=APPROVED_MODEL_HASH,
        output=(0.1, -0.3, 0.5),
    )


# ---------------------------------------------------------------------------
# Property 1: Round-trip integrity
#   Claim: a freshly-signed receipt verifies successfully.
# ---------------------------------------------------------------------------


def test_signed_receipt_verifies(alpha_signer, verifier):
    receipt = _sample_receipt("alpha")
    signed = alpha_signer.sign(receipt)

    result = verifier.verify(signed)
    assert result.ok, result.reason


def test_wire_serialization_roundtrip(alpha_signer, verifier):
    receipt = _sample_receipt("alpha")
    signed = alpha_signer.sign(receipt)

    wire = signed.serialize()
    deserialized = SignedReceipt.deserialize(wire)

    assert verifier.verify(deserialized).ok


# ---------------------------------------------------------------------------
# Property 2: Tamper detection
#   Claim: modifying ANY field after signing breaks verification.
#   Maps to: paper §6 Experiment 2 (model-swap detection),
#            and the more general integrity guarantee.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_to_tamper, new_value",
    [
        ("drone_id", "bravo"),
        ("timestamp_ns", 999_999),
        ("input_hash", "f" * 64),
        ("model_hash", "e" * 64),
        ("output", (9.9, 9.9, 9.9)),
        ("nonce", "a" * 32),
    ],
)
def test_tampered_receipt_fails(alpha_signer, verifier, field_to_tamper, new_value):
    receipt = _sample_receipt("alpha")
    signed = alpha_signer.sign(receipt)

    # Build a tampered receipt with the same signature attached.
    tampered_fields = {**signed.receipt.__dict__, field_to_tamper: new_value}
    tampered = Receipt(**tampered_fields)
    forged = SignedReceipt(receipt=tampered, signature=signed.signature)

    result = verifier.verify(forged)
    assert not result.ok
    # Different fields trip different branches. All are valid rejections:
    #   drone_id      -> unknown_drone_id if the new id isn't registered,
    #                    else bad_signature
    #   timestamp_ns  -> stale_receipt, because freshness is checked before
    #                    the signature so a replay flood costs no curve
    #                    arithmetic (see ReceiptVerifier.verify, step 0)
    #   everything else -> bad_signature
    assert result.reason.startswith(
        ("bad_signature", "unknown_drone_id", "stale_receipt")
    )


# ---------------------------------------------------------------------------
# Property 3: Cross-signer forgery resistance
#   Claim: a receipt signed by Bravo cannot impersonate Alpha.
# ---------------------------------------------------------------------------


def test_cross_signer_forgery_rejected(alpha_signer, bravo_signer, verifier):
    receipt = _sample_receipt("alpha")  # claims to be from alpha
    forged = bravo_signer.sign(receipt)  # but signed by bravo's key

    result = verifier.verify(forged)
    assert not result.ok
    assert result.reason == "bad_signature"


# ---------------------------------------------------------------------------
# Property 4: Model-hash allowlist enforcement
#   Claim: a perfectly-signed receipt with a non-approved model_hash
#          is rejected. Maps to paper §6 Experiment 2.
# ---------------------------------------------------------------------------


def test_unapproved_model_hash_rejected(alpha_signer, verifier):
    malicious_model_hash = sha256_hex(b"malicious-yolov8n-backdoored")
    receipt = build_receipt(
        drone_id="alpha",
        input_bytes=b"\x00" * 128,
        model_hash=malicious_model_hash,
        output=(0.0, 0.0, 0.0),
    )
    signed = alpha_signer.sign(receipt)

    result = verifier.verify(signed)
    assert not result.ok
    assert result.reason.startswith("model_hash_not_approved")


# ---------------------------------------------------------------------------
# Property 5: Unknown sender rejected
# ---------------------------------------------------------------------------


def test_unknown_drone_id_rejected(verifier):
    rogue = ReceiptSigner()
    receipt = build_receipt(
        drone_id="charlie",  # not in verifier's registry
        input_bytes=b"\x00" * 128,
        model_hash=APPROVED_MODEL_HASH,
        output=(0.0, 0.0, 0.0),
    )
    signed = rogue.sign(receipt)

    result = verifier.verify(signed)
    assert not result.ok
    assert result.reason.startswith("unknown_drone_id")


# ---------------------------------------------------------------------------
# Property 6: Canonical serialization determinism
#   Claim: identical inputs produce byte-identical canonical bytes.
#   This is required for Ed25519 to be deterministic across nodes.
# ---------------------------------------------------------------------------


def test_canonical_is_deterministic():
    fixed_kwargs = dict(
        drone_id="alpha",
        timestamp_ns=1_716_850_000_123_000_000,
        input_hash="a" * 64,
        model_hash="b" * 64,
        output=(0.1, -0.3, 0.5),
        nonce="c" * 32,
    )
    r1 = Receipt(**fixed_kwargs)
    r2 = Receipt(**fixed_kwargs)
    assert r1.canonical() == r2.canonical()


def test_canonical_is_sorted_and_compact():
    receipt = _sample_receipt("alpha")
    canon = receipt.canonical().decode()
    # No whitespace.
    assert " " not in canon
    # Keys sorted: drone_id < input_hash < model_hash < nonce < output < timestamp_ns
    keys_in_order = [k for k in json.loads(canon).keys()]
    assert keys_in_order == sorted(keys_in_order)


# ---------------------------------------------------------------------------
# Property 7: Nonce uniqueness
#   Claim: two receipts built back-to-back with identical inputs differ.
# ---------------------------------------------------------------------------


def test_nonces_unique_across_calls():
    seen = {fresh_nonce() for _ in range(10_000)}
    assert len(seen) == 10_000


def test_repeated_build_yields_distinct_receipts():
    """Even with the same input and same model, the nonce differs."""
    a = build_receipt("alpha", b"x", APPROVED_MODEL_HASH, (0.0,))
    b = build_receipt("alpha", b"x", APPROVED_MODEL_HASH, (0.0,))
    assert a.canonical() != b.canonical()


# ---------------------------------------------------------------------------
# Property 8: Invariant enforcement
# ---------------------------------------------------------------------------


def test_rejects_empty_drone_id():
    with pytest.raises(ValueError):
        Receipt(
            drone_id="",
            timestamp_ns=0,
            input_hash="a" * 64,
            model_hash="b" * 64,
            output=(0.0,),
            nonce="c" * 32,
        )


def test_rejects_malformed_hash():
    with pytest.raises(ValueError):
        Receipt(
            drone_id="alpha",
            timestamp_ns=0,
            input_hash="short",
            model_hash="b" * 64,
            output=(0.0,),
            nonce="c" * 32,
        )


# ---------------------------------------------------------------------------
# Property 9: Replay resistance
#   Claim (paper, S3.3): a previously accepted receipt cannot be re-used.
#   The signature on a captured receipt stays valid forever, so replay is
#   stopped by the verifier's freshness window, not by cryptography.
# ---------------------------------------------------------------------------


def test_fresh_receipt_passes_the_freshness_window(alpha_signer, verifier):
    signed = alpha_signer.sign(_sample_receipt("alpha"))
    assert verifier.verify(signed).ok


def test_replayed_receipt_is_rejected_as_stale(alpha_signer, verifier):
    """A captured receipt, re-sent later, must not verify."""
    signed = alpha_signer.sign(_sample_receipt("alpha"))

    # It verifies at the moment it was made.
    assert verifier.verify(signed).ok

    # The attacker replays it one second past the window.
    replay_at = signed.receipt.timestamp_ns + DEFAULT_MAX_AGE_NS + 1_000_000_000
    result = verifier.verify(signed, now_ns=replay_at)

    assert not result.ok
    assert result.reason == "stale_receipt"


def test_replay_fails_even_though_the_signature_is_still_valid(alpha_signer, verifier):
    """
    The point of the property: nothing about the receipt is forged. Its
    signature verifies for all time. Only the freshness window rejects it.
    """
    signed = alpha_signer.sign(_sample_receipt("alpha"))

    # Signature alone still checks out, which is exactly the problem.
    no_window = ReceiptVerifier(
        peer_keys={"alpha": alpha_signer.public_key_hex},
        approved_models={APPROVED_MODEL_HASH},
        max_age_ns=0,
    )
    old = signed.receipt.timestamp_ns + 10 * DEFAULT_MAX_AGE_NS
    assert no_window.verify(signed, now_ns=old).ok

    # With the window on, the same receipt at the same instant is refused.
    assert verifier.verify(signed, now_ns=old).reason == "stale_receipt"


def test_future_dated_receipt_is_also_rejected(alpha_signer, verifier):
    """The window is symmetric: a skewed or lying clock fails both ways."""
    signed = alpha_signer.sign(_sample_receipt("alpha"))
    ahead = signed.receipt.timestamp_ns - (DEFAULT_MAX_AGE_NS + 1_000_000_000)
    assert verifier.verify(signed, now_ns=ahead).reason == "stale_receipt"


def test_receipt_inside_the_window_still_verifies(alpha_signer, verifier):
    """Boundary: legitimate in-flight latency must not be read as an attack."""
    signed = alpha_signer.sign(_sample_receipt("alpha"))
    just_inside = signed.receipt.timestamp_ns + DEFAULT_MAX_AGE_NS - 1
    assert verifier.verify(signed, now_ns=just_inside).ok


def test_freshness_is_checked_before_the_signature(alpha_signer, verifier):
    """
    Ordering matters for denial of service: a replay flood should be shed
    without paying for an Ed25519 verification each time.
    """
    signed = alpha_signer.sign(_sample_receipt("alpha"))
    tampered = SignedReceipt(receipt=signed.receipt, signature="00" * 64)

    stale = signed.receipt.timestamp_ns + 10 * DEFAULT_MAX_AGE_NS
    # Signature is garbage AND the receipt is stale; staleness must win.
    assert verifier.verify(tampered, now_ns=stale).reason == "stale_receipt"
