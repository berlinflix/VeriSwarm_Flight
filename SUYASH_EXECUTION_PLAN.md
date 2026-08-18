# Suyash — standalone execution plan

**Role:** M1, trusted integration, OP-TEE, evidence and acceptance owner  
**Primary outcome:** freeze the contracts and security boundary that connect perception,
autonomy, attacks and Cosys; ensure unverifiable/stale/unsafe decisions cannot reach motion;
and produce reproducible evidence and a final runbook.

## Immediate internal-qualifier overlay — 19 August 2026

Read `FIVE_CABLE_EXECUTION_FREEZE_2026-08-19.md` first. Suyash owns cable/IP acceptance,
Charlie at `192.168.50.13:51003`, the fresh Jetson OP-TEE preflight and final GO/NO-GO.
Ayush designs `covis_live`; Samik reviews, integrates and operates it on P2 before starting
Bravo. Suyash reviews their single frozen commit and accepts the real P2 camera evidence.
There are five cables: the fifth endpoint is Abhijan's `.14` attack-control Mac; Ayush has
no runtime IP and no `.15` endpoint exists.

This document contains Suyash's work only. Other names identify required inputs or review
participants, not tasks Suyash should silently absorb.

## 1. Non-negotiable boundaries

- `MissionRunner`/the reviewed autonomy integration seam and `SafetySupervisor` are the
  only accepted path toward motion.
- Never approve a system from UI output alone. Acceptance comes from immutable raw evidence,
  conformance tests and declared invariants.
- Keep authority approval separate from model provisioning and attack generation. The
  authority private key never enters Git, a run bundle, a simulation node or Samik's model
  downloader.
- In this simulation, one OP-TEE-backed node is deliberate and sufficient: Alpha. Bravo–
  Echo use distinct development keys. Never pretend the single Alpha key represents five
  hardware identities.
- Alpha's originator process runs locally on the Jetson after the separate P2 camera stage.
  Do not add
  a generic unauthenticated remote signing service.
- Describe OP-TEE precisely: it protects Alpha's Ed25519 key and signs canonical bytes; it
  does not attest YOLO, pose, VIO or model loading.
- No protected collision is acceptable. The isolated unprotected baseline has no path to
  the accepted command sink.
- Do not call the result “military-ready,” “certified,” “foolproof” or formal Byzantine
  agreement. State the tested simulation envelope and remaining real-world gates.

**Audited code baseline (2026-08-18):** protocol v4 is active. A receipt signs a measured,
class-aware `PerceptionClaim` and the exact candidate command in `Receipt.output`; the gRPC
bridge preserves the complete claim; action-only agreement is veto-only and cannot form
semantic quorum; `SafetySupervisor` matches the consensus target digest, actual evidence
receipt and exact requested action. The current suite is **347 passed, 3 skipped**; all
three skips are Ultralytics-asset-dependent co-visibility tests, not passes. Re-run and
record the actual count whenever the baseline changes.

## 2. Files and artifacts Suyash owns

| Path/artifact | Required purpose |
|---|---|
| `codebase/docs/AUTONOMY_CONTRACT.md` | Versioned schemas, frames, clocks, bounds and ownership |
| `codebase/autonomy/contracts.py` | Machine-validated contract types |
| `codebase/autonomy/decision_record.py` | Trace from state/evidence to requested/released command |
| `codebase/perception/safety_supervisor.py` | Final fail-closed motion authorization |
| `codebase/node/mission.py` | Reviewed integration seam, not an actuator CLI |
| `codebase/models/registry.json` | Model source/hash/license/status/reviewer registry |
| `codebase/docs/MODEL_SELECTION_REPORT.md` | Independent model approval decision |
| `codebase/tools/optee_preflight.py` | Jetson handover and pinned-key evidence |
| Camera-code acceptance for `codebase/tools/covis_live.py` | Review Ayush's implementation, Samik's test reproduction and the real P2 evidence |
| `codebase/docs/COVIS_LIVE.md` | Physical rig setup/calibration/run instructions |
| `codebase/tools/event_collector.py` | Bounded authenticated/best-effort event collection |
| `codebase/console/app.py` | Operator visualization; never an authority source |
| `RUNBOOK.md` | Final cold-start, handover, operation, abort and recovery procedure |
| Evidence index/claims matrix | What was tested, passed, failed and not claimed |

