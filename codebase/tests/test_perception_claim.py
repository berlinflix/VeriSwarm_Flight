"""
Perception claims, and the binding between evidence and the command it releases.

Two properties, both of which used to be missing:

1. The semantic layer compares **observations**, not decisions. Comparing control
   outputs let a blinded drone and an honest one agree, because the avoidance
   action passes through the origin as threat rises.
2. An accepted perception certificate authorizes **the command it was earned
   for**, and no other. A valid signature proves an observation is authentic; it
   does not prove the observation is about the command being released.
"""

from __future__ import annotations

import pytest

from perception.claim import (
    DEFAULT_OCCUPANCY_TOLERANCE,
    PerceptionClaim,
    claims_agree,
)
from perception.yolo_action import Detection
from protocol.receipts import PROTOCOL_VERSION, build_receipt, sha256_hex

APPROVED = sha256_hex(b"yolov8n-weights-v1")


def _det(size=0.6, x=0.5, conf=0.9, cls=5):
    return Detection(cls=cls, x=x, y=0.7, w=size, h=size, conf=conf)


# ---------------------------------------------------------------------------
# Claim construction
# ---------------------------------------------------------------------------


def test_empty_scene_is_measured_but_reports_no_detections():
    claim = PerceptionClaim.from_detections([])
    assert claim.measured is True
    assert claim.detections_present is False
    assert claim.detection_count == 0


def test_unmeasured_is_not_the_same_as_an_empty_scene():
    """
    'I did not look' and 'I looked and saw nothing' are different statements.
    Collapsing them is the original blind band, one layer up.
    """
    assert PerceptionClaim.unmeasured().measured is False
    assert PerceptionClaim.from_detections([]).measured is True


def test_claim_summarises_detections():
    claim = PerceptionClaim.from_detections([_det(0.5), _det(0.2, x=0.1, conf=0.4)])
    assert claim.detections_present and claim.detection_count == 2
    assert claim.occupancy == pytest.approx(0.25 + 0.04)
    assert claim.max_confidence == pytest.approx(0.9)
    assert claim.class_ids == (5,)


def test_occupancy_is_clipped_to_one():
    huge = [Detection(cls=5, x=0.5, y=0.5, w=1.0, h=1.0, conf=0.9)] * 3
    assert PerceptionClaim.from_detections(huge).occupancy == 1.0


@pytest.mark.parametrize("kwargs", [
    {"occupancy": 1.5},
    {"occupancy": float("nan")},
    {"max_confidence": -0.1},
    {"bearing": 2.0},
    {"detection_count": -1},
    {"class_ids": (2, 1)},
    {"measured": False, "detections_present": True, "detection_count": 1},
    {"measured": True, "detections_present": True, "detection_count": 0},
])
def test_malformed_claims_are_rejected_at_construction(kwargs):
    """These cross the wire into another aircraft's decision."""
    with pytest.raises(ValueError):
        PerceptionClaim(**{"measured": True, **kwargs})


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def test_presence_disagreement_disputes_regardless_of_size():
    """The signal that closes the blind band. Independent of obstacle scale."""
    for size in (0.1, 0.3, 0.5, 0.6, 0.8, 1.0):
        assert claims_agree(
            PerceptionClaim.from_detections([]),
            PerceptionClaim.from_detections([_det(size)]),
        ) is False


def test_both_empty_agree():
    empty = PerceptionClaim.from_detections([])
    assert claims_agree(empty, empty) is True


def test_similar_occupancy_agrees():
    assert claims_agree(
        PerceptionClaim.from_detections([_det(0.60)]),
        PerceptionClaim.from_detections([_det(0.65)]),
    ) is True


def test_grossly_different_occupancy_disputes():
    assert claims_agree(
        PerceptionClaim.from_detections([_det(0.2)]),
        PerceptionClaim.from_detections([_det(1.0)]),
    ) is False


def test_same_presence_and_occupancy_but_different_classes_disputes():
    assert claims_agree(
        PerceptionClaim.from_detections([_det(0.5, cls=0)]),
        PerceptionClaim.from_detections([_det(0.5, cls=5)]),
    ) is False


