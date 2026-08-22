# MESSAGE — autonomous movement, obstacle deflection and command-centre inputs

**From:** Abhijan  
**To:** Pratik  
**Cc:** Suyash, Ayush  
**Date:** 2026-08-22  
**Priority:** P0 movement integration; P1 presentation after event producers are stable

## Decision

The rescue mission must be autonomous after the operator starts it. Point A, Point B,
vehicle roster, search cells and safety limits remain configuration inputs. The operator
does not manually pilot the route.

The current joint contract at `bc5cbd7` freezes a straight route with no deviation and
uses depth only for telemetry/post-run evaluation. That is not sufficient for the desired
obstacle-deflection demonstration. Pratik and Abhijan must not silently reinterpret the
frozen contract. Pratik should propose the movement-contract change described below, and
Suyash must freeze the additive event fields before they become a shared dependency.

Do not merge `pratik/sih26177-disaster-cosys` wholesale into
`codex/abhijan-rescue-security`; the branches have different integration ancestry. Port
the compatible FactoryCity files/commits without deleting the rescue collector,
dashboard, model-hash adapter or current operations documentation.

## Runtime topology

```text
Abhijan Mac (.14)
  dashboard + rescue collector + simulator authorization control + post-run evaluator
          |
          | authenticated Ethernet control/events
          v
Pratik Windows simulator host (.11, exact address to be confirmed)
  autonomous mission supervisor + CoSys Python controller
          |
          | CoSys RPC 41451
          v
Unreal/CoSys FactoryCity_Disaster with five simulated drones
```

Abhijan's Mac has Unreal Engine 5.8.1, but it is not the accepted simulator host for this
handoff. The portable repository does not contain the FactoryCity Unreal project, derived
map or CoSys plugin, and the official CoSys 3.4.1 precompiled plugin is not supplied for
macOS. Keep the qualified Unreal world on Pratik's Windows machine and use the Mac as the
control, dashboard and evaluation client.

## Required autonomous movement behavior

1. Load Point A, Point B, roster, initial poses, cells, geofence and motion limits from
   configuration; do not hard-code them in the controller.
2. After one start command, perform API acquisition, arm, takeoff, route planning,
   coordinated search, landing, disarm and cleanup without manual piloting.
3. Plan a nominal A-to-B route inside the frozen geofence while preserving the configured
   minimum pairwise separation.
4. Detect obstructions from measured CoSys depth/collision/vehicle state. Hidden Unreal
   actor coordinates and evaluator truth are forbidden controller inputs.
5. Before reaching the stopping-distance boundary, enter a mission-safety hold, hover and
   evaluate deterministic left/right/vertical deflection candidates inside the geofence.
6. Select only a collision-free deflection that satisfies clearance, acceleration, speed,
   separation and route-progress constraints. Rejoin the nominal route after clearance.
7. If no safe deflection exists, retain hover, mark the affected cell blocked/unreachable
   and reassign unfinished work rather than flying through the obstacle.
8. Continue to Point B and land every available vehicle. A quarantined, failed or collided
   vehicle is excluded from nominal commands.
9. Preserve positive person/hazard observations when their observing drone is held,
   quarantined or unavailable.
10. Retain measured route, obstacle, command, authorization, reassignment and collision
    evidence outside Git.

The exact planner can remain deliberately small for the demo, but it must be deterministic
and sensor-driven. A fixed list of hidden obstacle coordinates is not autonomous avoidance.

## Keep mission safety separate from model-hash security

- `abhijan-security` remains the physical Jetson model-identity decision source.
- Proposed simulator-only operator source: `abhijan-sim-control`.
- Simulator and Jetson sources require independent sequence/state/evidence files and clear
  dashboard labels.
- No raw model hash, OP-TEE receipt or Jetson private material enters the simulator.
- Obstacle response is emitted by Pratik's mission supervisor as measured mission safety;
  it must not impersonate an Abhijan model-identity authorization event.

Authorization effects remain:

- `ALLOW`: release a valid pending command only after all movement gates pass.
- `HOLD`: cancel/prevent new nominal motion and hover; recover only after a fresh valid
  decision and safe route are available.
