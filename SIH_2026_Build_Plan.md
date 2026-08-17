# SIH 2026 — VeriSwarm Sentinel autonomy plan

> Read [`urgent_new_changes.md`](urgent_new_changes.md) before starting. A newer OPEN
> entry there overrides this plan.

**Plan revision:** 2026-08-18

**Selected simulator:** the official `Cosys-Lab/Cosys-AirSim` project, pinned to one
release/commit and one compatible Unreal version on both simulator PCs.

**Primary SIH solution:** resilient autonomous multi-UAV contested-border ISR in a
GPS-denied and communications-degraded environment.

**Reusable mission profile:** search and rescue using the same coverage, localization,
perception, tracking, task-reallocation, and safety engine.

## What problem we are actually solving

Do not pitch VeriSwarm as five unrelated solutions. Pitch one coherent platform:

> A five-drone autonomous ISR swarm partitions a contested border corridor, follows
> coverage missions, maps and replans around obstacles, detects and tracks people or
> vehicles, accepts cues from ground sensors, operates through GNSS jamming/spoofing and
> intermittent C2 using visual-inertial localization, reallocates work when a drone or
> link fails, and uses VeriSwarm to stop compromised or unverifiable decisions from
> reaching flight control.

This combines the supplied problem statements as follows:

| SIH problem statement | What VeriSwarm Sentinel contributes |
|---|---|
| Autonomous Border Surveillance | Primary mission: persistent sector coverage, ground-sensor cueing, person/vehicle detection, tracking, confirmation, and alerts |
| GPS-denied Drone Navigation | VIO + IMU/barometer state estimation, GNSS consistency monitoring, uncertainty-gated planning, and local-map navigation |
| AI-powered Battlefield Object Detection | RGB/depth object detection, tracking, world-coordinate localization, and cross-drone confirmation; no weapon engagement |
| Swarm Drone Command & Control | Mission distribution, task leases, health/heartbeat state, collision separation, task reassignment, and fleet evidence |
| Autonomous Drone Swarm for Search & Rescue | Second mission configuration: frontier/sector search, survivor cues, confirmation, and damage mapping |

The demonstrated scenario may look and behave like real contested-border or battlefield
ISR, but the platform remains **unarmed surveillance and rescue autonomy**, not a weapon
system. The current COCO-pretrained YOLOv8n can support validated categories such as
person and common vehicles. Weapon/equipment classes may be added only with a lawful,
domain-relevant dataset, trained weights, and held-out evaluation; detection never
authorizes engagement. “Unusual activity” is derived from tracked behavior and declared
rules, not invented as a YOLO class.

## Simulation fidelity — treat the world as operational, not as a game

The simulation must exercise the same autonomy logic intended for a real deployment. It
does not get perfect pose, object IDs, occupancy, or expected actions from Cosys. Build a
contested scenario with:

- irregular terrain, vegetation, roads, fences, buildings, occlusion and narrow corridors;
- daytime, low-light/night, shadows, glare, rain/fog, wind and camera degradation;
- moving people, civilian and mission vehicles, decoys, stopped vehicles, groups that
  split/merge, and objects that temporarily occlude each other;
- noisy ground-sensor cues, false cues, missed cues and sensor outages;
- static obstacles, newly blocked routes, moving obstacles and no-path cases;
- GNSS loss, gradual/step spoofing, VIO drift/reset/dropout and barometer/IMU faults;
- latency, packet loss/reordering/duplication, partitions, bandwidth limits and node restarts;
- battery/energy reserve, slow inference, vehicle failure, task reassignment and recovery;
- physical 3-D adversarial patches/occluders, model/runtime compromise, replay, rogue
  identities, collusion/equivocation and resource pressure;
- independent truth for scoring collision, position, coverage and detection—never exposed
  to the autonomy process.

There are no “demo shortcuts”: no teleporting a vehicle as navigation, no simulator pose
as VIO, no segmentation labels as perception, no direct verdict injection, and no route
that is pre-cleared after an obstacle changes. However, do not write “100% foolproof” or
“no limitations” in evidence or the pitch: a simulator has a domain gap. The acceptance
target is zero invariant violations across the declared campaign, with every residual risk
and unmodelled physical effect recorded honestly.

## Autonomy means more than drawing boxes

Claude correctly identified that the existing perception action alone cannot complete a
mission. A complete autonomy stack has separate responsibilities:

1. **Mission definition:** where to search, what to detect, constraints, priorities, and
   completion conditions.
2. **State estimation:** where the drone believes it is, with uncertainty, even when GNSS
   is absent or inconsistent.
3. **Mapping:** which space is free, occupied, unknown, or temporarily blocked.
4. **Task allocation:** which drone owns which sector or confirmation task.
5. **Global planning:** a route through known free space toward the next task.
6. **Local safety planning:** a collision-safe command satisfying depth, geofence,
   separation, dynamics, and stopping-distance constraints.
7. **Perception and tracking:** what was detected, where it is in the world, and how it
   moves over time.
8. **Flight control:** PX4/SimpleFlight follows the released setpoint.
9. **Verification and supervision:** VeriSwarm and `SafetySupervisor` decide whether the
   evidence is sufficient to release motion.

Only object perception needs a neural network in the initial build. Coverage, A* search,
tracking filters, task leases, constraint checking, and flight control are deterministic
algorithms. That does not make them optional; without them the drone cannot autonomously
complete A→B or a surveillance mission.

## AI model-weight decision

There is **no autonomy model weight** to download. Navigation is produced by VIO/state
estimation, mapping, coverage, A*, local constraint filtering, task allocation and PX4—not
by asking YOLO to fly.

Do **not** download everything in the list. Only two new candidate files are required now.
Samik is the model-acquisition and benchmarking owner; Suyash is the independent approval
authority; Pratik owns evaluation data; Abhijan owns attack-only variants.

| Purpose | Weight or method | Download now? | Owner | Exact local location |
|---|---|---:|---|---|
| Regression baseline | Existing `yolov8n.pt` | Already present | Suyash protects baseline; Samik benchmarks | `codebase/yolov8n.pt` — do not move because current evaluation code expects this path |
| Primary RGB candidate | Official `yolov8s.pt` | **Yes** | Samik | `codebase/models/candidates/yolov8s.pt` |
| Higher-capacity comparison | Official `yolov8m.pt` | **Yes** | Samik | `codebase/models/candidates/yolov8m.pt` |
| New-generation comparison | Official `yolo11s.pt` or a later supported successor | **No, not in the first integration** | Samik may open a separate experiment after G4 | `codebase/models/experimental/`; it never replaces the baseline silently |
| Selected mission detector | Exact winning candidate copied without modification | After benchmark | Suyash approves; Samik provisions | `codebase/models/approved/rgb_detector.pt` plus registry SHA-256 |
| Attack artifact | Existing `yolov8n_tampered.pt` | Already present | Abhijan only | `codebase/yolov8n_tampered.pt`; never place in `approved/` |
| Tracking | Kalman filter + ByteTrack-style association | No weight | Samik | Code/config in `codebase/autonomy/`; registry records algorithm/config hash |
| Metric depth | Cosys depth/LiDAR | No weight | Pratik supplies; Samik consumes | Calibration/settings in the scenario bundle |
| GPS-denied localization | Selected ORB-SLAM3/VINS-class VIO | No neural weight | Samik | Pinned source/build/config under the dependency manifest; any vocabulary file is recorded separately |
| Thermal/person detection | Sensor-specific fine-tuned detector | Not now | Pratik supplies future data; Samik integrates after validation | `codebase/models/experimental/thermal/`; never labelled approved from RGB data |
| Weapon/special-equipment classes | Domain-specific, legally sourced fine-tuned detector | Not now | Requires a separately approved dataset/model task | `codebase/models/experimental/domain/`; excluded from SIH claims until held-out validation passes |

