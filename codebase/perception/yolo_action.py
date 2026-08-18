"""
Perception layer: turn real YOLOv8 detections into one atomic action-and-claim
result for the attestation pipeline.

This is the bridge that makes the perception attacks real rather than abstract.
Each drone runs an object detector on its camera frame, and a small deterministic
reactive controller (`detections_to_action`) maps the detections to a 3-D action
(forward, lateral, vertical), each in [-1, 1]. That action is what goes into the
Receipt's `output`; the measured `PerceptionClaim` is signed alongside it and is
what co-visible peers compare semantically.

The two attacks land naturally here:
  * Model swap  — a drone runs tampered detector weights. `model_hash` over the
    real weight file gives the provenance value, so a swapped model fails the
    allowlist regardless of what it outputs.
  * Adversarial patch — a patch placed in the scene (`apply_patch`) can make the
    detector miss or misclassify an obstacle, so the fooled drone's signed claim
    differs from independently measured peer claims.

The controller and `model_hash` are dependency-free and unit-tested. The frame
helpers take a NumPy image and an Ultralytics YOLO model supplied by the caller,
so this module imports nothing heavy itself.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .claim import MAX_CLASS_ID, PerceptionClaim, PerceptionResult

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

    def __post_init__(self) -> None:
        if type(self.cls) is not int or not (0 <= self.cls <= MAX_CLASS_ID):
            raise ValueError(f"cls must be an integer in [0, {MAX_CLASS_ID}]")
        values = (self.x, self.y, self.w, self.h, self.conf)
        if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in values):
            raise ValueError("normalized detection fields must be finite and in [0, 1]")


def _clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def detections_to_action(
    dets: Sequence[Detection],
    k_forward: float = 3.0,
    k_lateral: float = 3.0,
    k_vertical: float = 2.5,
    *,
    free_space_confirmed: bool = False,
) -> Action:
    """
    Deterministic reactive avoidance: large, central, confident detections ahead
    push the action toward slowing down, steering away, and climbing over.

    Object-detector silence is ambiguous: it can mean clear space, but also a
    failed/covered camera, darkness, blur, an unsupported obstacle, or a
    successful evasion attack. It therefore yields a zero-motion command unless
    a separate, independent free-space sensor has positively confirmed the
    corridor. The caller must make that evidence explicit with
    ``free_space_confirmed=True``; YOLO absence alone never authorises motion.
    """
    if not dets:
        return (1.0, 0.0, 0.0) if free_space_confirmed else (0.0, 0.0, 0.0)

    tx = ty = mass = 0.0
    for d in dets:
        area = max(0.0, d.w) * max(0.0, d.h)
        centrality = 1.0 - min(1.0, 2.0 * abs(d.x - 0.5))  # 1 centre, 0 edge
        weight = d.conf * area * (0.5 + 0.5 * centrality)
        tx += weight * d.x
        ty += weight * d.y
        mass += weight

    if mass <= 0.0:
        return (0.0, 0.0, 0.0)

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


def frame_to_action(
    frame,
    model,
    conf: float = 0.25,
    *,
    free_space_confirmed: bool = False,
) -> Action:
    """Full perception step: camera frame -> detections -> action vector."""
    return frame_to_perception(
        frame,
        model,
        conf,
        free_space_confirmed=free_space_confirmed,
    ).action


def frame_to_perception(
    frame,
    model,
    conf: float = 0.25,
    *,
    free_space_confirmed: bool = False,
) -> PerceptionResult:
    """Run one detector pass and return its action and measured claim atomically."""
    dets = frame_to_detections(frame, model, conf)
    return PerceptionResult(
        action=detections_to_action(
            dets,
            free_space_confirmed=free_space_confirmed,
        ),
        claim=PerceptionClaim.from_detections(dets),
    )


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
