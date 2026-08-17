"""
Free-space self-consistency: cross-check a commanded action against range data.

The detector answers "is something ahead?" from an image plus a neural network.
An adversarial patch attacks that image. A range sensor answers a different
question with different physics — "how far is the nearest surface?" — and no
amount of printing on a wall changes how far away the wall is.

So a drone that commands full forward while its own depth return shows a solid
surface at 8 m has contradicted *itself*. That check needs no peers, no votes,
and no co-visibility, which makes it the one detection that still works when a
drone is flying alone and the semantic layer has nothing to compare against.

This module is deliberately dependency-free at import time: the scalar check is
pure Python, and NumPy is imported lazily inside the frame reducer so a node that
only has a single-point rangefinder never needs it.

Sources of range
----------------
* AirSim / any depth camera — a full depth image, reduced by :func:`nearest_range`
* A single-point ToF (Benewake TF-Luna and similar) — one scalar, used directly
* Simulated LiDAR — reduce the returns to a nearest range over the forward cone

What this does NOT prove
------------------------
A rangefinder samples the direction it points; the detector makes a claim about
the whole frame. A boresighted single-point sensor therefore cannot see an
obstacle off to the side, and will agree with a detector that was fooled about
one. Coverage is a property of the sensor, not of this check — see
:func:`nearest_range`, which reduces over a configurable central region, and
prefer a depth image over a single beam where you can.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

#: Assumed top speed the forward action commands, m/s. The action vector is
#: normalised to [-1, 1], so converting it into a stopping distance needs the
#: vehicle's speed scale.
DEFAULT_MAX_SPEED_MPS = 5.0

#: Seconds of travel that must be clear ahead. Covers the sense-decide-act loop
#: plus deceleration; at 2 Hz attestation rounds, 2 s is four rounds of margin.
DEFAULT_LOOKAHEAD_S = 2.0

#: Clearance required regardless of speed, m. Keeps the check meaningful for
#: near-hover commands, where the speed-scaled term goes to zero.
DEFAULT_STOP_MARGIN_M = 1.5


@dataclass(frozen=True)
class FreeSpaceCheck:
    """Result of testing a commanded action against measured range."""

    contradicted: bool
    commanded_forward: float
    measured_range_m: Optional[float]
    required_clear_m: float
    reason: str

    def describe(self) -> str:
        if self.measured_range_m is None:
            return f"depth unavailable ({self.reason}); no conclusion drawn"
        if self.contradicted:
            return (
                f"CONTRADICTION: commanded forward={self.commanded_forward:.2f} "
                f"needs {self.required_clear_m:.1f} m clear, but the nearest "
                f"surface is at {self.measured_range_m:.1f} m"
            )
        return (
            f"consistent: forward={self.commanded_forward:.2f} needs "
            f"{self.required_clear_m:.1f} m, measured {self.measured_range_m:.1f} m"
        )


def required_clearance(
    forward: float,
    max_speed_mps: float = DEFAULT_MAX_SPEED_MPS,
    lookahead_s: float = DEFAULT_LOOKAHEAD_S,
    stop_margin_m: float = DEFAULT_STOP_MARGIN_M,
) -> float:
    """
    Range that must be clear for a given forward command to be defensible.

    Linear in commanded speed plus a fixed margin. Deliberately simple: this is a
    consistency check on a claim, not a flight-control law, and a rule an operator
    can compute in their head is one they will actually trust when it fires.
    """
    return stop_margin_m + max(0.0, forward) * max_speed_mps * lookahead_s


def check_free_space(
    action: Sequence[float],
    measured_range_m: Optional[float],
    *,
    max_speed_mps: float = DEFAULT_MAX_SPEED_MPS,
    lookahead_s: float = DEFAULT_LOOKAHEAD_S,
    stop_margin_m: float = DEFAULT_STOP_MARGIN_M,
    min_valid_m: float = 0.1,
    max_valid_m: float = 100.0,
) -> FreeSpaceCheck:
    """
    Test a commanded action against a measured range to the nearest surface.

    `action` is the (forward, lateral, vertical) vector from
    `perception.yolo_action.detections_to_action`, each component in [-1, 1].

    Out-of-band readings are treated as *no measurement*, never as clear space.
    A rangefinder reports its floor or ceiling for a dirty lens, a specular
    surface, or open sky alike, and reading "no return" as "nothing there" would
    convert a broken sensor into a confident all-clear — the exact failure this
    check exists to prevent.
    """
    forward = float(action[0]) if len(action) else 0.0
    required = required_clearance(forward, max_speed_mps, lookahead_s, stop_margin_m)

    if measured_range_m is None:
        return FreeSpaceCheck(False, forward, None, required, "no_reading")
    measured = float(measured_range_m)
    if not (min_valid_m <= measured <= max_valid_m):
        return FreeSpaceCheck(False, forward, None, required, "reading_out_of_range")

    return FreeSpaceCheck(
        contradicted=measured < required,
        commanded_forward=forward,
        measured_range_m=measured,
        required_clear_m=required,
        reason="contradiction" if measured < required else "consistent",
    )


def nearest_range(
    depth_image,
    *,
    roi_fraction: float = 0.5,
    percentile: float = 5.0,
    min_valid_m: float = 0.1,
    max_valid_m: float = 100.0,
) -> Optional[float]:
    """
    Reduce a depth image to a single "nearest surface ahead" range, in metres.

    `depth_image` is an (H, W) array of metric depths — AirSim's
    `DepthPerspective` in `simGetImages` gives exactly this.

    Two choices worth stating:

    `roi_fraction` restricts the reduction to a centred box, because the action
    being checked is about the path ahead and the frame edges see ground and sky
    that would dominate a whole-frame minimum.

    `percentile` is used instead of a hard minimum. A single hot pixel — sensor
    noise, a rain streak, an interpolation artefact at a depth discontinuity —
    would otherwise report an obstacle centimetres away and cry wolf on every
    frame. The 5th percentile keeps the answer "near" while requiring a coherent
    patch of pixels to agree.

    Returns None when no valid depth remains, which `check_free_space` treats as
    no measurement rather than as clear space.
    """
    import numpy as np

    depth = np.asarray(depth_image, dtype=float)
    if depth.ndim != 2 or depth.size == 0:
        return None

    height, width = depth.shape
    frac = min(1.0, max(0.05, roi_fraction))
    half_h, half_w = int(height * frac / 2), int(width * frac / 2)
    cy, cx = height // 2, width // 2
    roi = depth[
        max(0, cy - half_h): min(height, cy + half_h + 1),
        max(0, cx - half_w): min(width, cx + half_w + 1),
    ]

    valid = roi[np.isfinite(roi) & (roi >= min_valid_m) & (roi <= max_valid_m)]
    if valid.size == 0:
        return None
    return float(np.percentile(valid, percentile))
