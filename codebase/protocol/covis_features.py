"""
VeriSwarm Protocol — feature-level co-visibility fallback for the semantic check.

The geometric co-visibility gate in ``geometry.py`` decides whether two drones
observed the same patch of ground from their *poses*. That gate is exact and
cheap, but it inherits the trust we place in the pose estimate. Under GPS
spoofing (a threat this work explicitly considers) or ordinary EKF drift, the
reported pose can be wrong, and a spoofed pose could make two drones that truly
share a view look far apart — which would silently disable the one layer that
catches adversarial-perception attacks.

This module supplies the fallback the semantic gate falls back to when the
geometric test is inconclusive: it decides co-visibility from the *images*
themselves, independent of any reported pose. Two frames of the same scene from
nearby viewpoints share many local features related by a single homography; two
frames of different scenes do not.

Method
------
1. ORB keypoints + descriptors on each grayscale frame (patent-free, fast).
2. Brute-force Hamming match with Lowe's ratio test to keep only distinctive
   correspondences.
3. RANSAC homography on the surviving matches; the number of geometric
   *inliers* is the co-visibility score. The RANSAC step is what makes the
   metric robust: a repeated texture (e.g. identical obstacle decals) throws up
   many raw matches, but only a genuine shared scene admits one consistent
   homography, so only a real co-view yields a high inlier count.

A peer treats the two frames as co-visible when the inlier count reaches
``m_min``. ``m_min`` is swept in the evaluation the same way the semantic
threshold theta is; the default of 15 is the elbow of that sweep.

OpenCV is imported lazily so that importing this module (and, transitively,
``peer_consensus``) never requires cv2 in pure-consensus contexts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Sensible defaults, overridable per mission / per experiment.
DEFAULT_M_MIN = 15          # inlier matches required to confirm co-visibility
DEFAULT_N_FEATURES = 1500   # ORB keypoint budget per frame
DEFAULT_RATIO = 0.75        # Lowe's ratio-test cutoff
DEFAULT_RANSAC_REPROJ = 5.0 # RANSAC reprojection tolerance, pixels


@dataclass(frozen=True)
class FeatureMatchResult:
    """Outcome of a feature-level co-visibility test between two frames."""

    inliers: int          # RANSAC-consistent matches (the co-visibility score)
    good_matches: int     # matches surviving the ratio test, pre-RANSAC
    keypoints_a: int
    keypoints_b: int

    def covisible(self, m_min: int = DEFAULT_M_MIN) -> bool:
        return self.inliers >= m_min


@dataclass(frozen=True)
class FeatureAlignmentResult:
    """Feature evidence plus the measured homography from frame A to frame B.

    ``homography_a_to_b`` is ``None`` when fewer than four consistent matches
    exist or RANSAC cannot estimate a model.  It is intentionally typed as an
    object so importing this light-weight protocol module does not import
    NumPy/OpenCV in pure-consensus processes.
    """

    match: FeatureMatchResult
    homography_a_to_b: object | None


def _load_gray(frame):
    """Accept a path, a BGR array, or a grayscale array; return grayscale."""
    import cv2  # lazy
    import numpy as np

    if isinstance(frame, str):
        img = cv2.imread(frame, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(f"could not read frame: {frame}")
        return img
    if isinstance(frame, np.ndarray):
        if frame.ndim == 2:
            return frame
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    raise TypeError(f"unsupported frame type: {type(frame)!r}")


def feature_alignment(
    frame_a,
    frame_b,
    n_features: int = DEFAULT_N_FEATURES,
    ratio: float = DEFAULT_RATIO,
    ransac_reproj: float = DEFAULT_RANSAC_REPROJ,
) -> FeatureAlignmentResult:
    """
    Estimate the ORB/RANSAC alignment from frame A into frame B.

    ``frame_a`` / ``frame_b`` may each be an image path, a BGR ``np.ndarray``,
    or a grayscale ``np.ndarray``.  The match summary remains the co-visibility
    evidence; the homography allows downstream code to compare image regions in
    one coordinate system rather than taking invalid raw pixel-coordinate IoU.
    """
    import cv2  # lazy
    import numpy as np

    gray_a = _load_gray(frame_a)
    gray_b = _load_gray(frame_b)

    orb = cv2.ORB_create(nfeatures=n_features)
    kp_a, des_a = orb.detectAndCompute(gray_a, None)
    kp_b, des_b = orb.detectAndCompute(gray_b, None)

    n_kp_a = 0 if kp_a is None else len(kp_a)
    n_kp_b = 0 if kp_b is None else len(kp_b)

    if des_a is None or des_b is None or n_kp_a < 2 or n_kp_b < 2:
        return FeatureAlignmentResult(
            FeatureMatchResult(0, 0, n_kp_a, n_kp_b), None
        )

    # k=2 nearest neighbours per descriptor for Lowe's ratio test.
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn = matcher.knnMatch(des_a, des_b, k=2)

    good = []
    for pair in knn:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < ratio * n.distance:
            good.append(m)

    if len(good) < 4:
        # Too few correspondences to fit a homography at all.
        return FeatureAlignmentResult(
            FeatureMatchResult(0, len(good), n_kp_a, n_kp_b), None
        )

    src = np.float32([kp_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_b[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, ransac_reproj)
    inliers = int(mask.sum()) if mask is not None else 0

    return FeatureAlignmentResult(
        FeatureMatchResult(inliers, len(good), n_kp_a, n_kp_b),
        homography,
    )


def feature_match(
    frame_a,
    frame_b,
    n_features: int = DEFAULT_N_FEATURES,
    ratio: float = DEFAULT_RATIO,
    ransac_reproj: float = DEFAULT_RANSAC_REPROJ,
) -> FeatureMatchResult:
    """Count RANSAC-consistent ORB matches without exposing the transform."""
    return feature_alignment(
        frame_a,
        frame_b,
        n_features=n_features,
        ratio=ratio,
        ransac_reproj=ransac_reproj,
    ).match


def covisible_by_features(
    frame_a,
    frame_b,
    m_min: int = DEFAULT_M_MIN,
    **kwargs,
) -> bool:
    """True iff the two frames share at least ``m_min`` RANSAC-consistent matches."""
    return feature_match(frame_a, frame_b, **kwargs).covisible(m_min)
