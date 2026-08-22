# STATUS — SUYASH

Updated: 2026-08-22 IST

Branch: `suyash/sih26177-rescue-integration`

Latest reviewed integration parent: `bad1acc` plus `f7db83a`

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
- Independently reviewed Pratik's FactoryCity movement lane through `f2bbd65`: the
  focused contract suite passed `18/18`, the full branch passed `379 passed, 3 skipped`,
  and every FactoryCity Python file compiled.
- Integrated Pratik's frozen Point A/Point B, five-drone roster, 10 route cells, nominal
  A-to-B controller and joint movement/security contract through merge parent `e432f42`.
  This accepts the artifacts for development integration, not as live flight or
  authorization-aware movement qualification.
- Verified the merged Suyash/Pratik integration at `490 passed, 3 skipped`; the only
  warning was pytest cache creation being denied by the local Windows sandbox.
- Reviewed Abhijan's staged dashboard change `dd70244`. The production build passed, but
  the clean-stage transition must also require
  `cleanEvidence.dashboard_proof.proof_valid === true` before the UI may claim an
  approved baseline or enable the model-hash attack stage.
- Integrated Abhijan's corrected proof gate and fail-closed movement-security policy
  through `f610667`.
- Frozen an additive cell/coverage/reassignment interface and measured
  `movement_safety` event so a heatmap can be reconstructed from retained evidence rather
  than inferred from counts or animation.
- Frozen simulation timing boundaries and exact movement reason/result codes. Missed
  authorization, control, sensing or enqueue deadlines fail closed to `HOLD`.
- Preserved backward replay compatibility for old count-only evidence while requiring new
  FactoryCity producers to emit exact cell IDs.
- Integrated Pratik's sensor-driven movement v2 (`bad1acc`) and Abhijan's retained
  five-drone cell-mission dashboard (`f7db83a`).
- Corrected hover-before-deflect sequencing, sticky blocked/collision states, fail-closed
  directional depth, deterministic upward escape, projected deflection bounds and the
  measured braking-distance floor.
- Added unique per-drone sector IDs and durable unfinished-cell reassignment with current
  assignment/coverage publication for both source and destination drones.
- Corrected the dashboard to reject stale historical ownership after reassignment and to
  show partial/inconsistent replay as conflict rather than valid coverage.
- Corrected stale authority hashes in the movement extension and added end-to-end
  transition/enqueue deadline checks.
- Verified the integrated result at `546 passed, 3 skipped, 1 deselected` on Windows,
  with the deselected test being the existing POSIX-only owner-mode assertion; dashboard
  tests and its production build also pass.

## In progress now

- Integrating the new detector and live CoSys producers as their branches publish.
- Pratik's movement core and Abhijan's heatmap/replay consumer are integrated for
  development; live CoSys command wiring and retained scenario evidence remain next.

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
- `codebase/sim/cosys/factorycity/factorycity_joint_movement_contract.development.json`
- `codebase/sim/cosys/factorycity/factorycity_ab_mission.development.json`
- `codebase/sim/cosys/factorycity/tools/run_factorycity_ab_swarm.py`
- `codebase/config/rescue_cell_movement_event_extension.v1.json`
- `codebase/docs/RESCUE_CELL_MOVEMENT_EVENT_EXTENSION.md`

## Blockers

- NONE for RGB dataset work, cloud training or unlocated rescue observations.
- No code blocker for detector training, person alerts or bearing-only observations.
- Metric map localization still needs Pratik's calibrated camera pose/depth producer.
- Point B, the straight A-to-B route, roster, route cells and joint movement/security
  contract are now frozen for development. The first retained live five-drone A-to-B
  run is still missing.
- The reviewed core consumes `ALLOW`/`HOLD`/`QUARANTINE`, measured depth and collision
  state in software, but actual CoSys command wiring and retained live proof for every
  returned action are still missing.
- The required original 10-second no-person CoSys RGB negative-control recording is
  still missing and must remain outside Git.
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
- New FactoryCity producers must follow
  `veriswarm.rescue.event_extension.cell_movement.v1`. It is additive to the existing v1
  event envelope and forbids controller access to evaluation-only Unreal truth.

## Next checkpoint

- Review Pratik's first retained live run through the integrated command-boundary gate,
  measured left/right/up deflection and exact cell-event producer.
- Review Abhijan's screenshot/video replay of the integrated current-owner heatmap,
  including a deliberately partial reassignment that must show conflict.
- Review Pratik's first retained live A-to-B run, durable rescue-event producer and
  original no-person CoSys RGB control when published.
- Accept the first real detector-derived multi-view observation from Samik and the first
  calibrated RGB/depth/pose capture group from Pratik, then run the one-view-occluded
  broken-building scenario through the collector and dashboard.
