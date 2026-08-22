# MESSAGE — freeze the rescue movement contract

**From:** Suyash  
**To:** Pratik and Abhijan  
**Date:** 2026-08-22  
**Priority:** P0 integration blocker  

This records the joint movement ownership announced by Pratik in commit `7105938`.
Continue without waiting for routine authorization.

## Ownership

- Pratik owns the CoSys-facing nominal flight, formation, route, telemetry, landing and
  reset path.
- Pratik and Abhijan jointly own authorization-aware movement safety: `ALLOW`, `HOLD`,
  `QUARANTINE`, abort, hover, landing and unfinished-sector reassignment.
- Samik supplies perception/tracking observations and does not own the movement controller.

## Required next joint deliverable

Publish one configuration-driven movement contract containing:

1. exact Point A and Point B in the declared CoSys NED frame;
2. exact five-drone roster, initial poses and assigned search sectors/cells;
3. nominal route/waypoints and the mission-completion condition;
4. altitude band, speed, acceleration, separation and geofence limits;
5. blocked-route/obstacle locations and independent depth/collision policy;
6. simulator survivor/hazard locations retained only for post-run evaluation;
7. `ALLOW`, `HOLD` and `QUARANTINE` effects on pending/in-flight commands;
8. abort, hover, land and reassignment policy after a drone becomes unavailable;
9. coverage, ownership, vehicle-state, collision, authorization and reassignment event
   fields emitted through the frozen rescue data plane;
10. startup, landing, cleanup and repeatable reset procedure;
11. normal and failure tests, including one quarantined drone with its unfinished cells
    reassigned; and
12. one original 10-second no-person CoSys RGB recording from the same disaster domain
    for detector false-positive evaluation.

The hidden simulator truth must never enter perception, planning, collision avoidance,
localization or command authorization. It is an Abhijan post-run evaluator input only.

The synthetic POV videos remain perception/presentation inputs, not pose, depth or flight
evidence. A positive survivor observation must remain visible even when the observing
drone is held or quarantined.

## Handoff

Commit the contract, configuration example, tests and updated `STATUS_PRATIK.md` on
`pratik/sih26177-disaster-cosys`. Abhijan should publish the compatible security movement
tests on `codex/abhijan-rescue-security`. Do not commit raw videos or generated runtime
evidence.

Post blockers or interface changes in `codebase/docs/team_updates/`; otherwise continue
independently.