## 3. Ordered implementation plan

### Y0 — audit and freeze the current baseline

1. Run the full unit/adversarial suite and `python -m sim.closed_loop` before integration.
2. Inventory implemented versus planned files. A document command must not be presented as
   runnable when its entry point does not exist.
3. Record current model/runtime hashes, protocol-v4 receipt/claim schema, reason codes,
   the closed action-alias regression and the still-open object-association/shared-ROI
   limitation.
4. Freeze the isolated unprotected baseline so it cannot import or call `MissionRunner`,
   `SafetySupervisor`, the Cosys adapter or PX4.
5. Open a decision log for every contract/protocol change; require regenerated test vectors
   when signed canonical bytes change.

**Gate Y0:** baseline tests pass, missing deliverables are explicit, and no unsupported
claim appears in the plan/topology/runbook.

### Y1 — freeze interfaces and contracts

Define machine-readable types for:

- `SensorSnapshot`—frame/depth hashes, timestamps, calibration, source and health;
- `EstimatorState`—pose/velocity/orientation, covariance, mode, age, reset and health;
- `MapSnapshot`—frame, resolution/origin, version, timestamp, free/occupied/unknown;
- `TaskLease`—task, owner, monotonic version, issue/expiry and completion state;
- `TargetTrack`—class, state, covariance, observation IDs and confirmation status;
- `PerceptionClaim`—frame/input/model/runtime identity plus detection presence/count,
  supported classes, confidence/extent summary, supporting depth and validity;
- `WaypointProposal`—path/map/estimator versions, active waypoint, candidate velocities,
  progress/arrival/replan status and reason;
- `AutonomyDecisionRecord`—mission/task/map/estimator/perception inputs, candidates,
  rejected constraints, requested action and reason;
- `CommandRequest/Release/Acknowledgement`—vehicle, frame, timestamp, TTL, bounds,
  authorization and actual command;
- attack, event and run-bundle schemas.

Every numeric field has units, frame, valid range, clock source and stale/invalid behavior.
Unknown enum/schema versions fail closed. Encode reason classes centrally so the runner,
oracle, console and report cannot invent incompatible strings.

**Gate Y1:** Pratik's sensor sample, Samik's adapter fixtures and Abhijan's manifests pass
the same conformance suite; malformed/unknown/stale samples are rejected deterministically.

### Y2 — enforce the only motion path (v4 core implemented; autonomy integration open)

1. Review `MissionRunner`, `SafetySupervisor`, autonomy interfaces and command adapter
   imports/call graph.
2. Add tests proving planner, detector, consensus, UI, attack runner and simulator helpers
   cannot reach the raw command API.
3. Preserve the implemented v4 binding: `ConsensusResult.target_receipt_hash` must match
   the actual `evidence_receipt`, and the requested action must exactly equal that signed
   receipt's `output`. Missing claim/evidence, a different receipt or a substituted command
   must remain a HOLD.
4. Refactor the integration seam so `PerceptionClaim` is the attested/peer-verified scene
   evidence and Samik's `WaypointProposal` is a separate mission-intent input. Bind their
   versions/IDs and the exact selected candidate in `AutonomyDecisionRecord`; an accepted
   perception certificate cannot authorize a different command.
5. Reject NaN/Inf, wrong dimensions/frames, stale/repeated requests, unhealthy state,
   unknown clearance and out-of-envelope commands.
6. Ensure expired/missing/rejected/no-quorum/unhealthy/unknown cases become HOLD; only a
   separately validated recovery state may LAND.
7. Ensure process death or simulator pause cannot repeat the last nonzero command.
8. Record requested action, rejected constraints, released action and acknowledgement.

**Gate Y2:** deterministic tests show no bypass and every absent/invalid evidence path ends
in a bounded safe result. The clean path still authorizes a valid command.

### Y3 — protocol-v4 semantic blind-band fix (implemented; evidence calibration open)

