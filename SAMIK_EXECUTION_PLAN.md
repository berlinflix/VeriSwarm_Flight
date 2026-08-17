# Samik — standalone execution plan

**Role:** M4, autonomy/runtime owner  
**Primary outcome:** make the five simulated drones estimate, decide, coordinate and move
inside Pratik's frozen Cosys scenario, while ensuring that only supervisor-released,
fresh and bounded commands can reach Cosys/PX4.

This document contains Samik's work only. References to another owner identify an input or
review dependency; they do not transfer that person's task to Samik.

## 1. Non-negotiable boundaries

- Work against the single scenario **Contested Border Sentinel**. Do not create a second
  Unreal world.
- Do not use simulator truth, segmentation IDs or object IDs as autonomy input. Truth is
  evaluation-only.
- Do not call Cosys/PX4 directly from a planner, detector, consensus component or UI. The
  only motion path is `AutonomyRunner → SafetySupervisor → CommandSink → Cosys/PX4`.
- Do not add attacks until the clean control path is repeatable.
- Do not silently substitute HOLD for mission success. HOLD is a safe terminal/degraded
  state whose unfinished work must be declared and, when possible, reassigned.
- Do not implement a generic remote Jetson signing endpoint. In this simulation Alpha's
  `MissionRunner`/`Originator` executes on the Jetson after the webcam handover and signs
  locally through OP-TEE. Samik may operate it over SSH.
- Do not claim that OP-TEE attests inference. It protects Alpha's signing key only.
- Do not hard-code PC addresses, vehicle names, coordinate offsets, model paths or mission
  thresholds. Load them from validated manifests/CLI arguments.

## 2. Inputs Samik must receive before integration

Reject an incomplete handoff rather than guessing. Required inputs are:

1. Pinned Cosys-AirSim repository/release, Unreal version, Python client version, vehicle
   firmware/controller mode and archive/checksum.
2. Explicit `settings.json` path and the exact output of `client.listVehicles()`.
3. Five case-sensitive vehicle names: `alpha`, `bravo`, `charlie`, `delta`, `echo`.
4. Vehicle type, initial pose, collision state and enabled sensor list for every vehicle.
5. Clear A/B smoke-flight points in Cosys NED metres, permitted altitude band, geofence,
   maximum speed/acceleration envelope and goal tolerance.
6. Camera/depth/LiDAR/IMU/barometer/GNSS calibration, rates, units, coordinate frames and
   measured inter-sensor skew.
7. Attack-free scenario mode, deterministic reset procedure and at least one frozen
   sensor sample per vehicle.
8. Frozen tune/held-out perception split manifest and data hashes.
9. Signed mission/authority material and reason-code/interface contracts.

Record missing items in `codebase/results/handoff/samik_pratik_gap_report.json`; do not
bury workarounds in code.

## 3. Files and modules Samik owns

| Path | Required purpose |
|---|---|
| `codebase/sim/cosys_smoke_flight.py` | Minimal, attack-free flight-state-machine test |
| `codebase/sim/cosys_adapter.py` | Sensor source, state conversion and supervisor-only command sink |
| `codebase/tools/run_campaign.py` | Checked startup, execution, shutdown, reset and evidence orchestration |
| `codebase/tools/fetch_models.py` | Registry-driven, hash-verified candidate acquisition |
| `codebase/eval/model_selection.py` | Identical benchmark for `yolov8n/s/m` |
| `codebase/autonomy/mission_manager.py` | Signed mission lifecycle and terminal-state handling |
| `codebase/autonomy/state_estimator.py` | Estimator/VIO health and uncertainty interface |
| `codebase/autonomy/mapper.py` | Timestamped occupied/free/unknown representation |
| `codebase/autonomy/planner.py` | Coverage and deterministic A* route/replan logic |
| `codebase/autonomy/waypoint_follower.py` | Convert a validated path into bounded mission-progress velocity candidates |
| `codebase/autonomy/task_allocator.py` | Task leases, heartbeats, reassignment and tie-breaking |
| `codebase/autonomy/tracker.py` | World-coordinate tracks, association and confirmation state |
| `codebase/autonomy/local_safety.py` | Candidate-command filtering against all motion constraints |
| `codebase/tests/` | Unit, adapter-contract, failure and end-to-end tests for all owned modules |

## 4. Ordered implementation plan

### S0 — freeze and reproduce the simulator

