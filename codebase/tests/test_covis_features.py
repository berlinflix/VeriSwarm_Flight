"""
Unit tests for the feature-level co-visibility fallback.

Backs the claim in Section 3.4 that when a pose estimate is untrustworthy
(GPS spoofing, EKF drift) the semantic gate falls back to a pose-independent,
image-based co-visibility test: two frames are co-visible when they share at
least m_min RANSAC-consistent ORB matches.

    test_identical_frames_covisible          -> a frame co-observes itself
    test_same_scene_two_viewpoints_covisible -> sim_a vs sim_b (real flight)
    test_different_scenes_not_covisible       -> sim_a vs bus / zidane
    test_warped_viewpoint_covisible           -> emulated peer view still matches
    test_blank_frames_not_covisible           -> degenerate input, no crash
    test_fallback_catches_patch_under_spoof   -> PeerVerifier end-to-end (spoof)
    test_no_frames_no_fallback_abstains       -> spoof + no frames -> ACK
    test_noncovisible_frames_no_false_positive-> different scenes -> ACK
"""

from __future__ import annotations

import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

cv2 = pytest.importorskip("cv2")
import numpy as np  # noqa: E402

from protocol.covis_features import (  # noqa: E402
    covisible_by_features,
    feature_match,
)
from protocol.geometry import Pose  # noqa: E402
from protocol.peer_consensus import PeerVerifier, Vote  # noqa: E402
from protocol.receipts import (  # noqa: E402
    ReceiptSigner,
    ReceiptVerifier,
    build_receipt,
    sha256_hex,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
SIM = REPO / "sim" / "sim_frames"
SIM_A = str(SIM / "sim_a.jpg")
SIM_B = str(SIM / "sim_b.jpg")

try:
    import ultralytics
    ASSETS = pathlib.Path(ultralytics.__file__).resolve().parent / "assets"
    BUS = str(ASSETS / "bus.jpg")
    ZIDANE = str(ASSETS / "zidane.jpg")
except Exception:  # pragma: no cover
    BUS = ZIDANE = None

APPROVED = sha256_hex(b"yolov8n-weights-v1")

pytestmark = pytest.mark.skipif(
    not (SIM / "sim_a.jpg").exists(), reason="sim frames not present"
)


# --------------------------------------------------------------------------- #
# Metric-level tests
# --------------------------------------------------------------------------- #
def test_identical_frames_covisible():
    """A frame trivially co-observes itself with many inliers."""
    res = feature_match(SIM_A, SIM_A)
    assert res.inliers >= 100
    assert res.covisible(m_min=15)


def test_same_scene_two_viewpoints_covisible():
    """Two real drone viewpoints of the same Gazebo scene are co-visible."""
    res = feature_match(SIM_A, SIM_B)
    assert res.inliers >= 100
    assert covisible_by_features(SIM_A, SIM_B, m_min=15)


@pytest.mark.skipif(BUS is None, reason="ultralytics assets not found")
def test_different_scenes_not_covisible():
    """Frames of different scenes fall well below m_min."""
    assert not covisible_by_features(SIM_A, BUS, m_min=15)
    assert not covisible_by_features(SIM_B, BUS, m_min=15)
    assert feature_match(SIM_A, BUS).inliers < 15


def test_warped_viewpoint_covisible():
    """An emulated off-nadir peer view (23 deg) still matches the base frame."""
    img = cv2.imread(SIM_A)
    h, w = img.shape[:2]
    f = 0.9 * w
    K = np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1.0]])
    phi = math.radians(23.0)
    Rx = np.array([[1, 0, 0],
                   [0, math.cos(phi), -math.sin(phi)],
                   [0, math.sin(phi), math.cos(phi)]])
    H = K @ Rx @ np.linalg.inv(K)
    warped = cv2.warpPerspective(img, H, (w, h))
    assert covisible_by_features(img, warped, m_min=15)


def test_blank_frames_not_covisible():
    """Featureless frames yield no matches and do not raise."""
    blank_a = np.zeros((120, 160, 3), dtype=np.uint8)
    blank_b = np.zeros((120, 160, 3), dtype=np.uint8)
    assert not covisible_by_features(blank_a, blank_b, m_min=15)


