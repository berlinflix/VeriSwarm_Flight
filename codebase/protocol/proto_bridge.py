"""
Bridge between in-memory protocol dataclasses and the protobuf wire
messages generated from `proto/attestation.proto`.

We keep the dataclasses (`Receipt`, `SignedReceipt`, `PeerVote`,
`SignedVote`) as the source of truth: they own canonical serialization
and the signature contract. Protobuf is purely a transport.

Encoding rules:
- `Receipt.output` (Sequence[float]) <-> repeated double `output`
- All hash / nonce / signature fields are hex strings on both sides.
- Enum: protocol Vote.ACK/DISPUTE <-> proto Vote.ACK/DISPUTE.

Round-trip invariant
--------------------
For any honest SignedReceipt `sr`:
    from_pb(to_pb(sr)) == sr

(Tested in `tests/test_proto_bridge.py`.)
"""

from __future__ import annotations

from . import attestation_pb2 as pb
from .receipts import Receipt, SignedReceipt
from .peer_consensus import PeerVote, SignedVote, Vote


# ---------------------------------------------------------------------------
# Receipt <-> proto.Receipt
# ---------------------------------------------------------------------------


def receipt_to_pb(r: Receipt) -> pb.Receipt:
    msg = pb.Receipt(
        drone_id=r.drone_id,
        timestamp_ns=r.timestamp_ns,
        input_hash=r.input_hash,
        model_hash=r.model_hash,
        nonce=r.nonce,
    )
    msg.output.extend(r.output)
    return msg


def receipt_from_pb(msg: pb.Receipt) -> Receipt:
    return Receipt(
        drone_id=msg.drone_id,
        timestamp_ns=msg.timestamp_ns,
        input_hash=msg.input_hash,
        model_hash=msg.model_hash,
        output=tuple(msg.output),
        nonce=msg.nonce,
    )


def signed_receipt_to_pb(sr: SignedReceipt) -> pb.SignedReceipt:
    return pb.SignedReceipt(
        receipt=receipt_to_pb(sr.receipt),
        signature_hex=sr.signature,
    )


def signed_receipt_from_pb(msg: pb.SignedReceipt) -> SignedReceipt:
    return SignedReceipt(
        receipt=receipt_from_pb(msg.receipt),
        signature=msg.signature_hex,
    )


# ---------------------------------------------------------------------------
# Vote <-> proto.PeerVote / proto.Vote
# ---------------------------------------------------------------------------


_VOTE_TO_PB = {Vote.ACK: pb.ACK, Vote.DISPUTE: pb.DISPUTE}
_VOTE_FROM_PB = {pb.ACK: Vote.ACK, pb.DISPUTE: Vote.DISPUTE}


def peer_vote_to_pb(v: PeerVote) -> pb.PeerVote:
    return pb.PeerVote(
        voter_id=v.voter_id,
        target_receipt_hash=v.target_receipt_hash,
        decision=_VOTE_TO_PB[v.decision],
        reason=v.reason,
        timestamp_ns=v.timestamp_ns,
    )


def peer_vote_from_pb(msg: pb.PeerVote) -> PeerVote:
    decision_pb = msg.decision
    if decision_pb not in _VOTE_FROM_PB:
        raise ValueError(f"unsupported proto Vote enum: {decision_pb}")
    return PeerVote(
        voter_id=msg.voter_id,
        target_receipt_hash=msg.target_receipt_hash,
        decision=_VOTE_FROM_PB[decision_pb],
        reason=msg.reason,
        timestamp_ns=msg.timestamp_ns,
    )


def signed_vote_to_pb(sv: SignedVote) -> pb.SignedVote:
    return pb.SignedVote(
        vote=peer_vote_to_pb(sv.vote),
        signature_hex=sv.signature,
    )


def signed_vote_from_pb(msg: pb.SignedVote) -> SignedVote:
    return SignedVote(
        vote=peer_vote_from_pb(msg.vote),
        signature=msg.signature_hex,
    )
