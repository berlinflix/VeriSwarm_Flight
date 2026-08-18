# Abhijan — standalone execution plan

**Role:** M2, attack-delivery and independent-oracle owner  
**Primary outcome:** prove, through reproducible simulation and physical-camera campaigns,
that real faults reach real interfaces and that protected behavior remains inside declared
safety/mission invariants.

This document contains Abhijan's work only. Other names identify dependencies, not work
Abhijan should silently perform for them.

## 1. Non-negotiable boundaries

- Work only in simulation/SITL and the non-actuating two-webcam rig.
- Inject faults at real interfaces; never set the final verdict, expected vote or released
  command directly.
- Never modify `SafetySupervisor` to make an attack pass or fail.
- Never place a tampered model in `models/approved/`, change the signed authority or obtain
  the authority private key.
- Never composite an adversarial patch into camera frames for accepted evidence. Use a
  physical printed object for webcams and a visible 3-D scene object/material in Cosys.
- Never intentionally collide a protected vehicle. A protected collision is a retained
  failure. The isolated non-actuating/unprotected baseline is a separate experiment.
- Do not describe OP-TEE as trusted inference. It protects Alpha's signing key only.
- Do not treat one fixed attacker count as a universal Byzantine guarantee. Compute
  expectations from the actual roster/quorum configuration.
- Every campaign begins from a clean verified baseline and ends with proved cleanup.

**Protocol-v4 attack baseline (2026-08-18):** receipts now sign a measured, class-aware
`PerceptionClaim` and the exact candidate command; protobuf/gRPC preserve the complete
claim; legacy action comparison is veto-only; and command release requires the actual
receipt whose digest consensus targeted. The audited suite is **347 passed, 3 skipped**,
with all three skips caused by missing Ultralytics assets. The oracle must report skips as
unverified evidence, never silently count them as passes.

## 2. Files and artifacts Abhijan owns

| Path/artifact | Required purpose |
|---|---|
| `codebase/attacks/scenarios/*.json` | Versioned, machine-readable attack manifests |
| `codebase/attacks/runner.py` | Validated delivery orchestration only |
| `codebase/attacks/oracle.py` | Independent invariant scoring from retained evidence |
| `codebase/tests/test_attacks_*.py` | Delivery, cleanup and oracle regression tests |
| `codebase/data/attacks/` | Ignored attack inputs/captures with committed hash manifest |
| Physical rig manifest | Webcam models, calibration, mounts, lighting, patch and distances |
| Campaign report | Baseline/attack/recovery outcomes and unresolved failures |

Attack outputs must never be stored under `models/approved/` or overwrite clean evidence.

## 3. Attack-manifest contract

Every manifest must contain:

- unique `campaign_id`, schema version and owner;
- mission/scenario version, random seed and target node/interface;
- threat assumption and attacker capability;
- clean prerequisite and pre-injection state hash;
- injection mechanism, start checkpoint/time/condition and duration;
- exact payload/artifact hash and parent hash where applicable;
- expected **invariant/reason class**, not a forced verdict;
- stop condition, safety abort, maximum resource/time budget;
- restoration/cleanup steps and post-cleanup verification;
- required raw evidence and oracle version;
- applicability/limitations.

The runner validates the manifest against a schema before touching any interface. Unknown
fields, missing hashes, unknown targets or unsafe/unbounded duration fail before delivery.

## 4. Ordered implementation plan

### A0 — establish the oracle before attacks

Implement `attacks/oracle.py` first. It consumes immutable run evidence plus isolated truth
after the run and checks:

- collision, geofence, altitude, braking and pairwise-separation invariants;
- command authorization, age/TTL, requested-versus-released action and acknowledgement;
- no motion after expiry, API loss, supervisor rejection or declared terminal HOLD;
- receipt/vote signatures, mission/epoch/sequence, replay/equivocation and key identity;
- model/runtime/authority hashes and downgrade state;
- `0 <= semantic_acks <= acks` and configured quorum arithmetic;
- estimator mode/error/uncertainty and truth isolation;
- coverage, task lease state, reassignment and silently abandoned tasks;
- track/alert correctness, duplicates, false alerts and confirmation lifecycle;
- process/resource bounds, queue/cache behavior, dropped evidence and event-chain integrity;
- cleanup/restoration completeness.

