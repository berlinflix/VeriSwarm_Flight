# Pratik C4 world-lane status

Owner: Pratik

Branch: `pratik/sih26177-disaster-cosys`

Plan base: `e3f9559add11f4815bafac43b29a953e0eba742f`

## Owned Git surface

- `codebase/sim/cosys/factorycity/**`
- `codebase/tests/test_factorycity_*.py`

## External owned development surface

- Derived CoSys/Unreal disaster project and map created from the protected FactoryCity
  baseline. Unreal projects, generated settings, captures, logs and development evidence
  stay outside Git.
- Overwriteable development output uses
  `scratch/pratik/factorycity-disaster/latest/` outside this worktree.

## Read-only inputs owned by other lanes

- Abhijan's scenario manifest, truth actors and expected-event contract.
- Samik's inference/model registry contract.
- Suyash's rescue event schema and collector interface.

This lane will not change a shared schema incompatibly or edit another owner's files
without the coordination required by `05_FAST_INDEPENDENT_EXECUTION_RULES.md`.

## Current milestone

Development milestone implemented and live-verified:

- `/Game/VeriSwarm/FactoryCity_Disaster` is a separate derived map;
- the protected FactoryCity map remains byte-identical to its checkpoint;
- Point_A and `AirSimOrigin_Point_A` remain at `(13070, 2950, 130)` Unreal cm;
- the replacement development manifest specifies one full-Landscape, runtime-movable,
  non-colliding flood surface plus distributed `BlockAll` roadblock/debris/damage actors;
- the rising-flood controller derives drone altitude from the observed water pose and
  checks minimum clearance, swarm separation, collision state, and roster on every step;
- every disaster actor is more than 30 m from Point_A (observed minimum: 51.7 m);
- two independent cold boots passed for alpha, bravo, charlie, delta and echo;
- a refined camera profile passed RGB, DepthPlanar and pose probes on all five vehicles.

The new map-wide layer and controller are implemented in Git but remain pending a closed-
editor Unreal rebuild and live cold-boot run.

## Current limitation

`SCENARIO_MANIFEST.json` has not been handed off. The checked-in layout is explicitly
`development_only`; it proves the world/tooling/collision integration but is not final
scenario truth. Replace its anchors/transforms with Abhijan's frozen manifest before the
final demo freeze. No Point_B, survivor truth actor, fire claim or detector truth has been
invented here.
