"""
Manifest handling and node construction for the live distributed runtime.

A *manifest* is the deployment roster every node is given at mission start (in a
real system, a ground-station-signed file). It lists, per drone:
  host, port            - where its AttestationService listens
  pubkey                - Ed25519 public key (so peers can verify it)
  seed                  - Ed25519 seed for a software signer (omitted for a
                          hardware/OP-TEE node, whose key lives in the secure
                          element)
  backend               - "software" | "optee"
  pose                  - [x, y, z, yaw] world pose, for the co-visibility gate
  observation           - the action this drone perceives for the current scene
                          (the experiment's stand-in for live per-drone perception)

Building signers and verifiers from a manifest reuses the exact `protocol/`
classes, so the distributed path and the in-process path are cryptographically
identical.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import nacl.signing

from protocol.receipts import (
    DEFAULT_MAX_AGE_NS,
    ReceiptSigner,
    ReceiptVerifier,
    sha256_hex,
)
from protocol.peer_consensus import VoteVerifier
from protocol.geometry import Pose

# ---------------------------------------------------------------------------
# Provenance values
# ---------------------------------------------------------------------------
#
# SYNTHETIC identities, for the experiment harness ONLY. They are hashes of a
# *label*, not of any weight file, and they match eval/harness.py so the
# distributed results line up with the in-process ones. They are the right thing
# to use when a run is exercising consensus arithmetic and the model is a stand-in.
#
# They are the WRONG thing to use anywhere a real model is loaded. A live node
# must report `file_model_hash(<the weights it actually loaded>)`, because the
# whole provenance layer rests on the receipt's model_hash being derived from
# the delivered bytes. Report a label instead and a swapped weight file still
# hashes to the approved constant — the check passes on a trojaned model, which
# is precisely the attack the layer exists to catch.
APPROVED_MODEL = sha256_hex(b"yolov8n-weights-v1")
MALICIOUS_MODEL = sha256_hex(b"yolov8n-backdoored")


_HASH_CACHE: Dict[tuple, str] = {}


def file_model_hash(weights_path: str | Path) -> str:
    """
    SHA-256 of an actual weight file — the provenance value a live node reports.

    Computed once per (path, size, mtime) and cached, mirroring the deployment
    rule that a node hashes its weights at boot and never re-hashes in flight:
    the in-memory model is immutable, so a mid-flight re-hash could only ever
    disagree with what was actually loaded.

    This is the same function as `perception.yolo_action.model_hash`, duplicated
    here so the node runtime does not have to import the perception stack (and
    transitively torch) just to identify a file.
    """
    p = Path(weights_path)
    stat = p.stat()
    key = (str(p.resolve()), stat.st_size, stat.st_mtime_ns)
    cached = _HASH_CACHE.get(key)
    if cached is not None:
        return cached
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _HASH_CACHE[key] = digest
    return digest


# ---------------------------------------------------------------------------
# Mission authority — the allowlist, and who is allowed to write it
# ---------------------------------------------------------------------------

DEFAULT_AUTHORITY_FILE = "mission_authority.json"


class AuthorityError(RuntimeError):
    """The mission authority file is missing, malformed, or unsafe."""


def authority_payload_bytes(payload: dict) -> bytes:
    """
    Canonical bytes of an authority payload — what the mission authority signs.

    Same discipline as `Receipt.canonical`: sorted keys, no whitespace, ASCII
    escaping. Signer and verifier must serialise byte-identically or valid
    signatures fail, and a single canonical form keeps the signed bytes readable
    by a human auditing the file.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def load_authority(
    path: str | Path, authority_pubkey: Optional[str] = None
) -> dict:
    """
    Load the mission authority: the signed-off list of model hashes a node will
    accept from any peer.

    Signing
    -------
    Pass `authority_pubkey` (hex Ed25519 verify key) and the file **must** carry
    a valid signature over its payload or this raises. Without that, the list is
    an ordinary file: anyone who can write to the drone's filesystem appends
    their own hash and the entire provenance layer evaporates silently, since
    every receipt then passes. The signature moves the trust from "nobody edited
    this file" to "the authority's private key signed this content", which is the
    only version that survives a compromised host.

    Where the public key comes from matters. It must NOT come from the manifest,
    which the provisioner writes — an attacker who can rewrite the allowlist can
    equally rewrite the key it is checked against. In deployment it belongs in
    OP-TEE secure storage beside the signing key; `VERISWARM_AUTHORITY_PUBKEY`
    is the development stand-in.

    Omitting `authority_pubkey` accepts an unsigned file. That is for local
    development and the experiment harness, where there is no provisioner to
    distrust; do not fly it.

    Why this is a separate file from the manifest
    ---------------------------------------------
    The manifest is written by whatever provisions the aircraft. The authority
    is not. That separation is the entire reason a compromised provisioning host
    is a *catchable* attack rather than a total one: an attacker who owns the
    ground-crew workstation controls which weights get flashed onto every SD
    card it touches, but not the list those weights are checked against. The
    trojaned model is delivered successfully and still fails provenance at every
    peer, because the peers are comparing against a list the attacker never had.

    Collapse the two files into one and that property is gone — the provisioner
    would simply add its own hash to the allowlist and the layer would pass.

    Format — signed::

        {"payload": {"issued_by": "...", "approved_models": [...]},
         "signature": "<hex Ed25519 over authority_payload_bytes(payload)>"}

    Format — unsigned (development)::

        {"issued_by": "...", "approved_models": [{"name": "...", "sha256": "..."}]}

    Entries carry hashes, never paths. A path would be resolved on the node at
    load time, against a file the attacker may control — which would re-admit
    exactly the substitution this file exists to prevent.
    """
    p = Path(path)
    if not p.exists():
        raise AuthorityError(
            f"mission authority not found: {p}\n"
            f"Mint one from known-good weights:  python -m tools.make_authority "
            f"--out {p} yolov8n.pt"
        )
    try:
        doc = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise AuthorityError(f"mission authority {p} is not valid JSON: {exc}") from exc

    signed = isinstance(doc.get("payload"), dict)
    payload = doc["payload"] if signed else doc

    if authority_pubkey:
        if not signed or "signature" not in doc:
            raise AuthorityError(
                f"mission authority {p} is unsigned, but a public key was pinned. "
                f"Refusing to trust it — an unsigned allowlist is writable by "
                f"anyone with filesystem access. Re-mint with "
                f"`python -m tools.make_authority --sign-key <hex> ...`"
            )
        import nacl.exceptions
        import nacl.signing

        try:
            nacl.signing.VerifyKey(bytes.fromhex(authority_pubkey)).verify(
                authority_payload_bytes(payload), bytes.fromhex(doc["signature"])
            )
        except (nacl.exceptions.BadSignatureError, ValueError) as exc:
            raise AuthorityError(
                f"mission authority {p} failed signature verification against "
                f"{authority_pubkey[:16]}...: the file has been modified, or it "
                f"was issued by a different authority. ({exc})"
            ) from exc

    entries = payload.get("approved_models")
    if not isinstance(entries, list) or not entries:
        raise AuthorityError(f"mission authority {p} lists no approved_models")
    for entry in entries:
        if not isinstance(entry, dict) or "sha256" not in entry:
            raise AuthorityError(
                f"mission authority {p}: every entry needs a 'sha256' field; got {entry!r}"
            )
        if "path" in entry:
            raise AuthorityError(
                f"mission authority {p}: entry {entry.get('name', '?')!r} carries a "
                f"'path'. The authority must pin hashes, not paths — a path is "
                f"resolved on the node against a file an attacker may have replaced."
            )
        if len(entry["sha256"]) != 64:
            raise AuthorityError(
                f"mission authority {p}: {entry['sha256']!r} is not a 64-char SHA-256"
            )
    return payload