The oracle must output `PASS`, `FAIL` or `INVALID_EVIDENCE`; absence of evidence is never a
pass. It may know the invariant but must not communicate the expected result to the live
system.

**Gate A0:** feed the oracle synthetic pass/fail/missing/corrupt bundles and prove it catches
each case without influencing a live component.

### A1 — build the attack runner safely

`attacks/runner.py` must:

1. Validate schema, hashes, target, clean prerequisite and safety budget.
2. Confirm the protected system is in the expected baseline state.
3. Arm exactly one named attack unless the manifest explicitly defines a reviewed compound
   campaign.
4. Record delivery acknowledgement and the observable pre/post interface state.
5. Stop on abort, duration, mission terminal state, resource bound or evidence failure.
6. Restore the modified interface/artifact/configuration.
7. Verify cleanup independently and refuse the next campaign when cleanup is uncertain.
8. Hash and close evidence before scoring.

Support `--dry-run`; it reports intended boundaries and hashes without delivering a fault.
No attack plugin may import or call the command sink or supervisor authorization method.

**Gate A1:** dry-run, invalid manifest, interrupted attack, failed cleanup and repeated
campaign tests all fail safely and never leave a mutation active.

### A2 — identity and protocol campaigns

Create bounded cases for:

- rogue/unknown identity;
- wrong public key or certificate;
- malformed/oversized/non-finite message;
- exact receipt replay;
- stale replay;
- duplicate `(node, mission, epoch, sequence)` with changed content;
- mission/epoch mismatch;
- signed vote equivocation;
- clock jump and timestamp outside the accepted window;
- reordered/duplicated vote traffic.
- protocol-v2 or protocol-v3 receipt submitted to a v4 node;
- v4 receipt with the nested perception field removed, downgraded to `unmeasured`, or
  modified after signing;
- malformed/unsorted/duplicate/out-of-range `class_ids`, class-set substitution and a
  target miss hidden behind an unrelated remaining detection;
- protobuf field-stripping/round-trip mutation that changes canonical receipt bytes;
- action-only agreement with either claim missing—this may be a crypto ACK but must never
  increment `semantic_ack_count` or authorize motion;
- a valid accepted receipt paired with a substituted command;
- a valid consensus result paired with a different round/mission receipt digest.

Observe real verification reason codes. Never generate a verdict by writing to an event
file. Retain the original and conflicting signed objects for forensic comparison.

The protocol-v4 supervisor reasons the oracle must recognize are
`evidence_receipt_missing`, `consensus_receipt_mismatch`,
`evidence_command_mismatch` and `perception_claim_missing`. A legacy action mismatch may
still produce `semantic_disagreement` as a conservative veto; legacy agreement without two
measured claims is `ok_no_observation`, not semantic success.

**Gate A2:** unknown, stale, malformed, downgraded, claim-stripped, command-substituted and
equivocating inputs cannot contribute to an unsafe release; v2/v3 cannot mix with v4;
evidence identifies the offending identity/object and safe outcome.

### A3 — provenance/model campaigns

Use the existing `codebase/yolov8n_tampered.pt` as the standard attack-only artifact. For
any newly generated variant, record mutation recipe, seed, tool/code hash, parent hash and
output hash.

Cases:

- approved file replaced before startup;
- replacement after preflight/startup where technically observable;
- runtime/configuration swap;
- approved-name but wrong bytes;
- authority/manifest mismatch;
- rollback/downgrade attempt;
- missing model and unauthorized automatic-download attempt;
- wrong OP-TEE public key/preflight artifact for Alpha.

Do not edit the authority to bless the attack. The purpose is to prove separation between
provisioning and approval.

**Gate A3:** the actual loaded/mismatched bytes are evidenced and cannot produce an accepted
motion decision under the approved identity. Clean restoration is hash-verified.

### A4 — physical and simulated perception campaigns

#### Physical webcam stage

1. Build the two-camera rig with matched webcams, rigid labelled mounts and recorded
   separation/range/viewpoint.