def test_bearing_difference_alone_never_disputes():
    """
    Peers sit at >= phi_min of parallax, so the same obstacle legitimately
    appears at different bearings. Disputing on that would fire on honest peers,
    which is exactly the false positive the co-visibility gate exists to avoid.
    """
    left = PerceptionClaim.from_detections([_det(0.5, x=0.05)])
    right = PerceptionClaim.from_detections([_det(0.5, x=0.95)])
    assert left.bearing != right.bearing
    assert claims_agree(left, right) is True


@pytest.mark.parametrize("pair", [
    (PerceptionClaim.unmeasured(), PerceptionClaim.from_detections([])),
    (PerceptionClaim.from_detections([]), PerceptionClaim.unmeasured()),
    (None, PerceptionClaim.from_detections([])),
    (PerceptionClaim.from_detections([]), None),
])
def test_missing_evidence_draws_no_conclusion(pair):
    assert claims_agree(*pair) is None


def test_tolerance_must_be_positive():
    empty = PerceptionClaim.from_detections([])
    with pytest.raises(ValueError):
        claims_agree(empty, empty, occupancy_tolerance=0.0)


# ---------------------------------------------------------------------------
# Binding to the receipt
# ---------------------------------------------------------------------------


def test_receipt_carries_the_claim_and_bumped_the_protocol():
    claim = PerceptionClaim.from_detections([_det(0.7)])
    receipt = build_receipt(
        drone_id="alpha", input_bytes=b"frame", model_hash=APPROVED,
        output=(0.0, 0.0, 0.0), perception=claim,
    )
    assert PROTOCOL_VERSION == 4
    assert receipt.perception == claim


def test_receipt_defaults_to_unmeasured_not_empty():
    receipt = build_receipt(
        drone_id="alpha", input_bytes=b"frame", model_hash=APPROVED,
        output=(0.0, 0.0, 0.0),
    )
    assert receipt.perception.measured is False


def test_claim_is_covered_by_the_signature(tmp_path):
    """
    Tampering with the claim must invalidate the receipt. This is what makes the
    observation bound to its frame, model, runtime and round — a claim lifted out
    of one receipt cannot be replayed into another.
    """
    import dataclasses

    from protocol.receipts import ReceiptSigner, ReceiptVerifier

    signer = ReceiptSigner()
    verifier = ReceiptVerifier(
        peer_keys={"alpha": signer.public_key_hex}, approved_models={APPROVED}
    )
    receipt = build_receipt(
        drone_id="alpha", input_bytes=b"frame", model_hash=APPROVED,
        output=(0.0, 0.0, 0.0),
        perception=PerceptionClaim.from_detections([_det(0.7)]),
    )
    signed = signer.sign(receipt)
    assert verifier.verify(signed).ok

    forged = dataclasses.replace(
        signed, receipt=dataclasses.replace(
            receipt, perception=PerceptionClaim.from_detections([])
        )
    )
    assert not verifier.verify(forged).ok


def test_wire_roundtrip_preserves_the_claim():
    from protocol.receipts import ReceiptSigner, SignedReceipt

    signed = ReceiptSigner().sign(build_receipt(
        drone_id="alpha", input_bytes=b"frame", model_hash=APPROVED,
        output=(0.0, 0.0, 0.0),
        perception=PerceptionClaim.from_detections([_det(0.5)]),
    ))
    restored = SignedReceipt.deserialize(signed.serialize())
    assert restored == signed
    assert isinstance(restored.receipt.perception, PerceptionClaim)


def test_non_boolean_claim_flags_are_rejected():
    with pytest.raises(ValueError, match="must be booleans"):
        PerceptionClaim(measured=1)


def test_unmeasured_claim_cannot_smuggle_forensic_values():
    with pytest.raises(ValueError, match="cannot carry detection evidence"):
        PerceptionClaim(measured=False, max_confidence=0.9)


def test_empty_claim_cannot_smuggle_occupancy():
    with pytest.raises(ValueError, match="empty-scene"):
        PerceptionClaim(measured=True, occupancy=0.5)
