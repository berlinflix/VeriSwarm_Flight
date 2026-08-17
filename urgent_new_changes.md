# ⚠️ URGENT — READ THIS FIRST

**This file overrides [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md).** Anything written
here is newer than the plan and **wins wherever the two disagree.** The plan is not
rewritten every time something changes — changes land here first, and get folded into the
plan later.

### Read order for anyone picking up this project
1. **This file** — top to bottom. Newest entries are at the top.
2. [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) — architecture, attacks, member plans.
3. [`HARDWARE_LIST.md`](HARDWARE_LIST.md) — what to buy and the gotchas.

---

## How to use this file

**Suyash** writes entries here whenever a decision changes the algorithm, the protocol, the
infrastructure, or anyone's task. Newest at the top, directly under this section.

**Everyone else** reads the top of this file before starting work each day. If an entry says
your section of the plan is stale, the entry is what you build — not the plan.

**Every collaborator or coding assistant** reads this file first, before the build plan, at
the start of every session. When an entry is fully folded into
`SIH_2026_Build_Plan.md`, mark it `FOLDED` rather than deleting it — the history of why
something changed is worth keeping.

### Entry template — copy this

```markdown
### YYYY-MM-DD — Short title
**Status:** OPEN
**Changed:** what is now different, concretely.
**Makes stale:** which sections/files of the plan are now wrong. Name them.
**Who must act:** M1 Suyash / M2 Abhijan / M3 Pratik / M4 — and what each does differently.
**Why:** the reasoning, so nobody re-litigates it in three weeks.
```

Set `Status: FOLDED` once the build plan has been updated to match.

---

## Open overrides

