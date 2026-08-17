"""Hardware-preflight evidence logic, using an injectable software test key."""

from __future__ import annotations

import json

import pytest

from protocol.receipts import ReceiptSigner
from tools.optee_preflight import create_preflight_evidence, write_evidence


def test_preflight_signs_canonical_receipt_and_records_boundary(tmp_path):
    ca = tmp_path / "veriswarm_optee_ca"
    ca.write_bytes(b"reviewed-client-application")
    ta = tmp_path / "veriswarm.ta"
    ta.write_bytes(b"reviewed-trusted-application")
    signer = ReceiptSigner()

    evidence = create_preflight_evidence(
        signer=signer,
        expected_pubkey=signer.public_key_hex,
        ca_path=ca,
        ta_path=ta,
        mission_id="contested-border-001",
        mission_epoch=4,
        challenge=b"c" * 32,
    )

    assert evidence["signature_verified"] is True
    assert evidence["actual_pubkey"] == signer.public_key_hex
    assert len(evidence["ca_sha256"]) == 64
    assert len(evidence["ta_sha256"]) == 64
    assert evidence["receipt"]["drone_id"] == "alpha"
    assert evidence["receipt"]["mission_id"] == "contested-border-001"
    assert evidence["receipt"]["mission_epoch"] == 4
    assert "inference and pose were not attested" in evidence["claim_boundary"]


def test_preflight_refuses_unpinned_signer_key(tmp_path):
    ca = tmp_path / "veriswarm_optee_ca"
    ca.write_bytes(b"client")
    signer = ReceiptSigner()
    other = ReceiptSigner()

    with pytest.raises(RuntimeError, match="does not match the pinned Alpha key"):
        create_preflight_evidence(
            signer=signer,
            expected_pubkey=other.public_key_hex,
            ca_path=ca,
            mission_id="contested-border-001",
            mission_epoch=4,
        )


def test_preflight_evidence_is_non_overwriting(tmp_path):
    path = tmp_path / "optee-preflight.json"
    write_evidence(path, {"result": "first"})
    assert json.loads(path.read_text())["result"] == "first"

    with pytest.raises(FileExistsError):
        write_evidence(path, {"result": "replacement"})
    assert json.loads(path.read_text())["result"] == "first"
