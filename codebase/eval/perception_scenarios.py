"""
Stage 1: run the three attack scenarios through REAL YOLOv8 perception into the
attestation pipeline, so Tables 4.4 and 4.12 rest on genuine detections rather
than synthetic action vectors.

For each real image (scene):
  honest            - originator and peers run the approved model on the scene;
                      actions agree -> ACCEPTED.
  model_swap        - originator runs a tampered yolov8n (weights perturbed, so
                      its model_hash is not on the allowlist) -> provenance
                      DISPUTE -> REJECTED.
  adversarial_patch - originator's frame is patched so the detector misses an
                      object; its action diverges from the co-visible peers
                      (who see the clean scene) -> semantic DISPUTE -> REJECTED.

The originator is a 3-drone swarm's Alpha; Bravo and Charlie are co-visible
peers (overlapping ground footprints). Outcomes + the action divergences are
written to results/perception_scenarios.csv.

Run (ultralytics + opencv required):  python3 -m eval.perception_scenarios
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from protocol.receipts import (
    ReceiptSigner,
    ReceiptVerifier,
    build_receipt,
)
from protocol.peer_consensus import (
    ConsensusEngine,
    ConsensusOutcome,
    PeerVerifier,
    VoteVerifier,
)
from protocol.geometry import Pose
from node.frame_source import frame_bytes
from perception.yolo_action import (
    Detection,
    apply_patch,
    detections_to_action,
    frame_to_action,
    frame_to_detections,
    model_hash,
)
from eval import harness as H

GOOD = "yolov8n.pt"
TAMPERED = "yolov8n_tampered.pt"
THETA = 0.5  # semantic threshold (matches the synthetic ROC operating point)

ALPHA_POSE = Pose(0.0, 0.0, 10.0)
PEER_POSES = {"bravo": Pose(2.0, 0.0, 10.0), "charlie": Pose(-2.0, 0.0, 10.0)}


def ensure_tampered(src: str, dst: str) -> None:
    """Create a tampered copy of the detector by perturbing its weights, so it
    still runs but its file hash (provenance value) no longer matches."""
    if Path(dst).exists():
        return
    import torch
    from ultralytics import YOLO

    print(f"generating tampered model {dst} (perturbed weights)")
    model = YOLO(src)
    with torch.no_grad():
        for param in model.model.parameters():
            param.add_(torch.randn_like(param) * 0.01)  # subtle backdoor-style tamper
            break
    model.save(dst)


def _l2(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _patch_largest(frame, model):
    """Occlude the largest detected object so the detector misses it."""
    dets: List[Detection] = frame_to_detections(frame, model)
    if not dets:
        return frame
    import numpy as np

    h, w = frame.shape[:2]
    big = max(dets, key=lambda d: d.w * d.h)
    pw, ph = max(8, int(big.w * w * 1.1)), max(8, int(big.h * h * 1.1))
    c0 = max(0, int(big.x * w) - pw // 2)
    r0 = max(0, int(big.y * h) - ph // 2)
    patch = np.full((ph, pw, 3), 127, dtype=frame.dtype)
    return apply_patch(frame, patch, (r0, c0))


def _build_swarm():
    ids = ["alpha", "bravo", "charlie"]
    signers = {d: ReceiptSigner() for d in ids}
    peer_keys = {d: s.public_key_hex for d, s in signers.items()}
    rv = ReceiptVerifier(peer_keys=peer_keys, approved_models={model_hash(GOOD)})
    vv = VoteVerifier(peer_keys=peer_keys)
    return ids, signers, rv, vv


def _run(scenario, frame, good_model, tampered_model, signers, rv, vv):
    # Originator's frame, model, and provenance value depend on the scenario.
    if scenario == "honest":
        orig_frame, orig_model, orig_hash = frame, good_model, model_hash(GOOD)
    elif scenario == "model_swap":
        orig_frame, orig_model, orig_hash = frame, tampered_model, model_hash(TAMPERED)
    else:  # adversarial_patch
        orig_frame, orig_model, orig_hash = _patch_largest(frame, good_model), good_model, model_hash(GOOD)

    orig_action = frame_to_action(orig_frame, orig_model)
    receipt = build_receipt(drone_id="alpha", input_bytes=frame_bytes(orig_frame),
                            model_hash=orig_hash, output=orig_action)
    signed = signers["alpha"].sign(receipt)

    # Peers perceive the clean, co-visible scene with the approved model.
    peer_action = frame_to_action(frame, good_model)
    votes = []
    for peer, pose in PEER_POSES.items():
        pv = PeerVerifier(peer, signers[peer], rv, o_min=0.1)
        votes.append(pv.vote_on(signed, my_observation=peer_action,
                                my_pose=pose, originator_pose=ALPHA_POSE))

    result = ConsensusEngine(num_peers=2).tally(
        signed.receipt, votes, vv, expected_voters={"alpha", "bravo", "charlie"})
    reason = votes[0].vote.reason
    return result.outcome, reason, orig_action, peer_action, _l2(orig_action, peer_action)


def main():
    import cv2
    from ultralytics import YOLO
    from ultralytics.utils import ASSETS

    ensure_tampered(GOOD, TAMPERED)
    good_model = YOLO(GOOD)
    tampered_model = YOLO(TAMPERED)

    if len(sys.argv) > 1:
        scenes = [Path(p) for p in sys.argv[1:] if Path(p).exists()]
    else:
        scenes = [p for p in (ASSETS / "bus.jpg", ASSETS / "zidane.jpg") if p.exists()]
    expected = {"honest": ConsensusOutcome.ACCEPTED,
                "model_swap": ConsensusOutcome.REJECTED,
                "adversarial_patch": ConsensusOutcome.REJECTED}

    ids, signers, rv, vv = _build_swarm()
    rows = []
    print(f"{'scene':12} {'scenario':18} {'outcome':10} {'match':6} {'L2':>6}  reason")
    for scene in scenes:
        frame = cv2.imread(str(scene))
        if frame is None:
            continue
        for scenario in ("honest", "model_swap", "adversarial_patch"):
            outcome, reason, oa, pa, l2 = _run(
                scenario, frame, good_model, tampered_model, signers, rv, vv)
            ok = outcome is expected[scenario]
            print(f"{scene.name:12} {scenario:18} {outcome.value:10} "
                  f"{str(ok):6} {l2:6.3f}  {reason}")
            rows.append({"scene": scene.name, "scenario": scenario,
                         "outcome": outcome.value, "expected": expected[scenario].value,
                         "match": ok, "action_l2": round(l2, 4), "reason": reason,
                         "orig_action": tuple(round(v, 3) for v in oa),
                         "peer_action": tuple(round(v, 3) for v in pa)})

    H.write_csv("perception_scenarios.csv", rows)
    passed = sum(1 for r in rows if r["match"])
    print(f"\n{passed}/{len(rows)} scenarios produced the expected decision")
    print("wrote results/perception_scenarios.csv")


if __name__ == "__main__":
    main()
