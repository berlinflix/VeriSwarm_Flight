"""
Evaluation for the feature-level co-visibility fallback (protocol/covis_features.py).

Three questions, answered on real images and through the real PeerVerifier:

  PART 1  Does the image-feature test separate co-observed frames from frames of
          different scenes? (validity of the metric)
  PART 2  What inlier count m_min cleanly separates the two? (choosing m_min)
  PART 3  Does the fallback restore the semantic layer when a pose is spoofed,
          without introducing false positives when views genuinely differ?
          (the GPS-spoofing scenario, end-to-end through PeerVerifier)

All frames are real: sim_a/sim_b are two drone viewpoints of the same Gazebo
scene from the multi-drone flight (Section 4.5); bus.jpg/zidane.jpg are distinct
scenes. Peer viewpoints at controlled angles are emulated with the same planar
homography H = K.Rx(phi).K^-1 used in the adversarial-patch transfer study.

Writes results/covis_feature.csv and prints a report. RANSAC has internal
randomness, so every inlier count is reported as a mean over REPEATS runs.
"""

from __future__ import annotations

import csv
import math
import os
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from protocol.covis_features import feature_match, DEFAULT_M_MIN
from protocol.geometry import Pose
from protocol.peer_consensus import PeerVerifier, Vote
from protocol.receipts import (
    ReceiptSigner,
    ReceiptVerifier,
    build_receipt,
    sha256_hex,
)
from ultralytics import __file__ as _ul

REPO = pathlib.Path(__file__).resolve().parent.parent
SIM = REPO / "sim" / "sim_frames"
ASSETS = pathlib.Path(_ul).resolve().parent / "assets"
RESULTS = REPO / "results"
RESULTS.mkdir(exist_ok=True)

SIM_A = str(SIM / "sim_a.jpg")
SIM_B = str(SIM / "sim_b.jpg")
BUS = str(ASSETS / "bus.jpg")
ZIDANE = str(ASSETS / "zidane.jpg")

REPEATS = 10  # RANSAC is randomised; average the inlier count over runs
APPROVED = sha256_hex(b"yolov8n-weights-v1")


# --------------------------------------------------------------------------- #
# Viewpoint warp: emulate a peer looking from off-nadir angle phi (degrees).
# --------------------------------------------------------------------------- #
def warp_viewpoint(img: np.ndarray, phi_deg: float) -> np.ndarray:
    """Apply the planar homography H = K . Rx(phi) . K^-1 to an image."""
    h, w = img.shape[:2]
    f = 0.9 * w  # plausible focal length in pixels
    K = np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1.0]])
    phi = math.radians(phi_deg)
    Rx = np.array(
        [
            [1, 0, 0],
            [0, math.cos(phi), -math.sin(phi)],
            [0, math.sin(phi), math.cos(phi)],
        ]
    )
    H = K @ Rx @ np.linalg.inv(K)
    return cv2.warpPerspective(img, H, (w, h))


def mean_inliers(a, b, repeats: int = REPEATS):
    """Mean +/- stdev of the RANSAC inlier count over repeated runs."""
    counts = [feature_match(a, b).inliers for _ in range(repeats)]
    good = feature_match(a, b).good_matches
    mean = statistics.mean(counts)
    sd = statistics.pstdev(counts)
    return mean, sd, good, counts


# --------------------------------------------------------------------------- #
# PART 1 + 2 : metric validity and m_min separation
# --------------------------------------------------------------------------- #
def part_1_2():
    print("=" * 72)
    print("PART 1/2  Feature-match inlier counts (mean over %d RANSAC runs)" % REPEATS)
    print("=" * 72)

    sim_a = cv2.imread(SIM_A)
    warp12 = warp_viewpoint(sim_a, 12.0)
    warp23 = warp_viewpoint(sim_a, 23.0)

    cases = [
        ("sim_a", "sim_a  (identical)", SIM_A, SIM_A, True),
        ("sim_a", "sim_b  (same scene, other drone)", SIM_A, SIM_B, True),
        ("sim_a", "warp 12deg (peer at 3 m)", sim_a, warp12, True),
        ("sim_a", "warp 23deg (peer at 6 m)", sim_a, warp23, True),
        ("sim_a", "bus.jpg (different scene)", SIM_A, BUS, False),
        ("sim_a", "zidane.jpg (different scene)", SIM_A, ZIDANE, False),
        ("sim_b", "bus.jpg (different scene)", SIM_B, BUS, False),
        ("bus.jpg", "zidane.jpg (different scenes)", BUS, ZIDANE, False),
    ]

    rows = []
    covis_scores, noncovis_scores = [], []
    for left, right, a, b, expect_covis in cases:
        mean, sd, good, counts = mean_inliers(a, b)
        rows.append((left, right, expect_covis, mean, sd, good))
        (covis_scores if expect_covis else noncovis_scores).append(mean)
        tag = "CO-VISIBLE" if expect_covis else "different"
        print(
            f"  {left:>8s} vs {right:<34s} "
            f"inliers={mean:6.1f} +/- {sd:4.1f}  (good={good:4d})  [{tag}]"
        )

    lo_covis = min(covis_scores)
    hi_noncovis = max(noncovis_scores)
    print("-" * 72)
    print(f"  lowest  co-visible inliers : {lo_covis:.1f}")
    print(f"  highest different  inliers : {hi_noncovis:.1f}")
    margin = lo_covis - hi_noncovis
    print(f"  separation margin          : {margin:.1f}")
    midpoint = (lo_covis + hi_noncovis) / 2.0
    print(f"  any m_min in ({hi_noncovis:.0f}, {lo_covis:.0f}] separates them; "
          f"default m_min={DEFAULT_M_MIN}, midpoint={midpoint:.0f}")
    assert margin > 0, "co-visible and different-scene inliers overlap!"
    assert hi_noncovis < DEFAULT_M_MIN <= lo_covis, (
        f"default m_min={DEFAULT_M_MIN} does not sit in the gap "
        f"({hi_noncovis:.1f}, {lo_covis:.1f}]"
    )
    print("  OK: default m_min cleanly separates the two classes.")
    return rows, lo_covis, hi_noncovis


