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

### 2026-08-18 — The crash demo is protected; do not remove it again
**Status:** OPEN
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
* **M4 / whoever narrates** — Act 1 is intact, but say *"this is a conventional
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
  obstacle (≥ 0.7 of frame height). Anything in the 0.5–0.6 band demos as a
  silent miss. This reinforces the existing scene-design rule for a second,
  independent reason.
* **M1 Suyash** — the fix is to attest the perception claim (detection count /
  extent / confidence) rather than the control output. Cheapest interim: carry a
  `detections_present` flag in the attested output so absence and hold stop
  colliding.
**Why:** worth stating plainly because it is also the strongest argument in the
paper for attesting perception rather than control — and it is a measured result,
not a hypothesis. `tests/test_baseline_controller.py` pins the band so the fix
announces itself by turning those tests red.

None as of 2026-08-18. Add the next OPEN entry directly below this line.

## Recent folded overrides

### 2026-08-18 — Environment tracks reconciled with the safety role reset
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.

**Changed:** the project will not rely on multiple teammates co-editing one Unreal map.
Pratik's primary scenario is now documented as a dark urban / night-mode world with
buildings, rooftops, and physical 3-D adversarial artifacts on rooftops or walls. A
light-terrain variant with hills, mountains, desert, lakes, and trees may be accepted as
secondary robustness evidence, but only if it passes the same pinned-world, replay,
safety, and artifact gates.

**Makes stale:** any interpretation that a light-terrain scene replaces Samik's primary
command-adapter, failsafe, reset, and evidence-collection duties from the 2026-08-18 role
reset.

**Who must act:** M3 Pratik owns the primary dark urban world and sensor-truth handoff.
M4 Samik independently reproduces the pinned CoSys setup and owns control/failsafes first;
any light-terrain pack is secondary. M2 Abhijan validates clean/attacked frame sets,
vehicle mapping, RGB/depth/pose metadata, detector outputs, action deltas, expected
invariants, and reason codes for each accepted scenario pack.

**Why:** Unreal binary assets are easy to desynchronize, so scenario packs are safer than
ad hoc shared-map Git collaboration. At the same time, Suyash's safety role reset remains
the authoritative ownership model.

### 2026-08-18 — Role reset: CoSys AirSim on Samik and Pratik's PCs
**Status:** FOLDED into [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) on 2026-08-18.

**Changed:** CoSys AirSim is now the selected external simulator. **Pratik and Samik both
run the same pinned project independently on their own PCs.** Pratik owns the world,
camera/depth/pose truth, calibration, formation, and replay data. Samik owns simulated
vehicle/SITL control, the supervisor-only command adapter, failsafes, deterministic reset,
campaign automation, and evidence collection. **Abhijan now owns attack delivery and all
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
- **M3 Pratik** — export a pinned CoSys world/settings bundle; supply atomic timestamped
  RGB + metric depth + calibrated pose/health; build/preflight the formation and physical
  3-D patch/occluder; produce ground truth and replay datasets without leaking truth into
  perception.
- **M4 Samik** — reproduce the pinned CoSys setup independently; implement the
  supervisor-only command sink, unit/frame/TTL validation, HOLD/LAND/watchdog behavior,
  deterministic reset, repeated campaign runner, telemetry/video collection, and artifact
  hashing. Configure and prove independent simulator/autopilot failsafes; a missing
  collision barrier blocks the protected campaign gate.

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
* **M4 Samik** — load the exported formation on his independent CoSys installation,
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
* **M4 Samik** — verify coordinate conversion and pose timestamps in the independent CoSys
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
  the RGB frame where the pinned CoSys API supports it; record measured pairing skew and
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

### 2026-08-15 — Member roles assigned; Windows/Mac split
**Status:** FOLDED
**Changed:** M2 is Abhijan (Mac), M3 is Pratik (Windows). M4 is still unassigned.
**Makes stale:** this historical allocation is superseded by the 2026-08-18 role reset.
**Who must act:** nobody; retain this entry only as history.
**Why:** only one machine runs Unreal and it must be Windows. AirSim's macOS support was
never first-class and Microsoft archived the project in 2022, so the Mac dev drives the sim
over the LAN through the `airsim` RPC client on port 41451 instead of installing Unreal.

**Superseded on 2026-08-18:** M4 is Samik; Pratik and Samik both own independent CoSys
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
