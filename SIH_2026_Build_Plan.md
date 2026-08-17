# SIH 2026 — VeriSwarm simulation and integration plan

> Read [`urgent_new_changes.md`](urgent_new_changes.md) before starting work. It records
> the reasons behind changes and overrides this file if a newer OPEN entry conflicts.

**Plan revision:** 2026-08-18

**Current target:** a repeatable, fail-closed, distributed CoSys AirSim demonstration.

**Later target:** hardware-in-the-loop and restrained flight only after the documented
simulation and safety gates pass.

## Current baseline

The hardened protocol/safety core already exists in `codebase/`:

- `node/mission.py` is the only supported perception → consensus → command seam.
- `perception/safety_supervisor.py` is the only component allowed to release motion.
- `node/live_node.py` provides an atomic, timestamped perception snapshot.
- Protocol-v2 receipts bind the mission, epoch, sequence, runtime measurement, action
  frame/validity, and pose metadata.
- Exact replay, duplicate sequence misuse, vote equivocation, insufficient semantic
  evidence, stale commands, unknown clearance, and unhealthy state fail closed.
- `sim/closed_loop.py` is the deterministic pre-simulator gate.

This baseline is not certification. It must not be described as “military grade,” formal
Byzantine agreement, trusted inference, or proof that a model is safe. OP-TEE protects a
signing key; it does not prove that inference or pose data are truthful.

## Locked execution decisions

1. **CoSys AirSim is the selected external simulator.** Samik and Pratik each run the
   pinned build on their own PC. The exact repository URL, commit/tag, Unreal version,
   Python package version, vehicle firmware, settings, and asset hashes go into the run
   manifest. “Latest” is not a version.
2. **Pratik owns the simulated world and sensor truth.** Samik owns vehicle control,
   reset/recovery, and repeatable campaign execution. Neither may inject an expected
   detector action or a preselected verdict.
3. **Abhijan owns attack delivery and adversarial campaigns.** He attacks real interfaces
   and produces scenario manifests; he never writes verdicts directly or bypasses the
   safety supervisor.
4. **Suyash owns the trusted integration boundary.** He freezes interfaces, reviews
   protocol/safety changes, owns manifests/authority material, and accepts or rejects
   evidence at each gate.
5. **The protected system never intentionally collides.** An unsafe legacy outcome may be
   shown only as a non-actuating shadow trajectory or a clearly separated prerecorded
   sandbox result. Any collision during a protected campaign is a retained test failure.
6. **Simulation and future hardware use the same semantics.** REJECTED, DEFER, NO_QUORUM,
   stale, unhealthy, expired, or clearance-unknown always map to HOLD; a separately
   validated recovery state may map to LAND. No adapter may reinterpret these outcomes.

## End-to-end architecture

The external-simulator integration must preserve this chain:

```text
CoSys AirSim RGB + metric depth + pose + health
              ↓ one atomic timestamped snapshot
actual detector/controller → signed receipt → peer verification/tally
              ↓
SafetySupervisor → released command with short validity/TTL
              ↓
Samik's CoSys/PX4 command adapter → simulated vehicle
              ↓
telemetry + ground truth + events + video + checksums
```

The adapter boundary is deliberately narrow:

- **Sensor source:** one atomic record containing monotonic time, simulator time, RGB,
  metric depth, camera intrinsics/extrinsics, vehicle pose, pose uncertainty, and health.
- **Command sink:** accepts only the supervisor-released command, action frame, expiry,
  mission/epoch, and sequence. It rejects invalid frames, non-finite values, excessive
  magnitude, expired commands, repeated sequences, and unhealthy/offboard state.
- **Ground truth:** obstacle geometry, contact/collision state, and true vehicle pose are
  logged separately from the evidence supplied to the detector. Ground truth must never
  leak into the decision path.
- **Time:** every record identifies its clock domain. Conversion or synchronization error
  is measured and logged; wall-clock equality is never assumed across PCs.

## Member ownership