- `QUARANTINE`: terminal for the current run; hover, controlled safety landing, exclude the
  drone and immediately reassign unfinished cells. It cannot resume until reset.
- Missing, stale or malformed authorization: fail closed to `HOLD`.

Pratik's controller must check the current authorization lease before arming/takeoff and
before every movement-control step. The current nominal controller does not yet do this.

## Event requirements for Ayush's heatmap and scoreboard

Ayush's search heatmap, judge scoreboard and read-only command-centre proposal are accepted
as phased dashboard work. They must be deterministic projections of retained events, not
animation or a second mutable truth store.

The current count-only event surface cannot identify individual covered, blocked or
reassigned cells. Pratik should retain and propose these additive payload fields for Suyash
to freeze:

- `assignment`: `cell_ids`;
- `coverage`: `in_progress_cell_ids`, `completed_cell_ids`, `blocked_cell_ids`;
- `task_reassigned`: `cell_ids` in addition to `cells_count`;
- collision/obstacle evidence: node, affected cell, measured distance/source, event time
  and terminal/non-terminal result.

Cell geometry remains in the immutable movement configuration; events carry cell IDs and
state transitions, not duplicate geometry.

Pratik owns production of:

- mission start/completion;
- cell assignment, ownership and coverage transitions;
- measured blockage/unreachable state;
- vehicle state, position and link state;
- collision result; and
- exact cell reassignment events.

Abhijan owns:

- simulator authorization input and fail-closed policy tests;
- dashboard heatmap and scoreboard presentation;
- unsafe-command-release count;
- replay-equivalence and stale/malformed decision tests;
- isolated post-run truth evaluation for recall, false alerts and geolocation error; and
- the optional read-only four-intent command-centre UI after P0 integration passes.

Suyash owns freezing the additive event fields, timing boundaries and reason codes. Ayush
reviews clarity and evidence-to-display consistency. The natural-language view cannot
issue movement, authorization, assignment or quarantine commands and must not delay P0.

## Required clarifications from Pratik

Publish these in Git status or a team-update message:

1. Exact simulator host IP, RPC bind/firewall configuration and launch command.
2. Exact CoSys-AirSim release/commit, Python wheel identity and `settings.json` path.
3. FactoryCity project/map identity and the procedure for cold reset.
4. Measured depth camera frame, calibration, range and invalid-reading policy.
5. Stopping-distance calculation and the deterministic deflection candidate order.
6. Maximum permitted lateral/vertical deviation and route-rejoin condition.
7. Exact HOLD recovery/escalation timeout.
8. Controlled landing target/policy for a quarantined drone.
9. Exact definition of cell `IN_PROGRESS`, `COMPLETED`, `BLOCKED` and `UNREACHABLE`.
10. Whether Alpha's unfinished `route_cell_00` and `route_cell_05` deterministically move
    to Bravo and Charlie when the other four drones are healthy.
11. Transport used by the controller to consume Abhijan's authorization lease and its
    maximum safe reaction time.
12. Ownership and format of the durable rescue-event outbox.

## Joint acceptance runs

1. Normal autonomous five-drone Point-A-to-Point-B run with landing and cleanup.
2. Measured obstacle on the nominal route causes safe hold, autonomous deflection and
   route rejoin without hidden-truth access.
3. Fully blocked cell is marked blocked/unreachable and unfinished work is reassigned.
4. Transient security `HOLD` releases no new motion, preserves observations and resumes
   only after a fresh `ALLOW`.
5. Alpha `QUARANTINE` releases no new Alpha motion, lands/excludes Alpha, preserves its
   observations and reassigns every unfinished Alpha cell.
6. Missing/stale/malformed authorization fails closed.
7. Event-log replay reconstructs exactly the same heatmap, ownership, alerts, scoreboard
   operational metrics and reassignment history.
8. Across retained protected runs: zero unsafe releases and zero protected collisions.

Do not claim autonomous obstacle avoidance, heatmap correctness or authorization-aware
movement until the corresponding live run and retained evidence pass.
