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

### S3 — implement the campaign runner and Alpha placement

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

**Gate S3:** the runner cold-starts and safely terminates clean, OP-TEE-unavailable,
peer-missing, Cosys-missing and Ctrl-C cases without orphan motion or overwritten evidence.

### S4 — state estimation and GPS-denied navigation

1. Select and pin one VIO implementation/configuration; record camera/IMU calibration and
   timestamp requirements.
2. Publish estimator pose, velocity, orientation, covariance, mode, last-update age, reset
   count and health—never only a pose tuple.
3. Feed the estimator/PX4 external-vision path with explicit frame conversion.
4. Implement GNSS consistency monitoring and declared healthy→denied→reacquisition modes.
5. Use simulator truth only in the isolated scorer.
6. Reduce speed or HOLD when uncertainty exceeds signed mission bounds; never mask VIO
   loss with true Cosys pose.

**Gate S4:** healthy, GNSS-loss, gradual spoof, step spoof, VIO drift/reset/dropout and
timestamp-skew tests produce the declared modes and safe behavior. The GPS-denied route
passes its predeclared error/uncertainty envelope or ends safely.

### S5 — mapping, planning and local safety

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

**Gate S5:** clear route, new obstacle, moving obstacle, thin/small obstacle, stale map,
fully blocked corridor and planner-timeout cases all match their invariant. No-path never
becomes forward motion.

### S6 — swarm mission autonomy

1. Decompose the border corridor into versioned tasks/cells.
2. Allocate deterministic leases using health, energy reserve, distance and capability.
3. Maintain monotonic lease versions, expiry and deterministic tie-breaks.
4. Return unfinished work after node/link/estimator failure.
5. Assign a second-view confirmation task without creating a collision convergence point.
6. Maintain predicted peer trajectories and dynamic separation in the local safety layer.
7. Use distinct launch/rally positions and sequential takeoff/landing.
8. Complete surveillance, GPS-denied, attack and SAR-diversion checkpoints inside the one
   scenario.

**Gate S6:** killing or isolating one node returns its unfinished tasks, a healthy vehicle
completes them, all required traversable cells reach a declared terminal state and no
collision/geofence/separation invariant fails.

### S7 — perception model acquisition, tracking and alerts

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

**Gate S7:** selected model/runtime fits the command deadline and held-out thresholds;
tracks survive declared occlusion; duplicate views fuse; false cues do not directly become
confirmed alerts.

### S8 — adversarial and reliability integration

Expose only reviewed, named fault-injection boundaries. For every attack manifest:

- validate schema, seed, target, start/duration, stop condition and cleanup;
- retain the real delivered fault and pre/post state;
- prevent the injector from setting verdict, expected output or released command;
- prove attack removal/restoration before the next run;
- retain failures; never overwrite them with a later pass.

Run clean baseline before each attack family, then model swap, perception, navigation,
mapping, C2 and availability campaigns. Repeat on Pratik's and Samik's installations.

**Gate S8:** zero unsafe release, protected collision, geofence or separation violation in
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
5. Implement `run_campaign.py`, with the Alpha originator executing on the Jetson.
6. Integrate state estimator/VIO.
7. Implement map, A*, local safety and no-path behavior.
8. Implement task allocation/reassignment and tracking.
9. Benchmark the two candidate models.
10. Integrate Abhijan's attacks only after clean gates pass.

## 8. Definition of done for Samik

Samik is done only when the frozen mission starts cleanly on both simulator PCs; Alpha's
originator runs on the post-webcam Jetson with verified OP-TEE signing; five vehicles
complete or safely terminate their assigned work; geometric obstacles cause safe replan;
GPS-denied operation uses measured VIO rather than truth; failed tasks are reassigned;
supported targets become evidence-backed alerts; every command passes the supervisor and
adapter; all declared attacks fail safely; and a teammate reproduces the run from the final
runbook without Samik's verbal intervention.