`codebase/.gitignore` already ignores `*.pt` and `*.onnx`, so the binaries do not travel
through Git. Commit only the downloader, registry, hashes, metadata and evaluation summary.
Store the approved binary in the team’s immutable release bundle under
`artifacts/models/<sha256>/` and copy it to each machine before an accepted run.

### Model download and custody procedure

1. **Suyash** creates and reviews `codebase/models/registry.json`. Each entry contains model
   ID, official source URL/host, framework/version, expected filename, license, SHA-256,
   size, task/classes, status (`pending`, `candidate`, `approved`, `attack-only`, `rejected`)
   and reviewer. A `pending` entry cannot run in a mission.
2. **Samik** implements `codebase/tools/fetch_models.py`. In `--stage` mode it downloads a
   pending registry entry from the official host into quarantine, records the final URL,
   size and computed SHA-256, and never marks it approved. Suyash independently retrieves
   the same official artifact and compares the hash. Only after both match does Suyash lock
   the hash/status in the committed registry. Normal `--sync` mode then requires that hash,
   writes to a temporary file and atomically places it in `models/candidates/`. Redirects to
   unapproved hosts, partial files and hash mismatches fail.
3. On Samik’s connected preparation PC—not a mission node—run staging once, then use sync
   after Suyash locks the hashes:

   ```powershell
   cd C:\Users\<name>\sih\codebase
   python -m tools.fetch_models --registry models\registry.json --group rgb-candidates --stage
   python -m tools.fetch_models --registry models\registry.json --group rgb-candidates --sync
   ```

4. **Pratik** captures the frozen tune and held-out datasets from the single scenario. Raw
   data stays outside Git under `codebase/data/model_selection/`; its manifest, labels,
   split IDs and hashes are retained with the run bundle.
5. **Samik** runs every candidate with identical frames, preprocessing, thresholds and
   hardware. Raw results go to ignored `codebase/eval/results/model_selection/<run_id>/`;
   the reviewable summary goes to `codebase/docs/MODEL_SELECTION_REPORT.md`.
6. **Suyash** checks class-wise precision/recall, small/distant-target performance, false
   alarms, p50/p95/p99 latency, GPU/CPU/RAM, model/runtime hashes and license. He approves
   exactly one detector; he does not approve all candidates.
7. **Samik** copies the exact approved bytes to
   `codebase/models/approved/rgb_detector.pt`, verifies the SHA-256 still matches, exports
   ONNX/TensorRT only if needed, and registers each runtime artifact separately. An engine
   built on one GPU/runtime is not assumed portable to another.
