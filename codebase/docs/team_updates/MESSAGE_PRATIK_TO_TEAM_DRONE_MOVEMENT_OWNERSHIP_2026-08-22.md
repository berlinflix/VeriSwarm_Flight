# MESSAGE — Pratik and Abhijan jointly own drone movement

**From:** Pratik
**To:** Suyash, Samik, Abhijan, Ayush
**Date:** 2026-08-22
**Priority:** P0 ownership/interface update

The execution plan has changed: **Pratik and Abhijan jointly own the five-drone movement
lane.** Do not continue assuming that Samik owns the mission movement controller.

This is an ownership update, not a backward-incompatible rescue-event schema change and
not a request for routine approval. Suyash should carry the ownership change into the
central integration plan; all owners should use this split in new work.

## Joint movement boundary

Pratik owns the CoSys-facing nominal execution path:

- exact vehicle roster and settings;
- RPC/API-control acquisition, arming and takeoff;
- formation targets and configured separation limits;
- flood-relative altitude control and geofence/command limits;
- mission waypoint/path execution in the CoSys world;
- live collision, vehicle-state and link telemetry;
- landing, disarming, cleanup, reset and reproducible evidence.

Abhijan jointly owns the safety/security movement behavior with Pratik:

- authorization-aware command release;
- `ALLOW`, `HOLD` and `QUARANTINE` effects on pending movement;
- abort, hover, land or reassignment behavior during demonstrated attacks/failures;
- security-state integration tests around movement;
- adversarial/fault trials without exposing hidden truth to the controller.

Pratik and Abhijan may co-author movement-controller files. They will coordinate before a
backward-incompatible command or shared-schema change and will retain fail-closed tests.

## Interfaces that do not change

- Samik owns detector/inference output, tracking and perception observations. Movement
  consumes those outputs through the agreed rescue interfaces; Samik no longer owns the
  drone movement implementation.
- Abhijan's hidden truth remains post-run evaluation data only. It must never enter path
  selection, collision avoidance, localization or command authorization as an oracle.
- Perception `HOLD`, `REJECTED` or `NO_QUORUM` may gate unsafe autonomous motion but must
  not delete a positive responder alert.
- Vehicle, mission, coverage and link events use per-node durable producer identities such
  as `alpha.telemetry`; separate identities must not share one SQLite outbox.
- CoSys remains authoritative for flight, NED pose, metric depth/range, collision and
  mission-motion evidence.
- Synthetic POV video remains presentation/perception input, not metric flight evidence.

## Current baseline and next joint deliverable

Pratik's current baseline is the checkpointed five-drone FactoryCity flood controller at:

```text
commit: 06ea152cd3afd2c14884b9d5ff63a0906d6de2a6
tag: checkpoint/pratik-factorycity-flood-persistent-peak-v1
```

It proves roster, arming/takeoff, synchronized flood-relative altitude, separation and
collision monitoring, persistent hover, and safe recession/landing cleanup. It is not yet
the complete rescue movement algorithm.

The next Pratik+Abhijan deliverable is one configuration-driven movement contract covering
mission targets, command limits, authorization gating, abort/hover/landing policy,
telemetry/outbox events and repeatable normal/failure tests. Point_B and the final route
must be explicitly frozen before they are presented as accepted mission inputs.

## Coordination action

- **Suyash:** update the central ownership table and integration sequencing.
- **Samik:** continue perception/fusion; hand observations to the shared interface without
  implementing the movement controller.
- **Abhijan:** coordinate the joint controller/security boundary with Pratik and retain
  hidden truth only for evaluation.
- **Ayush:** consume movement/mission state for display; do not infer control authority
  from the synthetic POV presentation.
- **Pratik:** publish the first joint movement contract and runnable CoSys evidence on the
  Pratik branch, then hand the compatible interface to Abhijan.

