# MESSAGE — SUYASH TO SAMIK, PRATIK, ABHIJAN AND AYUSH

Date: 2026-08-22 IST

Subject: Adopt survivor-first multi-view fusion from `e208551`

This is a work handoff, not an approval gate. Continue independently, commit useful
checkpoints to your own branch and update your own `STATUS_<NAME>.md`. Do not wait for a
confirmation message.

## Read and adopt

Fetch branch `suyash/sih26177-rescue-integration` and inspect commit:

```text
e208551617d2aa3f6b04f2221d16e3454c759965
```

Required contract:

- `codebase/docs/MULTIVIEW_SURVIVOR_FUSION.md`
- `codebase/docs/RESCUE_DATA_PLANE.md`
- `codebase/examples/rescue_multiview_person_sample.json`

Cherry-pick `e208551` when you need these APIs on your branch. Resolve only real overlap
with your own work; do not copy generated results, environments, datasets or weights into
Git.

## Shared policy

- One detector-thresholded positive `person_candidate` immediately becomes a `HIGH`
  responder alert and always remains explicitly unconfirmed.
- A missing angle abstains when unhealthy, stale, outside common coverage, occluded or
  not independently proven visible.
- A healthy synchronized `EXPECTED_VISIBLE_MISS` raises a perception-security review but
  never deletes the positive.
- Two **positive**, synchronized calibrated views may triangulate. A negative view cannot.
- One positive plus metric CoSys depth/range may localize immediately.
- One positive without range remains a bearing-only alert and requests another view; no
  fake map coordinate is allowed.
- VeriSwarm receipt/model/runtime checks and semantic consensus label evidence and gate
  autonomous control. `HOLD`, `REJECTED` or `NO_QUORUM` never suppresses the responder
  alert.

## Samik — perception integration

Continue cloud setup/training without waiting for this work.

1. Convert each thresholded person detection to `PersonView` with the exact frame time,
   normalized box and calibrated camera record supplied by Pratik.
2. Add metric depth/range and uncertainty when the simulator supplies it; do not use
   monocular guessed depth as metric truth.
3. Call `fuse_person_views(...)` for measured associations and place each returned
   observation enrichment into the normal rescue event/outbox path.
4. Derive `evidence_security` with `assess_consensus_security(...)` from the actual bound
   receipt digest, model/runtime checks and real `ConsensusResult`. Never let the detector
   self-assert `VERIFIED`.
5. Emit every positive even when fusion cannot localize or consensus does not authorize
   motion.
6. For association, use tracking plus calibrated geometry/appearance. Do not group raw
   cross-camera boxes using unregistered box IoU.
7. Add one detector-derived fixture before the integrated run; the supplied JSON is only
   a deterministic contract example.

## Pratik — CoSys disaster geometry

1. Create the broken-building beat: Camera/Drone A sees the building but the wall blocks
   the person; Camera/Drone B has a positive view of that same person.
2. Export image size and intrinsics (`fx`, `fy`, `cx`, `cy`), right-handed camera-to-NED
   rotation, camera NED position, pose uncertainty and synchronized frame timestamp.
3. Export metric depth/range at the positive person box plus a defensible uncertainty.
4. For any claimed expected-visible miss, provide independent scene/raycast line-of-sight
   evidence. Unknown visibility is an abstention, not a dispute.
5. Keep simulator ground truth separate for Abhijan's post-run evaluation. Do not feed the
   survivor's truth position into perception or association.
6. Demonstrate: A misses due to occlusion, B detects, candidate appears on the map from B's
   depth, priority is HIGH, and mission search/control continues under the independent
   safety policy.

## Ayush — physical different-angle display

1. Preserve the existing camera panels, live YOLO boxes and projected common-area panel.
2. Add person-specific state to the UI: `POSITIVE`, `ABSTAIN_OCCLUDED/NOT_COVISIBLE`,
   `EXPECTED_VISIBLE_MISS`, and fused `HIGH PERSON CANDIDATE`.
3. If one common-area view has a person and another does not, keep the person alert. Show
   the second result as abstention unless visibility is independently proven.
4. Keep homography claims honest: it measures shared planar image area and projected box
   overlap, not world-coordinate 3-D localization.
5. Hand UI changes to Samik for execution on P2; Ayush remains the UI owner and does not
   take over Abhijan's attack terminal.

## Abhijan — patch/model-swap evaluation

1. Add a one-view adversarial-patch trial in the broken-building scenario.
2. Required result: an unaffected positive view remains a HIGH person alert; the affected
   expected-visible miss opens `PERCEPTION_SECURITY_REVIEW`; unsafe autonomous control may
   HOLD without deleting the candidate.
3. Preserve model-swap/receipt rejection as the separate VeriSwarm security demonstration.
4. Score survivor recall, false alerts, localization error and security detection against
   withheld simulator truth after the run. Truth is an oracle, never a producer input.
5. Include a total-failure case in the report: if every useful sensor is occluded or fooled
   and no positive exists, the system cannot invent a survivor. Record this honestly.

## Integrated acceptance beat

The smallest convincing run is:

1. Show two or more angles of one damaged building.
2. A is occluded and reports no person; B detects a person.
3. B's depth or two positive rays locates the candidate in NED.
4. Dashboard shows one HIGH, unconfirmed person candidate and its evidence/security state.
5. Apply a one-view patch or model swap.
6. VeriSwarm raises security review/HOLD as applicable while the person alert remains.
7. Save detector output, fusion result, rescue event, projected dashboard state and oracle
   metrics under a new run ID.

No per-file approval loop is required during development. Hash only the final selected
weight and final release artifacts.