def authority_pubkey_from_env() -> Optional[str]:
    """Development stand-in for a TEE-anchored authority key."""
    import os

    return os.environ.get("VERISWARM_AUTHORITY_PUBKEY") or None


def approved_models_of(
    manifest: dict,
    authority_path: Optional[str | Path] = None,
    authority_pubkey: Optional[str] = None,
) -> set:
    """
    Resolve the approved-model allowlist for this node.

    Precedence: an explicit `authority_path`, then `manifest["authority"]`, then
    the manifest's own `approved_models`. The last is the legacy path — fine for
    the experiment harness, where the manifest is generated by the experiment
    itself and there is no provisioner to distrust. Any deployment that models a
    hostile provisioning host must set an authority file.

    The verify key comes from the caller or `VERISWARM_AUTHORITY_PUBKEY`, never
    from the manifest: the manifest is provisioner-written, so reading the key
    from it would let whoever forged the allowlist supply the key that validates
    it.
    """
    src = authority_path or manifest.get("authority")
    if src:
        pubkey = authority_pubkey or authority_pubkey_from_env()
        payload = load_authority(src, authority_pubkey=pubkey)
        return {e["sha256"] for e in payload["approved_models"]}
    return set(manifest.get("approved_models", ()))


def generate_manifest(
    ids: Sequence[str],
    *,
    base_port: int = 51000,
    host: str = "127.0.0.1",
    poses: Optional[Mapping[str, Sequence[float]]] = None,
    observations: Optional[Mapping[str, Sequence[float]]] = None,
    approved_models: Iterable[str] = (APPROVED_MODEL,),
    o_min: float = 0.1,
    phi_min: float = 0.0,
    authority: Optional[str] = None,
) -> dict:
    """Build a manifest with a fresh software keypair per node.

    `authority` is the path to a `mission_authority.json`. When set, it is the
    allowlist every node uses and the manifest's own `approved_models` is
    ignored — see :func:`load_authority` for why that separation matters.
    """
    nodes: Dict[str, dict] = {}
    for i, nid in enumerate(ids):
        sk = nacl.signing.SigningKey.generate()
        nodes[nid] = {
            "host": host,
            "port": base_port + i,
            "pubkey": bytes(sk.verify_key).hex(),
            "seed": bytes(sk).hex(),
            "backend": "software",
            "pose": list(poses[nid]) if poses and nid in poses else None,
            "observation": (
                list(observations[nid]) if observations and nid in observations else None
            ),
        }
    manifest = {
        "approved_models": list(approved_models),
        "o_min": o_min,
        "phi_min": phi_min,
        "nodes": nodes,
    }
    if authority:
        manifest["authority"] = str(authority)
    return manifest


