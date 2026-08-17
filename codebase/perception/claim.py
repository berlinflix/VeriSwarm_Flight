"""
`PerceptionClaim` — what a drone *observed*, as distinct from what it *decided*.

The problem this solves
-----------------------
The semantic layer originally compared **control outputs**: two co-observing
drones each produced a `(forward, lateral, vertical)` command and the peer
disputed when the L2 distance exceeded theta. That is a category error, and it
produced a measured, reproducible hole.

A control output is a *lossy, non-injective projection* of what was perceived.
Many different world-states map to the same command. Concretely, with the
protected fallback:

    "I detect nothing"                       -> (0.000, 0, 0.000)
    "I detect a moderate obstacle, slow down"-> (0.028, 0, 0.324)

Those are 0.325 apart, comfortably inside theta = 0.5, so a drone blinded by an
adversarial patch and an honest drone looking straight at a truck **agree**. The
band sat at roughly 0.5-0.6 frame occupancy, because the avoidance action passes
through the origin as threat rises. See `tests/test_perception_claim.py`.

No threshold tuning fixes this. The information required to tell the two apart
was destroyed before the comparison happened.

What is attested instead
------------------------
The observation itself. `detections_present` alone closes the band: a patched
drone reports `False`, an honest peer reports `True`, and that disagreement is
independent of obstacle size, range, threat gain, and controller tuning.

Viewpoint robustness drove the field choice. Peers are deliberately separated
(`phi_min`), so anything strongly viewpoint-dependent would produce false
disputes between honest drones:

* ``detections_present`` -- effectively viewpoint-invariant for a real obstacle.
  The primary signal.
* ``occupancy`` -- roughly comparable between co-visible peers at similar range,
  so it is compared with a wide tolerance and only when both peers see something.
* ``max_confidence`` -- carried for the console and forensics, **not** compared.
  It varies too much with angle and lighting to be evidence.
* ``bearing`` -- carried, **never** compared. Two drones 6 m apart legitimately
  see the same obstacle at different bearings; comparing it would dispute honest
  peers, which is exactly the false-positive mode the co-visibility gate exists
  to prevent.

Binding
-------
A claim is only meaningful attached to the frame it was derived from. It carries
no hashes itself; it is embedded in the `Receipt`, which already binds
`input_hash`, `model_hash`, `runtime_hash`, `mission_id`, `mission_epoch` and
`sequence`. Signing the receipt therefore binds the claim to a specific frame,
processed by a specific model, on a specific runtime, in a specific mission
round. A claim lifted out of one receipt cannot be replayed into another without
breaking the signature.

Unmeasured claims
-----------------
`PerceptionClaim.unmeasured()` marks "no perception evidence available" — used by
the evaluation harness and by any path that constructs a receipt without running
a detector. It is **not** a claim that the scene was empty, and the comparison
treats it as *no evidence*, never as agreement. Reading absence of evidence as
evidence of absence is the same mistake as the original blind band, one layer up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

#: Occupancy difference two co-visible peers may show while still agreeing.
#: Wide on purpose: peers sit at >= phi_min of parallax and at slightly different
#: ranges, so the same obstacle legitimately covers a different fraction of each
#: frame. `detections_present` is the sharp signal; this only catches gross
#: disagreement about scale.
DEFAULT_OCCUPANCY_TOLERANCE = 0.35


@dataclass(frozen=True)
class PerceptionClaim:
    """
    One drone's attested statement about what its detector observed.

    Immutable, and embedded in the signed `Receipt`. Every field is finite and
    range-checked at construction, because these values cross the wire into
    another aircraft's decision and a NaN that reaches a comparison silently
    poisons it.
    """

    measured: bool = False
    detections_present: bool = False
    detection_count: int = 0
    occupancy: float = 0.0        # summed detection area / frame area, [0, 1]
    max_confidence: float = 0.0   # [0, 1] -- forensics only, never compared
    bearing: float = 0.0          # dominant detection centre x, [-1, 1] -- never compared

    def __post_init__(self) -> None:
        if self.detection_count < 0:
            raise ValueError("detection_count must be non-negative")
        for name in ("occupancy", "max_confidence", "bearing"):
            value = getattr(self, name)
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0.0 <= self.occupancy <= 1.0:
            raise ValueError("occupancy must be in [0, 1]")
        if not 0.0 <= self.max_confidence <= 1.0:
            raise ValueError("max_confidence must be in [0, 1]")
        if not -1.0 <= self.bearing <= 1.0:
            raise ValueError("bearing must be in [-1, 1]")
        if not self.measured and (self.detections_present or self.detection_count):
            raise ValueError("an unmeasured claim cannot report detections")
        if self.detections_present and self.detection_count == 0:
            raise ValueError("detections_present requires detection_count >= 1")
        if self.detection_count and not self.detections_present:
            raise ValueError("detection_count > 0 requires detections_present")

    @classmethod
    def unmeasured(cls) -> "PerceptionClaim":
        """No perception evidence. NOT a claim that the scene was empty."""
        return cls(measured=False)

    @classmethod
    def from_detections(cls, dets: Sequence) -> "PerceptionClaim":
        """
        Build a claim from `perception.yolo_action.Detection` objects.

        Occupancy is the summed box area, clipped at 1.0 rather than being a true
        union — overlapping boxes would otherwise double-count. The clip is
        deliberate and harmless: the comparison only needs a coarse scale
        agreement, and a union computation would add cost to the per-frame hot
        path for precision nothing downstream uses.
        """
        if not dets:
            return cls(measured=True, detections_present=False)
        occupancy = min(1.0, sum(max(0.0, d.w) * max(0.0, d.h) for d in dets))
        dominant = max(dets, key=lambda d: d.w * d.h)
        return cls(
            measured=True,
            detections_present=True,
            detection_count=len(dets),
            occupancy=occupancy,
            max_confidence=min(1.0, max(0.0, max(float(d.conf) for d in dets))),
            bearing=max(-1.0, min(1.0, 2.0 * (dominant.x - 0.5))),
        )

    def describe(self) -> str:
        if not self.measured:
            return "no perception evidence"
        if not self.detections_present:
            return "detector reports an empty scene"
        return (f"{self.detection_count} detection(s), occupancy={self.occupancy:.2f}, "
                f"conf={self.max_confidence:.2f}")


def claims_agree(
    claimed: Optional[PerceptionClaim],
    observed: Optional[PerceptionClaim],
    occupancy_tolerance: float = DEFAULT_OCCUPANCY_TOLERANCE,
) -> Optional[bool]:
    """
    Compare two perception claims.

    Returns **True** (agree), **False** (dispute), or **None** meaning *no
    conclusion* — one side carried no measured evidence, so there is nothing to
    compare. `None` must never be collapsed to True by the caller: an
    unverifiable claim is not an agreed one.

    The decision rule, in order:

    1. Either side unmeasured -> ``None``. No evidence, no verdict.
    2. ``detections_present`` differs -> ``False``. This is the sharp signal and
       the one that closes the blind band: it does not depend on obstacle size,
       range, or controller gains. One drone says the scene is empty, a
       co-observing peer says it is not, and only one of them can be right.
    3. Both report an empty scene -> ``True``.
    4. Both see something -> agree iff occupancy is within tolerance. Wide, since
       peers view from >= phi_min apart and the same obstacle legitimately covers
       a different fraction of each frame.

    Bearing and confidence are deliberately not compared — both vary with
    viewpoint, and disputing honest peers over parallax is precisely the false
    positive the co-visibility gate exists to avoid.
    """
    if not math.isfinite(occupancy_tolerance) or occupancy_tolerance <= 0.0:
        raise ValueError("occupancy_tolerance must be finite and positive")
    if claimed is None or observed is None:
        return None
    if not claimed.measured or not observed.measured:
        return None
    if claimed.detections_present != observed.detections_present:
        return False
    if not claimed.detections_present:
        return True
    return abs(claimed.occupancy - observed.occupancy) <= occupancy_tolerance
