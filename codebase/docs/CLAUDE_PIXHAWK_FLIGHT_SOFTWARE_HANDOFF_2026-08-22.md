# VeriSwarm current-state handoff for Claude — Pixhawk/PX4 flight software

**Prepared:** 2026-08-22 IST
**Owner:** Suyash
**Authoritative integration branch:** `suyash/sih26177-rescue-integration`
**Scope:** debug and implement flight software in simulation/SITL first. Do not connect
this repository to physical motors from this handoff.

This is the single current-state handoff. Treat older plan/status files as historical
unless this file explicitly points to them. Resolve the exact checked-out handoff commit
with `git rev-parse HEAD`; do not assume that a short hash copied from chat is current.

## 1. Objective

Build and debug a fail-closed Pixhawk/PX4 command adapter that connects VeriSwarm's
tested decision pipeline to PX4 SITL, and later to propeller-free HITL. The adapter must
never let perception, consensus, a dashboard, or a planner issue MAVLink commands
directly.

Required command path:

```text
camera/depth/pose/health snapshot
              |
              v
        MissionRunner
              |
  signed receipt + semantic consensus
              |
              v
       SafetySupervisor
              |
  fresh bounded authorization + command
              |
              v
       PixhawkCommandSink       <-- Claude's primary owned work
              |
  frame conversion + rate/limit/watchdog + PX4 mode/failsafe checks
              |
              v
           PX4 SITL
```

`PixhawkCommandSink` is a working name, not an existing implementation.

## 2. Repository truth at handoff

| Area | Current state | Evidence / code |
|---|---|---|
| Receipt signing and verification | Implemented and tested. Alpha has an OP-TEE signing experiment; other identities are software-backed. | `protocol/`, `signing/`, `optee/` |
| Semantic quorum and fail-closed authorization | Implemented as an experimental signed quorum/tally, not formally proven Byzantine agreement. | `protocol/peer_consensus.py`, `perception/safety_supervisor.py` |
| Mission integration seam | Implemented for deterministic replay. It calls only a supplied `command_sink`. | `node/mission.py` |
| Normalized action frame | Frozen as `BODY_FLU_NORMALIZED_VELOCITY`: `(forward, left, up)`, each in `[-1,1]`. | `protocol/receipts.py`, `perception/yolo_action.py` |
| Range/stopping check | Implemented, fail-closed on missing/invalid forward range. Defaults are research values and must be replaced by measured aircraft limits. | `perception/depth_check.py` |
| Authorization-aware movement gate | Implemented and tested; returns RELEASE/HOVER/ABORT instructions but does not talk to PX4. | `rescue/movement_security.py`, `sim/cosys/factorycity/movement_v2.py` |
| Sensor-driven avoidance core | Implemented for measured left/right/up selection, real hold-before-deflect, route rejoin, sticky block/collision and durable cell events. | `sim/cosys/factorycity/movement_v2.py` |
| Five-drone CoSys nominal mission | Implemented for SimpleFlight/CoSys. It is simulator-specific and is not a PX4 adapter. | `sim/cosys/factorycity/tools/run_factorycity_ab_swarm.py` |
| Five-drone cell dashboard | Implemented with exact current-owner validation and conflict display. | `c2_dashboard/src/cellMissionView.js` |
| PX4 pose probe | Exists as a measurement utility only. | `sim/pose_probe.py` |
| PX4 multi-drone script | Exists as an old Gazebo co-visibility experiment. It directly arms/takes off/lands and bypasses `MissionRunner`/`SafetySupervisor`; it must not be promoted into the flight adapter. | `sim/multi_drone.py` |
| Real Pixhawk command sink | **Not implemented.** | Open P0 |
| PX4 release, airframe, parameter archive and hardware target | **Not frozen.** | Open P0 |
| PX4 SITL closed-loop VeriSwarm campaign | **Not run.** | Open P0 |
| HITL | **Not started.** | Open P1 after SITL |
| Physical restrained flight | **Prohibited by the current project gate.** | `SIMULATION_AND_FLIGHT_GATES.md` |

Latest fetched owner refs when this handoff was prepared:

- Suyash integration parent before this handoff: `57a6222`.
- Pratik CoSys branch: `deafa38`; its landing changes are merged here and corrected
  further in the commit containing this handoff.
- Abhijan security/dashboard branch: `f7db83a`; integrated here.
- Samik perception/training branch: `5a5d875`; not merged into this flight handoff.
- Main: `d67a801`; it is behind the integration branch.

Always run `git fetch --all --prune` and inspect the refs again before assuming these are
still tips.

## 3. Corrections already made in the Suyash integration

The movement review fixed these concrete defects:

- safety evidence could say HOLD while releasing a deflection in the same cycle;
- BLOCKED and COLLIDED states could later resume on a clean sample;
- invalid directional depth could be interpreted as free space;
- no upward escape existed;
- deflection attempts and cross-track/vertical projections were not bounded correctly;
- all drones reused one sector key, overwriting projection state;
- stale/historical ownership was accepted after reassignment;
- transition-to-durable-enqueue latency was not checked end to end;
- authority JSON hashes were not portable across LF/CRLF checkouts.

The latest CoSys rooftop landing review additionally fixed:

- a below-surface `moveToZAsync` was joined before contact monitoring began;
- contact now overrides the active descent with hover before the controller can keep
  pushing into the roof;
- only a new collision with the exact configured landing surface is accepted;
- position, full linear/angular speed, roll/pitch, landing-zone radius and penetration
  depth are checked;
- separation remains monitored throughout landing;
- disarm must return `True`;
- stable contact must persist before and after disarm;
- the known stale CoSys `landed_state` exception can be used only when the scenario
  carries an explicit approval; `QB-LANDED-STATE-001` is scoped to its frozen Blocks
  qualification and is deliberately **not approved** for FactoryCity;
- successful use of that exception is labelled
  `PASS_WITH_APPROVED_SIMULATOR_DEVIATION`, never plain PASS.

`StaticMeshActor_0` is the currently observed FactoryCity landing collision object, not
a portable identity. Re-observe and freeze the landing object for every rebuilt map;
an object-name mismatch must fail the run rather than broaden the allow-list.

These corrections have unit/configuration evidence. They are not retained live CoSys or
PX4 flight evidence.

## 4. Critical gaps Claude must not paper over

### P0.1 — hardware and firmware authority is unknown

Before implementing hardware-specific behavior, obtain and record:

- exact Pixhawk model and hardware revision;
- PX4 versus ArduPilot decision (the current architecture assumes PX4);
- exact firmware Git tag/commit and bootloader;
- airframe/mixer/control-allocation configuration;
- ESC protocol, motor order and rotation;
- GPS, compass, barometer, IMU, range/depth and external-vision devices;
- companion computer, serial/USB/Ethernet transport and link baud/rates;
- RC/safety switch/kill channel and battery/power setup.

Do not invent defaults when any item is unknown. Claude may build a simulator-only
configuration while these are collected.

### P0.2 — action semantics are normalized body FLU, not PX4 setpoints

The signed action is `(forward, left, up)` in body FLU. It is not metres/second and is
not NED. The adapter must:

1. require the receipt/action frame to be exactly `BODY_FLU_NORMALIZED_VELOCITY`;
2. apply frozen horizontal/vertical velocity and acceleration limits;
3. convert FLU to the selected PX4 frame explicitly. For a PX4 body FRD velocity frame,
   the sign conversion is `(forward, -left, -up)` before scaling;
4. never silently mix ENU receipt pose, NED mission coordinates and body commands;
5. test yaw and all axis signs with asymmetric vectors, not only `(1,0,0)`.

### P0.3 — authorization freshness is checked before the present callback, not inside it

`SafetySupervisor` validates the receipt at authorization time, but its current
`Authorization` object contains only `allowed`, `action` and `reason`. A production sink
cannot independently re-check receipt expiry or bind a dispatched setpoint to the exact
receipt.

Extend the command handoff with an immutable envelope containing at least:

- node/vehicle ID;
- action and action frame;
- receipt digest and consensus/finality identifier;
- generated monotonic timestamp and absolute expiry;
- command sequence;
- mission ID/epoch;
- applicable frozen limit/profile ID.