8. **Suyash**, on the separate authority machine, mints the signed allowlist from the
   approved weight and reviewed runtime artifacts using `tools.make_authority`. The private
   authority seed never goes to Samik’s provisioner, a drone, Git or the run bundle.

   ```powershell
   cd C:\Users\<name>\sih\codebase
   python -m tools.make_authority `
     --sign-key-file <secure-path-outside-repository> `
     --out <run-bundle>\mission_authority.json `
     models\approved\rgb_detector.pt
   ```

9. **Samik** provisions the approved artifact to each simulated node. Every process hashes
   the bytes it actually loaded and must match the signed authority before mission start.
10. **Abhijan** provisions `codebase/yolov8n_tampered.pt` only inside named attack runs. He
    never edits the approved registry/allowlist and must prove that the resulting hash is
    rejected.

### Battlefield-class extension after the baseline detector passes

The pretrained model immediately supports only validated COCO classes such as person and
common vehicles. To claim specialized equipment/weapon detection as surveillance output:

1. Suyash freezes a lawful detection taxonomy and alert-only use policy; vague labels such
   as `threat` are forbidden.
2. Pratik adds representative 3-D assets, ranges, occlusions, viewpoints and annotation
   exports to the one scenario and prepares a separately sourced, legally usable real-image
   evaluation set. Synthetic renders never substitute for the real held-out set.
3. Samik fine-tunes the selected base detector in a separate reproducible training job,
   recording dataset/split hashes, seed, code, hyperparameters, checkpoints and metrics.
   Output stays at `codebase/models/experimental/domain/contested_isr_v1.pt`.
4. Abhijan tests patches, camouflage/occlusion, class confusion and false-alert cases.
5. Suyash promotes the domain model to `models/approved/rgb_detector.pt` only if it passes
   every per-class recall/precision, false-alarm, latency and robustness gate. Otherwise the
   SIH claim remains person/common-vehicle ISR. Detection still produces an operator alert,
   never an engagement decision.

Model selection is evidence-driven:

1. Freeze a tune set and a never-touched held-out test set containing the actual Cosys
   viewpoints, ranges, weather, occlusion and target sizes.
2. Benchmark `yolov8n`, `yolov8s` and `yolov8m` with identical preprocessing and thresholds.
3. Select the smallest model that clears the predeclared per-class recall/precision and
   whose measured p99 sensor→decision time fits comfortably inside the command TTL on the
   target machine. A larger model is not automatically safer.
4. Pin the exact source URL, license, file SHA-256, Ultralytics version, export settings,
   runtime/TensorRT version and hardware result. Add only the chosen model/runtime hashes
   to the separate mission-authority allowlist.
5. Never auto-download weights during a mission or accepted run. Archive and verify them
   before startup. An open-vocabulary detector may be shown as research output but cannot
   control motion or support a class claim without a frozen vocabulary and validation.
   The current development helper may auto-download during an explicit preparation step;
   the accepted runner must use a local-only resolver and fail closed when a file/hash is
   missing.

## Current trusted baseline

The hardened protocol/safety core already exists in `codebase/`:

- `node/mission.py` is the only supported perception → consensus → command seam.
- `perception/safety_supervisor.py` is the only component allowed to release motion.
- `node/live_node.py` provides an atomic, timestamped perception snapshot.
- Protocol-v2 receipts bind mission, epoch, sequence, runtime measurement, action
  frame/validity, and pose metadata.
- Exact replay, duplicate sequence misuse, vote equivocation, insufficient semantic
  evidence, stale commands, unknown clearance, and unhealthy state fail closed.
- `sim/closed_loop.py` is the deterministic pre-Cosys gate.

This is not certification. OP-TEE protects a signing key; it does not prove trusted
inference, truthful pose, correct mapping, or safe planning. The current independent vote
tallies are not formal Byzantine agreement.

## Immediate priority — make the pictured drones fly A→B

The drones sitting still in Pratik’s Cosys window is normal. Cosys starts vehicles but does
not invent a mission. API control is disabled by default; a client must connect, request
control, arm, take off and send a position/velocity command.

Do this before YOLO, attacks, VIO or swarm consensus. The first clean flight isolates the
simulator/control boundary from every later subsystem.

### Pratik’s first-flight handoff

Pratik does not write the flight controller. He must give Samik:

1. the exact Cosys release/commit, Unreal version and explicit `settings.json` path;
2. API host/port and whether Samik’s first script runs locally on Pratik’s PC or remotely;
3. `client.listVehicles()` output and the exact case-sensitive names—eventually
   `alpha`, `bravo`, `charlie`, `delta`, `echo`;
4. each vehicle’s configured `VehicleType`, initial pose and collision status;
5. two visually and geometrically clear NED points, A and B, plus altitude band and
   geofence. Cosys uses metres and NED, so flight altitude has negative `z`;
6. one RGB/depth capture and `getMultirotorState()` result per vehicle;
7. a scenario copy with attacks, moving obstacles and GNSS faults disabled for the smoke run.

Pratik must not use Unreal object teleportation as flight and must not manually pilot the
accepted route. His job is to make the world/API deterministic and observable.

### Samik’s first executable deliverable

Samik creates `codebase/sim/cosys_smoke_flight.py` using the pinned `cosysairsim` Python
client. It must be parameterized by host, vehicle and A/B values; no machine IP or vehicle
name is buried in code. He pins the verified client version in
`codebase/requirements-sim.txt`; if the packaged simulator requires its matching source
client, pin that source commit rather than mixing it with an unrelated PyPI build.

For one vehicle, the script performs and checks this exact state machine:

```text
CONNECT → list/validate vehicle → enableApiControl(True) → verify control enabled
→ armDisarm(True) → takeoffAsync().join() → stable hover check
→ moveToPositionAsync(Bx, By, Bz, speed).join()
→ position/tolerance + collision check → hoverAsync().join()
→ landAsync().join() → armDisarm(False) → enableApiControl(False)
```

Every transition has a timeout. On timeout, API-control loss, collision, non-finite state or
geofence violation, send the safest available hover/land command, record the failure and
stop. `reset()` is cleanup after a stopped run, never an in-flight recovery policy; after
reset, API control and arming must be requested again.

### First-flight build order

1. **F0 — connection:** on Pratik’s PC, Samik’s script connects, `ping()` succeeds,
   `listVehicles()` matches settings and `isApiControlEnabled()` becomes true.
2. **F1 — one-drone vertical:** `alpha` arms, takes off to a conservative clear altitude,
   hovers for a timed interval, lands and disarms with collision count zero.
3. **F2 — one-drone A→B:** `alpha` flies between clear points with no perception or attack,
   reaches the configured tolerance, hovers, lands and produces telemetry/video.
4. **F3 — five-drone basic movement:** take off sequentially. Each drone receives its own
   offset goal `B_i`; never send all five to one coordinate. Maintain the configured
   minimum separation and land sequentially.
5. **F4 — adapter:** replace direct smoke commands with Samik’s `CommandSink`; prove TTL,
   wrong-frame, non-finite and API-loss cases yield HOLD/LAND.
6. **F5 — waypoint follower:** implement `waypoint_follower.py`; follow a versioned path,
   approach/stop at B using position-plus-velocity dwell, detect no progress, and request
   bounded replan. Do not add a YOLO action vector.
7. **F6 — protected mission path:** separate `PerceptionClaim` from `WaypointProposal`, bind
   both to the selected command in `AutonomyDecisionRecord`, then connect
   `AutonomyRunner → SafetySupervisor → CommandSink`.
   Direct `moveToPositionAsync` remains only in the isolated F0–F3 smoke utility.
8. **F7 — attacks:** only after F6 is repeatable does Abhijan inject attacks at named route
   checkpoints. Never debug connectivity, navigation and an attack simultaneously.

**F2 acceptance:** start from reset three times; `alpha` reaches B within the predeclared
position/velocity tolerance, collision count remains zero, every transition completes
before timeout, and the complete telemetry/configuration artifact is retained.

**F3 acceptance:** all five healthy drones reach their distinct `B_i` goals or enter their
declared safe terminal state with zero collision, geofence and minimum-separation
violations. “One drone reached B” is not swarm success.

## The single integrated scenario

Pratik owns one scenario: **Contested Border Sentinel**. Start from one prebuilt
Neighborhood/City-style environment shipped for the pinned Cosys build; do not model
terrain. Add only the scenario layer: border/geofence zones, urban and rough-terrain
segments, people and vehicles, ground sensors, obstacles, weather/night variations,
GNSS/C2 fault regions, a casualty/SAR cue and adversarial objects.

The same run tackles surveillance, battlefield ISR, GPS-denied navigation and swarm C2:

1. The mission authority loads a signed mission definition containing the border corridor,
   geofence/no-fly areas, launch/home locations, traversable search cells, supported object
   classes, confirmation policy, energy reserve and abort conditions.
2. Five drones perform preflight and receive sector leases. They fly coordinated coverage
   paths; they use formation only for safe transit or a required confirmation geometry.
3. Noisy simulated ground sensors produce time/location cues, including false and missed
   cues. A cue creates an inspection task, never a confirmed intrusion.
4. A healthy drone replans to inspect the cue. RGB/depth detects a supported person or
   vehicle, projects it into world coordinates, and creates a time-stamped track.
5. A second drone receives a different confirmation viewpoint. Reports fuse only when
   time, geometry, class compatibility, depth and uncertainty gates pass.
6. Behavior rules classify the track as border crossing, restricted-zone entry, loitering,
   convoy/group movement or unresolved. This is how surveillance becomes an operational
   alert rather than a collection of YOLO boxes.
7. One drone enters the GNSS-denied/spoofed zone. It navigates using VIO/IMU/barometer;
   Cosys true pose remains isolated as scoring truth.
8. Rocks, trees, walls, buildings, vehicles and a newly blocked corridor update the
   occupancy map through depth/LiDAR. A* replans around them; the local safety filter may
   move laterally or climb only when clearance, altitude and geofence checks pass.
9. A casualty/SAR cue appears inside the same world. The allocator pauses a lower-priority
   patrol task, sends the nearest capable drone to search, requests confirmation, records
   the safest known approach and later returns unfinished border cells to the queue.
10. Abhijan triggers a cyber, perception, navigation or C2 fault. VeriSwarm rejects,
    defers or isolates affected evidence; the vehicle holds and its unfinished task lease
    is reassigned to a healthy drone.
11. The mission completes only when required traversable cells are covered and every
    intrusion/SAR alert is confirmed, dismissed, expired or explicitly unresolved.

This answers the surveillance problem with persistent coverage, cueing, tracking and
operator-ready alerts. It answers the battlefield-imagery problem with rapid supported-
class detection, world coordinates, multi-view confirmation, behavior rules and evidence.
It does **not** claim weapon identification or engagement from generic COCO weights.

### A→B is the mission spine, not the whole mission

Keep Pratik’s A→B idea, but add named zones between them:

| Route stage | What the swarm must do |
|---|---|
| **A — launch/base** | Preflight five vehicles, validate sensors/models/authority, allocate sectors and take off sequentially |
| **C1 — surveillance sector** | Cover assigned cells; ground sensor creates a cue; detect, track and confirm a person/vehicle from two viewpoints |
| **C2 — terrain/obstacle corridor** | Map rocks/trees/walls/buildings with depth/LiDAR and replan without needing YOLO labels |
| **C3 — GNSS-denied zone** | Reject absent/spoofed GNSS, use VIO and slow/hold if localization covariance exceeds the mission bound |
| **C4 — attack checkpoint** | Abhijan triggers one named replay/model/patch/C2 fault; the affected vehicle holds/is isolated and its unfinished task is reassigned |
| **B — rally/landing zone** | Healthy fleet reaches distinct offset goals, unresolved tasks/alerts are declared, then vehicles land sequentially |

Initial demonstration roles help the audience follow the swarm, but the allocator—not a
hardcoded leader—owns reassignment:

- `alpha`: inspection originator and the visibly attacked vehicle;
- `bravo` and `charlie`: separated co-observers/semantic voters;
- `delta`: second-view target confirmation and normal patrol;
- `echo`: normal patrol plus reserve capacity for reassigned work.

Mission success requires all declared coverage/alert tasks to reach a terminal state, all
healthy assigned vehicles to reach their distinct B offsets, attacked/failed tasks to be
reassigned, zero protected collisions/geofence/separation violations, and no unsafe command
after expiry or failed verification. If `alpha` is safely held and `echo` finishes its task,
the mission can succeed even though `alpha` never reaches B. That is stronger swarm evidence
than simply making five drones follow the same line.

Thermal is an adapter, not a checkbox. If Cosys-AirSim does not provide a validated
radiometric thermal sensor for the pinned build, use a clearly labelled synthetic thermal
surrogate for software integration only. Do not claim that false-colour RGB, annotation,
or segmentation proves real thermal performance. Physical thermal transfer requires a
real calibrated sensor and HIL evaluation later.

## End-to-end autonomy architecture

```text
signed mission definition
        │
        ├── coverage/task allocator ────────────────┐
        │                                           │
