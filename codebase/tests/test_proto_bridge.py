"""
Round-trip tests for the protocol <-> protobuf bridge.

Critical property: round-tripping a SignedReceipt through protobuf must
*not* invalidate its signature. If the bridge silently re-orders fields
or loses precision, signatures break at the receiver.
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from protocol.receipts import (  # noqa: E402
    ReceiptSigner,
    ReceiptVerifier,
    build_receipt,
    sha256_hex,
)
from protocol.peer_consensus import (  # noqa: E402
    PeerVerifier,
    Vote,
    VoteVerifier,
)
from protocol.proto_bridge import (  # noqa: E402
    receipt_to_pb,
    receipt_from_pb,
    signed_receipt_to_pb,
    signed_receipt_from_pb,
    signed_vote_to_pb,
    signed_vote_from_pb,
)


APPROVED = sha256_hex(b"model-v1")


def test_receipt_roundtrip():
    alpha = ReceiptSigner()
    r = build_receipt(
        drone_id="alpha",
        input_bytes=b"\x00" * 64,
        model_hash=APPROVED,
        output=(0.1, -0.3, 0.5),
    )
    msg = receipt_to_pb(r)
    r2 = receipt_from_pb(msg)
    assert r2 == r
    # Canonical bytes are byte-identical -> signatures will still verify.
    assert r2.canonical() == r.canonical()


def test_signed_receipt_roundtrip_preserves_signature():
    alpha = ReceiptSigner()
    rv = ReceiptVerifier(
        peer_keys={"alpha": alpha.public_key_hex},
        approved_models={APPROVED},
    )

    r = build_receipt(
        drone_id="alpha",
        input_bytes=b"\xab" * 64,
        model_hash=APPROVED,
        output=(1.0, 0.2, -0.3),
    )
    sr = alpha.sign(r)

    msg = signed_receipt_to_pb(sr)
    sr2 = signed_receipt_from_pb(msg)
    assert sr2 == sr
    # And it still verifies under the original verifier.
    assert rv.verify(sr2).ok


def test_signed_vote_roundtrip_preserves_signature():
    alpha = ReceiptSigner()
    bravo = ReceiptSigner()
    rv = ReceiptVerifier(
        peer_keys={"alpha": alpha.public_key_hex, "bravo": bravo.public_key_hex},
        approved_models={APPROVED},
    )
    vv = VoteVerifier(
        peer_keys={"alpha": alpha.public_key_hex, "bravo": bravo.public_key_hex}
    )

    r = build_receipt(
        drone_id="alpha",
        input_bytes=b"\x11" * 64,
        model_hash=APPROVED,
        output=(0.0, 0.0, 0.0),
    )
    sr = alpha.sign(r)

    bravo_pv = PeerVerifier("bravo", bravo, rv)
    sv = bravo_pv.vote_on(sr, my_observation=(0.0, 0.0, 0.0))
    assert sv.vote.decision is Vote.ACK

    msg = signed_vote_to_pb(sv)
    sv2 = signed_vote_from_pb(msg)
    assert sv2 == sv
    assert vv.verify(sv2)


def test_dispute_vote_roundtrip():
    alpha = ReceiptSigner()
    bravo = ReceiptSigner()
    rv = ReceiptVerifier(
        peer_keys={"alpha": alpha.public_key_hex, "bravo": bravo.public_key_hex},
        approved_models={APPROVED},
    )

    bad_hash = sha256_hex(b"malicious-model")
    r = build_receipt(
        drone_id="alpha",
        input_bytes=b"\x00" * 64,
        model_hash=bad_hash,
        output=(0.0, 0.0, 0.0),
    )
    sr = alpha.sign(r)

    bravo_pv = PeerVerifier("bravo", bravo, rv)
    sv = bravo_pv.vote_on(sr)
    assert sv.vote.decision is Vote.DISPUTE

    msg = signed_vote_to_pb(sv)
    sv2 = signed_vote_from_pb(msg)
    assert sv2 == sv
    assert sv2.vote.decision is Vote.DISPUTE