# --------------------------------------------------------------------------- #
# PART 3 : GPS-spoofing scenario, end-to-end through PeerVerifier
# --------------------------------------------------------------------------- #
def part_3():
    print()
    print("=" * 72)
    print("PART 3  GPS-spoofing scenario through PeerVerifier.vote_on()")
    print("=" * 72)

    # Swarm: alpha (originator, fooled by a patch) + bravo (honest peer).
    signers = {d: ReceiptSigner() for d in ("alpha", "bravo")}
    peer_keys = {d: s.public_key_hex for d, s in signers.items()}
    rv = ReceiptVerifier(peer_keys=peer_keys, approved_models={APPROVED})
    pv = PeerVerifier(my_drone_id="bravo", signer=signers["bravo"], receipt_verifier=rv)

    # Alpha is patched: it reports "clear path" though an obstacle is present.
    fooled_action = (0.0, 0.0, 0.0)
    receipt = build_receipt(
        drone_id="alpha", input_bytes=b"\x00" * 128,
        model_hash=APPROVED, output=fooled_action,
    )
    signed = signers["alpha"].sign(receipt)
    # Bravo, seeing the real obstacle, would act very differently.
    bravo_obs = (1.6, 0.0, 0.0)

    # True geometry: alpha and bravo are close and co-observe the scene.
    alpha_pose_true = Pose(0.0, 0.0, 14.0)
    bravo_pose_true = Pose(3.0, 0.0, 14.0)
    # Spoofed geometry: bravo's GPS is spoofed to look 100 m away.
    bravo_pose_spoofed = Pose(100.0, 0.0, 14.0)

    scenarios = []

    # (A) Honest poses, no frames: geometric gate fires -> patch caught.
    vA = pv.vote_on(signed, my_observation=bravo_obs,
                    my_pose=bravo_pose_true, originator_pose=alpha_pose_true)
    scenarios.append(("A honest pose, geometric gate", vA.vote.decision, vA.vote.reason,
                      Vote.DISPUTE))

    # (B) Spoofed pose, NO fallback frames: gate wrongly abstains -> patch slips.
    vB = pv.vote_on(signed, my_observation=bravo_obs,
                    my_pose=bravo_pose_spoofed, originator_pose=alpha_pose_true)
    scenarios.append(("B spoofed pose, no fallback", vB.vote.decision, vB.vote.reason,
                      Vote.ACK))

    # (C) Spoofed pose, WITH co-visible frames: fallback restores gate -> caught.
    vC = pv.vote_on(signed, my_observation=bravo_obs,
                    my_pose=bravo_pose_spoofed, originator_pose=alpha_pose_true,
                    my_frame=SIM_B, originator_frame=SIM_A)
    scenarios.append(("C spoofed pose, feature fallback", vC.vote.decision, vC.vote.reason,
                      Vote.DISPUTE))

    # (D) Genuinely different scenes (no co-view): fallback abstains -> no FP.
    vD = pv.vote_on(signed, my_observation=bravo_obs,
                    my_pose=bravo_pose_spoofed, originator_pose=alpha_pose_true,
                    my_frame=BUS, originator_frame=SIM_A)
    scenarios.append(("D non-covisible frames, honest abstain", vD.vote.decision,
                      vD.vote.reason, Vote.ACK))

    all_ok = True
    for name, decision, reason, expected in scenarios:
        ok = decision is expected
        all_ok &= ok
        print(f"  [{ 'OK ' if ok else 'XX ' }] {name:<38s} -> "
              f"{decision.value:8s} ({reason})  expected {expected.value}")

    print("-" * 72)
    print("  Headline: without the fallback a spoofed pose (B) lets the patch"
          " through;")
    print("  the feature fallback (C) catches it again, while genuinely"
          " different views (D)")
    print("  still abstain, so no false positive is introduced.")
    assert all_ok, "a PeerVerifier scenario did not match its expected decision"
    print("  OK: all four scenarios decided as expected.")
    return scenarios


def main():
    for p in (SIM_A, SIM_B, BUS, ZIDANE):
        if not os.path.exists(p):
            raise SystemExit(f"missing input image: {p}")

    rows, lo_covis, hi_noncovis = part_1_2()
    scenarios = part_3()

    out = RESULTS / "covis_feature.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["section", "left", "right", "expect_covisible",
                    "mean_inliers", "stdev_inliers", "good_matches"])
        for left, right, expect, mean, sd, good in rows:
            w.writerow(["metric", left, right, expect,
                        f"{mean:.2f}", f"{sd:.2f}", good])
        for name, decision, reason, expected in scenarios:
            w.writerow(["scenario", name, "", "",
                        decision.value, reason, expected.value])
    print()
    print(f"wrote {out}")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