RGB + depth + IMU + barometer + GNSS health        │
        │                                           │
        ├── state estimator (GNSS/VIO modes) ──┐    │
        ├── occupancy map + tracked objects ───┼────┤
        └── perception receipts / peer votes ──┘    │
                                                    ▼
                                      global route planner
                                                    │ candidate path
                                                    ▼
                              deterministic local safety filter
                       depth + geofence + separation + braking envelope
                                                    │ candidate command
                                                    ▼
                                VeriSwarm policy + SafetySupervisor
                                                    │ released command + TTL
                                                    ▼
                                    Cosys/PX4 command adapter
                                                    │
                                       simulated vehicle + truth logs
```

### 1. Signed mission definition

Create one canonical schema containing:

- `mission_id`, `epoch`, plan version/hash, authorized signer, start/expiry;
- local mission origin and coordinate frame;
- geofence, altitude band, no-fly polygons, home/landing points;
- traversable coverage cells, search priority, visit/confirmation requirements;
- target classes and allowed alert states;
- maximum estimator uncertainty, sensor staleness, command TTL, speed, acceleration,
  stopping margin, and minimum separation;
- task-lease duration, retry policy, communication-loss policy, energy reserve, and abort
  conditions.

The simulator configuration is not the mission authority. Pratik may build a world but may
not silently change the signed geofence, thresholds, or completion rules.

### 2. State estimation and GPS-denied behavior

Implement explicit estimator modes:

- `GNSS_HEALTHY`: GNSS and visual/inertial innovation are consistent.
- `GNSS_REJECTED_VIO`: GNSS is absent or inconsistent; VIO/IMU/barometer drives the local
  state estimate.
- `LOCALIZATION_DEGRADED`: covariance or timestamp skew exceeds the mission threshold;
  reduce speed and refuse new long-range tasks.
- `LOCALIZATION_LOST`: no bounded estimate; HOLD, then execute the separately validated
  recovery/land policy.

For the GPS-denied gate, Samik must integrate an actual VIO pipeline receiving simulated
camera and IMU data. Direct Cosys pose, segmentation, annotation, object IDs, or perfect
depth-derived pose are scoring truth only and are forbidden from estimator input. VIO
publishes pose, velocity, timestamp, covariance, health, reset counter, and frame ID.

GNSS spoof detection compares innovation against the independent visual/inertial estimate;
it does not assume that either source is always correct. Re-entry from VIO to GNSS needs a
bounded consistency window and must not cause a discontinuous control jump.

### 3. Mapping and world model

- Use RGB plus metric depth or LiDAR to maintain a local 2.5-D/voxel occupancy map with
  `FREE`, `OCCUPIED`, and `UNKNOWN`; unknown is never free.
- Store observation time, source, pose/covariance, and map version for every update.
- Decay dynamic obstacles separately from permanent structure.
- Project detections to world coordinates only when depth, calibration, pose covariance,
  and timestamp skew pass their gates.
- Simulator annotation/segmentation supplies evaluation labels only. It must never update
  the autonomy map or target tracks.
- Shared map fragments require frame alignment and uncertainty checks. Conflicting maps do
  not get averaged blindly; the affected region becomes unknown until resolved.

### 4. Coverage and task allocation

- Decompose the traversable mission area into explicit cells/sectors.
- Generate deterministic lawnmower/contour coverage paths per sector.
- Allocate work using a scored lease: travel cost, remaining energy, estimator health,
  sensor capability, current load, and communication health.
- Use deterministic tie-breaking and monotonic lease versions.
- A failed/unhealthy drone stops receiving new tasks. When its lease expires, unfinished
  cells return to the queue and are reassigned.
- During a partition, a drone may finish its already leased safe task but may not invent a
  conflicting lease. If evidence or localization expires, it holds.
- Formation flight is used only for transit or a required confirmation geometry. Search
  coverage uses separated sectors with enforced minimum distance.

Five drones are the demonstrated scale. “Hundreds of drones” requires a separate
discrete-event C2/load test and network-capacity evidence; renderer instances are not a
scalability proof.

### 5. Global replanning

- Convert the current occupancy map, geofence, no-fly areas, and altitude policy into a
  bounded planning grid.
- Use deterministic A* for the first implementation. Re-run when an occupied/unknown cell
  invalidates the path, a task changes, or localization uncertainty changes the allowable
  corridor.
- Smooth and time-parameterize the path only after collision checking; revalidate the
  result, not just the unsmoothed grid path.
- The planner must return `NO_PATH` rather than crossing unknown space, leaving the
  geofence, or ignoring vehicle dynamics.
- Incremental D* Lite is an optimization only after A* correctness and replanning latency
  are measured. Complexity is not autonomy evidence.

### 6. Local collision-safe command selection

Do not add waypoint and avoidance vectors blindly. Use constrained selection:

1. The waypoint/path follower proposes a bounded set of candidate velocities.
2. Discard candidates that violate depth-based braking distance, occupied/unknown space,
   geofence/no-fly constraints, predicted peer separation, command continuity, vehicle
   limits, or estimator uncertainty.
3. Choose the safe candidate closest to mission progress.
4. If no candidate is safe, release HOLD. A validated recovery state may choose LAND.

The final supervisor sees the exact command that the vehicle would receive, not just the
YOLO avoidance contribution. Emergency collision/geofence constraints dominate mission
progress. Detector silence does not authorize forward motion without independent free-space
evidence.

**Explicit Samik deliverable:** `autonomy/waypoint_follower.py` consumes a versioned path
plus current estimator state and mission limits, then produces a deterministic ordered set
of bounded candidate velocities—not a supposedly safe actuator command. It must implement
monotonic waypoint advancement, bounded look-ahead, braking-based approach speed,
position-plus-velocity arrival with dwell, progress/stuck detection and bounded replan
requests. Invalid/stale state or a changed/empty path produces HOLD, never reuse of the last
motion request.

The integration must separate three objects that the old code conflates:

1. `PerceptionClaim` — what the detector observed and peers verified;
2. `WaypointProposal` — how the local mission/path follower proposes progressing;
3. `AutonomyDecisionRecord` — the exact path/map/estimator/perception evidence and selected
   candidate submitted to `SafetySupervisor`.

An accepted perception certificate cannot authorize an unrelated waypoint command. The
decision record binds the exact selected candidate and all evidence versions; the
supervisor gates that exact command and TTL. The current `MissionRunner` still treats
`inference(frame)` as the requested action, so protected A→B is not accepted until this
separation is implemented and its W0–W3 gates in `SAMIK_EXECUTION_PLAN.md` pass.

### 7. Detection, tracking, and alert fusion

- Run the actual RGB detector on every accepted frame; retain input/model/runtime hashes.
- Use depth and calibrated pose to localize supported detections in the mission frame.
- Track objects over time with a deterministic motion filter and explicit track covariance.
- Fuse reports only when time, position uncertainty, class compatibility, and viewpoint
  checks pass. Preserve contradictory reports.
- “Unusual activity” is a declared rule over tracks—such as entering a restricted polygon,
  crossing a border direction, or loitering beyond a configured duration—not a magical
  YOLO class.
- An alert state machine is `CUE → DETECTED → CONFIRM_REQUESTED → CONFIRMED/DISMISSED/
  UNRESOLVED`. Every transition records its evidence.

Collision mapping and semantic detection are different. Depth/LiDAR must treat a rock,
tree, wall or building as an obstacle even when YOLO has no class for it. For the
judge-facing adversarial semantic test, however, the attacked object must be a class the
selected detector demonstrably recognizes at the exact range, scale, light and viewpoints.

The measured 0.5–0.6 frame-occupancy blind band is a P0 verification defect: different
perceptions can collapse to similar control vectors. Before G4, Suyash must version the
attested semantic claim so peers compare detection presence/count, class, extent,
confidence and supporting depth—not only the final control vector. Until that fix is
implemented and regression-tested, use a frame-dominating supported-class target (at least
the measured 0.7 frame-height condition) for the stage demonstration, and retain the blind
band as an explicit negative test rather than hiding it.

### 8. Decision evidence

The existing perception receipt is necessary but not enough to explain autonomous flight.
Add a simulation-stage `AutonomyDecisionRecord` to the hash-chained event log containing:

- mission-plan hash and task/lease version;
- estimator sample hash, mode, covariance, timestamp, and frame;
- map version and route hash;
- sensor/perception receipt IDs and track IDs;
- candidate set/constraint summary and selected candidate;
- VeriSwarm outcome, supervisor reason, released command, frame, TTL, and sequence;
- adapter acceptance/rejection and resulting vehicle state.

For SIH simulation this is auditable evidence. Before real flight, decide which fields are
signed/attested and extend protocol compatibility deliberately; do not casually break the
protocol-v2 receipt during simulator integration.

## Locked ownership

Pratik owns the single Cosys scenario. Samik owns everything that consumes it and makes the
swarm autonomous. The split is precise:

> **Pratik builds what the drones see. Samik builds how the drones estimate, decide,
> coordinate and move.**

| Member | Primary ownership | Required handoff |
|---|---|---|
| **M1 Suyash** | VeriSwarm protocol, supervisor policy, mission/evidence schemas, integration contracts, authority/manifests, review and gate acceptance | Versioned interfaces, canonical manifest, reason catalogue, conformance tests, final runbook/evidence report |
| **M2 Abhijan** | Attack delivery, adversarial navigation/perception/C2 campaigns, invariant oracle, resource/network faults | Machine-readable scenarios, real delivery mechanisms, baseline-vs-attack evidence, failure report |
| **M3 Pratik** | The single Cosys scenario, all sensor definitions, calibration, GNSS-fault zones, ground sensors, people/vehicles/obstacles, weather and evaluation truth | Pinned scenario/settings/assets, atomic sensor adapter, calibration, truth logs and replay/model-selection datasets |
| **M4 Samik** | Model acquisition/benchmarking, Cosys vehicles/SITL, autonomy engine, VIO, mapping/planning, task allocation, tracking, command adapter, recovery and campaign runner | Verified candidate cache, model report, `AutonomyRunner`, planner/allocator tests, PX4/Cosys config, failsafe proof and run bundles |

No one writes directly into another owner’s truth domain. Abhijan cannot set expected
verdicts; Pratik cannot feed simulator truth to autonomy; Samik cannot bypass the
supervisor; Suyash cannot treat UI output as acceptance evidence.

Standalone, person-specific execution plans are authoritative for daily task ordering but
do not override the shared interfaces or gates in this document:

- [`SUYASH_EXECUTION_PLAN.md`](SUYASH_EXECUTION_PLAN.md)
- [`ABHIJAN_EXECUTION_PLAN.md`](ABHIJAN_EXECUTION_PLAN.md)
- [`PRATIK_EXECUTION_PLAN.md`](PRATIK_EXECUTION_PLAN.md)
- [`SAMIK_EXECUTION_PLAN.md`](SAMIK_EXECUTION_PLAN.md)

### Planned implementation paths

These paths make ownership reviewable; new files should be created under `codebase/`:

| Owner | Planned paths |
|---|---|
| Suyash | `docs/AUTONOMY_CONTRACT.md`, `docs/COVIS_LIVE.md`, `tools/covis_live.py`, `tools/optee_preflight.py`, `tools/event_collector.py`, `console/app.py`, `autonomy/contracts.py`, `autonomy/decision_record.py`, `models/registry.json`, `node/mission.py`, `perception/safety_supervisor.py`, `docs/MODEL_SELECTION_REPORT.md` approval and final `RUNBOOK.md` |
| Abhijan | `attacks/scenarios/*.json`, `attacks/runner.py`, `attacks/oracle.py`, adversarial tests, the printed/webcam attack rig and attack artifacts outside `models/approved/` |
| Pratik | `sim/cosys/contested_border/` scenario bundle, explicit `settings.json`, calibration/sensor/world manifests, truth exporter and local ignored `data/model_selection/` captures |
| Samik | `tools/fetch_models.py`, `eval/model_selection.py`, `autonomy/mission_manager.py`, `autonomy/state_estimator.py`, `autonomy/mapper.py`, `autonomy/planner.py`, `autonomy/waypoint_follower.py`, `autonomy/task_allocator.py`, `autonomy/tracker.py`, `autonomy/local_safety.py`, `sim/cosys_adapter.py` and `tools/run_campaign.py` |

## M1 — Suyash deliverables

1. Freeze the mission, sensor snapshot, estimator state, task lease, target track, autonomy
   decision, and command-sink schemas with units, frames, clocks, bounds, and error behavior.
2. Create the signed five-node mission manifest and separate authority material. Model,
   runtime, map/world, camera, VIO, planner, PX4, and Cosys configuration hashes are explicit.
3. Provide Pratik’s sensor-source conformance test, Samik’s estimator/command conformance
   tests, and Abhijan’s invariant/reason-code catalogue.
4. Keep `MissionRunner` as the only integration seam and `SafetySupervisor` as the final
   motion authority. Add a test proving planner, consensus, UI, and simulator helpers cannot
   reach the command sink directly.
5. Fix the semantic blind band with a versioned attested perception claim; regenerate
   protocol/codegen vectors and add tests sweeping object size, confidence and depth so
   “no detection” cannot alias “moderate obstacle” merely because control vectors match.
6. Integrate the `AutonomyDecisionRecord` without modifying the frozen protocol receipt
   format unless a versioned change is reviewed and all regression vectors are regenerated.
7. Review every result disagreement between nodes/PCs, own go/no-go, and maintain the
   runbook, evidence index, known limitations, and precise claims.
8. Own `models/registry.json`, independently verify Samik’s candidate hashes, review
   `MODEL_SELECTION_REPORT.md`, approve exactly one model/runtime bundle and mint the
   signed authority allowlist on a machine separate from provisioning.
9. Before every external-simulator acceptance run:

   ```bash
   cd codebase
   python -m pytest -q
   python -m sim.closed_loop
   ```

## M2 — Abhijan deliverables

Abhijan attacks real boundaries. A control file may trigger delivery, but it may not force
the result.

| Campaign family | Required cases |
|---|---|
| Identity/protocol | Rogue identity, invalid key/certificate, exact replay, stale replay, duplicate sequence with altered content, malformed/non-finite message |
| Provenance | Actual model/runtime swap, compromised provisioner, authority separation, rollback/downgrade attempt |
| Perception | Physical 3-D patch/occluder, glare/blur/fog, lens obstruction, RGB freeze/drop, depth freeze/drop/NaN/Inf, contradictory modalities |
| Navigation | GNSS loss, gradual spoof, step spoof, VIO drift/reset/dropout, IMU bias, barometer error, stale pose, frame mismatch, estimator covariance growth |
| Mapping/planning | Phantom obstacle, removed obstacle, stale/conflicting map fragment, blocked route, no-path condition, planner timeout |
| Swarm/C2 | Delay, loss, duplication, reorder, partition, peer restart, task-lease replay/conflict, collusion, signed vote equivocation, clock jump |
| Availability | Invalid-message flood, reconnect churn, cache pressure, slow inference, process death, simulator pause and command-stream loss |

Every scenario manifest contains the threat assumption, affected interfaces/nodes, seed,
initial state, injection method/time/duration, invariant, expected reason class, stop
condition, recovery, and cleanup. Quorum and collusion expectations are generated from
configured `N` and thresholds; no fixed “two attackers are safe” claim is allowed.

Abhijan also owns the automated result oracle. It checks mission completion, coverage,
localization error, separation/geofence/collision invariants, alert correctness, task
reassignment, event-chain integrity, certificate consistency, command expiry, resource
bounds, and `0 <= semantic_acks <= acks` from raw evidence.

Abhijan never downloads an “attack model” from an unknown third party. The standard attack
artifact is the existing, reproducibly generated `codebase/yolov8n_tampered.pt`. If an
attack against the newly approved detector is required, the mutation recipe, seed, parent
hash and output hash must be recorded, and the output remains outside `models/approved/`.

## M3 — Pratik deliverables: environment and sensor truth

### Cosys project

1. Install an explicit Cosys-AirSim release/commit and compatible Unreal version on
   Pratik’s PC. Always launch with an explicit `-settings` path; do not rely on the
   simulator’s settings-file search order.
2. Select one prebuilt Neighborhood/City-style environment available in the pinned release
   for **Contested Border Sentinel**. Do not model terrain. Configure dark urban/night and
   daylight/weather variants inside the same scenario, then add the border/geofence layer,
   five launch points, people/vehicles, a casualty/SAR cue, ground sensors, rooftop/wall
   adversarial objects, rough/unclassified geometry, dynamic obstacles, blocked routes,
   GNSS/C2 fault zones and deterministic seeds.
3. Define GPS-healthy, GPS-denied, and spoof-transition regions independently of the
   estimator. Truth logs record the real trajectory and injected signal, but the autonomy
   API receives only the declared sensor stream.
4. Add noisy ground-sensor cues with false positives, missed detections, position error,
   timestamps, sensor ID, and health. A ground cue never directly creates a confirmed track.
5. Place adversarial patches/occluders as physical 3-D objects or materials. No mission
   demonstration may composite the patch into camera pixels.
6. Write the complete clean-start/reproduction procedure for Samik. The scenario is not
   accepted until Samik reproduces it without Pratik’s editor state or verbal help.

### Sensors and datasets

1. Configure front/nadir RGB, metric depth or LiDAR, IMU, barometer, magnetometer, and GNSS
   for each vehicle as required by the pinned simulator build. Record sensor pose in the
   vehicle frame and all units/noise/rate settings.
2. Provide one timestamped `SensorSnapshot` with RGB, depth, sensor timestamps, pose-source
   metadata, health, calibration ID, and measured inter-sensor skew. If the API cannot make
   acquisition atomic, quantify and gate the skew.
3. Record image dimensions, intrinsics/FOV, distortion, camera-body translation, yaw,
   pitch, roll, NED/Unreal/body conversions, and calibration hash.
4. Use Cosys annotation/segmentation/true pose only for evaluation labels and videos kept
   outside the autonomy process.
5. Produce tune and held-out test datasets across day/night, blur, glare, fog/rain, partial
   lens obstruction, small/thin obstacles, non-planar scenes, and different viewpoints.
   Include supported people/cars/trucks at near, medium and distant scales, negative frames
   with rocks/trees/buildings, occlusion, groups and civilian traffic. Freeze split IDs
   before Samik compares models; Pratik may not retune the held-out set after seeing results.
6. Export the scenario layer, settings, assets, calibration, base-environment identifier,
   world manifest, seeds, and smoke test.
   Samik must be able to reproduce it without editor-only state or machine-local paths.

### Pratik acceptance

- All five vehicles and sensors enumerate with exact expected names and rates.
- Every autonomy sensor sample is traceable to matching truth without exposing that truth.
- Calibration/frame round-trip tests pass and measured sensor skew stays within the mission
  bound or the snapshot is rejected.
- The single scenario runs from a clean start on Samik’s independent installation using
  Pratik’s written procedure.

## M4 — Samik deliverables: the autonomy engine

Samik’s deliverable is not “help Pratik with AirSim.” It is one concrete subsystem:

> **`AutonomyRunner`: given a signed mission and real sensor/estimator inputs, make the
> swarm cover the assigned area, replan safely, track targets, reassign failed work, and
> send only supervisor-released commands to Cosys/PX4.**

### Modules Samik owns

1. **Cosys vehicle/SITL adapter** — reproduce Pratik’s scenario on Samik’s PC; configure
   five named vehicles, pinned firmware/configuration,
   explicit NED/body/map conversions, command acknowledgement, telemetry, collision and
   health status. Start with SimpleFlight only for adapter smoke tests; the accepted
   GPS-denied gate uses the pinned PX4 SITL/VIO path if that is the selected flight stack.
2. **VIO integration** — camera + IMU into an actual VIO implementation, timestamp/frame
   conversion, covariance/health/reset output, and delivery to PX4’s external-vision path
   or the declared simulator controller. Cosys truth is comparison-only.
3. **Mission manager** — validates mission signature/hash/expiry, maintains mission state,
   dispatches tasks, records completion, and handles HOLD/recovery/abort.
4. **Coverage planner** — decomposes accepted traversable cells and produces deterministic
   coverage paths.
5. **Occupancy mapper** — consumes depth/LiDAR using Pratik’s calibration, marks unknown
   distinctly, timestamps updates, and exposes a versioned planning grid.
6. **Global planner** — A* route generation and deterministic replan on obstruction,
   task change, uncertainty change, or invalidated path.
7. **Waypoint follower** — consumes estimator state plus a versioned path and produces
   bounded candidate velocities with look-ahead, braking approach, dwell-based arrival,
   progress/stuck detection and bounded replan; it never adds a YOLO vector to a waypoint.
8. **Task allocator** — scored monotonic leases, heartbeat/health handling, deterministic
   tie-breaks, unfinished-cell return, and confirmation-drone assignment.
9. **Target tracker/fusion** — world-coordinate tracks, uncertainty, duplicate association,
   confirmation state, and alert lifecycle using validated detector outputs.
10. **Local safety filter** — candidate velocity generation and rejection against depth,
   map unknowns, geofence/no-fly areas, predicted peer separation, dynamics, braking,
   command continuity, and estimator uncertainty. Empty safe set means HOLD.
11. **Supervisor-only command sink** — clamps and expires commands, rejects non-finite or
    wrong-frame/repeated input, streams at the required offboard rate, and proves link-loss
    HOLD/LAND behavior. No planner or consensus code may call Cosys/PX4 directly.
12. **Campaign/model runner** — owns `tools/fetch_models.py`; validates all model/config
    hashes, cold-starts the system, waits for preflight,
    runs one named scenario, stops safely, resets all transient state, and gathers/hashes
    every artifact.

### Samik acceptance

- **A→B:** in an empty known corridor, the drone reaches the waypoint within the declared
  position and velocity tolerances for the required dwell and stops; it does not drift,
  oscillate, reuse a stale path or overshoot without a bound. The decision trace binds
  `PerceptionClaim`, `WaypointProposal`, path/map/estimator versions and released command.
- **Replanning:** a new obstacle invalidates the route, A* returns a different collision-free
  path, and the executed path matches the approved route/constraint log.
- **Unclassified geometry:** rocks, trees, walls and buildings that have no detector class
  still enter the map through depth/LiDAR and force a safe lateral route or a climb only
  when overhead clearance, altitude band and geofence all pass. Otherwise the result is
  HOLD/`NO_PATH`. “YOLO did not label it” can never mean free space.
- **No path:** a completely blocked corridor produces HOLD/`NO_PATH`, never traversal of
  unknown/occupied space.
- **GPS denied:** with GNSS removed from estimator input, the vehicle uses VIO; truth is used
  only to score error. Excess covariance forces degraded speed or HOLD.
- **Swarm:** five drones complete all required cells while preserving geofence and dynamic
  separation. Killing one returns its unfinished cells and reassigns them.
- **Safety:** stale/missing/rejected/unverified input, process death, offboard loss, or
  simulator pause cannot repeat the last motion command and causes the configured fail-safe.
- **Reproduction:** Pratik’s frozen scenario and the same run bundle work on both Pratik’s
  and Samik’s PCs; Samik fixes every undocumented dependency before accepting the handoff.

## Joint Pratik–Samik handoff contract

They share the environment but cannot work from verbal assumptions. The committed bundle
must contain:

- exact Cosys repository URL + commit/release + archive checksum;
- compatible Unreal version and project/plugin/build instructions;
- explicit `settings.json` path and hash;
- world/asset/calibration/sensor manifests and deterministic seed;
- vehicle names, sensor names, API endpoint/port, coordinate frames and units;
- PX4 firmware commit, parameters, vehicle IDs, UDP/TCP ports and failsafe settings if used;
- `SensorSnapshot` and `CommandSink` interface versions;
- one sensor smoke test, one command/TTL smoke test and one deterministic reset test;
- a clean-start script and an artifact collection script.

There is one scenario bundle owned by Pratik. Handoff is accepted only when Samik reproduces
it from Pratik’s written procedure and Samik’s `AutonomyRunner` completes the smoke mission
on both PCs without machine-specific paths or editor state.

## Execution sequence and gates

### Phase 0 — two days: interface and installation freeze

- **Suyash:** freeze schemas, frames, reason classes and conformance tests.
- **Pratik:** select/export the single prebuilt base plus scenario layer and one full sensor
  sample per vehicle; write Samik’s reproduction procedure.
- **Samik:** reproduce Pratik’s bundle, command one vehicle through a supervisor stub,
  prove expiry→HOLD, stage the two model candidates and reset deterministically.
- **Abhijan:** publish the first identity, replay, GNSS-loss/spoof, sensor-freeze and
  partition scenario manifests.

**Gate G0:** the pinned scenario cold-starts on both PCs from Pratik’s written procedure;
vehicle/sensor names, configuration hashes, candidate model hashes and smoke results agree.

### Phase 1 — single-drone autonomy

- Mission manager, sensor adapter, estimator interface, occupancy map, A*, explicit
  waypoint follower, local safety filter, command sink and decision record.
- Separate detector `PerceptionClaim` from `WaypointProposal`; bind both to the exact
  selected command in `AutonomyDecisionRecord`. Do not add their vectors.
- Demonstrate A→B, obstacle replan, blocked `NO_PATH`, TTL expiry and process-loss HOLD.
- In parallel, Pratik freezes the model-selection tune/test split; Samik benchmarks
  `yolov8n/s/m`; Suyash approves at most one candidate and records the decision. This
  perception subtask does not block geometric navigation work.

**Gate G1:** one drone completes a waypoint route using actual sensor-derived free space,
with dwell-based arrival, bounded overshoot/replan behavior, zero collision/geofence
violations and full traceability from mission/path/perception evidence to the exact released
command.

### Phase 2 — GPS-denied localization

- Integrate VIO from camera + IMU; configure external-vision input if PX4 is used.
- Test GNSS healthy→denied→reacquisition, gradual/step spoof, VIO reset/dropout and rising
  covariance. Truth is scorer-only.

**Gate G2:** the drone completes the declared GPS-denied route while estimator error and
uncertainty remain inside predeclared mission bounds; otherwise it degrades or holds safely.

### Phase 3 — five-drone mission autonomy

- Coverage decomposition, task leases, health/heartbeat, separation, failure reassignment,
  ground-sensor cueing and target confirmation.
- Formation geometry is preflighted only for transit/confirmation segments that use it.

**Gate G3:** all traversable required cells are completed, no task is silently lost after a
drone failure, and every separation/geofence/command invariant holds.

### Phase 4 — perception, tracking and VeriSwarm

- The signed-authority-approved detector, depth contradiction, world-coordinate tracks,
  confirmation state,
  peer receipts/votes and supervisor integration.
- Physical 3-D adversarial scene, one provenance attack, the versioned semantic-claim fix
  for the measured blind band, and the independent two-webcam co-visibility rig.

**Gate G4:** baseline targets are detected/tracked to the declared held-out threshold;
object-size sweeps no longer confuse “absent” with “moderate threat”; an unverifiable or
attacked decision cannot release motion; and healthy drones continue/recover the unfinished
mission safely.

### Phase 5 — adversarial and reliability campaign

- Run Abhijan’s full protocol, provenance, perception, navigation, mapping, C2 and
  availability matrix across the same frozen scenario/seed list on both PCs.
- Preserve all failures and investigate every cross-PC or cross-node disagreement.

**Gate G5:** zero protected collisions, geofence/minimum-separation violations, commands
after expiry, or unsafe releases. Every campaign either completes or enters its declared
safe terminal state.

### Phase 6 — freeze and evidence

- Freeze code, models, world, dependencies, mission, attacks and seeds.
- Run at least 30 randomized repetitions per safety-critical case while developing. Select
  final repetitions from the reliability claim: 299 independent zero-failure trials are
  needed before the one-sided 95% binomial upper bound falls below 1% per trial. This does
  not prove zero risk.
- Generate the runbook, evidence index, latency/resource distributions, localization error,
  detection/tracking metrics, coverage results, failure table and known limitations.

**Gate G6:** a teammate reproduces the full named mission from a clean start using archived
dependencies/assets and the committed instructions. No feature enters after this gate.

## Acceptance metrics

Declare numeric thresholds in the signed mission before the held-out run. At minimum log:

- percentage of required traversable cells covered and cells left unresolved;
- mission/sector completion time and path efficiency;
- collision count, geofence violations, minimum pairwise separation and braking-margin
  violations;
- A* success/no-path correctness and replan latency distribution;
- estimator mode, position/orientation error against isolated truth, covariance consistency,
  VIO resets and time in degraded/lost modes;
- detector precision/recall by supported class on held-out scenes, track continuity,
  localization error, confirmation latency and false-alert rate;
- task-allocation/reassignment latency and abandoned/duplicated tasks;
- end-to-end sensor→decision→command latency, command age, TTL violations and control rate;
- per-node CPU/GPU/memory/network use, queue/cache bounds and dropped records;
- security outcomes: unsafe accept, safe hold, false hold, equivocation/replay detection and
  honest-node result divergence.

Safety thresholds are derived from the mission envelope, not guessed globally. For example,
minimum separation and estimator-error bounds must be stricter than the available corridor
and geofence margins; p99 decision latency must stay comfortably below command TTL.

## Evidence required for every accepted run

Each immutable run bundle contains:

- run/scenario ID, operator, UTC time, PC identity, random seed and repository state;
- code, dependency, Cosys, Unreal, model/runtime, VIO, PX4, world, asset, calibration,
  settings, mission and authority hashes;
- node roster/key fingerprints, mission/epoch, task leases, quorum and all thresholds;
- raw hash-chained events, sensor metadata, estimator states, maps/routes, perception
  receipts, votes/certificates, tracks/alerts, decision records, commands/acknowledgements,
  PX4/Cosys telemetry, isolated truth, resource metrics and synchronized video;
- reset/start/stop results, operator interventions, unexpected behavior, collisions,
  invariant violations, crashes and dropped records;
- artifact index and checksums. A later success never overwrites a failure.

## Safe SIH demonstration

1. **Isolated control arm:** in a simulator-only process with no import/path to
   `MissionRunner`, `SafetySupervisor`, PX4 or the accepted command sink, run
   `baseline_controller.naive_action()` against the physical 3-D patch and allow the
   unprotected simulated vehicle to collide. Mark the screen **UNPROTECTED CONTROL —
   SIMULATION ONLY** and retain the failed outcome as experimental evidence.
2. **Protected autonomy baseline:** five drones accept sectors, cover the corridor and
   replan around a newly placed rock/tree/wall using depth-derived occupancy even though
   YOLO has no semantic class for it. Show mission progress, map/path changes and exact
   released commands.
3. **GPS denied:** disable/spoof GNSS for one drone. Show GNSS rejection, VIO mode,
   covariance, truth-only scoring and continued bounded navigation.
4. **Surveillance cue:** a ground sensor cues a region; one drone detects/tracks a person or
   vehicle and a second confirms it from another view.
5. **Attack:** Abhijan triggers a real replay, model/runtime swap, physical 3-D patch, or
   navigation fault. Show the delivered fault, reason, peer evidence and HOLD.
6. **Swarm recovery:** Samik kills/isolates the affected node; its unfinished cells are
   reassigned and the healthy fleet reaches the declared completion state.
7. **Physical co-visibility evidence:** on the separate Jetson/two-webcam rig, move the
   printed object/patch and show the real shared-scene evidence change. This rig never
   commands a vehicle and remains useful if the simulator is unavailable.
8. **Evidence reveal:** show the immutable run bundle and state the boundaries: simulation,
   supported object classes, VIO performance, no weapon engagement, and hardware work not
   yet completed.

Demo roles are fixed: **Samik operates autonomy/vehicles**, **Pratik operates the world and
sensor/truth view**, **Abhijan triggers and explains attacks**, and **Suyash explains
VeriSwarm/safety, owns abort, and answers claims/evidence questions**.

The isolated unprotected collision is the only permitted intentional collision. It is not
a protected campaign and must remain structurally unable to reach the accepted command
adapter. Every protected collision remains a gate failure.

## Independent physical co-visibility rig

This is non-flying hardware evidence, not a substitute for either Cosys scenario:

- two calibrated USB webcams view the same supported object from different angles;
- the Jetson runs the existing `protocol/covis_features.py` through
  `tools/covis_live.py`; the algorithm is called, not copied or forked;
- Abhijan owns the actual printed patch/object, lighting/distance/viewpoint attack matrix
  and choreography; Suyash owns protocol integration and result validation;
- raw frames, calibration, timestamps, input hashes, overlap/match/inlier results and video
  are retained;
- the rig has no path to flight control and can be demonstrated independently of Cosys.

### Sequential Jetson handover and OP-TEE receipt signing

The Jetson is time-multiplexed; it is not expected to run the two-webcam workload and the
simulated Alpha node concurrently:

1. **Webcam stage:** run only `tools/covis_live.py`, retain the real-camera evidence, then
   stop it cleanly and verify both camera devices are released.
2. **Handover gate:** hash/close the webcam artifact, record `WEBCAM_STAGE_COMPLETE`, verify
   the pinned OP-TEE public key with a fresh random sign/verify challenge, and measure the
   end-to-end signing deadline with `tools/optee_preflight.py` before enabling Alpha. The
   evidence path is create-once; a mismatch, timeout, invalid signature or attempt to
   overwrite earlier evidence fails the gate.
3. **Swarm stage:** start `node.server` for `alpha` on the Jetson with
   `OPTEEReceiptSigner`. Every Alpha-originated receipt and Alpha peer vote is signed over
   its full canonical bytes inside OP-TEE. Run Alpha's `node.mission`/`Originator` process
   on the Jetson too: the current `Originator` constructs its signer locally, so launching
   `--id alpha` on L1 would look for `/dev/tee0` on L1 and fail. Samik may operate the
   mission over SSH, but the process and signing call execute on the Jetson. The other four
   nodes retain their own distinct development keys.
4. **Failure rule:** unavailable/late OP-TEE signing means no accepted Alpha receipt and
   therefore HOLD/abstention/task reassignment under the mission policy. Never fall back
   silently to Alpha's software key.

Do not route all five node identities through the current single-key Trusted Application.
That would centralize the protocol, remove meaningful per-node key isolation and create a
single signing bottleneck. An all-TEE remote signer is a later architecture only if it has
distinct protected per-node keys, mutually authenticated clients, TA-enforced identity
binding, anti-replay/sequence state, bounded queues/rate limits and a tested unavailable-
signer safety policy.

The accepted claim remains narrow: OP-TEE protects Alpha's Ed25519 private key and signs
Alpha's canonical receipt/vote bytes. It does not attest that remote inference, model
loading, pose estimation or the other nodes executed inside secure world.

`node.mission` is a library, not a CLI. The old topology command
`python -m node.mission --id alpha ...` was therefore invalid as well as placed on the
wrong host. Samik's real `tools/run_campaign.py` entry point is a blocking deliverable: it
must construct `MissionRunner`/`Originator` on the Jetson, connect the real Cosys source,
autonomy stack, supervisor and command sink, and refuse startup until the OP-TEE evidence
and live signer/manifest public-key binding pass. `node.run_phaseb` remains only a local
hardware-key protocol regression, not Cosys mission evidence.

## Real-world transfer boundary

The architecture is designed to transfer, but simulator success does not prove flight
safety. Before real flight add HIL with the exact flight computer, autopilot, cameras,
depth/LiDAR/thermal sensors and power system; calibrated estimator and stopping envelopes;
secure boot/update/rollback; per-device keys and key rotation; mutually authenticated links
and MAVLink 2 signing; independent geofence/collision barrier; safety pilot and physical
kill; legal/regulatory approval; environmental/EMI/thermal/vibration testing; hazard log;
and witnessed recovery tests.

Follow `codebase/SIMULATION_AND_FLIGHT_GATES.md`. No propeller-on or outdoor flight begins
before S0–S3-equivalent simulation, H0, an independent safety review and written risk
acceptance are complete.

## Authoritative implementation references

- [Cosys-AirSim repository](https://github.com/Cosys-Lab/Cosys-AirSim)
- [Cosys-AirSim settings and coordinate/sensor configuration](https://github.com/Cosys-Lab/Cosys-AirSim/blob/main/docs/settings.md)
- [PX4 visual-inertial odometry integration](https://docs.px4.io/main/en/computer_vision/visual_inertial_odometry)
- [PX4 safety and offboard-loss configuration](https://docs.px4.io/main/en/config/safety)
