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
The observation itself. `detections_present` closes this specific band: a patched
drone reports `False`, an honest peer reports `True`, and that disagreement is
independent of threat gain and controller tuning. `class_ids` also prevents an
unrelated detection from automatically hiding a target-class miss.

Viewpoint robustness drove the field choice. Peers are deliberately separated
(`phi_min`), so anything strongly viewpoint-dependent would produce false
disputes between honest drones:

* ``detections_present`` -- the primary signal after synchronized co-visibility
  gating. Occlusion and threshold effects can still produce an honest mismatch;
  the fail-safe result is a hold.
* ``class_ids`` -- sorted unique IDs in one pinned mission taxonomy. Compared
  exactly; model-specific label spaces must be mapped before claim construction.
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

This compact claim is a fail-safe demo protocol, not object-level proof. It does
not associate individual tracks across views; production object identity needs
calibrated multi-view projection or track association in addition to this check.
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
MAX_DETECTION_COUNT = 4096
MAX_CLASS_ID = 65_535

Action = tuple[float, float, float]


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
    class_ids: tuple[int, ...] = ()  # sorted unique mission-taxonomy class IDs
    occupancy: float = 0.0        # summed detection area / frame area, [0, 1]
    max_confidence: float = 0.0   # [0, 1] -- forensics only, never compared
    bearing: float = 0.0          # dominant detection centre x, [-1, 1] -- never compared

    def __post_init__(self) -> None:
        if type(self.measured) is not bool or type(self.detections_present) is not bool:
            raise ValueError("measured and detections_present must be booleans")
        if type(self.detection_count) is not int or not (
            0 <= self.detection_count <= MAX_DETECTION_COUNT
        ):
            raise ValueError(
                f"detection_count must be an integer in [0, {MAX_DETECTION_COUNT}]"
            )
        try:
            class_ids = tuple(self.class_ids)
        except TypeError as exc:
            raise ValueError("class_ids must be a sequence of integers") from exc
        if any(
            type(class_id) is not int or not (0 <= class_id <= MAX_CLASS_ID)
            for class_id in class_ids
        ):
            raise ValueError(
                f"class_ids must contain integers in [0, {MAX_CLASS_ID}]"
            )
        if class_ids != tuple(sorted(set(class_ids))):
            raise ValueError("class_ids must be sorted and unique")
        object.__setattr__(self, "class_ids", class_ids)
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
        if not self.measured and (
            self.detections_present
            or self.detection_count
            or self.class_ids
            or self.occupancy != 0.0
            or self.max_confidence != 0.0
            or self.bearing != 0.0
        ):
            raise ValueError("an unmeasured claim cannot carry detection evidence")
        if self.detections_present and self.detection_count == 0:
            raise ValueError("detections_present requires detection_count >= 1")
        if self.detections_present and not self.class_ids:
            raise ValueError("detections_present requires at least one class_id")
        if self.detection_count and not self.detections_present:
            raise ValueError("detection_count > 0 requires detections_present")
        if len(self.class_ids) > self.detection_count:
            raise ValueError("unique class_ids cannot exceed detection_count")
        if self.measured and not self.detections_present and (
            self.occupancy != 0.0
            or self.max_confidence != 0.0
            or self.bearing != 0.0
        ):
            raise ValueError("an empty-scene claim cannot carry detection evidence")

    @classmethod
    def unmeasured(cls) -> "PerceptionClaim":
        """No perception evidence. NOT a claim that the scene was empty."""
        return cls(measured=False)

    @classmethod
    def from_detections(cls, dets: Sequence) -> "PerceptionClaim":
        """
        Build a claim from `perception.yolo_action.Detection` objects.

        Occupancy is the summed box area clipped at 1.0, not a true geometric
        union, so duplicate or overlapping boxes can inflate it. It is only a
        coarse secondary check; class presence and detection presence carry the
        sharp fail-safe decisions.
        """
        if not dets:
            return cls(measured=True, detections_present=False)
        if len(dets) > MAX_DETECTION_COUNT:
            raise ValueError(f"too many detections (maximum {MAX_DETECTION_COUNT})")
        occupancy = min(1.0, sum(max(0.0, d.w) * max(0.0, d.h) for d in dets))
        dominant = max(dets, key=lambda d: d.w * d.h)
        return cls(
            measured=True,
            detections_present=True,
            detection_count=len(dets),
            class_ids=tuple(sorted({int(d.cls) for d in dets})),
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
    4. Both see something but their class sets differ -> ``False``. Class IDs must
       be mapped to the same pinned mission taxonomy before claims are built.
    5. Otherwise agree iff occupancy is within tolerance. Wide, since peers view
       from >= phi_min apart and the same obstacle legitimately covers a
       different fraction of each frame.

    Bearing and confidence are deliberately not compared — both vary with
    viewpoint, and disputing honest peers over parallax is precisely the false
    positive the co-visibility gate exists to avoid.
    """
    if not math.isfinite(occupancy_tolerance) or not (
        0.0 < occupancy_tolerance <= 1.0
    ):
        raise ValueError("occupancy_tolerance must be finite and in (0, 1]")
    if not isinstance(claimed, PerceptionClaim) or not isinstance(
        observed, PerceptionClaim
    ):
        return None
    if not claimed.measured or not observed.measured:
        return None
    if claimed.detections_present != observed.detections_present:
        return False
    if not claimed.detections_present:
        return True
    if claimed.class_ids != observed.class_ids:
        return False
    return abs(claimed.occupancy - observed.occupancy) <= occupancy_tolerance


@dataclass(frozen=True)
class PerceptionResult:
    """Atomic detector output used by live workers and the mission loop.

    Keeping the action and claim in one immutable value prevents an adapter from
    accidentally signing a claim from one inference while releasing the action
    from another.
    """

    action: Action
    claim: PerceptionClaim

    def __post_init__(self) -> None:
        try:
            action = tuple(float(v) for v in self.action)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("action must contain three normalized numbers") from exc
        if len(action) != 3 or not all(
            math.isfinite(v) and -1.0 <= v <= 1.0 for v in action
        ):
            raise ValueError("action must contain three finite values in [-1, 1]")
        if not isinstance(self.claim, PerceptionClaim) or not self.claim.measured:
            raise ValueError("a perception result requires a measured claim")
        object.__setattr__(self, "action", action)
