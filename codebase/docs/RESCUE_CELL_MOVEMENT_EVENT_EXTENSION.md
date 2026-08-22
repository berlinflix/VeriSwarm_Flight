# Rescue cell and movement-event extension

## Status

This is Suyash's frozen additive interface for the SIH 26177 FactoryCity rescue
demonstration. The machine-readable authority is
`codebase/config/rescue_cell_movement_event_extension.v1.json`.

The base envelope remains `veriswarm.rescue.event.v1`. This avoids breaking retained
evidence and existing producers. Count-only legacy events remain replayable, but every new
FactoryCity producer created after this freeze must emit the additive cell-ID fields.

## Why the extension exists

Counts alone cannot reconstruct a heatmap, prove ownership or show exactly which work was
reassigned. The extension makes those facts explicit without copying cell geometry into
events. Geometry remains in the immutable movement configuration; events carry only IDs
and transitions.

## Frozen cell fields

- `assignment.cell_ids`: every cell assigned to the node. Its length equals
  `cells_total`.
- `coverage.in_progress_cell_ids`: assigned cells entered but not completed.
- `coverage.completed_cell_ids`: cells whose configured end boundary was crossed and for
  which required sensing/evidence was durably enqueued. Its length equals `visited_cells`.
- `coverage.blocked_cell_ids`: cells for which every permitted sensor-driven deflection
  was rejected. `BLOCKED` means unreachable for the current run and never counts as
  completed.
- `task_reassigned.cell_ids`: the exact cells transferred. Its length equals
  `cells_count`.

The coverage lists are pairwise disjoint. A producer must validate every ID against the
movement configuration before enqueueing the event. A blocked cell changes owner only by
an explicit later `task_reassigned` plus `assignment` transition; it never silently
becomes completed.

If replay finds the same cell in incompatible states or assigned to multiple nodes, the
projection lists it in `coverage.cell_state_conflicts` or `assignment_conflicts` and
removes it from the colored state lists. The dashboard must show the conflict and must not
choose a convenient color or owner.

## Frozen movement-safety event

`movement_safety` is emitted by `<node>.telemetry` only. Required payload fields are:

```json
{
  "node": "alpha",
  "cell_id": "route_cell_05",
  "event_type": "OBSTACLE_DETECTED",
  "measurement_source": "front_depth_and_vehicle_state",
  "measured_distance_m": 3.7,
  "reason_code": "depth_below_stopping_boundary",
  "result": "NON_TERMINAL"
}
```

The event-to-reason/result mapping is fixed:

| Event | Reason | Result |
|---|---|---|
| `OBSTACLE_DETECTED` | `depth_below_stopping_boundary` | `NON_TERMINAL` |
| `SAFETY_HOLD` | `obstacle_safety_hold` | `NON_TERMINAL` |
| `DEFLECTION_SELECTED` | `safe_deflection_selected` | `NON_TERMINAL` |
| `ROUTE_REJOINED` | `nominal_route_rejoined` | `NON_TERMINAL` |
| `CELL_BLOCKED` | `no_safe_deflection` | `TERMINAL_CELL` |
| `COLLISION_DETECTED` | `cosys_collision_detected` | `TERMINAL_VEHICLE` |

`observed_at_ms` in the envelope is the transition time. Collision events are for new
en-route contact only; expected takeoff/landing ground contact remains governed by the
existing landing policy and must not be mislabeled as an en-route collision.

## Frozen timing boundaries for the simulation

- Authorization decision age: at most 2,000 ms.
- Mission/control loop: at most 200 ms between gates.
- Depth sample age at a deflection decision: at most 250 ms.
- HOLD command dispatch: within 400 ms of the triggering decision.
- Movement transition durable enqueue: within 500 ms.
- Any missed boundary causes `HOLD`; it never silently releases a command.

These are simulation qualification limits, not flight-hardware certification claims.
Future real-aircraft limits must be derived from measured dynamics, braking distance,
sensor latency and the certified flight-control stack.

## Separation of truth and control

The controller may consume measured front depth, `simGetCollisionInfo`, vehicle state,
the immutable route/safety limits and a fresh authorization lease. It may not consume
Unreal actor coordinates, survivor truth or post-run evaluator output. Abhijan may use
those hidden values only after the run to score detection, geolocation and collision
outcomes.

## Integration ownership

- Pratik emits the cell and movement events from the CoSys mission supervisor and invokes
  the authorization gate immediately before every mutating CoSys command.
- Abhijan projects the retained events into the heatmap/scoreboard and proves replay
  equivalence. The dashboard remains read-only for movement and authorization.
- Suyash owns this interface. Further shared-field changes require a new additive contract
  revision in Git, not an in-place reinterpretation.

No claim of live autonomous avoidance is permitted until retained runs show obstacle
detection, hold, safe deflection, route rejoin, no hidden-truth access and zero protected
collisions.
