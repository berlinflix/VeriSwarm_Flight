"""
Provenance identity and the mission-authority separation of duties.

Two properties are under test, and they are the two halves of what makes a
compromised provisioning host a *catchable* attack:

  1. A live node's provenance value is the hash of the weight bytes it actually
     loaded. Hashing a label instead would let swapped weights hash to the
     approved constant, and the provenance layer would pass on a trojaned model.

  2. The allowlist comes from a mission authority that the provisioner does not
     write. An attacker who owns the provisioning host controls the weights but
     not the list they are checked against, so the tampered model is delivered
     and still fails at every peer.
"""

from __future__ import annotations

import json

import pytest

from node import common
from protocol.receipts import build_receipt


# ---------------------------------------------------------------------------
# 1. Provenance is derived from the delivered bytes
# ---------------------------------------------------------------------------


def _write_weights(tmp_path, name: str, payload: bytes):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_file_hash_tracks_content_not_name(tmp_path):
    """Two files with the same name in different places hash by content."""
    good = _write_weights(tmp_path, "yolov8n.pt", b"honest weights")
    other = tmp_path / "sub"
    other.mkdir()
    trojan = _write_weights(other, "yolov8n.pt", b"trojaned weights")

    assert common.file_model_hash(good) != common.file_model_hash(trojan)


def test_file_hash_changes_when_weights_are_swapped(tmp_path):
    """The whole attack is a file swap; the hash must move when the bytes do."""
    weights = _write_weights(tmp_path, "yolov8n.pt", b"honest weights")
    before = common.file_model_hash(weights)

    weights.write_bytes(b"trojaned weights")
    after = common.file_model_hash(weights)

    assert before != after, "cache must key on size/mtime, not path alone"


def test_label_hash_is_refused_when_an_authority_is_in_use(tmp_path):
    """
    The regression this guards against: wiring a live node to the synthetic
    harness constant out of habit. Under an authority minted from real weights,
    a receipt carrying the label hash fails provenance — so the mistake surfaces
    as a rejected receipt rather than as a provenance layer that silently passes
    everything.
    """
    weights = _write_weights(tmp_path, "yolov8n.pt", b"honest weights")
    authority = _authority(tmp_path, common.file_model_hash(weights))
    manifest = common.generate_manifest(["alpha", "bravo"], authority=str(authority))

    approved = common.approved_models_of(manifest)

    assert common.file_model_hash(weights) in approved
    assert common.APPROVED_MODEL not in approved


# ---------------------------------------------------------------------------
# 2. The authority is separate from, and beats, the manifest
# ---------------------------------------------------------------------------


def _authority(tmp_path, *hashes, name="mission_authority.json"):
    path = tmp_path / name
    path.write_text(json.dumps({
        "issued_by": "test",
        "approved_models": [
            {"name": f"m{i}", "sha256": h} for i, h in enumerate(hashes)
        ],
    }))
    return path


def test_authority_overrides_manifest_allowlist(tmp_path):
    """
    A manifest written by a compromised provisioner may claim anything. When an
    authority is present it is the allowlist, and the manifest's own list is
    ignored entirely.
    """
    authority = _authority(tmp_path, common.APPROVED_MODEL)
    manifest = common.generate_manifest(
        ["alpha", "bravo"],
        approved_models=[common.MALICIOUS_MODEL],  # attacker-supplied
        authority=str(authority),
    )

    approved = common.approved_models_of(manifest)

    assert approved == {common.APPROVED_MODEL}
    assert common.MALICIOUS_MODEL not in approved


def test_trojaned_weights_fail_provenance_against_the_authority(tmp_path):
    """
    End to end: the provisioner delivers tampered weights, the node honestly
    reports their hash, and the peer rejects on provenance because the authority
    never blessed that hash.
    """
    honest = _write_weights(tmp_path, "yolov8n.pt", b"honest weights")
    trojan = _write_weights(tmp_path, "yolov8n_tampered.pt", b"trojaned weights")
    authority = _authority(tmp_path, common.file_model_hash(honest))

    manifest = common.generate_manifest(["alpha", "bravo"], authority=str(authority))
    verifier = common.receipt_verifier_for(manifest)
    signer = common.signer_for(manifest["nodes"]["alpha"])

    # The compromised aircraft is not lying about what it ran — that is the
    # point. It ran the weights it was given, and reports their real hash.
    receipt = build_receipt(
        drone_id="alpha", input_bytes=b"frame",
        model_hash=common.file_model_hash(trojan), output=(1.0, 0.0, 0.0),
    )
    result = verifier.verify(signer.sign(receipt))

    assert not result.ok
    assert result.reason.startswith("model_hash_not_approved")