| Member | Primary ownership | Must hand off | Must not do |
|---|---|---|---|
| **M1 Suyash** | Protocol, safety contract, integration API, manifests/authority, review, final evidence and runbook | Versioned adapter contract, node manifests, acceptance report | Add simulator shortcuts to protocol code; release motion outside `SafetySupervisor` |
| **M2 Abhijan** | Attack delivery, threat/scenario catalogue, network fault campaigns, adversarial evidence | Reproducible scenario definitions, expected invariant/reason code, raw attack logs | Set a verdict/action directly; label a flag flip as a real attack |
| **M3 Pratik** | CoSys AirSim world, cameras, RGB/depth/pose capture, calibration, formation geometry, scene ground truth | Pinned world/settings bundle and synchronized replay dataset | Inject expected actions; use separately sampled “latest” sensor values |
| **M4 Samik** | CoSys AirSim vehicle/SITL control, command adapter, failsafes, reset automation, repeated runs | Command-adapter tests, campaign runner, telemetry/video/artifact bundle | Send consensus output directly to the vehicle; weaken simulator/autopilot failsafes |

Suyash is the merge and gate owner, not the author of everyone’s code. Pratik and Samik
develop against the same interface but on independent installations; a scenario that
works only on one PC is not accepted.

## M1 — Suyash: protocol, safety, and integration owner

### Immediate

1. Freeze and publish the sensor-source and command-sink Python interfaces, including
   units, coordinate frames, timestamp domains, error behavior, and maximum command age.
2. Create the canonical five-node mission manifest. Keep node keys, the roster, approved
   model/runtime measurements, mission ID, epoch, quorum parameters, `phi_min`, and camera
   configuration explicit. Do not accept machine-local absolute paths in committed files.
3. Give Pratik a snapshot conformance test and Samik a command-sink conformance test so
   they can work without waiting for the full swarm.
4. Give Abhijan the authoritative rejection/invariant catalogue. Expected outcomes are
   checked from emitted events and certificates, never injected into the live path.
5. Re-run the deterministic baseline before accepting any external-simulator result:

   ```bash
   cd codebase
   python -m pytest -q
   python -m sim.closed_loop
   ```

### Integration

1. Wire Pratik’s atomic snapshot source into `PerceptionWorker`/`MissionRunner` without
   adding a second mission loop.
2. Wire Samik’s command sink only to `SafetySupervisor` output. Add an integration test
   proving that direct consensus output cannot reach the sink.
3. Validate node-scoped manifests and authority separation. The provisioning path may
   install weights/configuration but must not write the approved-model allowlist.
4. Verify independent node results. Until a finality/certificate protocol exists, report
   disagreement or undecided nodes honestly; do not call the current independent tally
   formal Byzantine agreement.
5. Own the final `RUNBOOK.md`, evidence index, known-limitations list, demo narration, and
   go/no-go decision. Every numeric claim must point to a retained artifact.

### Acceptance

- No simulator-specific truth or attack flag enters protocol verification.
- Every motion command can be traced to one receipt, votes/certificate, supervisor
  decision, adapter acceptance, and simulator telemetry record.
- Every key/allowlist/config change is attributable and hashed in the run manifest.

## M2 — Abhijan: attacks and adversarial campaigns

Abhijan owns **delivery**, not the defenses. Each campaign has a baseline run and an
attacked run using the same world, route, detector, initial state, and seed.

### Attack catalogue

| Campaign | Delivery to implement | Required safe evidence |
|---|---|---|
| Rogue node | Start a process with an unrostered identity/key and send normal protocol messages | Rejected as unknown; no state or motion authority gained |
| Replay/duplicate | Capture a valid signed receipt, resend it within and outside the freshness window, and reuse its sequence with altered signed content | Exact replay and sequence misuse rejected; cache remains bounded |
| Model/runtime swap | Change the actual weights or measured runtime through the update/provisioning path | Authority mismatch is visible; motion remains held |
| Compromised provisioner | Provision multiple rostered nodes with unapproved artifacts while the authority file remains separately controlled | Each mismatch is rejected independently; allowlist is unchanged |
| Physical adversarial scene | Ask Pratik to place a 3-D patch/occluder in the world; do not composite pixels in the mission path | Actual detector runs for every viewpoint; depth and peer evidence remain visible |
| Collusion/equivocation | Compromised peers emit signed dishonest or conflicting votes | Equivocation counts on neither side; integer quorum is never bypassed by reputation |
| Network faults | Add measured delay, loss, duplication, reordering, partition, restart, and recovery at the process/network boundary | No motion after evidence/command expiry; recovery does not reuse stale state |
| Sensor/time faults | Drop/freeze RGB or depth, stale/spoof pose, jump clocks, send malformed/non-finite data | Snapshot rejected or HOLD; no “unknown means clear” behavior |
| Resource pressure | Sustain invalid identities/messages, cache churn, and reconnects at a declared rate | CPU, memory, queue, and thread bounds are recorded; legitimate safety traffic remains serviced or system holds |

