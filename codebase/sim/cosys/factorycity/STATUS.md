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

The rebuilt development map passed a live cold run on 2026-08-22: 60 paced rising
steps produced an observed 4.5999998 m water rise, followed by a peak hold and 20 paced
recession steps. Across the retained run, minimum drone clearance above water was
3.7410 m (configured abort floor: 3.0 m), minimum pairwise separation was 2.8284 m,
no new post-climb collision occurred, the water returned to its initial pose, all five
drones landed, and API control was released.

The default development demo now uses persistent peak mode: after the paced rise, the
controller keeps issuing altitude commands and verifies clearance/separation/collisions
while the flood remains visible. Ctrl+C is the explicit operator signal for recession and
the already-validated landing/cleanup path.

Point_B is now saved and collision-qualified at
`(3787.536049, 4993.491058, 921.693295)` Unreal cm. It is 95.0474 m horizontally from
Point_A and 4.2169 m above the configured peak-water plane. The source hall advertises
`BlockAll` but its mesh collision does not contain a usable rooftop surface, so the
derived map contains one invisible, bounded 5.5 x 5.5 m `BlockAll` landing surface named
`VS_PointB_LandingCollision`. All nine 5 x 5 m footprint probes hit that surface with
zero height variation and surface-normal Z=1.0.

The configuration-bound nominal A-to-B controller is implemented but not yet claimed as
live-qualified. It raises the flood while the drones climb with it, establishes a 10 m
cruise height, flies the five vehicles along the same 95.0474 m horizontal vector,
monitors only new en-route collisions, terminates collided vehicles by disarming and
excluding them, and lands survivors at Point_B before API cleanup. Its first live run is
the next acceptance gate.

## Current limitation

`SCENARIO_MANIFEST.json` has not been handed off. The checked-in layout is explicitly
`development_only`; it proves the world/tooling/collision integration but is not final
scenario truth. Replace its disaster anchors/transforms with Abhijan's frozen manifest
before the final demo freeze. Point_B is a Pratik-owned nominal navigation endpoint; no
survivor truth actor, fire claim or detector truth has been invented here.