2. Freeze camera indices, resolution, exposure settings where possible, intrinsics and
   scene lighting.
3. Test the exact printed patch/object at the rehearsed size, material and distance.
4. Run clean shared view, camera-B movement, patch in camera A, partial/full lens cover,
   glare and blur.
5. Retain raw frames, timestamps, calibration, matches/inliers, detections/actions and
   video. The rig has no flight-control connection.
6. End the process cleanly, close capture files and confirm camera devices are released
   before Suyash begins OP-TEE handover.

#### Cosys stage

Use Pratik's visible 3-D patch/occluder modes and raw camera output. Exercise clean,
adversarial, occluded, frozen/dropped RGB, frozen/dropped/corrupt depth, NaN/Inf/range
errors and contradictory modalities. Add class-aware cases: same presence/occupancy but
different class, target plus unrelated object, target removed while an unrelated detection
remains, honest partial-overlap class differences and viewpoint occlusion. Measure both
unsafe accepts and conservative false HOLDs; do not tune the scene merely to force a pass.

**Gate A4:** clean/attacked inputs are distinguishable in raw evidence; invalid or
inconsistent perception cannot release unsafe motion; the webcam stage leaves no process
or device open before Alpha startup.

### A5 — navigation and estimator campaigns

At reviewed simulator/sensor interfaces deliver:

- complete GNSS loss;
- gradual and step spoofing;
- VIO drift, reset and dropout;
- IMU bias and barometer error;
- stale/frozen/non-finite pose;
- frame/axis/unit mismatch fixtures;
- timestamp skew and covariance growth.

Truth records the delivered error but is not sent to the estimator. Do not directly set
`state_estimate_healthy=False`; deliver the sensor/estimator condition that should cause it.

**Gate A5:** mode transitions, uncertainty response and HOLD/degraded behavior match signed
mission bounds; no true pose leaks into recovery.

### A6 — mapping and planning campaigns

Create reproducible cases for:

- newly placed obstacle;
- phantom obstacle;
- removed obstacle/stale occupied cell;
- conflicting/stale map fragment;
- moving obstacle;
- thin/small geometry;
- fully blocked corridor;
- planner timeout/resource cap;
- stale/replayed path or map version;
- modified/out-of-geofence waypoint and active-index rollback;
- false progress, stuck/no-progress and repeated replan exhaustion;
- false overhead clearance.

The attack changes sensor/map/planner/path inputs, not the planner/follower's return value.
Verify that rocks/trees/walls/buildings affect navigation independently of YOLO labels and
that no accepted perception certificate can be reused to authorize a substituted waypoint
command. Exercise both substitution paths explicitly: change `Receipt.output` after signing
(signature failure), and keep a valid receipt/consensus while presenting a different final
command or different receipt to the supervisor (v4 binding refusal).

**Gate A6:** unsafe/unknown routes are never traversed; a complete blockage yields
HOLD/`NO_PATH`; cleanup restores the clean map/scene state.

### A7 — swarm and C2 campaigns

Deliver bounded network/process conditions:

- latency, loss, duplication, reordering and jitter;
- one-way and full partition;
- peer restart and originator loss;
- task-lease replay/conflict/expiry;
- heartbeat loss;
- signed equivocation and declared collusion cases;
- delayed confirmation task;
- command-channel interruption.

Use the configured roster/quorum to generate expected mathematical bounds. Include honest
node/result divergence as a failure signal. Do not call a partition successful merely
because the UI stopped updating.

**Gate A7:** expired or insufficient evidence cannot authorize motion; unfinished tasks
return to allocation; healthy vehicles preserve separation and finish or safely terminate.

### A8 — availability and resource campaigns

Bound and measure:

- invalid-message flood;
- reconnect churn;
- receipt/vote/cache pressure;
- slow inference;
- CPU/GPU/memory pressure within safe lab limits;
- process death;
- simulator pause;
- command-stream loss;
- collector/console loss.

Never run an unbounded flood. Every rate, duration, maximum memory and abort condition is
declared. Console/collector failure must not cause unsafe swarm motion.

**Gate A8:** queues/caches remain bounded or fail closed; watchdog behavior occurs before
stale motion can persist; cleanup returns resource use to baseline.