### Required method

1. Define every scenario in a machine-readable manifest: ID, threat assumption, affected
   nodes, seed, start state, injection time, duration, expected invariant, expected reason
   code, stop condition, and cleanup/reset procedure.
2. Implement attacks at the real boundary named in the scenario. A JSON control file may
   trigger delivery, but the node under test must still receive a real malformed/replayed
   message, artifact update, network fault, or altered scene.
3. Generate quorum expectations from the manifest’s `N`, fault bound, and configured
   thresholds. Do not hardcode “two attackers are safe” or a fixed reputation-round count.
4. Validate event invariants, including `0 <= semantic_acks <= acks`; semantic abstentions
   are not successes. Preserve raw events even when the campaign fails.
5. Coordinate only the injection mechanism with Pratik/Samik. Abhijan owns the attack
   schedule and truth table; Pratik owns scene truth; Samik owns vehicle recovery.

### Acceptance

- Turning an attack off restores the same baseline without reinstalling the system.
- No campaign helper imports or calls the consensus/verdict implementation to force its
  expected result.
- All unexpected accepts, releases, collisions, crashes, and result disagreements fail
  the campaign and remain in the artifact bundle.

## Scenario ownership — two independent scenarios

**Pratik and Samik each own one complete scenario, end to end.** Not one scenario
split between them. Each designs the scene *and* the tasks the other must perform
to reproduce it on their own PC.

| | Owner | Second operator |
|---|---|---|
| **Scenario 1** | Pratik designs it and defines what Samik must do to reproduce it | Samik |
| **Scenario 2** | Samik designs it and defines what Pratik must do to reproduce it | Pratik |

**Why two.** A scenario that only runs on the PC it was built on is not a result,
it is an anecdote. Cross-reproduction is the check — and because each person had
to write instructions precise enough for someone else to follow, the pinning,
versioning, and export gaps surface during Week 2 rather than on stage. It also
means a scene that breaks on the day is not the whole demo.

Each scenario ships with: a pinned environment build, a `settings.json`, an asset
and version manifest, a seeded initial state, an expected-outcome sheet, and a
reproduction procedure written for the *other* operator.

### Start from a prebuilt environment — do not model a world

CoSys AirSim / Colosseum ship prebuilt environments (Blocks, Neighborhood, City,
Landscape, Mountains, Forest, Africa). **Use one.** Building terrain in Unreal is
days of work that earns zero marks, and a hand-built scene looks worse than the
shipped ones.

**But the obstacle must be a COCO class.** YOLOv8n detects 80 COCO categories —
person, car, bus, truck, boat, bench, traffic light. It does **not** detect
"mountain", "rock", "cliff", "building", or "tree". Fly at a beautiful mountain
and every drone honestly reports zero detections, every action is `(0,0,0)`, and
the entire perception layer sits idle. The Gazebo run already hit this: a plain
grey cube was invisible until a bus texture was applied to it.

So:

* **Prefer Neighborhood or City** — they already contain parked **cars and
  trucks**, which YOLOv8n detects reliably and which are genuinely plausible
  obstacles for a low-flying UAV.
* If you want mountains or open terrain for the look, **place a vehicle-class
  object** as the actual obstacle within it.
* **Verify detection before building anything else.** Fly to the rehearsal
  distance and altitude, capture one frame per vehicle, run YOLO, confirm the
  obstacle is detected with confidence ≥ 0.25 from every drone's viewpoint. If it
  is not, the scenario is dead and no amount of protocol work will save it.
* The obstacle must fill **≥ 0.7 of frame height** — required both for a strong
  avoidance action and to stay clear of the semantic blind band (see
  `urgent_new_changes.md`).