### 2026-08-18 — Jetson runs webcams first, then Alpha with OP-TEE; no shared swarm key
**Status:** FOLDED into [`DEMO_TOPOLOGY.md`](DEMO_TOPOLOGY.md) and
[`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.

**Changed:** the Jetson workload is explicitly sequential. First it runs only the physical
two-webcam `covis_live` demonstration. After that process exits, releases both cameras and
closes its evidence, the team performs a fresh OP-TEE sign/verify challenge and starts the
Jetson as simulated node `alpha`. Every Alpha-originated receipt and Alpha peer vote then
uses the existing local `OPTEEReceiptSigner`; OP-TEE is not skipped from the Cosys run.
Alpha's `node.mission`/`Originator` process must also run on the Jetson, even if Samik
operates it over SSH, because the current code constructs the signer inside that process.
The older command placement on L1 would search for `/dev/tee0` on L1 and fail.
The older `python -m node.mission ...` command is also removed because `node.mission` is a
library without a CLI. `tools/optee_preflight.py` is now the executable handover check;
Samik's planned `tools/run_campaign.py` must become the accepted Cosys entry point and run
Alpha's `MissionRunner`/`Originator` on the Jetson.

The proposal to send all five nodes' unsigned receipts to one generic Jetson signer is not
adopted. The current Trusted Application has one persistent Ed25519 key. Sharing it across
five claimed identities would collapse per-node identity isolation, centralize the trust
boundary and introduce a common signing failure. `bravo`, `charlie`, `delta` and `echo`
therefore keep distinct development keys. If an all-TEE remote design is built later, it
requires separate protected key slots, authenticated callers, TA-enforced identity binding,
anti-replay state, bounded queues and fail-closed timeout behavior.

**Makes stale:** any topology that runs `covis_live` and the Alpha node concurrently, any
accepted run that omits OP-TEE merely because the webcams used the Jetson earlier, any
generic unauthenticated network endpoint that signs arbitrary bytes, and any claim that an
OP-TEE signature proves inference or pose ran inside trusted code.

**Who must act:** Suyash owns the handover/runbook, pinned Alpha public key and retained
challenge evidence. Abhijan ends the physical-camera choreography and confirms the capture
artifact is closed. Samik must not start the accepted mission until Alpha passes preflight;
he implements HOLD/reassignment on signer loss or deadline expiry. Pratik may preload the
Cosys world during the webcam stage but does not start accepted mission motion before the
handover gate passes.

**Definition of done:** a cold rehearsal shows the webcam stage, clean process/device
handover, successful OP-TEE challenge, at least one canonical Alpha receipt that verifies
under the same pinned public key, no Alpha software fallback, and safe HOLD/reassignment
when the signer is deliberately stopped.

**Why:** this preserves the tangible webcam demonstration and the hardware-protected key
operation without pretending that one central key represents five independent drones.

### 2026-08-18 — One integrated scenario; Pratik owns it; Samik owns autonomy and models
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.

**Changed:** there is now exactly one Cosys scenario: **Contested Border Sentinel**. Pratik
owns the prebuilt environment plus scenario layer. It combines border surveillance,
unarmed battlefield ISR, GPS-denied navigation, swarm C2 and one SAR/casualty diversion in
the same repeatable run. Samik does not build a second world; he reproduces Pratik’s bundle
and owns everything that makes the drones autonomous inside it.

Surveillance/battlefield handling is concrete: five drones cover leased sectors; noisy
ground sensors create inspection tasks; RGB/depth detects supported people/vehicles;
detections become world-coordinate tracks; a second viewpoint confirms them; explicit
rules identify border crossing, restricted-zone entry, loitering and group/convoy movement;
GNSS jam/spoof forces VIO; obstacles force occupancy/A* replan; link/node failure returns
unfinished work to the allocator; VeriSwarm blocks attacked/unverifiable commands. Output
is an operator alert with track, location, time, confidence, evidence and status—never a
weapon-engagement command.

**Model custody:** do not download every row in the old table. Keep the existing
`codebase/yolov8n.pt` baseline and `codebase/yolov8n_tampered.pt` attack artifact. Samik
downloads only official `yolov8s.pt` and `yolov8m.pt` candidates into ignored
`codebase/models/candidates/` using the planned registry-driven
`codebase/tools/fetch_models.py`. Pratik supplies frozen tune/held-out scenario frames.
Samik benchmarks them. Suyash independently verifies hashes, approves exactly one model,
then Samik copies those exact approved bytes to
`codebase/models/approved/rgb_detector.pt` and provisions them. Suyash mints the signed
authority allowlist separately from Samik’s provisioning role. Abhijan may use only
recorded/reproducible tampered variants in named attack runs. YOLO11, thermal and
specialized battlefield weights are not downloaded now.

**Makes stale:** the two-independent-scenarios override below, the separate dark/light
environment tracks, any Scenario 2 task for Samik, reciprocal environment authorship, and
any instruction to download all model rows or auto-download weights during a mission.

**Who must act:**

- **M1 Suyash** — own schemas/protocol/supervisor, `models/registry.json`, independent hash
  verification, model approval, signed authority, blind-band fix, integration gates,
  evidence and runbook.
- **M2 Abhijan** — own attack scenarios/delivery/oracle and the printed/webcam attack rig;
  apply the same attacks to the one frozen mission without writing expected verdicts into
  the live path.
- **M3 Pratik** — own the single prebuilt Cosys scenario, sensor/calibration manifests,
  GNSS/C2 fault regions, targets/obstacles/ground sensors, isolated truth and frozen
  model-selection/replay data. For the immediate flight handoff, provide exact vehicle
  names/types, API host/port, initial poses, clear NED A/B points, altitude/geofence and an
  attack-free scenario copy.
- **M4 Samik** — own official candidate acquisition, model benchmarking, scenario
  reproduction, VIO, mission manager, occupancy map, coverage/A*, task allocation,
  tracking/fusion, local safety, Cosys/PX4 command sink, recovery and campaign runner. His
  first executable deliverable is `codebase/sim/cosys_smoke_flight.py`: connect, validate
  vehicle, enable API control, arm, take off, hover, fly A→B, hover, land, disarm and retain
  telemetry—with timeouts and safe cleanup at every transition.

**Definition of done:** the one frozen mission cold-starts on Pratik’s and Samik’s PCs;
the selected model and every runtime/configuration match the signed registry/authority;
the swarm covers the declared area, produces confirmed operator alerts, navigates the
GNSS-denied segment without truth input, replans around classified and unclassified
geometry, reassigns failed work and never releases an unsafe/expired/unverified command.

**Why:** one deep, reproducible mission is stronger and more achievable than two shallow
worlds. The ownership split is now unambiguous: Pratik creates the operational world;
Samik creates the autonomous system that succeeds in it.

### 2026-08-18 — Two independent scenarios; use a PREBUILT environment; new webcam rig
**Status:** FOLDED, then SUPERSEDED by the one-scenario decision above on 2026-08-18.

**Historical only:** do not create Scenario 2. The physical webcam rig and prebuilt-
environment rule remain active; the two-scenario split does not.
**Changed:** three things, all affecting Pratik and Samik directly.

**1. Two scenarios, not one split in half.** Pratik designs Scenario 1 *and*
writes the procedure Samik follows to reproduce it. Samik designs Scenario 2 *and*
writes the procedure Pratik follows. Each ships a pinned environment build,
`settings.json`, asset/version manifest, seeded initial state, expected-outcome
sheet, and a reproduction procedure written for the other operator. A scenario
that runs only on the PC it was built on is an anecdote, not a result — and
writing instructions precise enough for someone else forces the version-pinning
gaps out in Week 2 instead of on stage.

**2. Do NOT model a world. Start from a prebuilt environment.** Cosys-AirSim /
Colosseum ship ready-made environments (Blocks, Neighborhood, City, Landscape,
Mountains, Forest, Africa). Building terrain in Unreal is days of work worth zero
marks, and the shipped ones look better.

**The catch that will otherwise waste a week:** the **adversarial semantic target**
must be a validated detector class. YOLOv8n detects 80 COCO categories — including
person, car, bus, truck, boat and bench — but not mountain, rock, cliff, building
or tree. Those unclassified objects must still cause mapping/replanning through
depth/LiDAR; they are excellent navigation tests. They are only unsuitable as the
target used to prove semantic YOLO cross-verification. The Gazebo run hit this
distinction: a grey cube was invisible to YOLO until a bus texture was applied,
even though a correct geometric mapper should still treat the cube as occupied.

* Prefer **Neighborhood** or **City** — they already contain parked **cars and
  trucks**, which YOLOv8n detects reliably and which are plausible UAV obstacles.
* Want mountains for the look? Fine — **place a vehicle-class object** as the
  actual obstacle inside it.
* **Verify detection first, before building anything else.** Fly to the rehearsal
  distance/altitude, capture one frame per vehicle, run YOLO, confirm conf ≥ 0.25
  from every viewpoint. If it fails there, the scenario is dead and no protocol
  work will rescue it.
* The current semantic-attack target must fill **≥ 0.7 of frame height** until the
  blind-band fix below lands — needed for a strong semantic divergence. This is
  not a rule for geometric navigation obstacles.

**3. New deliverable — the physical two-webcam co-visibility rig.** Two identical
USB webcams on the Jetson, both looking at one object from different angles,
running the real `protocol/covis_features.py` on live frames. It is the only part
of the demo a judge can physically interfere with, and it shares no machine, no
network path, and no software with the simulator — so it stands on its own if
AirSim will not start.

**Makes stale:** any M3/M4 text implying one shared scenario, or implying a world
must be modelled from scratch. The old "build a bounded five-vehicle world" task.
**Who must act:**
* **M3 Pratik** — own Scenario 1 end to end. Pick a prebuilt environment. Run the
  detection check before anything else.
* **M4 Samik** — own Scenario 2 end to end, same rules, independently. Do not
  reuse Pratik's environment choice; two different environments is the point.
* **M2 Abhijan** — you own the physical rig, the printed patch, and the attack
  choreography for the webcam demo. Test the *actual print* at the rehearsed
  distance; paper, scale and lighting all change whether it works.
* **M1 Suyash** — writes `codebase/tools/covis_live.py` (calls protocol code, must not
  fork the algorithm). Full wiring, static IPs, and start order now in
  [`DEMO_TOPOLOGY.md`](DEMO_TOPOLOGY.md).
**Why:** the simulator was the single point of failure for the entire demo. Two
scenarios on two PCs plus a physical rig that needs neither means three
independent things must fail before there is nothing to show.

### 2026-08-18 — The crash demo is protected; do not remove it again
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.
**Changed:** the unprotected controller is now an explicitly named module,
`perception/baseline_controller.py`, with `naive_action()`. It commands **full
forward on an empty detection set** — the unsafe behaviour is deliberate and it is
the demo.

Background: the safe controller `detections_to_action` used to do this, was
correctly flagged as a P0 hazard (empty detections also mean a dead camera, glare,
blur, or crashed inference), and was changed to hold. That fix is right and stays.
But it silently deleted **Act 1**, because the judge-facing contrast depends on
seeing an unprotected drone act on a lie:

> **Run A** patch raised → detector sees nothing → full forward → the drone flies
> into the obstacle. *"This is what happens today."*
> **Run B** same patch → co-observing peers still see the obstacle → DISPUTE →
> REJECTED → the drone holds and is isolated, mission continues.

Delete Run A and both runs stop, and a judge learns nothing about what the
protocol bought. A defence is only legible next to the failure it prevents. It is
also the paper's Table 4.14 baseline.

New file [`docs/DEMO_INVARIANTS.md`](codebase/docs/DEMO_INVARIANTS.md) lists every
deliberate behaviour that looks like a bug, why it must stay, and which path to
harden instead. `tests/test_baseline_controller.py` pins it.

**Makes stale:** any plan text implying the crash comes from `detections_to_action`.
**Who must act:**
* **M2 Abhijan** — Run A uses `naive_action()`. Never wire it to `SafetySupervisor`
  or `MissionRunner`; it is the control arm, not a flight controller.
* **M4 Samik / whoever narrates** — Act 1 is intact, but say *"this is a conventional
  controller, not a broken one"* — the two policies agree on every input where the
  detector sees anything. Run A crashes because it was **lied to**, not because it
  is bad.
* **Everyone, and any AI tool** — read `docs/DEMO_INVARIANTS.md` before
  "hardening" perception. This behaviour has been removed once already by a tool
  acting in good faith.
**Why:** the safe path and the baseline answer different questions. Conflating
them costs either safety or the demo. Separating them costs neither.

---

### 2026-08-18 — Known defect: semantic blind band at 0.5–0.6 frame occupancy
**Status:** OPEN — **not yet fixed**
**Changed:** nothing yet. Recording a measured defect so nobody rediscovers it on
stage.

With the protected fallback, a patched drone emits `(0,0,0)`. So does an honest
peer facing a *moderate* obstacle, because the avoidance action passes through the
origin as threat rises. Measured at θ = 0.5:

| obstacle (frame fraction) | peer action | L2 | patch caught? |
|---|---|---|---|
| 0.4 × 0.4 | (0.568, 0, 0.144) | 0.586 | ✅ |
| **0.5 × 0.5** | (0.325, 0, 0.225) | **0.395** | ❌ |
| **0.6 × 0.6** | (0.028, 0, 0.324) | **0.325** | ❌ |
| 0.7 × 0.7 | (−0.323, 0, 0.441) | 0.547 | ✅ |

Root cause: `(0,0,0)` means both *"I see nothing"* and *"I see a moderate threat
and chose to slow."* The semantic layer compares **control outputs**, and control
outputs are a non-injective projection of what was perceived — two different
world-states map to the same command.

**Who must act:**
* **M3 Pratik** — until this is fixed, build the scene with a **frame-dominating**
  supported-class semantic target (≥ 0.7 of frame height). Anything in the
  0.5–0.6 band demos as a silent miss. Rocks/trees/walls outside this semantic
  test still enter the occupancy map through depth/LiDAR and must trigger replan.
* **M1 Suyash** — the fix is to attest the perception claim (detection count /
  extent / confidence) rather than the control output. Cheapest interim: carry a
  `detections_present` flag in the attested output so absence and hold stop
  colliding.
**Why:** worth stating plainly because it is also the strongest argument in the
paper for attesting perception rather than control — and it is a measured result,
not a hypothesis. `tests/test_baseline_controller.py` pins the band so the fix
announces itself by turning those tests red.

## Recent folded overrides

### 2026-08-18 — Full autonomy stack and contested-border ISR mission
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.

**Changed:** the SIH build is no longer a reactive YOLO avoidance demo. The primary mission
is an unarmed, operationally realistic contested-border ISR swarm that performs coverage,
ground-sensor cueing, person/vehicle detection and tracking, GPS-denied VIO navigation,
occupancy mapping, A* replanning, collision-safe command selection, task leases and failure
reassignment. Search and rescue is a second mission configuration of the same engine.

The final command is not `waypoint_vector + YOLO_vector`. A deterministic local safety
filter rejects candidate velocities that violate depth/braking, occupied or unknown space,
geofence/no-fly areas, peer separation, vehicle limits or estimator uncertainty. If its
safe set is empty, the result is HOLD. `SafetySupervisor` still has final authority over
the exact command sent to the vehicle.

An object does not need a detector class to affect navigation. Rocks, trees, walls and
buildings enter the occupancy map through depth/LiDAR and force a lateral route, a climb
only when overhead/geofence/altitude constraints pass, or HOLD/`NO_PATH`. Only the
judge-facing semantic adversarial target must be a validated detector class.

Simulation is treated like deployment: terrain/weather, moving and occluded targets, noisy
ground sensors, GNSS jam/spoof, VIO drift/reset, link faults, battery/vehicle failure,
dynamic/no-path obstacles and adversarial cyber/perception cases are exercised without
Cosys truth leaking into autonomy. The world may represent a battlefield/contested border,
but the system performs surveillance, tracking and rescue only; detection never authorizes
weapon engagement.

**Model decision:** no single AI weight provides autonomy. Keep `codebase/yolov8n.pt` as
the regression baseline; benchmark official `yolov8s.pt` and `yolov8m.pt` on frozen tune
and held-out Cosys scenes, then select the smallest model that passes per-class accuracy
and target-hardware p99 latency. VIO, A*, mapping, task allocation and deterministic
tracking need algorithms/configuration rather than neural weights. Thermal and specialized
battlefield classes require sensor/domain-specific trained weights and cannot be claimed
from generic COCO weights. The existing tampered weight remains attack-only.

**Makes stale:** “YOLO action is the whole command,” reactive avoidance as complete
autonomy, simple waypoint-plus-avoidance vector addition, decorative formation as the
mission, “no training anywhere” as a permanent project claim, and any use of simulator
pose/segmentation/object IDs as GPS-denied navigation or perception.

**Who must act:**

- **M1 Suyash** — freeze mission/estimator/map/task/track/decision/command schemas; keep
  `MissionRunner`/`SafetySupervisor` as the only command path; own authority hashes, model
  selection evidence and gate acceptance.
- **M2 Abhijan** — extend attacks to GNSS loss/spoof, VIO drift/reset/dropout, stale or
  conflicting maps, planner no-path/timeout, task-lease replay/conflict, target/perception
  faults and C2/resource failures. His oracle checks mission and safety invariants from raw
  evidence; it never sets the expected verdict in the live path.
- **M3 Pratik** — build/export the contested Cosys world, all sensors/calibration,
  GPS-denied/spoof regions, weather, ground cues, targets, obstacles and isolated evaluation
  truth. Produce tune/held-out perception datasets without exposing labels/true pose to
  autonomy.
- **M4 Samik** — deliver `AutonomyRunner`: VIO integration, mission manager, occupancy map,
  coverage, A*, task leases/reassignment, track fusion, local safety filter, supervisor-only
  Cosys/PX4 command sink, recovery and repeatable campaign runner.

**Definition of done:** five drones complete the declared traversable mission area, replan
around new obstacles, navigate the declared GNSS-denied segment without truth input,
detect/track/confirm supported classes, preserve separation/geofence/braking constraints,
reassign unfinished work after a node failure, and hold on any unsafe/unknown/expired
decision. The full scenario reproduces on both simulator PCs with immutable evidence.

**Why:** boxes are perception output, not autonomy. The previous plan could react to an
obstacle but had no state estimator, map, mission completion, global route, target track or
task recovery. The new stack answers the actual SIH autonomy problem while keeping
VeriSwarm at the security/supervision boundary it is meant to protect.

### 2026-08-18 — Role reset: Cosys-AirSim on Samik and Pratik's PCs
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.

**Changed:** Cosys-AirSim is now the selected external simulator. **Pratik and Samik both
run the same pinned project independently on their own PCs.** Pratik owns the world,
camera/depth/pose truth, calibration, formation, and replay data. Samik owns the autonomy
engine, VIO integration, simulated vehicle/SITL control, supervisor-only command adapter,
failsafes, deterministic reset, campaign automation, and evidence collection. **Abhijan
now owns attack delivery and all
adversarial campaigns**, not the simulator client or the primary console. Suyash owns the
protocol/safety boundary, manifests/authority, adapter contracts, review, and final gates.

**Makes stale:** the old M2 console/simulator-client assignment, the solo-M3 AirSim role,
the unassigned M4/presentation role, the Mac-to-Pratik RPC plan, the AirSim-vs-Gazebo
timebox, and all instructions that make Suyash implement the attack surface on Abhijan's
behalf. It also replaces any unsafe “crash-vs-caught” acceptance gate with protected HOLD
and a non-actuating shadow-path comparison.

**Who must act:**

- **M1 Suyash** — freeze the sensor/command interfaces, canonical five-node manifest,
  authority separation, reason-code catalogue, and conformance tests; integrate only
  through `MissionRunner` and `SafetySupervisor`; own gate acceptance and the runbook.
- **M2 Abhijan** — implement real rogue, replay/duplicate, model/runtime/provisioning,
  collusion/equivocation, network, sensor/time, resource-pressure, and 3-D scene attack
  campaigns. Every campaign needs a machine-readable manifest, baseline, expected
  invariant, real delivery boundary, cleanup, and retained raw evidence. Never set a
  verdict/action directly.
- **M3 Pratik** — export a pinned Cosys world/settings bundle; supply atomic timestamped
  RGB + metric depth + calibrated pose/health; build/preflight the formation and physical
  3-D patch/occluder; produce ground truth and replay datasets without leaking truth into
  perception.
- **M4 Samik** — reproduce the pinned Cosys setup independently; deliver `AutonomyRunner`
  with VIO, mission/coverage planning, occupancy mapping, A* replanning, task leases,
  tracking/fusion, local collision-safe selection and the supervisor-only command sink;
  prove TTL/HOLD/LAND/watchdog/failsafe behavior and gather hashed run artifacts.

**Definition of done:** the same frozen scenario/seed list runs from a clean start on both
simulator PCs; protected campaigns have zero collisions and no motion after expiry or an
unsafe/unknown outcome; every result is traceable from sensor snapshot to receipt, votes,
supervisor decision, adapter acknowledgement, and simulator telemetry. Differences between
the two PCs are failures until explained and fixed.

**Why:** the previous allocation had one simulator owner, an unfilled fourth role, and
split the attack surface between people. The new split gives the high-risk sensor and
control boundaries separate owners, makes reproduction on a second machine mandatory, and
puts all attack truth/delivery under one accountable owner.

### 2026-08-18 — Safety audit hardening; simulation is the current target
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.
**Changed:** the executable safety contract is now fail-closed. Detector silence is HOLD
unless independent free-space evidence is positive; an ACCEPTED receipt without semantic
quorum is DEFER/HOLD; reputation cannot create a reject below the integer quorum; exact
within-window replay and duplicate sequence numbers are rejected; signed equivocation is
counted on neither side. Receipts are protocol-v2 and bind mission, epoch, sequence,
runtime measurement, action frame/validity, and pose metadata. From `codebase/`, run
`python -m sim.closed_loop` before any external simulator.

`codebase/node/mission.py` is the only supported perception → consensus → command seam. It
sends all output through `SafetySupervisor`. Static manifest observations, dummy frames,
and direct consensus-to-PX4 commands are demonstration scaffolding and must not be
described as a closed-loop mission.

**Makes stale:** the unsafe collision run-of-show, `EXECUTE_DEGRADED`, 4.68 ms live OP-TEE
latency, `semantic_acks` counts that included DISPUTEs, and any claim that the existing
independent tally is formal Byzantine agreement.

**Who must act:** M1 Suyash wires `MissionRunner` to the versioned simulator interfaces and
audits the safety boundary. M2 Abhijan validates attack outcomes and
`0 <= semantic_acks <= acks` from raw events. M3 Pratik supplies atomic synchronized
RGB/depth/pose/health plus camera calibration. M4 Samik accepts commands only from
`SafetySupervisor`, maps every non-release outcome to HOLD, and proves TTL/watchdog
behavior. Everyone describes OP-TEE as key protection, not trusted inference.

**Why:** the immediate deliverable is impeccable simulation. Simulation must exercise the
same failure semantics required later on hardware rather than relying on injected actions
or UI-only claims. See `codebase/SIMULATION_AND_FLIGHT_GATES.md`.

### 2026-08-18 — Formation geometry: preflight the actual camera pose
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.
**Changed:** peers no longer fly at the 3 m / 6 m offsets the plan specifies. A 5-node,
5.5 m-radius ring at 14 m is only the **nadir-camera reference case** (zero pitch and
roll). It is not a universal formation. Every run must use measured camera pitch/roll,
FOV, altitude and pose uncertainty, then pass `common.assert_covisible_formation` before
starting. A 35-degree inward pitch, for example, fails at 5.5 m in the current ground-plane
model and needs replanning (10 m passes the model's current test). The mission parameter
`phi_min = 23.0` is enforced for this experiment, but must be recalibrated for a different
camera, scene, detector or threat model.

Two bounds pin this, and both are real:
* **Too close** and peers see the same scene from the same angle. One adversarial patch
  fools all of them, and their agreement proves nothing.
* **Too far** and their camera footprints stop overlapping, so there is no shared scene to
  cross-check at all.

For the five-node, 14 m, nadir-camera reference only, the simplified model's feasible band
is `R ∈ [5.0, 6.0] m`; 5.5 sits in the middle. The pitch-sensitive counterexample is pinned
by `codebase/tests/test_angular_diversity.py::test_tilted_camera_ring_is_replanned_from_actual_pitch`.

**Makes stale:** the build plan's *"Peers at the measured 3 m and 6 m offsets (12° and 23°)"*
in the patch section; M3's Week 3 `settings.json` task; every diagram showing a line-abreast
formation.
**Who must act:**
* **M3 Pratik** — build the scene and `settings.json` for a preflighted formation, not a
  line. Do not copy the 5.5 m reference unless the cameras are actually nadir. Supply
  measured intrinsics and extrinsics.
* **M1 Suyash** — set `phi_min` in every demo manifest.
* **M4 Samik** — load the exported formation on his independent Cosys installation,
  preserve the calibrated frames in the control adapter, and block campaign start when
  preflight fails.
* **M2 Abhijan** — derive physical-scene attack placement from Pratik's accepted formation;
  do not move vehicles or falsify poses to manufacture semantic disagreement.

**Why:** 3 m at 14 m is only ~12° of parallax, which is **below** the angle at which
Section 4.3 measured adversarial patches losing their grip. That is not a detail — it is the
direct cause of the silent failure in the Gazebo run, where the patch fooled all three
drones at once and the semantic layer caught nothing. The old spacing was chosen to
guarantee overlap and nobody checked the other bound.

---

### 2026-08-18 — Cameras have pitch and roll now; the nadir assumption was wrong
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.
**Changed:** `protocol.geometry.Pose` includes `pitch` (camera tilt off nadir) and `roll`,
in radians. Manifest poses accept `[x, y, z, yaw, pitch, roll]`; four-element poses retain
the legacy nadir interpretation for development manifests only.

**Makes stale:** any assumption that the co-visibility model matched the camera. It did not.
**Who must act:**
* **M3 Pratik** — record actual camera-body translation, yaw, pitch, roll, FOV/intrinsics,
  image size, distortion assumption, units, and simulator coordinate convention in the
  exported calibration. If a camera angle changes, the manifest and preflight must change.
* **M4 Samik** — verify coordinate conversion and pose timestamps in the independent Cosys
  run; reject non-finite/stale pose before control.
* **M1 Suyash** — validate the calibrated pose fields at the mission boundary.
* **M2 Abhijan** — include stale, frozen, spoofed, and malformed pose campaigns; a signed
  host claim is authenticated but is not automatically truthful.

**Why:** the footprint model projected a rectangle straight down and scaled it with
altitude, while the controller it feeds (`detections_to_action`) reads a *forward-looking*
scene — "climb if the obstacle sits low in the frame". Detector silence now fails closed
to HOLD unless independent free-space evidence authorizes forward motion. Two
forward-facing cameras' overlap is not the intersection of two ground rectangles. Because
the co-visibility gate **abstains rather than errors** when overlap looks low, a wrong
footprint would not have thrown anything: every peer would have ACKed, the console would
have been solid green, and the semantic layer would have been switched off with no
indication. The new projection reduces to the old rectangle exactly at `pitch = 0`, so
every published number is unchanged.

---

### 2026-08-18 — Depth is a required second modality
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.
**Changed:** new module `codebase/perception/depth_check.py`. A drone now cross-checks its own
commanded action against measured range and flags a contradiction on its own, with no peers
and no vote.

**Makes stale:** the attack table's implication that the adversarial patch is caught only by
peer cross-verification.
**Who must act:**
* **M3 Pratik** — capture `ImageType.DepthPerspective` in the same `simGetImages` call as
  the RGB frame where the pinned Cosys API supports it; record measured pairing skew and
  save metric depth with the matching snapshot/hash. Do not assume the call is atomic
  without testing it.
* **M2 Abhijan** — attack depth with drop, freeze, NaN/Inf, out-of-range, and timestamp-skew
  cases. Validate the `depth` event and contradiction result. **`nearest_m: null` means
  "unknown", never "clear".**
* **M4 Samik** — prove missing/stale/invalid depth and contradiction cannot release motion;
  record the supervisor result and adapter acknowledgement.
* **M1 Suyash** — defer hardware selection until H0; require electrical/interface review,
  calibration, range/failure-mode testing, and a pinned part before updating the hardware
  plan. A simulator depth image is not evidence that a future physical range sensor works.

**Why:** a printed patch attacks the *image*. It cannot change how far away the wall is. A
drone commanding full forward while its own rangefinder reports a surface at 8 m has
contradicted itself — and that check still works when the drone is alone and has no peers to
compare against, which is the one case the whole cross-verification design cannot cover.

---

### 2026-08-18 — Event contract is frozen; M2 is unblocked
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.
**Changed:** shipped `codebase/docs/EVENT_SCHEMA.md`, `codebase/node/events.py`,
`codebase/tools/mock_events.py`, and `codebase/node/frame_source.py`. Six replayable
scenarios: `honest`, `patch`, `model_swap`, `collusion`, `unverified`, `provisioning`.

```bash
cd codebase
python -m tools.mock_events --scenario patch --rate 2
```

**Makes stale:** nothing — this is the Week-1 deliverable the plan already called for,
arriving late.
**Who must act:**
* **M2 Abhijan** — use the event stream as the attack-oracle input and build campaign
  assertions before presentation work. A console is optional and must read the same
  validated events. Three things are easy to interpret wrongly:
  1. **Not every ACK is a pass.** `ok_no_covisibility` and `ok_no_observation` are
     *abstentions*. Draw them grey. Drawing them green claims a check that never happened.
  2. **You get one verdict per node, not one per round.** Every node tallies independently
     now. *"4 of 4 nodes independently reached REJECTED"* is the line worth showing.
  3. **`semantic_acks: 0` on an ACCEPTED means nobody checked it.** Run
     `--scenario unverified` — if that looks identical to a verified accept on your screen,
     that is the bug to fix first.
* **M3 Pratik** — your frame captures feed `FileSource`. Zero-pad filenames (`f_001.jpg`)
  or the ordering goes lexicographic; pair each frame with depth/pose/calibration metadata.
* **M4 Samik** — emit command-request, supervisor-release, adapter-accept/reject, TTL, and
  vehicle-state events so attack results can be traced to actual simulator behavior.

**Why:** event-driven attack validation and any optional presentation layer otherwise have
a hard dependency on the backend. The frozen contract lets Abhijan build assertions and
presentation without waiting for a live swarm.

---

### 2026-08-18 — Protocol changes attack validation and presentation must show
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.
**Changed:** three protocol gaps closed (commit `e2d3f57`), each with a visible consequence.
* **`semantic_ack_count`** on every verdict — how many peers *actually* returned an ACK
  after running the semantic check. An `ACCEPTED` with zero is cryptographically sound
  but semantically unverified; `fallback_action` returns `DEFER`/HOLD and never executes it.
* **Peers broadcast votes and tally independently.** `PushVote` was dead code and consensus
  rested entirely on the originator — the drone under scrutiny. It could have collected
  three DISPUTEs and announced ACCEPTED with nobody the wiser.
* **`covisibility` events** carry the measured overlap and parallax, so an abstaining gate
  is readable instead of invisible.

**Makes stale:** any hand-written event-schema block — `codebase/docs/EVENT_SCHEMA.md` is
authoritative.
**Who must act:** **M2 Abhijan** validates these fields and independent outcomes in every
attack campaign. **M4 Samik** converts DEFER/NO_QUORUM/divergent or semantically unverified
outcomes to HOLD and records the adapter result. **M1 Suyash** states the limitation
precisely: independent tallies reduce originator control but are not yet a final Byzantine
agreement protocol.
**Why:** each was a case where the system looked like it was verifying something it was not.

---

## Change log

<!-- Newest entries go directly below this line. -->

### 2026-08-18 — Unreal environments split into separate scenario tracks
**Status:** FOLDED
**Changed:** the team will not co-edit one shared Unreal environment through Git. Pratik
owns the dark urban / night-mode city scenario with buildings, rooftops, and adversarial
patches on rooftops or walls. Samik owns the light terrain scenario with hills, mountains,
desert, lakes, trees, and daylight visuals. Abhijan applies the same machine-readable
attack/invariant suite to both live Cosys-AirSim scenarios or their exported replay bundles.
**Makes stale:** the older assumption that M3 owns the only Unreal scene and that M4 is
unassigned deck-only support.
**Who must act:** M2 Abhijan verifies both scenario bundles with the same attack oracle and
retains raw evidence. M3 Pratik delivers/reproduces the dark urban live scenario. M4 Samik
delivers/reproduces the daylight terrain scenario and runs `AutonomyRunner` in both. M1
Suyash keeps the protocol/event contracts versioned and consumes either environment through
the same Python-facing interfaces.
**Why:** Unreal maps/assets are binary and painful to merge. Separate scenario packs avoid
Git synchronization conflicts while still proving the same VeriSwarm Python verifier works
across multiple environments.

**Superseded on 2026-08-18:** there is one scenario owned by Pratik. Samik reproduces it and
owns model acquisition plus the complete autonomy/runtime stack. Retain this entry only as
history; do not build the second light-terrain scenario.

### 2026-08-15 — Member roles assigned; Windows/Mac split
**Status:** FOLDED
**Changed:** M2 is Abhijan (Mac), M3 is Pratik (Windows). M4 is still unassigned.
**Makes stale:** this historical allocation is superseded by the 2026-08-18 role reset.
**Who must act:** nobody; retain this entry only as history.
**Why:** only one machine runs Unreal and it must be Windows. AirSim's macOS support was
never first-class and Microsoft archived the project in 2022, so the Mac dev drives the sim
over the LAN through the `airsim` RPC client on port 41451 instead of installing Unreal.

**Superseded on 2026-08-18:** M4 is Samik; Pratik and Samik both own independent Cosys
AirSim installations; Abhijan owns attack delivery instead of the simulator client. The
new role-reset entry above is authoritative.

### 2026-08-15 — Repo moved out of `Life`
**Status:** FOLDED
**Changed:** all SIH work lives in `github.com/berlinflix/VeriSwarm_SIH` (this repo), local
path `C:\Users\suyas\sih\`. It was briefly committed to the `Life` repo by mistake and
removed in `2413239`.
**Makes stale:** any path referring to `Life/srip/codebase`.
**Who must act:** everyone — clone this repo, not `Life`.
**Why:** SIH work must version separately from the personal planning repo.

### 2026-08-15 — Paper codebase is frozen
**Status:** FOLDED
**Changed:** WSL `~/veriswarm/` (→ `github.com/berlinflix/veriswarm`) is **read-only**. All
edits happen in this repo's `codebase/`.
**Makes stale:** nothing.
**Who must act:** M1 — never push SIH changes to the paper repo.
**Why:** every number in `VeriSwarm_IEEE_v11.docx` must stay reproducible from untouched
code. The two codebases diverge deliberately.