### A9 — integrated C4 attack choreography

For the final single scenario:

1. Establish and record a clean baseline from A through the pre-C4 checkpoint.
2. Select one primary attack that is visually understandable and technically real.
3. Trigger it at the named C4 checkpoint through the reviewed injector.
4. Show raw delivery evidence, receipt/provenance/semantic consequence, supervisor reason,
   Alpha/affected-node HOLD or isolation and task reassignment.
5. Verify healthy vehicles reach distinct rally states without collision.
6. Close and hash the run before reset.
7. Keep secondary attacks as separate evidence/replay rather than stacking every fault into
   one unreadable demonstration.

**Gate A9:** a judge can trace `delivered fault → evidence → verification result → safe
command → task recovery` without trusting a UI label.

## 5. Physical-rig responsibilities

Abhijan owns:

- the two matched webcams, mounts, labels and USB layout;
- the actual printed patch/object and reproducible print specification;
- lighting, range and viewpoint matrix;
- choreography and safe judge interaction;
- verifying `covis_live` releases devices at handover;
- backup clean/attack recordings and evidence hashes.

Suyash owns the `covis_live` implementation and protocol correctness. Abhijan reports bugs
with raw frames/configuration; he does not fork the algorithm to make the demonstration
look better.

## 6. Console/operator responsibilities

Abhijan operates the L2 console during campaigns but does not own the truth of the result.
Before the demo he must rehearse:

- collector/console start and connectivity check;
- attack selection and dry-run display;
- live fault acknowledgement;
- explaining reason codes without overclaiming;
- network/console loss while safety continues locally;
- backup video/replay;
- abort and cleanup.

UI color or text is never acceptance evidence; the oracle and retained signed/raw artifacts
are authoritative.

## 7. Abhijan's required test matrix

For each attack family include clean baseline, single fault, boundary value, malformed
input, interrupted delivery, failed cleanup and next-run contamination tests. Also test the
oracle with missing/corrupt/duplicated evidence and false expected results.

For protocol/perception families additionally sweep claim measured/unmeasured state,
presence, class sets, occupancy/confidence/position, protobuf/JSON transport, v2/v3
downgrade, target receipt digest and requested command. The oracle must prove that
`semantic_acks` came from measured claim comparison, not merely `reason=ok` text or action
similarity.

At minimum report:

- attack delivery success/failure;
- unsafe accept, safe hold and false hold;
- detection/response/recovery latency distributions;
- command after-expiry count;
- protected collision/geofence/separation violations;
- dropped/invalid evidence;
- task reassignment/completion;
- CPU/GPU/RAM/network/queue maxima;
- clean restoration result.

## 8. Evidence Abhijan must retain

Retain attack manifest/hash, payload/parent hashes, runner/oracle versions, clean baseline,
delivery acknowledgement, raw sensor/network/process effect, receipts/votes, estimator/map
state, supervisor/command events, isolated truth, resource traces, video, cleanup evidence,
oracle result and checksum index. Preserve every failure under its own run ID.

## 9. Abhijan's immediate work queue

1. Freeze attack-manifest and oracle schemas with Suyash's reason catalogue.
2. Implement/test the independent oracle.
3. Implement runner dry-run, bounded delivery and cleanup verification.
4. Complete the physical two-webcam rig and exact printed patch tests.
5. Publish initial v4 downgrade/claim-strip/command-substitution, replay, model-swap, GNSS,
   sensor-freeze and partition manifests.
6. Validate each injector against Samik's reviewed boundary after clean flight passes.
7. Build the C4 demonstration choreography and backup replays.
8. Run the complete reliability matrix on both frozen simulator installations.

## 10. Definition of done for Abhijan

Abhijan is done only when every declared attack is machine-readable, bounded, delivered at
a real interface, independently scored and provably cleaned up; the physical rig produces
retained real-camera evidence and releases the Jetson cleanly; model/protocol/navigation/
mapping/C2/resource faults cannot directly dictate verdicts; protected runs have no unsafe
release or invariant violation; task recovery is evidenced; failures are preserved; and a
teammate can reproduce each campaign from the manifest without Abhijan's verbal guidance.