The action-alias defect is fixed in code. Do not redesign or downgrade it during autonomy
integration:

1. Keep `PerceptionResult(action, claim)` atomic so a claim from one inference cannot be
   paired accidentally with an action from another.
2. Keep protocol v4 claim fields bounded and canonical: measured state, detection
   presence/count, sorted unique mission-taxonomy `class_ids`, occupancy, confidence and
   bearing. Any signed-field change requires protocol v5, regenerated protobufs and new
   vectors—never silently mutate v4.
3. Keep legacy action comparison veto-only. A disagreement may force HOLD; agreement with
   either claim missing is `ok_no_observation`, never a semantic ACK.
4. Keep exact receipt-to-command enforcement and the refusal catalogue:
   `evidence_receipt_missing`, `consensus_receipt_mismatch`,
   `evidence_command_mismatch`, `perception_claim_missing`.
5. Regenerate retained canonical/signature/size/campaign evidence under v4 and prove v2/v3
   receipts cannot mix with v4.
6. Complete the full occupancy/confidence/position/class sweep, including empty scenes,
   the 0.5–0.6 regression band, target-plus-unrelated-object scenes and class substitution.
7. Calibrate honest cross-view false-HOLD behavior. The compact claim does not yet associate
   individual objects or crop both views to a proven shared ROI; require calibrated
   multi-view projection/track association before making an object-identity claim.
8. Preserve independent depth/LiDAR geometric safety regardless of semantic labels.

**Gate Y3-code:** satisfied by deterministic, protobuf and real loopback-gRPC tests.
**Gate Y3-evidence remains OPEN:** install the pinned Ultralytics assets, eliminate or
justify every skip, run the shared-view/class/occupancy matrix and publish thresholds plus
false-HOLD/false-accept results.

### Y4 — model registry, approval and signed authority

1. Create `models/registry.json` with source URL/host, filename, license, task/classes,
   framework/runtime, size, SHA-256, status and reviewer.
2. Permit Samik to stage only official `yolov8s.pt` and `yolov8m.pt`; keep `yolov8n.pt` as
   regression baseline and the tampered model attack-only.
3. Independently retrieve/verify candidate hashes before locking registry entries.
4. Review held-out class-wise accuracy, small/distant targets, false alarms, latency/resource
   distributions and license. Approve exactly one or reject all.
5. Mint the signed authority allowlist on a separate authority machine/key path.
6. Pin the authority public key outside provisioner-controlled manifests.
7. Require each runtime to hash the exact bytes it loads. Exported ONNX/TensorRT artifacts
   receive separate registry hashes and approval.
8. Refuse auto-download or hash/status mismatch during an accepted run.

**Gate Y4:** a clean approved model loads; missing, modified, tampered, downgraded or
unapproved artifacts fail before motion. The authority private key is absent from every
node and run bundle.

### Y5 — OP-TEE installation, Alpha binding and sequential handover

1. Build/install the reviewed TA and Client Application on the Jetson; record source,
   toolchain, binary hashes, L4T/JetPack/OP-TEE versions and `/dev/tee0` permissions.
2. Enrol Alpha's OP-TEE public key through a controlled process and pin it independently.
3. Keep the P2 camera and protocol stages sequential:
   `P2 covis_live → close/hash evidence → release cameras → Jetson OP-TEE preflight →
   Alpha/Bravo/Charlie start`.
4. Run `tools/optee_preflight.py` using a fresh challenge, actual mission/epoch, pinned key,
   TA/CA paths and a create-once evidence path.
5. Require signer construction to compare the live key with Alpha's manifest identity.
6. Start Alpha's server and Alpha `MissionRunner`/`Originator` on the Jetson; Samik may
   operate it over SSH.
7. Never permit silent Alpha software fallback. Signer loss/timeout makes Alpha unavailable
   and triggers HOLD/reassignment through mission policy.
8. Measure end-to-end p50/p95/p99 signing and mission latency under the final load.
9. Test restart, wrong key, wrong mission/epoch, stale evidence, missing TA/CA and deliberate
   signer termination.