1. Install the exact pinned Cosys/Unreal/client combination on Samik's PC.
2. Launch with an explicit settings path; record resolved paths and version outputs.
3. Reproduce Pratik's scenario from a clean checkout/export without editor-only state.
4. Connect a read-only diagnostic client, run `ping()`, enumerate vehicles/sensors and
   compare every value with the handoff manifest.
5. Record coordinate probes that prove Cosys NED ↔ internal map/body conversions, including
   the negative-NED-z altitude convention.
6. Implement deterministic stop/reset verification. A reset must clear mission sequences,
   task leases, maps, tracks, command queues and transient attack state.

**Gate S0:** both PCs enumerate identical names/configuration hashes and produce matching
reset-state manifests. No flight is attempted while this differs.

### S1 — make one drone fly safely before adding autonomy

Implement `cosys_smoke_flight.py` with parameters for host, port, vehicle, A/B, speed,
timeouts, altitude band and evidence directory. Execute this state machine:

```text
CONNECT → validate roster → enable API control → verify control
→ arm → take off → stable hover → move A→B
→ verify position/velocity/collision → hover → land → disarm → release API control
```

Requirements:

- Check and log every transition; never assume an asynchronous command succeeded.
- Bound every wait. On timeout, invalid/non-finite state, collision, geofence departure or
  control loss, issue the safest available hover/land action, record failure and stop.
- Treat `reset()` as stopped-run cleanup, never as an in-flight recovery policy.
- F0: connection and roster; F1: vertical takeoff/hover/land; F2: Alpha A→B; F3: five
  vehicles with sequential takeoff/landing and distinct `B_i` goals.
- Never send all vehicles to one coordinate.

**Gate S1:** three cold Alpha A→B repetitions and the declared five-drone movement test
complete with zero collision, geofence and minimum-separation violations. Retain telemetry,
commands, acknowledgements, video, settings and hashes for every repetition.

### S2 — implement the real Cosys adapter

`cosys_adapter.py` must expose explicit interfaces rather than leaking the vendor client
through the project:

- `connect/close`, `health`, `vehicle_state`, `sensor_snapshot`, `collision_state`;
- RGB plus metric depth/LiDAR with timestamps and calibration ID;
- coordinate conversions with round-trip tests;
- a `CommandSink` accepting only the reviewed internal command type and authorization;
- command acknowledgement, age, expiry and actual released command;
- HOLD/LAND/watchdog behavior on stale commands, API loss, simulator pause or process loss.

The sink must reject unknown vehicle IDs, wrong frames, NaN/Inf, out-of-bounds velocity,
acceleration discontinuity, repeated/expired commands, missing authorization, unhealthy
estimation and unavailable clearance. It must be impossible to obtain the raw actuator
client from a planner object.

**Gate S2:** contract tests prove direct/unverified motion is unreachable and every invalid
case produces a logged safe result. Run the S1 route through this adapter, not through a
direct smoke command.

### S3 — implement the protected waypoint-follower MVP

This is the missing layer that makes “continue to B” real. It is the next functional
product milestone after the command adapter and must not be hidden inside YOLO or the Cosys
client.

#### S3.1 Module boundary

Implement `codebase/autonomy/waypoint_follower.py` as a deterministic, simulator-independent
module. It must not import Cosys, PX4, YOLO, the attack runner or the command sink.

Its validated input contains:

- mission ID/epoch and current monotonic time;
- one explicit command frame;
- current estimator pose, velocity, covariance/uncertainty, timestamp and health;
- path ID/version, map version and ordered 3-D waypoints;
- current monotonic waypoint index;
- geofence/altitude and vehicle speed/acceleration/braking limits from the signed mission;
- previous released command and timestamp;
- configured arrival position/velocity tolerances, dwell time, progress window and maximum
  bounded replan attempts.

Its output is a `WaypointProposal`, not an actuator command. It contains:

- status: `TRACKING`, `ARRIVED`, `REPLAN_REQUIRED`, `HOLD_INVALID_PATH`,
  `HOLD_STALE_STATE`, `HOLD_LOCALIZATION`, or `ABORTED`;
- active waypoint index and look-ahead point;
- a bounded, deterministically ordered set of candidate map-frame velocities, always
  including HOLD;
- along-track/cross-track error, distance remaining and progress since the last window;
- path/map/estimator versions and reason code;
- triggers requesting replan, task completion or mission abort.

The follower never returns “safe.” Only the local safety filter and supervisor can make
that determination.

#### S3.2 Deterministic following algorithm

For each control cycle:

1. Validate mission/epoch, frames, finite values, estimator health/age, path/map versions,
   nonempty route, waypoint bounds and all limits. Invalid or stale input returns HOLD with
   a reason; it never reuses the last nonzero proposal.
