# FactoryCity joint movement/security contract

Owners: Pratik (nominal CoSys execution) and Pratik + Abhijan (authorization-aware
movement safety)

The machine-readable contract is
`factorycity_joint_movement_contract.development.json`. It freezes the development
Point A/Point B geometry, roster, initial poses, route cells, limits, collision semantics,
authorization effects, rescue-event fields, cleanup and acceptance cases without changing
the shared `veriswarm.rescue.event.v1` schema.

## Security boundary

- The nominal controller consumes only configured mission inputs, CoSys state, measured
  depth/collision data and normalized `ALLOW`/`HOLD`/`QUARANTINE` authorization.
- Hidden survivor/hazard truth is forbidden from perception, planning, localization,
  collision handling and command authorization. It is an Abhijan post-run evaluator input.
- A positive survivor observation remains visible after `HOLD` or `QUARANTINE`.
- The route remains one straight A-to-B segment. Depth does not trigger avoidance; a new
  CoSys collision after `EN_ROUTE` begins terminates and excludes the affected drone.
- Takeoff and landing contact/penetration do not invoke the en-route crash rule.

## Abhijan integration point

Abhijan can load the JSON contract directly and implement value-specific tests for:

1. `ALLOW` releasing a valid pending command;
2. transient `HOLD` preventing new motion and causing safe hover;
3. stale, missing or malformed authorization failing closed to `HOLD`;
4. `QUARANTINE` aborting the affected route, preventing resumed nominal motion and
   reassigning every unfinished route cell; and
5. authorization, vehicle-state and task-reassignment projection through the frozen
   rescue data plane while observations remain present.

The listed disaster obstacle coordinates and future survivor truth are evaluation-only.
Neither side may use them as an oracle. Abhijan owns the final scenario-truth replacement;
Pratik owns the CoSys movement and telemetry producer implementation.

## Current qualification state

Point B and its landing collision are scene-qualified. The nominal controller and contract
are implemented and unit-tested, but the first retained live A-to-B RPC run and the joint
authorization/reassignment run are still required before final qualification.