At the final MAVLink dispatch boundary, reject missing, stale, future-dated, duplicate,
out-of-order, wrong-node, wrong-frame or digest-mismatched commands. Re-check immediately
before every mutating autopilot call.

### P0.4 — HOLD is not universally equal to a zero-velocity setpoint

`HOLD_ACTION == (0,0,0)` is safe only while offboard mode and the estimator are healthy.
Implement a reason/state policy:

- pre-arm HOLD: do not arm or enter offboard;
- healthy in-flight HOLD: continuously stream a valid zero-velocity or position-hold
  setpoint at the pinned PX4-required rate;
- estimator/local-position unhealthy: leave offboard and invoke the frozen PX4 failsafe
  action rather than trusting position hold;
- QUARANTINE: cancel nominal commands, command the frozen abort/land behavior, and do not
  permit automatic resumption;
- adapter/process/link death: PX4's independent offboard-loss failsafe must activate.

The watchdog must live outside the inference loop so a deadlocked AI process cannot leave
the last nonzero setpoint active.

### P0.5 — waypoint motion and avoidance are still separate implementations

`MissionRunner` presently releases the perception-derived reactive action. The CoSys
runner separately implements nominal A-to-B flight. There is no accepted arbiter that
combines the signed mission plan with avoidance and security for PX4.

Implement an explicit arbiter:

```text
signed/frozen waypoint intent ----+
                                  +--> bounded command candidate --> safety gate --> PX4
measured avoidance intent --------+

HOLD/QUARANTINE always preempts both.
```

Do not use unconstrained vector addition. Freeze priority, saturation, acceleration,
clearance, geofence, rejoin and terminal-block rules. A survivor observation remains
visible even if its observing drone is held or quarantined.

### P0.6 — synchronized autopilot health is missing

The `HealthState` provider for PX4 must derive current, timestamped facts—not constants:

- heartbeat/link age and expected system/component ID;
- armed state and navigation/offboard mode;
- local/global position validity and estimator flags;
- pose age, reset counter and covariance/uncertainty;
- time synchronization status;
- geofence and home validity;
- battery state and relevant failsafe flags;
- RC/operator abort state;
- setpoint-stream health and last acknowledged command;
- collision/distance-sensor health appropriate to every commanded axis.

Sample RGB, depth, pose and health atomically or with a measured maximum skew. A set of
independently fetched "latest" values is not an admissible control snapshot.

### P0.7 — the legacy PX4 scripts are evidence utilities, not a safe adapter

Do not base the final implementation on `sim/multi_drone.py` without replacing its
control model. It currently:

- directly sends MAVLink arm/takeoff/land commands;
- bypasses VeriSwarm authorization and command limits;
- writes a parameter without ACK/readback verification;
- relies on Gazebo command-line ground truth for co-visibility;
- does not prove offboard cadence, expiry, link-loss behavior, landing or disarm;
- does not provide a persistent independent watchdog.

`sim/pose_probe.py` merely prints pose. Neither file closes S3.

## 5. Required adapter structure

Prefer a small, testable package rather than adding more control to
`sim/multi_drone.py`:

```text
flight/
  command_envelope.py       immutable validated command + expiry/digest
  frame_conversion.py       BODY_FLU <-> PX4 selected frame
  limits.py                 velocity/acceleration/geofence enforcement
  px4_health.py             timestamped health snapshot
  px4_command_sink.py       only module permitted to issue mutating MAVLink calls
  watchdog.py               independent setpoint/offboard-loss safety process
  evidence.py               append-only command/ACK/mode/failsafe telemetry
sim/px4/
  sitl_config/              pinned firmware/airframe/parameters/ports
  run_sitl_campaign.py      reproducible simulator campaign
```

Names may change, but ownership boundaries may not.

The command sink must have a single owning execution context for the MAVLink connection;
no command may be submitted concurrently from unrelated threads. Reads may be separated
only through a bounded timestamped telemetry cache. Timeouts invalidate their execution
context; never automatically retry an arm, mode change, takeoff, land or other potentially
mutating command after an ambiguous timeout.

## 6. SITL acceptance sequence

Claude should work only through these gates, preserving failures instead of silently
rerunning them:

