# STATUS — SUYASH

Updated: 2026-08-22 IST

Branch: `suyash/sih26177-rescue-integration`

Latest work commit: `510c9c2`

## Completed since the previous update

- Implemented and pushed the offline rescue event schema, collector, deterministic
  alerts, situation report and command-dashboard integration.
- Verified the integration with 434 passing tests, 3 expected skips, a production
  dashboard build and a live visual dashboard check.
- Reviewed Samik's adapter candidate `15d1af6` as a suitable development starting point.
- Added a durable SQLite producer outbox with FIFO replay, bounded idempotent retry,
  dead-letter retention and offline recovery for Samik and Pratik producer events.
- Implemented calibrated multi-view person localization using metric range or two
  synchronized positive bearing rays, with uncertainty and strict geometry checks.
- Added survivor-first fusion: one detector-thresholded positive always creates a high
  responder alert; an occluded/missing/disputed view cannot veto it.
- Added a fail-closed bridge from the real VeriSwarm `ConsensusResult` plus receipt,
  model and runtime verification into `VERIFIED`/`UNVERIFIED`/`DISPUTED` evidence.
- Kept PBFT-style control authorization independent: a security `HOLD` remains visible
  and can stop motion without erasing a person candidate.
- Verified the complete repository at 463 passed and 3 dependency-gated skips.
- Reviewed Abhijan's rescue-integration handoff and corrected a producer sequencing gap:
  role-scoped node streams now let perception, telemetry and fusion producers maintain
  independent `source_seq` values without impersonating another node.
- Verified the corrected repository at 469 passed and 3 dependency-gated skips.
- Established Orin Nano 8GB at 15 W as the mandatory model deployment floor, including
  TensorRT FP16, held-out accuracy retention and a sustained full-pipeline benchmark.
- Added native video-file camera input for synthetic/recorded drone POV clips and
  published the two-rate native-display/newest-frame-inference contract.
- Accepted Pratik's movement ownership update: Pratik owns nominal CoSys flight and
  Pratik+Abhijan jointly own authorization-aware movement safety and reassignment.
- Published the P0 Point A/Point B, route, sector, safety, event and no-person CoSys
  negative-control freeze required from Pratik and Abhijan.

## In progress now

- Integrating the new detector, movement contract and CoSys calibration producers as
  their branches publish.

## Outputs available

- `codebase/docs/RESCUE_DATA_PLANE.md`
- `codebase/examples/rescue_event_sample.jsonl`
- `codebase/tools/rescue_event_collector.py`
- `codebase/tools/rescue_event_sender.py`
- `codebase/rescue/outbox.py`
- `codebase/rescue/multiview.py`
- `codebase/rescue/security_bridge.py`
- `codebase/tools/rescue_multiview_fusion.py`
- `codebase/docs/MULTIVIEW_SURVIVOR_FUSION.md`
- `codebase/examples/rescue_multiview_person_sample.json`
- `codebase/docs/JETSON_ORIN_NANO_MODEL_GATE.md`
- `codebase/examples/rescue_model_deployment_manifest.example.json`
- `codebase/docs/SYNTHETIC_DRONE_POV_INPUT.md`
- `codebase/node/frame_source.py` (`VideoFileSource`)

## Blockers

- NONE for RGB dataset work, cloud training or unlocated rescue observations.
- No code blocker for detector training, person alerts or bearing-only observations.
- Metric map localization still needs Pratik's calibrated camera pose/depth producer.
- Point B, the final route/search sectors and the joint movement/security contract are
  not yet frozen.
- The physical rig demonstrates common planar overlap; it cannot claim real 3-D location
  until its cameras are calibrated into a shared metric frame.
- The new rescue model is not Jetson-qualified yet: training, FP16 export and sustained
  Nano measurements have not been produced.

## Shared-interface changes

- Rescue producers use `veriswarm.rescue.event.v1` as documented in
  `codebase/docs/RESCUE_DATA_PLANE.md`.
- Team status and direct coordination now live under `codebase/docs/team_updates/`.
- Person evidence must follow the survivor-first policy in
  `codebase/docs/MULTIVIEW_SURVIVOR_FUSION.md`; semantic consensus is a security label
  and control gate, never a negative vote on survivor existence.
- Node producers use `<node>.perception`, `<node>.telemetry` and `<node>.fusion` stream
  identities. Exact-node sources remain backward-compatible only for a single combined
  producer.
- The final model allowlist binds the actually executed TensorRT engine and runtime
  lineage. A source `.pt` hash alone is insufficient when deployment executes `.engine`.

## Next checkpoint

- Accept the first real detector-derived multi-view observation from Samik and the first
  calibrated RGB/depth/pose capture group from Pratik, then run the one-view-occluded
  broken-building scenario through the collector and dashboard.