# --------------------------------------------------------------------------- #
# PeerVerifier end-to-end: the GPS-spoofing scenario
# --------------------------------------------------------------------------- #
def _patched_setup():
    """Alpha reports 'clear path' under a patch; honest bravo sees an obstacle."""
    signers = {d: ReceiptSigner() for d in ("alpha", "bravo")}
    peer_keys = {d: s.public_key_hex for d, s in signers.items()}
    rv = ReceiptVerifier(peer_keys=peer_keys, approved_models={APPROVED})
    pv = PeerVerifier(my_drone_id="bravo", signer=signers["bravo"], receipt_verifier=rv)
    receipt = build_receipt(
        drone_id="alpha", input_bytes=b"\x00" * 128,
        model_hash=APPROVED, output=(0.0, 0.0, 0.0),
    )
    signed = signers["alpha"].sign(receipt)
    bravo_obs = (1.6, 0.0, 0.0)
    return pv, signed, bravo_obs


def test_fallback_catches_patch_under_spoof():
    """Spoofed pose hides the co-view geometrically, but frames restore it."""
    pv, signed, bravo_obs = _patched_setup()
    vote = pv.vote_on(
        signed, my_observation=bravo_obs,
        my_pose=Pose(100.0, 0.0, 14.0),          # spoofed: looks far away
        originator_pose=Pose(0.0, 0.0, 14.0),
        my_frame=SIM_B, originator_frame=SIM_A,  # but frames are co-visible
    )
    assert vote.vote.decision is Vote.DISPUTE
    assert vote.vote.reason == "semantic_disagreement"


def test_feature_overlap_cannot_bypass_required_angular_diversity():
    """ORB can establish a shared scene, but it cannot prove camera baseline."""
    pv, signed, bravo_obs = _patched_setup()
    pv._phi_min = 23.0
    vote = pv.vote_on(
        signed, my_observation=bravo_obs,
        my_pose=Pose(100.0, 0.0, 14.0),
        originator_pose=Pose(0.0, 0.0, 14.0),
        my_frame=SIM_B, originator_frame=SIM_A,
    )
    assert vote.vote.decision is Vote.ACK
    assert vote.vote.reason == "ok_no_covisibility"
    assert pv.last_covis.method == "features_no_parallax"


def test_no_frames_no_fallback_abstains():
    """Spoofed pose with no frames: geometric gate abstains (the vulnerability)."""
    pv, signed, bravo_obs = _patched_setup()
    vote = pv.vote_on(
        signed, my_observation=bravo_obs,
        my_pose=Pose(100.0, 0.0, 14.0),
        originator_pose=Pose(0.0, 0.0, 14.0),
    )
    assert vote.vote.decision is Vote.ACK
    assert vote.vote.reason == "ok_no_covisibility"


@pytest.mark.skipif(BUS is None, reason="ultralytics assets not found")
def test_noncovisible_frames_no_false_positive():
    """Different-scene frames must not trigger a semantic dispute (no FP)."""
    pv, signed, bravo_obs = _patched_setup()
    vote = pv.vote_on(
        signed, my_observation=bravo_obs,
        my_pose=Pose(100.0, 0.0, 14.0),
        originator_pose=Pose(0.0, 0.0, 14.0),
        my_frame=BUS, originator_frame=SIM_A,   # genuinely different scenes
    )
    assert vote.vote.decision is Vote.ACK
    assert vote.vote.reason == "ok_no_covisibility"


def test_geometric_gate_still_primary():
    """When the true pose is co-visible, the geometric gate catches the patch
    with no frames needed."""
    pv, signed, bravo_obs = _patched_setup()
    vote = pv.vote_on(
        signed, my_observation=bravo_obs,
        my_pose=Pose(3.0, 0.0, 14.0),
        originator_pose=Pose(0.0, 0.0, 14.0),
    )
    assert vote.vote.decision is Vote.DISPUTE


@pytest.mark.skipif(BUS is None, reason="ultralytics assets not found")
def test_claimed_geometric_overlap_does_not_bypass_conflicting_frames():
    """When frames exist, a pose claim alone cannot activate semantic comparison."""
    pv, signed, bravo_obs = _patched_setup()
    vote = pv.vote_on(
        signed, my_observation=bravo_obs,
        my_pose=Pose(3.0, 0.0, 14.0),
        originator_pose=Pose(0.0, 0.0, 14.0),
        my_frame=BUS, originator_frame=SIM_A,
    )
    assert vote.vote.decision is Vote.ACK
    assert vote.vote.reason == "ok_no_covisibility"
    assert pv.last_covis.method == "geometry+features"