1. Unit tests with a fake MAVLink transport:
   - all frame/sign conversions;
   - NaN/Inf/out-of-range rejection;
   - stale/wrong-node/wrong-frame/replay rejection;
   - command saturation and acceleration bounds;
   - HOLD/QUARANTINE/operator-abort precedence;
   - timeout ambiguity and cleanup;
   - watchdog activation when command production stops.
2. One-vehicle PX4 SITL no-motion preflight:
   - exact heartbeat identity;
   - parameter snapshot and firmware identity;
   - health snapshot only; no arm.
3. Propulsion simulation, bounded world:
   - arm only after explicit health and setpoint-stream preconditions;
   - takeoff, hover, small asymmetric FLU-axis commands, HOLD, land and disarm;
   - verify actual PX4 state and acknowledgements, not only sent messages.
4. Failure injection:
   - command process crash;
   - setpoint loss/offboard loss;
   - MAVLink disconnect/reconnect;
   - stale pose and estimator reset;
   - GPS loss with and without accepted external vision;
   - depth loss/invalid range;
   - geofence breach request;
   - model swap, semantic dispute and no quorum;
   - battery and data-link failsafes.
5. Multi-vehicle SITL:
   - unique system IDs/ports/keys;
   - separation and per-vehicle command ownership;
   - one quarantined vehicle, unfinished-cell reassignment and preserved survivor alert;
   - no shared mutable connection or cross-addressed setpoint.
6. Propeller-free HITL only after all SITL evidence passes twice from cold state.

Any simulated collision, unauthorized motion, stale-command release, wrong-axis motion,
failed failsafe or unverified disarm is a retained failure.

## 7. Definition of done for Claude's first checkpoint

The first checkpoint is complete only when:

- a pinned PX4 SITL release and full parameter file are committed or referenced by exact
  immutable identity;
- the new command envelope, frame conversion, sink, health provider and independent
  watchdog have focused tests;
- `MissionRunner` can use the sink without any other module issuing MAVLink movement;
- a no-motion preflight and one bounded takeoff/HOLD/land SITL run produce complete
  native stdout/stderr, structured evidence and nonzero exit on failure;
- killing MissionRunner while moving causes the configured PX4 offboard-loss behavior;
- a stale or rejected VeriSwarm command never moves the simulated vehicle;
- landing and disarm are verified from PX4 state/ACKs—CoSys collision/deviation logic is
  not reused as Pixhawk truth;
- all pre-existing deterministic protocol/safety tests remain green.

## 8. Commands to establish the baseline

Run from a clean checkout of the integration branch:

```bash
git fetch --all --prune
git switch suyash/sih26177-rescue-integration
git pull --ff-only
git status --short
git rev-parse HEAD

cd codebase
python -m pytest -q
python -m sim.closed_loop
```

On Windows, the existing POSIX owner-mode assertion
`test_state_file_is_owner_only` is not meaningful because Windows does not expose POSIX
`0600` bits through `stat`; it remains required on Linux/Jetson and must not be deleted to
make Windows green.

Before changing code, Claude must read:

- `codebase/README.md`
- `codebase/SIMULATION_AND_FLIGHT_GATES.md`
- `codebase/node/mission.py`
- `codebase/perception/safety_supervisor.py`
- `codebase/perception/depth_check.py`
- `codebase/protocol/receipts.py`
- `codebase/rescue/movement_security.py`
- `codebase/sim/cosys/factorycity/movement_v2.py`
- `codebase/sim/multi_drone.py`
- `codebase/sim/pose_probe.py`

## 9. Claims that remain prohibited

- No physical-flight readiness claim.
- No military-ready, foolproof or 100% security claim.
- No claim that OP-TEE attests inference or pose.
- No claim that signed quorum is formally proven PBFT.
- No claim that CoSys SimpleFlight behavior proves PX4/Pixhawk behavior.
- No claim that detector training or a `.pt` model is Jetson-qualified until the actual
  Orin Nano TensorRT engine and sustained pipeline benchmark pass.
- No claim that one camera's missing person detection disproves a positive survivor from
  another calibrated view.

The immediate goal is a defensible, fail-closed PX4 SITL adapter with retained evidence.
Physical flight requires later HITL, independent safety review, containment, kill path,
regulatory compliance and accepted residual risk.