What remains genuinely yours: `settings.json` for five vehicles, camera
placement and calibration, the patch object, seeded reset, and version pinning.
That is configuration and verification work, not world-building.

## M3 — Pratik: CoSys world, perception, and calibration

### Environment and world

1. Install CoSys AirSim on Pratik’s PC and pin every relevant version. Export the exact
   settings and a one-command smoke test that connects, enumerates all vehicles, captures
   one RGB/depth pair per vehicle, reads pose/health, and resets cleanly.
2. Select a prebuilt environment per the section above and confirm the obstacle is a
   detectable COCO class at the rehearsal geometry before anything else is built.
   Record collision detection, known dimensions, and independent ground-truth logging.
3. Place adversarial artifacts as physical 3-D scene objects/textures so viewpoint,
   lighting, occlusion, scale, antialiasing, and depth are produced by the renderer. Keep
   any 2-D pixel-patch harness only as a separate unit/evaluation test.
4. Export the world, settings, asset hashes, camera settings, weather/lighting settings,
   and seed. Do not rely on editor-only unsaved state.

### Sensor and formation adapter

1. Capture RGB and metric depth in one simulator request where supported, then attach the
   corresponding pose, health, simulator timestamp, and uncertainty. Measure the maximum
   skew; do not describe separate “latest” reads as synchronized.
2. Record actual image size, focal parameters/FOV, distortion assumption, camera-body
   translation, yaw, pitch, and roll. State units and coordinate-frame conversions.
3. Preflight every formation with `common.assert_covisible_formation` using measured
   calibration and pose uncertainty. The 5.5 m ring is only a nadir-camera reference, not
   a default. Replan until overlap and angular diversity both pass.
4. Produce a replay dataset containing atomic RGB/depth/pose records and ground truth.
   Split scene/seed sets for tuning and final testing. Include hard cases: small/thin
   obstacles, glare, blur, low light, fog/rain, partial lens obstruction, non-planar scenes,
   and dropped/frozen frames.
5. Run the actual detector and controller on every vehicle view. Never substitute a
   hand-authored expected action.

### Acceptance

- The same exported world opens on Samik’s PC and produces the same vehicle/camera names,
  coordinate interpretation, and deterministic seeded initial state.
- Every frame used for a receipt is recoverable by hash and tied to depth/pose/ground truth.
- Patch transfer or non-transfer is reported per viewpoint; failures are not discarded.

## M4 — Samik: CoSys control, failsafes, and campaign automation

### Environment and control adapter

1. Install the exact pinned CoSys AirSim stack on Samik’s PC independently. Import
   Pratik’s exported world; do not copy an already-built machine image and call that
   independent reproduction.
2. Configure the five simulated vehicles and, if PX4 SITL is used, pin the firmware,
   parameters, vehicle IDs, ports, estimator, and offboard/failsafe settings. Record all
   of them in the run manifest.
3. Implement the command sink. It converts the declared VeriSwarm action frame into the
   simulator/autopilot frame, clamps limits, enforces expiry/deadman behavior, rejects
   non-finite or discontinuous commands, and acknowledges what was actually accepted.
4. Permit commands only from `SafetySupervisor`. HOLD is the default for missing, stale,
   rejected, deferred, no-quorum, clearance-unknown, unhealthy, or disconnected input.
5. Configure and prove independent geofence, collision prevention, offboard-loss,
   link-loss, and process-watchdog behavior. If the selected backend cannot provide an
   independent collision barrier, add a separate simulator safety monitor before C2.
   Define automatic HOLD/LAND/reset; do not depend on a human clicking fast enough.

### Repeatable execution

1. Build one command that validates versions/config hashes, starts the simulator and node
   processes, waits for health, runs one named scenario, stops safely, and gathers evidence.
2. Build a deterministic reset check: vehicle poses, world objects, patch state, clocks,
   queues, network faults, node epochs/sequences, and logs all return to their declared
   initial state. Keys remain persistent unless a rotation scenario explicitly changes them.
3. Exercise process death, command expiry, link loss, offboard loss, simulator pause,
   slow inference, and recovery. Confirm that the vehicle holds before recovery and never
   replays the last motion command after restart.
4. Capture simulator telemetry, command requests/acceptances, ground truth, protocol events,
   resource usage, and synchronized video. Hash the final bundle.