2. Project the estimated position onto the current path segment and compute along-track and
   cross-track error. Waypoint index is monotonic; estimator noise cannot move ownership
   backwards without a versioned replan.
3. Choose a bounded look-ahead point on the validated path. Clamp look-ahead using current
   speed, braking capability, path curvature, map resolution and estimator uncertainty.
4. Compute the nominal direction toward that point in the declared map frame.
5. Schedule speed using remaining stopping distance, curvature, uncertainty and signed
   mission limits. Close to the goal, reduce speed so the vehicle can stop inside the
   arrival envelope; never fly full speed until the position merely crosses B.
6. Generate deterministic alternatives around the nominal request: slower versions and
   bounded lateral/vertical candidates allowed by the path/mission. Do not assert that any
   alternative is collision-free.
7. Pass candidates plus their exact path/estimator/map versions to `local_safety.py`.
8. The local safety filter rejects candidates violating current depth/LiDAR clearance,
   occupied/unknown space, braking, overhead clearance, altitude, geofence, peer
   separation, dynamics, continuity or uncertainty.
9. Select the surviving candidate with lowest deterministic cost: first invariant safety,
   then mission progress, path deviation, smoothness and energy. If none survives, HOLD
   and request replan where appropriate.
10. Declare a waypoint reached only when position **and** speed remain inside the configured
    arrival envelope for the configured dwell time. Then advance exactly one index.
11. Declare final `ARRIVED` only after the final waypoint satisfies the same dwell rule;
    request HOLD/hover before land/next task.
12. If progress remains below the configured bound for its window while commands were
    actually released, request a versioned replan. After bounded failed replans, return
    HOLD/`NO_PATH`; never oscillate indefinitely.

All tolerances come from the signed mission/vehicle envelope. Do not invent universal
numbers in source code.

#### S3.3 Exact perception/waypoint combination rule

Do **not** calculate `waypoint_velocity + YOLO_avoidance_velocity`.

The integration rule is:

1. The signed mission, estimator and current A*/coverage path create mission intent.
2. The waypoint follower creates candidate motion requests toward the next path point.
3. Depth/LiDAR, occupancy, geofence, dynamics and peer state remove geometrically unsafe
   candidates.
4. Semantic perception receipts/certificates establish whether the current scene evidence
   is trustworthy and may create mission constraints, alerts or replans. They are not a
   second velocity vector.
5. `AutonomyDecisionRecord` binds the mission/task, path/map/estimator versions,
   perception-certificate IDs, rejected constraints and exact selected candidate.
6. `SafetySupervisor` evaluates that exact selected command, its current evidence and TTL.
7. Only the supervisor-released command reaches `CommandSink`.

The current `node.mission.MissionRunner` calls `inference(frame)` and treats that result as
the requested action. Before the protected waypoint gate, refactor this seam so detector
output becomes a versioned `PerceptionClaim` and waypoint output becomes the separate
candidate command. Do not disguise a waypoint velocity as detector output, and do not let
an accepted perception receipt automatically authorize an unrelated command. Suyash owns
the versioned contract; Samik owns its implementation in `AutonomyRunner`.

#### S3.4 Fast implementation sequence

1. Implement pure waypoint/path/estimator dataclasses and validation.
2. Implement straight-line and multi-segment following with HOLD/arrival/stuck states.
3. Add look-ahead, braking-based speed schedule and monotonic waypoint advancement.
4. Add deterministic candidate generation and integrate `local_safety.py`.
5. Refactor `MissionRunner`/`AutonomyRunner` to separate `PerceptionClaim` from
   `WaypointProposal` and bind both in `AutonomyDecisionRecord`.
6. Connect the selected proposal through `SafetySupervisor → CommandSink`.
7. Run an empty-corridor A→B route, then obstacle/replan, then five distinct routes.

#### S3.5 Required waypoint tests and gates

Unit tests must cover straight path, multi-segment path, altitude change, noisy cross-track
pose, slow approach, overshoot prevention, dwell-based arrival, stale pose, unhealthy
localization, wrong frame, empty/changed path, non-finite data, index monotonicity, command
continuity, no progress, bounded replans and no safe candidate.

Integration tests must prove:

- **W0:** the pure follower reaches terminal `ARRIVED` in deterministic kinematic replay;
- **W1:** one Cosys drone follows A→B through `SafetySupervisor → CommandSink`, stops inside
  the declared position/velocity envelope and never uses direct `moveToPositionAsync`;
