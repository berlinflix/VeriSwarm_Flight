# MESSAGE — joint rescue movement contract ready

**From:** Pratik  
**To:** Abhijan, Suyash  
**Date:** 2026-08-22  
**Priority:** P0 integration handoff

## Owner and branch

- Human owner: Pratik
- Nominal CoSys lane: Pratik
- Authorization-aware movement safety: Pratik + Abhijan
- Branch: `pratik/sih26177-disaster-cosys`
- Contract commit: `bc5cbd7`
- Nominal A-to-B implementation commit: `d96cf1c`

## What works

- Point A and collision-qualified rooftop Point B are frozen in the declared CoSys NED
  frame.
- The exact five-drone roster, initial poses, 10 route cells, 95.0474 m straight route,
  10 m cruise target, command/geofence/separation limits and reset procedure are config
  data rather than controller constants.
- The nominal controller performs API control, arming, takeoff, flood-relative climb,
  straight formation movement, en-route collision termination, Point B landing, disarm
  and API cleanup.
- The joint contract defines `ALLOW`, fail-closed `HOLD`, `QUARANTINE`, abort/hover/land
  and deterministic unfinished-cell reassignment behavior.
- The contract maps internal states to the frozen `veriswarm.rescue.event.v1` surface and
  uses Abhijan's existing `abhijan-security` authorization source.
- Hidden survivor/hazard truth and listed disaster coordinates are evaluator-only and are
  forbidden from the controller.

## Run and test

```powershell
git fetch origin
git show origin/pratik/sih26177-disaster-cosys:codebase/sim/cosys/factorycity/factorycity_joint_movement_contract.development.json
& 'C:\Users\raj20\OneDrive\Documents\ChatGPT\SIH\.venv-veriswarm312\Scripts\python.exe' -m pytest codebase/tests/test_factorycity_disaster.py -q
```

Pratik result at handoff: `18 passed`; full owned-branch result: `379 passed, 3 skipped`.

## Interface

- Contract:
  `codebase/sim/cosys/factorycity/factorycity_joint_movement_contract.development.json`
- Explanation:
  `codebase/sim/cosys/factorycity/JOINT_MOVEMENT_SECURITY_CONTRACT.md`
- Runtime configuration:
  `codebase/sim/cosys/factorycity/factorycity_ab_mission.development.json`
- Nominal controller:
  `codebase/sim/cosys/factorycity/tools/run_factorycity_ab_swarm.py`

Abhijan can now implement compatible tests on `codex/abhijan-rescue-security` using the
published values. Required first joint failure case: quarantine Alpha, release no new
nominal Alpha motion, preserve Alpha's positive observations, reassign every unfinished
Alpha cell, and project authorization, vehicle-state and task-reassignment events.

## Limitations and remaining gates

- The first retained live A-to-B RPC run has not yet been executed.
- The acceleration/geofence and authorization gates are frozen contract requirements;
  their integrated enforcement tests remain joint work.
- Pratik's durable rescue-event producer is not implemented yet.
- The original 10-second no-person CoSys RGB negative recording is still required and
  must remain outside Git.
- Abhijan's final scenario-truth manifest is still pending; no truth value may be invented
  or consumed by movement.

No acknowledgement is required for compatible work. Post only blockers or shared-interface
changes; otherwise continue independently.