### Acceptance

- The adapter’s frame/sign/unit tests pass and a deliberately wrong frame or stale command
  is rejected before reaching the simulator.
- Killing VeriSwarm, pausing it beyond TTL, or partitioning it from the command adapter
  causes HOLD without an operator action.
- Campaign results reproduce on both Pratik’s and Samik’s PCs from the committed bundle.

## Half B — the physical co-visibility rig (new deliverable)

Everything above is simulated. This is the co-visibility algorithm running on
**two real webcams**, in front of the judge, on hardware they can touch — and it
is the only part of the demo a judge can interfere with directly.

Two identical USB webcams, ~30 cm apart, both looking at the same object, wired
into the Jetson. `tools/covis_live.py` runs the real `protocol/covis_features.py`
(ORB + RANSAC, already unit-tested) on the live pair and displays: both feeds,
the matches drawn between them, the inlier count against `m_min = 15`, and the
co-visible verdict. With YOLO on both feeds it also shows each camera's action
and the L2 between them against θ.

Three judge-operable moments:

1. **Slide one camera away** — inliers fall, verdict flips to *not co-visible*,
   the semantic layer abstains. Shows why the false-positive rate is low.
2. **Hold the printed patch in front of one camera** — its detections vanish, its
   action diverges, L2 crosses θ, **DISPUTE**. A physical attack caught by a
   second viewpoint.
3. **Cover that camera completely** — detections vanish identically, but so does
   the scene. Absence of evidence is not evidence of absence, which is exactly
   the distinction the protected controller makes and the baseline does not.

Rehearse (3): a sharp judge will ask it, and having the answer already on screen
beats explaining it.

**Ownership.** *Suyash* writes `tools/covis_live.py` — it calls protocol code
directly and must not fork the algorithm. *Abhijan* owns the physical rig, the
printed patch (print it, test the actual print at the rehearsed distance —
paper, scale and lighting all matter), and the attack choreography, since he owns
attack delivery.

**Why it stands alone.** It shares no machine, no network path, and no software
with the simulator. If AirSim will not start, this still tells a complete story
about the core algorithm, on real photons.

Full wiring, IPs, and start order: `DEMO_TOPOLOGY.md`.

## Schedule and handoffs

### Days 1–2 — freeze interfaces and prove both installations

- **Suyash:** publish conformance interfaces, canonical manifest, reason-code catalogue,
  and baseline test command.
- **Pratik:** capture one atomic RGB/depth/pose/health sample per vehicle and export the
  minimal world/settings bundle.
- **Samik:** load that bundle independently, command one simulated vehicle through a stub
  supervisor output, prove TTL→HOLD, and automate reset.
- **Abhijan:** deliver machine-readable scenarios for rogue, replay, model swap, network
  partition, sensor freeze, and physical scene attack; implement the first two.

**Gate C0:** the same pinned five-vehicle world cold-starts and resets on both simulator
PCs. Configuration hashes and smoke-test logs match where they should.

### Week 1 — sensor and command seams

- Pratik completes calibration, atomic snapshot capture, formation preflight, ground truth,
  and the initial replay dataset.
- Samik completes the hardened command adapter, independent failsafes, watchdog, and
  evidence collector.
- Abhijan completes non-scene attack delivery and expected-invariant checks.
- Suyash integrates both adapters through `MissionRunner` and rejects any bypass path.

**Gate C1:** actual perception input produces a signed receipt and peer decision. Every
non-release outcome yields HOLD, and every record is traceable across the two clock domains.

### Week 2 — adversarial closed loop

- Pratik adds the 3-D patch/occluder and environmental/perception variations.
- Samik runs protected missions and failure recovery repeatedly on both PCs.
- Abhijan runs the complete attack matrix, including network/resource faults and collusion.
- Suyash audits discrepancies, manifest separation, certificate consistency, and artifacts.

**Gate C2:** zero collisions in protected campaigns; no command after expiry; no attack
obtains authorization below the configured integer/semantic requirements. Any violation
blocks the gate.

### Week 3 — distributed and reproducibility campaign

