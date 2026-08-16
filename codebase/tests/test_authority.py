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