**Simulation decision:** one hardware-protected Alpha is accepted because only one Jetson
exists. Bravo–Echo remain separate software identities. This is not the real-fleet design.

**Gate Y5:** after the independent P2 camera stage closes, the actual Jetson completes a
fresh OP-TEE preflight; canonical Alpha receipts verify under the pinned key; no fallback
occurs; signer loss safely removes Alpha from usable evidence and does not leave motion
active.

### Y6 — implement the physical co-visibility demonstration

Review Ayush's `tools/covis_live.py` implementation, which calls the existing
`protocol/covis_features.py`; do not
copy/fork its algorithm. Requirements:

- explicit Windows camera source, DroidCam URL, resolution, health/skew and thresholds;
- bounded camera-open/read/retry behavior and clean release on exit/error;
- synchronized capture/skew measurement;
- side-by-side annotated views, matches/inliers, projected view/box IoU and verdict;
- optional validated detector output and semantic comparison;
- hash-chained, timestamped evidence and video;
- no command/flight imports or network path to the command sink;
- clear states for co-visible, not co-visible, insufficient evidence and camera failure.

Require Ayush to write `docs/COVIS_LIVE.md` covering physical dimensions, Windows sources,
start/stop, patch, lighting, evidence and the P2 camera-to-Bravo handover. Ayush owns code/
docs, Samik owns integration/operation/recovery, Abhijan owns attack choreography and Suyash
owns final evidence/protocol acceptance.

**Gate Y6:** on Samik P2, clean view, moved camera, printed patch, partial/full cover,
dropped camera and normal/error/interrupt exits produce correct evidence and always release
both sources before Bravo starts.

### Y7 — implement event collector and console

`tools/event_collector.py` must:

- accept only bounded schema-valid events;
- identify source/node/run and preserve monotonic sequence/gap detection;
- reject oversized/malformed input and bound queues/disk use;
- append durable local evidence before/independently of UI delivery;
- tolerate duplicates/reordering and collector restart;
- never block or authorize flight;
- expose health and dropped-event counts.

`console/app.py` must show:

- node/vehicle health and estimator mode;
- receipt backend/key fingerprint, provenance and consensus reason;
- requested versus released command and TTL;
- map/route/task/track/alert state;
- attack delivery acknowledgement and safe outcome;
- evidence gaps and INVALID/UNKNOWN states prominently;
- explicit SIMULATION/UNARMED and claim-boundary labels.

The console must not contain buttons that directly set verdicts or actuator commands.
Samik operates it; Ayush owns design/implementation; Abhijan applies the attack; Suyash
owns evidence acceptance and shared protocol/schema correctness.

**Gate Y7:** console/collector loss, malformed flood, sequence gaps and restart cannot affect
local safety; the display never converts missing evidence into a green/pass state.

### Y8 — integration gates and independent review

Own go/no-go for G0–G6:

- G0 installation/scenario/interface freeze;
- G1 one-drone A→B and adapter safety;
- G2 GPS-denied/VIO bounds;
- G3 five-drone coverage/reassignment/separation;
- G4 detector/tracking/semantic/OP-TEE/supervisor integration;
- G5 adversarial/reliability matrix;
- G6 code/model/world/seed/evidence freeze and clean reproduction.

For each gate:

1. Freeze test version, thresholds and evidence requirements before the run.
2. Verify prerequisites and independent truth separation.
3. Witness or replay raw evidence.
4. Investigate every node/PC disagreement and every skipped/invalid test.
5. Record PASS/FAIL/BLOCKED and owner/action; never waive silently.
6. Prevent later feature changes from entering a frozen gate without reopening it.

**Gate Y8:** no downstream phase relies on an unpassed prerequisite; every waiver/risk is
explicit, owned and excluded from claims where appropriate.

### Y9 — final runbook, evidence index and claims matrix

Assemble `RUNBOOK.md` from tested contributor sections. It must contain:

