"""
Perception layer: turn real YOLOv8 detections into the action vector that the
attestation pipeline attests and that peers cross-check.

This is the bridge that makes the perception attacks real rather than abstract.
Each drone runs an object detector on its camera frame, and a small deterministic
reactive controller (`detections_to_action`) maps the detections to a 3-D action
(forward, lateral, vertical), each in [-1, 1]. That action is what goes into the
Receipt's `output`, gets signed, and gets compared across co-visible peers.

The two attacks land naturally here:
  * Model swap  — a drone runs tampered detector weights. `model_hash` over the
    real weight file gives the provenance value, so a swapped model fails the
    allowlist regardless of what it outputs.
  * Adversarial patch — a patch placed in the scene (`apply_patch`) makes the
    detector miss or misplace the obstacle, so the fooled drone's action diverges
    from its co-visible peers and the semantic check catches it.

The controller and `model_hash` are dependency-free and unit-tested. The frame
helpers take a NumPy image and an Ultralytics YOLO model supplied by the caller,
so this module imports nothing heavy itself.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Sequence, Tuple

ACTION_DIM = 3  # (forward, lateral, vertical), each in [-1, 1]

Action = Tuple[float, float, float]


@dataclass(frozen=True)
class Detection:
    """One detection in normalized image coordinates (all in [0, 1])."""

    cls: int
    x: float  # bbox center x
    y: float  # bbox center y (0 = top, 1 = bottom)
    w: float  # bbox width
    h: float  # bbox height
    conf: float


def _clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def detections_to_action(
    dets: Sequence[Detection],
    k_forward: float = 3.0,
    k_lateral: float = 3.0,
    k_vertical: float = 2.5,
) -> Action:
    """
    Deterministic reactive avoidance: large, central, confident detections ahead
    push the action toward slowing down, steering away, and climbing over.

    An empty detection set (a clear path, or a detector fooled into missing the
    obstacle) yields full forward, which is exactly why a patched drone's action
    diverges from peers that still see the obstacle.
    """
    if not dets:
        return (1.0, 0.0, 0.0)

    tx = ty = mass = 0.0
    for d in dets:
        area = max(0.0, d.w) * max(0.0, d.h)
        centrality = 1.0 - min(1.0, 2.0 * abs(d.x - 0.5))  # 1 centre, 0 edge
        weight = d.conf * area * (0.5 + 0.5 * centrality)
        tx += weight * d.x
        ty += weight * d.y
        mass += weight

    if mass <= 0.0:
        return (1.0, 0.0, 0.0)

    cx, cy = tx / mass, ty / mass
    threat = min(1.0, mass)
    forward = _clip(1.0 - k_forward * threat)
    lateral = _clip(-k_lateral * threat * 2.0 * (cx - 0.5))   # steer away in x
    vertical = _clip(k_vertical * threat * 2.0 * (cy - 0.5))  # climb if low in frame
    return (forward, lateral, vertical)


def model_hash(weights_path: str) -> str:
    """SHA-256 of a detector weight file — the provenance value in the receipt."""
    h = hashlib.sha256()
    with open(weights_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Frame helpers (caller supplies a NumPy image and an Ultralytics YOLO model)
# ---------------------------------------------------------------------------


def frame_to_detections(frame, model, conf: float = 0.25) -> List[Detection]:
    """Run `model` on `frame` (H x W x 3) and return normalized detections."""
    result = model(frame, verbose=False, conf=conf)[0]
    height, width = frame.shape[:2]
    dets: List[Detection] = []
    for box in result.boxes:
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        dets.append(Detection(
            cls=int(box.cls),
            x=((x1 + x2) / 2.0) / width,
            y=((y1 + y2) / 2.0) / height,
            w=(x2 - x1) / width,
            h=(y2 - y1) / height,
            conf=float(box.conf),
        ))
    return dets


def frame_to_action(frame, model, conf: float = 0.25) -> Action:
    """Full perception step: camera frame -> detections -> action vector."""
    return detections_to_action(frame_to_detections(frame, model, conf))


def apply_patch(frame, patch, top_left: Tuple[int, int]):
    """
    Overlay `patch` (h x w x 3) onto a copy of `frame` at (row, col).

    Occluding or perturbing the obstacle is a real perception attack: the fooled
    drone misses it and its action diverges from co-visible peers. Returns the
    patched frame; the original is left untouched.
    """
    out = frame.copy()
    row, col = max(0, top_left[0]), max(0, top_left[1])
    height, width = out.shape[:2]
    r1 = min(height, row + patch.shape[0])
    c1 = min(width, col + patch.shape[1])
    out[row:r1, col:c1] = patch[: r1 - row, : c1 - col]  # clip to frame bounds
    return out
