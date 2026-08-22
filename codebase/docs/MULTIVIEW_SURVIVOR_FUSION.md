# Multi-view survivor fusion

This layer connects the existing different-angle/co-visibility work to the SIH 26177
search-and-rescue mission. It does not replace VeriSwarm receipt verification, PBFT-style
consensus or the flight safety supervisor.

## Non-negotiable rescue policy

A model-thresholded positive `person_candidate` from one valid view creates a `HIGH`
responder alert. Another camera's non-detection never deletes or downgrades it.

The reason is physical: a collapsed wall, smoke, debris, vehicle or camera angle can hide
a person in one image while another camera sees them. Absence of a bounding box is not
evidence that the person is absent.

Every candidate still requires responder confirmation. `person_candidate` must never be
renamed to `confirmed_survivor` by vision alone.

## What “common intersection” means

There are two different geometries:

1. The physical webcam demonstration uses ORB/RANSAC homography to project one planar view
   into another before measuring shared image area and box IoU. It is useful for proving
   common scene coverage, but it is not 3-D localization.
2. Disaster localization uses calibrated camera rays in the shared NED frame. Raw boxes
   from different cameras are never compared directly.

For a positive person box, `rescue.multiview` projects its centre through the frozen camera
intrinsics and camera-to-NED rotation to produce a bearing ray.

- One positive view plus metric depth/range produces a 3-D point and uncertainty.
- Two synchronized positive views with sufficient baseline/parallax produce a ray
  intersection by triangulation.
- Several consistent range/triangulation estimates are uncertainty-weighted.
- One positive view without depth remains `bearing_only`. It still creates a high-priority
  alert and can task another view, but it is not shown as a point on the map.
- A non-detection cannot produce a ray and therefore cannot triangulate a person.

## Missing-view and patch policy

A camera with no person box is classified as one of:

| Disposition | Meaning |
|---|---|
| `ABSTAIN_UNHEALTHY` | stale/failed camera or inference; no semantic vote |
| `ABSTAIN_NOT_COVISIBLE` | does not cover the candidate region; no semantic vote |
| `ABSTAIN_OCCLUDED_OR_UNPROVEN` | common scene but line of sight is blocked or unknown |
| `ABSTAIN_STALE` | frame is outside the positive capture group's time window |
| `EXPECTED_VISIBLE_MISS` | healthy, synchronized and proven-visible candidate was missed |

Only `EXPECTED_VISIBLE_MISS` raises a perception-security review. It may mean occlusion
modelling is wrong, the detector failed, or an adversarial patch affected a view. The
positive candidate remains visible and high priority while the affected perception path is
checked.

A positive observation whose model/receipt layer is `DISPUTED` is handled similarly: keep
the rescue candidate, mark it for security and responder review, and let the independent
flight-control security layer decide whether that node may command motion.

`evidence_security=VERIFIED` must be assigned by the receipt/model-verification integration,
not asserted by an unauthenticated detector payload. `rescue.security_bridge` accepts the
real `ConsensusResult` only after the caller supplies the expected receipt digest and the
results of receipt, model and runtime verification. It maps evidence as follows:

- exact binding + verified receipt/model/runtime + `ACCEPTED` + semantic ACK threshold:
  `VERIFIED`;
- valid evidence without semantic quorum: `UNVERIFIED`;
- receipt-digest/integrity failure or `REJECTED`: `DISPUTED`.

Minority disputes and peer equivocation are retained as security-review reasons. The
adapter does not verify signatures itself and its result is never a survivor-presence
vote.

Therefore:

- PBFT/receipt consensus protects autonomous commands and node trust.
- Multi-view fusion protects survivor recall and improves localization.
- A control `HOLD` or camera quarantine never erases already observed rescue evidence.

## Broken-building example

`examples/rescue_multiview_person_sample.json` represents:

- Camera A sees the collapsed building but the wall blocks the person.
- Camera B detects one `person_candidate` and has a metric range.
- Camera A abstains as occluded.
- Camera B localizes the candidate at the measured NED point.
- The candidate receives a high-priority responder alert without multi-camera voting.

Run it from `codebase/`:

```bash
python -m tools.rescue_multiview_fusion \
  --input examples/rescue_multiview_person_sample.json \
  --out results/rescue_multiview_person_sample.result.json
```

The result contains `observation_enrichments`. Samik adds the enrichment for each positive
view to its normal `veriswarm.rescue.event.v1` observation before placing the event in the
offline outbox. Pratik supplies frozen camera calibration, synchronized camera poses,
metric depth/range and line-of-sight evidence from CoSys.

The receipt producer derives the view label with
`rescue.assess_consensus_security(...)`. The exact `target_receipt_hash` binding prevents a
consensus result for one frame from being attached to another. `NO_QUORUM` and a
cryptographic-only ACCEPT remain actionable rescue evidence but are visibly unverified.

`capture_group_id` is traceability metadata, not sufficient merging authority. Calibrated
multi-view observations merge only when the fusion association ID matches **and** their
measured NED locations are consistent. Different groups remain separate even when they are
nearby. If localization is unavailable or contradictory, candidates remain separate so an
incorrect association cannot hide a person.

## Required inputs

- frozen intrinsics: image size, `fx`, `fy`, `cx`, `cy`;
- right-handed orthonormal camera-to-NED rotation;
- camera NED position and pose uncertainty;
- synchronized frame time;
- normalized `[x1,y1,x2,y2]` person box;
- metric range and range uncertainty when available;
- explicit visibility/line-of-sight evidence for any claimed expected-visible miss.

## Fail-closed geometry

The implementation rejects invalid rotations, non-finite values, stale view pairs,
insufficient parallax, points behind a camera, excessive closest-ray gap, contradictory
location estimates and incomplete range/uncertainty pairs. A localization failure removes
only the map point; it does not remove the person candidate.

Positive views outside the synchronization window do not count as corroboration and are
reported with `positive_views_span_time_limit`; their underlying positive alerts remain.

Camera calibration, synchronization, detector association and simulated depth must still
be measured for the final scenario. The current code is a deterministic integration layer,
not proof that an uncalibrated physical camera rig can recover real-world coordinates.
If every useful view is occluded, unavailable or fooled and no positive observation is
produced, this layer cannot invent a survivor. The mitigation is angularly diverse views,
RGB/thermal sensing and active confirmation tasking—not a false guarantee of perfect
detection.