def save_manifest(manifest: dict, path: str | Path) -> Path:
    p = Path(path)
    p.write_text(json.dumps(manifest, indent=2))
    return p


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def peer_keys_of(manifest: dict) -> Dict[str, str]:
    return {nid: e["pubkey"] for nid, e in manifest["nodes"].items()}


def signer_for(entry: dict):
    """Build the signer for one node from its manifest entry."""
    if entry.get("backend") == "optee":
        from signing.optee_backend import OPTEEReceiptSigner

        return OPTEEReceiptSigner()
    sk = nacl.signing.SigningKey(bytes.fromhex(entry["seed"]))
    return ReceiptSigner(signing_key=sk)


def receipt_verifier_for(
    manifest: dict,
    authority_path: Optional[str | Path] = None,
    authority_pubkey: Optional[str] = None,
) -> ReceiptVerifier:
    return ReceiptVerifier(
        peer_keys=peer_keys_of(manifest),
        approved_models=approved_models_of(manifest, authority_path, authority_pubkey),
        max_age_ns=int(manifest.get("max_receipt_age_ns", DEFAULT_MAX_AGE_NS)),
    )


def vote_verifier_for(manifest: dict) -> VoteVerifier:
    return VoteVerifier(peer_keys=peer_keys_of(manifest))


def pose_of(entry: dict) -> Optional[Pose]:
    """Build a Pose from a manifest entry's `[x, y, z, yaw, pitch]`.

    `pitch` (camera tilt off nadir, radians) is optional and defaults to 0, so
    four-element poses from earlier manifests keep their nadir meaning exactly.
    """
    p = entry.get("pose")
    if not p:
        return None
    x, y, z, yaw, pitch = (list(p) + [0.0] * 5)[:5]
    return Pose(x, y, z, yaw, pitch)


def address_of(manifest: dict, node_id: str) -> str:
    e = manifest["nodes"][node_id]
    return f"{e['host']}:{e['port']}"


# ---------------------------------------------------------------------------
# Formation preflight — catch a dead semantic layer before flying, not after
# ---------------------------------------------------------------------------


