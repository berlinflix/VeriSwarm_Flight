"""
Render a two-panel figure proving the feature-level co-visibility test:

  (a) two real drone viewpoints of the same scene (sim_a, sim_b) -> many
      geometrically-consistent ORB matches (co-visible)
  (b) the same frame vs a different scene (sim_a, bus.jpg) -> almost none

Writes figures/fig_covis_features.png (+ .pdf) at the repo root and copies to
the paper's figure folder.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
SIM = REPO / "sim" / "sim_frames"
FIG = REPO / "figures"
FIG.mkdir(exist_ok=True)
import ultralytics
ASSETS = pathlib.Path(ultralytics.__file__).resolve().parent / "assets"


def _read_h(path, h=360):
    """Read an image and resize to a common height so panels align cleanly."""
    img = cv2.imread(str(path))
    scale = h / img.shape[0]
    return cv2.resize(img, (int(round(img.shape[1] * scale)), h))


def matched_panel(path_a, path_b, ratio=0.75, ransac=5.0, draw=40):
    a = _read_h(path_a)
    b = _read_h(path_b)
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=1500)
    ka, da = orb.detectAndCompute(ga, None)
    kb, db = orb.detectAndCompute(gb, None)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    good = []
    for pair in bf.knnMatch(da, db, k=2):
        if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance:
            good.append(pair[0])
    inliers = 0
    if len(good) >= 4:
        src = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(src, dst, cv2.RANSAC, ransac)
        if mask is not None:
            inliers = int(mask.sum())
            inlier_matches = [m for m, keep in zip(good, mask.ravel()) if keep]
        else:
            inlier_matches = []
    else:
        # Too few correspondences to fit a homography; show what little exists.
        inlier_matches = good
    shown = inlier_matches[:draw]
    vis = cv2.drawMatches(
        a, ka, b, kb, shown, None,
        matchColor=(0, 180, 0), singlePointColor=(160, 160, 160),
        flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
    )
    return vis, inliers


def label(img, text):
    bar = np.full((34, img.shape[1], 3), 255, dtype=np.uint8)
    cv2.putText(bar, text, (12, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (0, 0, 0), 1, cv2.LINE_AA)
    return np.vstack([bar, img])


def main():
    top, n_top = matched_panel(SIM / "sim_a.jpg", SIM / "sim_b.jpg")
    bot, n_bot = matched_panel(SIM / "sim_a.jpg", ASSETS / "bus.jpg")

    top = label(top, f"(a) same scene, two drone viewpoints  -  {n_top} consistent matches: CO-VISIBLE")
    bot = label(bot, f"(b) different scenes  -  {n_bot} consistent matches: not co-visible")

    w = max(top.shape[1], bot.shape[1])

    def pad(img):
        if img.shape[1] == w:
            return img
        p = np.full((img.shape[0], w - img.shape[1], 3), 255, dtype=np.uint8)
        return np.hstack([img, p])

    gap = np.full((16, w, 3), 255, dtype=np.uint8)
    fig = np.vstack([pad(top), gap, pad(bot)])

    out_png = FIG / "fig_covis_features.png"
    cv2.imwrite(str(out_png), fig)
    # PDF via matplotlib for a vector wrapper (IEEE submission).
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rgb = cv2.cvtColor(fig, cv2.COLOR_BGR2RGB)
    dpi = 150
    plt.figure(figsize=(rgb.shape[1] / dpi, rgb.shape[0] / dpi), dpi=dpi)
    plt.imshow(rgb)
    plt.axis("off")
    plt.tight_layout(pad=0)
    plt.savefig(FIG / "fig_covis_features.pdf", bbox_inches="tight", pad_inches=0)
    plt.close()

    # Copy into the paper's figure folder.
    srip = pathlib.Path("/mnt/c/Users/suyas/Life/srip/figures")
    if srip.exists():
        for ext in ("png", "pdf"):
            data = (FIG / f"fig_covis_features.{ext}").read_bytes()
            (srip / f"fig_covis_features.{ext}").write_bytes(data)

    print(f"top (co-visible) inliers   : {n_top}")
    print(f"bottom (different) inliers : {n_bot}")
    print(f"wrote {out_png} and .pdf; copied to srip/figures if present")


if __name__ == "__main__":
    main()
