"""Signer backend and manifest-identity binding regressions."""

from __future__ import annotations

import nacl.signing
import pytest

from node import common


def _software_entry() -> dict:
    key = nacl.signing.SigningKey.generate()
    return {
        "backend": "software",
        "seed": bytes(key).hex(),
        "pubkey": bytes(key.verify_key).hex(),
    }


def test_software_signer_is_bound_to_manifest_public_key():
    entry = _software_entry()
    signer = common.signer_for(entry)
    assert signer.public_key_hex == entry["pubkey"]


def test_software_seed_public_key_mismatch_fails_before_mission():
    entry = _software_entry()
    entry["pubkey"] = bytes(nacl.signing.SigningKey.generate().verify_key).hex()
    with pytest.raises(ValueError, match="does not match the manifest identity"):
        common.signer_for(entry)


def test_unknown_signer_backend_is_rejected():
    entry = _software_entry()
    entry["backend"] = "remote-unverified"
    with pytest.raises(ValueError, match="unsupported signer backend"):
        common.signer_for(entry)


def test_missing_software_seed_is_rejected_cleanly():
    entry = _software_entry()
    entry.pop("seed")
    with pytest.raises(ValueError, match="software signer requires"):
        common.signer_for(entry)


def test_optee_key_must_match_manifest(monkeypatch):
    import signing.optee_backend as optee_backend

    actual = nacl.signing.SigningKey.generate()

    class FakeOPTEESigner:
        public_key_hex = bytes(actual.verify_key).hex()

    monkeypatch.setattr(optee_backend, "OPTEEReceiptSigner", FakeOPTEESigner)
    entry = {
        "backend": "optee",
        "pubkey": bytes(nacl.signing.SigningKey.generate().verify_key).hex(),
    }
    with pytest.raises(ValueError, match="does not match the manifest identity"):
        common.signer_for(entry)