def covisibility_report(manifest: dict) -> List[dict]:
    """
    Pairwise co-visibility o(i, j) for every node pair in the manifest, with the
    inter-view angle each pair subtends.

    `angle_deg` is the parallax between the two viewpoints, measured at the patch
    they actually share. It is reported alongside the overlap because the two
    answer different questions and a formation needs both: overlap says the peers
    are looking at the same thing, parallax says they are looking at it from
    *different enough* places for the cross-check to carry information. Two
    drones flying wingtip to wingtip have o ~ 1.0 and near-zero parallax —
    maximum overlap, minimum independence, and an adversarial patch that fools
    one fools the other. Section 4.3 measured patch suppression falling off
    between 12 deg (3 m at 14 m) and 23 deg (6 m).
    """
    from protocol.geometry import covisibility, parallax_angle

    rows: List[dict] = []
    poses = {nid: pose_of(e) for nid, e in manifest["nodes"].items()}
    ids = sorted(poses)
    o_min = float(manifest.get("o_min", 0.1))
    phi_min = float(manifest.get("phi_min", 0.0))
    for i, a_id in enumerate(ids):
        for b_id in ids[i + 1:]:
            a, b = poses[a_id], poses[b_id]
            if a is None or b is None:
                rows.append({"i": a_id, "j": b_id, "iou": None, "angle_deg": None,
                             "separation_m": None, "co_visible": False,
                             "independent": False, "note": "no pose in manifest"})
                continue
            sep = math.hypot(a.x - b.x, a.y - b.y)
            iou = covisibility(a, b)
            phi = parallax_angle(a, b)
            overlapping = iou >= o_min
            rows.append({
                "i": a_id, "j": b_id,
                "iou": round(iou, 4),
                "separation_m": round(sep, 2),
                "angle_deg": round(phi, 1),
                "co_visible": overlapping,
                # A pair only verifies each other if they share a scene AND see
                # it from meaningfully different places.
                "independent": overlapping and phi >= phi_min,
                "note": "" if phi >= phi_min else "parallax below phi_min",
            })
    return rows


def assert_covisible_formation(manifest: dict, originator_id: str) -> List[dict]:
    """
    Fail loudly at startup if no peer can cross-check `originator_id`.

    The co-visibility gate is designed to abstain rather than dispute when views
    do not overlap, which keeps the false-positive rate low (Table 4.9) but has
    a sharp operational edge: a formation whose geometry never clears `o_min`
    produces a swarm in which every peer ACKs every receipt, forever, on
    cryptographic evidence alone. Nothing errors. Nothing logs. The console is
    entirely green while the semantic layer — the only layer that catches an
    adversarial patch — is switched off.

    That failure is silent by construction, so it has to be caught by an
    explicit check. Call this once at mission start. Returns the report so a
    caller can display it.
    """
    rows = covisibility_report(manifest)
    peers = [r for r in rows if originator_id in (r["i"], r["j"])]
    verifying = [r for r in peers if r["independent"]]
    if verifying:
        return rows

    detail = "\n".join(
        f"    {r['i']} <-> {r['j']}: o={r['iou']} "
        f"(need >= {manifest.get('o_min', 0.1)}), "
        f"phi={r['angle_deg']} deg (need >= {manifest.get('phi_min', 0.0)}), "
        f"sep={r['separation_m']} m  {r['note']}"
        for r in peers
    ) or "    (no peers in manifest)"

    # Distinguish the two ways this fails, because the fixes are opposite.
    overlapping = [r for r in peers if r["co_visible"]]
    if overlapping:
        why = (
            f"every peer overlapping '{originator_id}' is flying too close to it "
            f"to be an independent observer (parallax below phi_min="
            f"{manifest.get('phi_min', 0.0)} deg). They see the same scene from "
            f"the same place, so one adversarial patch fools all of them and "
            f"their agreement proves nothing."
        )
        fix = "  Spread the formation OUT until parallax clears phi_min."
    else:
        why = (
            f"no peer is co-visible with '{originator_id}' — the semantic layer "
            f"would be inactive for the whole mission and every peer would ACK "
            f"on cryptographic evidence alone."
        )
        fix = ("  Move peers CLOSER (or raise altitude), or supply camera frames "
               "so the ORB image fallback can run.")

    raise RuntimeError(f"{why}\n{detail}\n{fix}")