- **W2:** a new obstacle invalidates the path, causes a versioned A* replan and produces a
  different safe executed route; full blockage yields HOLD/`NO_PATH`;
- **W3:** rejected/stale/missing perception evidence, expired command, estimator loss or
  map uncertainty cannot release the selected waypoint command;
- **W4:** five vehicles follow separate paths to distinct `B_i` goals while maintaining
  the signed separation envelope.

**Gate S3:** W0–W3 pass from three cold resets with complete decision/command evidence.
Until then, the claim “the protected drone autonomously continues to B” is prohibited.

### S4 — implement the campaign runner and Alpha placement

Build `tools/run_campaign.py`; do not add a CLI to `node.mission` merely to hide missing
integration. The runner must:

1. Load and validate scenario, mission, node, model, runtime and authority manifests.
2. Verify repository state and every configuration/artifact hash.
3. Confirm the required processes/ports are absent before startup.
4. Start collector/peers in dependency order and wait for bounded readiness.
5. For Alpha, run the originator portion on the Jetson after the webcam handover.
6. Require a fresh create-once `tools/optee_preflight.py` evidence record whose node,
   mission/epoch, pinned key, TA/CA hashes and result match the run.
7. Refuse Alpha software fallback. OP-TEE unavailability makes Alpha unavailable; mission
   policy decides HOLD/reassignment.
8. Start the named clean or attack campaign with a deterministic seed.
9. Stop motion before stopping verification/telemetry processes.
10. Land/disarm/release control, close logs, hash artifacts and verify cleanup.

Provide `--dry-run`, `--preflight-only`, `--scenario`, `--seed`, `--attack-manifest`,
`--evidence-dir`, `--host` and explicit node-selection options. A dry run may inspect but
must not arm.

**Gate S4:** the runner cold-starts and safely terminates clean, OP-TEE-unavailable,
peer-missing, Cosys-missing and Ctrl-C cases without orphan motion or overwritten evidence.

### S5 — state estimation and GPS-denied navigation

1. Select and pin one VIO implementation/configuration; record camera/IMU calibration and
   timestamp requirements.
2. Publish estimator pose, velocity, orientation, covariance, mode, last-update age, reset
   count and health—never only a pose tuple.
3. Feed the estimator/PX4 external-vision path with explicit frame conversion.
4. Implement GNSS consistency monitoring and declared healthy→denied→reacquisition modes.
5. Use simulator truth only in the isolated scorer.
6. Reduce speed or HOLD when uncertainty exceeds signed mission bounds; never mask VIO
   loss with true Cosys pose.

**Gate S5:** healthy, GNSS-loss, gradual spoof, step spoof, VIO drift/reset/dropout and
timestamp-skew tests produce the declared modes and safe behavior. The GPS-denied route
passes its predeclared error/uncertainty envelope or ends safely.

### S6 — mapping, planning and local safety

1. Convert depth/LiDAR into a time-bounded map with distinct free, occupied and unknown
   states.
2. Include rocks, trees, walls and buildings whether or not YOLO labels them.
3. Generate deterministic coverage cells and A* routes through known traversable space.
4. Replan when obstacles, task ownership, map version or estimator uncertainty changes.
5. Build candidate velocities, then reject any candidate violating braking distance,
   current depth, occupied/unknown space, overhead clearance, altitude band, geofence,
   dynamics, peer separation, continuity or estimator limits.
6. Permit a climb only when upward clearance, altitude and geofence all pass; otherwise use
   a lateral route or HOLD/`NO_PATH`.

**Gate S6:** clear route, new obstacle, moving obstacle, thin/small obstacle, stale map,
fully blocked corridor and planner-timeout cases all match their invariant. No-path never
becomes forward motion.

### S7 — swarm mission autonomy

1. Decompose the border corridor into versioned tasks/cells.
2. Allocate deterministic leases using health, energy reserve, distance and capability.
3. Maintain monotonic lease versions, expiry and deterministic tie-breaks.
4. Return unfinished work after node/link/estimator failure.
5. Assign a second-view confirmation task without creating a collision convergence point.
6. Maintain predicted peer trajectories and dynamic separation in the local safety layer.
7. Use distinct launch/rally positions and sequential takeoff/landing.
8. Complete surveillance, GPS-denied, attack and SAR-diversion checkpoints inside the one
   scenario.

**Gate S7:** killing or isolating one node returns its unfinished tasks, a healthy vehicle
completes them, all required traversable cells reach a declared terminal state and no
collision/geofence/separation invariant fails.

### S8 — perception model acquisition, tracking and alerts