- equipment/cable/static-IP table and verified ports;
- exact versions, install paths, environment variables and hashes;
- cold boot and preflight order;
- P2 camera stage, source-release check and separate Jetson OP-TEE preflight;
- Pratik's Cosys launch/reset/settings procedure;
- Samik's adapter/campaign start, readiness and shutdown procedure;
- Abhijan's attack dry-run/delivery/cleanup procedure;
- go/no-go, abort, HOLD/LAND and emergency stop rules;
- evidence directories, expected files and checksum verification;
- fallback replay/video that makes no live-simulation claim;
- teardown and post-run integrity check;
- troubleshooting by symptom without bypassing safety.

Every command in the runbook must correspond to an existing tested entry point. Conduct a
clean rehearsal where a teammate follows it without verbal corrections; write every needed
correction into the document and restart the rehearsal.

Maintain a claims matrix with `claim`, `test`, `threshold`, `result`, `evidence`,
`limitations` and `owner`. Distinguish simulation evidence, physical-camera evidence,
hardware-key evidence and future real-flight work.

**Gate Y9:** a teammate reproduces the complete named demonstration and verifies its
evidence using only archived artifacts and the runbook.

## 4. Suyash's required test matrix

Suyash must own/add tests for:

- schema bounds, unknown versions/enums and canonical serialization;
- supervisor authorization and every fail-closed reason;
- separation/binding of `PerceptionClaim`, `WaypointProposal` and the exact selected
  command, including substitution and stale-version attempts;
- command-path import/call-graph bypass attempts;
- semantic presence/action alias, class-substitution and unrelated-object sweeps;
- measured-claim JSON/protobuf/gRPC round trips and v2/v3-to-v4 rejection;
- proof that action-only agreement yields no semantic ACK and cannot authorize motion;
- authority signature, wrong pinned key and model/runtime mismatch;
- signer seed/live-key versus manifest identity mismatch;
- OP-TEE preflight wrong key/mission/epoch, timeout, overwrite and signer loss;
- webcam open/read/synchronization/release failure;
- collector malformed/oversized/duplicate/reordered/gap/restart behavior;
- console UNKNOWN/INVALID presentation and absence of authority endpoints;
- event-chain corruption and artifact-index mismatch;
- complete cold-start/abort/recovery runbook rehearsal.

Before an external acceptance run:

```text
python -m pytest -q
python -m sim.closed_loop
```

Hardware evidence additionally requires the actual Jetson; Windows unit tests cannot prove
`/dev/tee0`, TA loading or secure storage.

## 5. Evidence Suyash must retain

Retain test outputs, skipped-test reasons, protocol vectors, interface versions, authority
and model-review records, public-key fingerprints, TA/CA/source/toolchain hashes, OP-TEE
challenge receipts/signatures/latency, gate decisions, run manifests, event-chain checks,
artifact indexes, known failures, risk decisions, rehearsal notes and final claims matrix.

Never store the authority private key or an OP-TEE private key in evidence.

## 6. Suyash's immediate work queue

1. Freeze the autonomy/sensor/task/track/command/attack/event contracts around the existing
   v4 `PerceptionClaim`/final-command receipt binding required for protected A→B.
2. Publish v4 canonical vectors and complete the open Y3 shared-view/class/asset evidence;
   do not reimplement the already-fixed action-alias logic.
3. Complete model registry and independent candidate approval workflow.
4. Deploy and execute OP-TEE preflight on the real Jetson using protocol-v4 receipts.
5. Implement/test `covis_live.py` and its guide.
6. Implement/test the event collector and console with the four v4 refusal reasons.
7. Review Samik's final-command-before-receipt ordering/Alpha placement and Abhijan's v4
   oracle isolation.
8. Maintain gate decisions while integration proceeds.
9. Assemble and cold-rehearse the final runbook/evidence/claims bundle.

## 7. Definition of done for Suyash

Suyash is done only when every cross-owner interface is versioned and tested; the semantic
blind band is fixed; model/runtime authority is independently approved; the only motion
path is demonstrably supervisor-controlled; the physical cameras release cleanly into a
real Jetson OP-TEE handover; Alpha receipts use the pinned hardware key without fallback;
collector/console failure cannot affect safety; all gates have evidence-backed decisions;
the limitations are stated precisely; and another teammate reproduces the complete frozen
demonstration from `RUNBOOK.md` without Suyash's verbal intervention.