def test_authority_refuses_path_entries(tmp_path):
    """
    A path is resolved on the node, against a file an attacker may have
    replaced. Pinning one would re-admit the substitution the file prevents.
    """
    path = tmp_path / "mission_authority.json"
    path.write_text(json.dumps({
        "approved_models": [
            {"name": "yolov8n", "sha256": "a" * 64, "path": "yolov8n.pt"}
        ]
    }))

    with pytest.raises(common.AuthorityError, match="carries a 'path'"):
        common.load_authority(path)


def test_missing_authority_fails_loudly(tmp_path):
    with pytest.raises(common.AuthorityError, match="not found"):
        common.load_authority(tmp_path / "absent.json")


def test_manifest_allowlist_still_works_without_an_authority():
    """The harness path is unchanged: no authority means the manifest's list."""
    manifest = common.generate_manifest(["alpha", "bravo"])
    assert common.approved_models_of(manifest) == {common.APPROVED_MODEL}


# ---------------------------------------------------------------------------
# 3. Signing — tampering with the allowlist must be detectable
# ---------------------------------------------------------------------------


def _signed_authority(tmp_path, *hashes, name="mission_authority.json"):
    """Mint a signed authority; returns (path, pubkey_hex)."""
    import nacl.signing

    from tools.make_authority import sign_payload

    key = nacl.signing.SigningKey.generate()
    payload = {
        "issued_by": "test-authority",
        "approved_models": [{"name": f"m{i}", "sha256": h} for i, h in enumerate(hashes)],
    }
    path = tmp_path / name
    path.write_text(json.dumps(sign_payload(payload, bytes(key).hex())))
    return path, bytes(key.verify_key).hex()


def test_signed_authority_verifies(tmp_path):
    path, pubkey = _signed_authority(tmp_path, common.APPROVED_MODEL)
    payload = common.load_authority(path, authority_pubkey=pubkey)
    assert payload["approved_models"][0]["sha256"] == common.APPROVED_MODEL


def test_appending_a_hash_breaks_the_signature(tmp_path):
    """
    The attack this exists to stop: someone with filesystem write access adds
    their own model hash so their trojaned weights pass provenance.
    """
    path, pubkey = _signed_authority(tmp_path, common.APPROVED_MODEL)

    doc = json.loads(path.read_text())
    doc["payload"]["approved_models"].append(
        {"name": "attacker", "sha256": common.MALICIOUS_MODEL}
    )
    path.write_text(json.dumps(doc))

    with pytest.raises(common.AuthorityError, match="failed signature verification"):
        common.load_authority(path, authority_pubkey=pubkey)


def test_a_different_authority_key_is_rejected(tmp_path):
    """Signing it yourself does not make you the mission authority."""
    import nacl.signing

    path, _ = _signed_authority(tmp_path, common.APPROVED_MODEL)
    impostor = bytes(nacl.signing.SigningKey.generate().verify_key).hex()

    with pytest.raises(common.AuthorityError, match="failed signature verification"):
        common.load_authority(path, authority_pubkey=impostor)


def test_unsigned_file_is_refused_when_a_key_is_pinned(tmp_path):
    """
    Stripping the signature must not be a way to downgrade to the unsigned path.
    """
    path = _authority(tmp_path, common.APPROVED_MODEL)  # unsigned form

    with pytest.raises(common.AuthorityError, match="unsigned"):
        common.load_authority(path, authority_pubkey="ab" * 32)


def test_unsigned_file_still_loads_for_local_development(tmp_path):
    path = _authority(tmp_path, common.APPROVED_MODEL)
    payload = common.load_authority(path)
    assert payload["approved_models"][0]["sha256"] == common.APPROVED_MODEL


def test_signature_is_checked_through_the_verifier_path(tmp_path):
    """End to end: a tampered allowlist fails when a node builds its verifier."""
    path, pubkey = _signed_authority(tmp_path, common.APPROVED_MODEL)
    doc = json.loads(path.read_text())
    doc["payload"]["approved_models"][0]["sha256"] = common.MALICIOUS_MODEL
    path.write_text(json.dumps(doc))

    manifest = common.generate_manifest(["alpha", "bravo"], authority=str(path))

    with pytest.raises(common.AuthorityError):
        common.receipt_verifier_for(manifest, authority_pubkey=pubkey)