1. Implement registry-only staged acquisition for official `yolov8s.pt` and `yolov8m.pt`.
2. Never auto-download during an accepted run; a missing or mismatched file fails startup.
3. Benchmark `yolov8n/s/m` on identical frozen frames, preprocessing, thresholds and
   target hardware. Record class-wise metrics and p50/p95/p99 latency/resource use.
4. Provision only the independently approved winner at
   `models/approved/rgb_detector.pt`; hash the bytes actually loaded.
5. Implement deterministic Kalman/ByteTrack-style association, world projection with
   uncertainty, cross-view confirmation and alert lifecycle.
6. Make only validated person/common-vehicle claims unless a separately approved domain
   model passes its own evaluation.

**Gate S8:** selected model/runtime fits the command deadline and held-out thresholds;
tracks survive declared occlusion; duplicate views fuse; false cues do not directly become
confirmed alerts.

### S9 — adversarial and reliability integration

Expose only reviewed, named fault-injection boundaries. For every attack manifest:

- validate schema, seed, target, start/duration, stop condition and cleanup;
- retain the real delivered fault and pre/post state;
- prevent the injector from setting verdict, expected output or released command;
- prove attack removal/restoration before the next run;
- retain failures; never overwrite them with a later pass.

Run clean baseline before each attack family, then model swap, perception, navigation,
mapping, C2 and availability campaigns. Repeat on Pratik's and Samik's installations.

**Gate S9:** zero unsafe release, protected collision, geofence or separation violation in
the declared campaign; every run completes or enters its declared safe terminal state.

## 5. Test obligations

Samik must add tests for:

- coordinate/frame round trips and unit signs;
- API-control loss at every flight-state transition;
- stale, repeated, malformed and non-finite command inputs;
- TTL expiry during consensus and during command transport;
- collision/geofence/separation/watchdog behavior;
- VIO uncertainty, reset, dropout and GNSS-mode transitions;
- occupied/free/unknown map semantics and stale-map rejection;
- waypoint validation, progress, look-ahead, braking approach, arrival dwell, overshoot,
  stuck/replan and candidate ordering;
- separation of `PerceptionClaim` from `WaypointProposal`, including proof that an accepted
  perception certificate cannot authorize a different/unbound command;
- A* replan/no-path/timeout determinism;
- lease replay/conflict/expiry and node death;
- process startup rollback and cleanup idempotence;
- model registry/hash mismatch and missing offline artifact;
- Alpha OP-TEE preflight missing, stale, wrong mission, wrong key and signer loss.

Before handing over a gate, run:

```text
python -m pytest -q
python -m sim.closed_loop
```

Then run the gate-specific Cosys campaign from a cold simulator start.

## 6. Evidence Samik must retain

Every run bundle must include run ID, UTC time, operator/machine, Git state, dependency and
configuration hashes, scenario/seed, vehicle roster, estimator modes/covariance, maps,
routes, leases, tracks, receipts/votes, supervisor decisions, requested/released commands,
acknowledgements, collision/geofence/separation state, resource metrics, video, reset
results, exceptions and an artifact checksum index.

An accepted run cannot contain an unexplained process restart, manual piloting, simulator
teleport, truth leak, overwritten failure or undocumented setting.

## 7. Samik's immediate work queue

1. Obtain and validate Pratik's first-flight handoff.
2. Deliver S0 reproduction report.
3. Implement `cosys_smoke_flight.py` and pass F0–F3.
4. Implement `cosys_adapter.py` and supervisor-only movement.
5. Implement `waypoint_follower.py`, the perception/command separation and pass W0–W3.
6. Implement `run_campaign.py`, with the Alpha originator executing on the Jetson.
7. Integrate state estimator/VIO.
8. Complete map, A*, local safety and no-path behavior.
9. Implement task allocation/reassignment and tracking.
10. Benchmark the two candidate models.
11. Integrate Abhijan's attacks only after clean gates pass.

## 8. Definition of done for Samik

Samik is done only when the frozen mission starts cleanly on both simulator PCs; Alpha's
originator runs on the post-webcam Jetson with verified OP-TEE signing; five vehicles
complete or safely terminate their assigned work; geometric obstacles cause safe replan;
the waypoint follower advances through versioned paths and reaches goals without blindly
adding perception vectors or bypassing safety;
GPS-denied operation uses measured VIO rather than truth; failed tasks are reassigned;
supported targets become evidence-backed alerts; every command passes the supervisor and
adapter; all declared attacks fail safely; and a teammate reproduces the run from the final
runbook without Samik's verbal intervention.
