# STATUS — Pratik cell/movement v2 freeze implementation

**Owner:** Pratik  
**Branch:** `pratik/sih26177-disaster-cosys`  
**Upstream authorities:** Suyash `940f4a7dc98aa772c66531ce1949c25b15971f12`,
Abhijan `f610667`

## Milestone delivered

- Merged the frozen cell/movement event extension and Abhijan movement gate into this
  feature branch after checkpointing the Point_B convergence/landing repair.
- Kept `factorycity_joint_movement_contract.development.json` unchanged.
- Added `factorycity_sensor_movement_extension.v2.development.json`, hash-bound to the
  accepted v1 movement contract and frozen cell-event contract.
- Added `movement_v2.py` with:
  - exact configured `assignment.cell_ids` and all three coverage lists;
  - ID validation before durable enqueue;
  - frozen movement-safety reason/result emission from `<node>.telemetry`;
  - measured DepthPlanar left/centre/right clearance reduction;
  - obstacle HOLD, measured deflection selection, route-rejoin, cell-block and measured
    collision transitions;
  - 200/250/400/500 ms fail-closed timing boundaries from configuration;
  - `evaluate_movement_command` immediately before the supplied mutating CoSys callback;
  - exact RELEASE, DO_NOT_DISPATCH, HOVER and ABORT_HOVER_LAND enforcement.

The controller surface contains no evaluator obstacle coordinates, survivor truth or
post-run evaluator input.

## Verification

```powershell
python -m pytest `
  codebase/tests/test_factorycity_movement_v2.py `
  codebase/tests/test_movement_security.py `
  codebase/tests/test_rescue_cell_movement_extension.py -q
```

Result: `32 passed`.

Merged full suite result on Windows: `538 passed, 3 skipped, 1 failed`. The sole failure is
the pre-existing POSIX owner-mode assertion
`test_rescue_authorization_adapter.py::test_state_file_is_owner_only`; Windows reports
mode `0666` instead of POSIX `0600`. No FactoryCity, movement, event or gate test failed.

## Interface/sample output

`SensorDrivenMovementSupervisor.decide(...)` returns one control action and frozen
transition names. `DurableMovementEvents.movement_safety(...)` produces, validates and
queues `veriswarm.rescue.event.v1` payloads such as:

```json
{
  "node": "alpha",
  "cell_id": "route_cell_00",
  "event_type": "OBSTACLE_DETECTED",
  "measurement_source": "front_depth_and_vehicle_state",
  "measured_distance_m": 3.4,
  "reason_code": "depth_below_stopping_boundary",
  "result": "NON_TERMINAL"
}
```

## Limitation / next owned task

This milestone is the tested configuration, state machine, gate and durable producer
core. Live CoSys call-site wiring and retained simulator acceptance runs are still
required before claiming autonomous avoidance. Next, Pratik will publish a separate v2
runner that binds this core to front-depth sampling, authorization refresh, route
commands, hover/abort/landing and exact task reassignment. The accepted nominal v1 runner
remains the current live path until that v2 run qualifies.