- Run separate node processes and node-scoped manifests.
- Run the same frozen scenario/seed list on Pratik’s and Samik’s PCs.
- Include delay, loss, duplication, reordering, partitions, restarts, clock faults, stale
  sensors, model/runtime swaps, adversarial scenes, and resource pressure.
- Compare certificates/events and investigate every divergence rather than averaging it away.

**Gate C3:** all honest nodes either agree on the finalized evidence available to the
current protocol or safely remain undecided; all undecided/divergent cases HOLD and are
reported as protocol limitations.

### Week 4 — freeze, statistics, and rehearsal

- Freeze code, model, world, configuration, attack manifests, and seeds before final runs.
- Run at least 30 randomized repetitions per safety-critical scenario during development.
  Set the final sample size from the claim: for example, **299 independent zero-failure
  runs are needed before a one-sided 95% binomial upper bound falls below 1% per run**.
  This is evidence, not proof of zero risk.
- Generate the evidence index, latency distributions, resource maxima, failure table,
  screenshots/video, cold-start runbook, and known limitations.
- Rehearse the safe demo roles: **Samik operates vehicles**, **Pratik monitors sensor and
  simulator truth**, **Abhijan triggers/explains attacks**, and **Suyash explains the
  protocol/safety result and owns abort/go-no-go**.

**Gate C4:** a teammate can reproduce the named demonstration from a clean start using
only committed instructions and archived dependencies/assets. No new feature enters after
this gate.

## Evidence required for every accepted run

Each run bundle must contain:

- run/scenario ID, UTC time, operator, PC identity, seed, and clean/dirty repository state;
- code commit plus dependency, CoSys AirSim, Unreal, model, firmware, world, settings, and
  manifest hashes;
- node roster, public-key fingerprints, authority hash, mission/epoch, quorum parameters,
  thresholds, and camera calibration;
- raw hash-chained events, receipts/votes/certificates, sensor metadata, released/rejected
  commands, adapter acknowledgements, simulator telemetry, ground truth, resource metrics,
  and synchronized video;
- exact reset/start/stop result, invariant/test result, unexpected behavior, collisions,
  process crashes, dropped records, and operator interventions;
- an artifact index and checksums. Never overwrite a failed run with a later success.

Report distributions and worst observed values, not only averages. A dashboard screenshot
is presentation material, not verification evidence.

## Demonstration run-of-show

1. **Baseline:** same frozen scene and seed with no attack; actual perception and depth
   authorize a bounded command through the supervisor.
2. **Physical scene attack:** Pratik raises the 3-D patch/occluder. Abhijan triggers only
   its movement; he does not alter pixels or verdicts. Show victim RGB/depth, peer views,
   co-visibility/parallax, votes, outcome, and the supervisor’s released HOLD.
3. **Cyber attack:** Abhijan chooses replay, rogue node, model/runtime swap, or partition.
   Show the real delivered artifact/message/fault and the corresponding reason code.
4. **Shadow comparison:** visualize what the unprotected controller requested as a
   non-actuating trajectory overlay. Do not route it to the vehicle.
5. **Reveal and limits:** show the retained run bundle, explain that OP-TEE protects the
   signing key, and state the open finality/pose/hardware limitations plainly.

## Stop conditions

Stop the current campaign immediately, preserve artifacts, and reopen the relevant gate if:

- any protected vehicle contacts an obstacle or violates the geofence;
- motion is released on stale, unknown, rejected, deferred, no-quorum, unhealthy, or
  semantically unverified evidence;
- simulator truth enters perception/consensus, or an attack helper sets an expected result;
- sensor/pose timestamps cannot be paired within the declared skew bound;
- configuration, authority, model, runtime, world, or firmware cannot be identified by hash;
- results differ across the two simulator PCs without an explained and fixed cause;
- logs are incomplete, reordered without detection, or cannot be tied to the run manifest.

## Deferred until simulation gates pass

Hardware-in-the-loop, propeller-on tests, field flight, secure boot/rollback enforcement,
real RF/GNSS fault injection, and performance claims are outside the current execution
phase. Follow `codebase/SIMULATION_AND_FLIGHT_GATES.md`; no physical flight begins before
S0–S3-equivalent simulation, H0, independent safety review, legal/regulatory approval,
containment, safety pilot, and physical kill/abort procedures are complete.
