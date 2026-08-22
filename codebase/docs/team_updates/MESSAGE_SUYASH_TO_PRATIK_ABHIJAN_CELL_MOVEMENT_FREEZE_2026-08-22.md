# MESSAGE — cell heatmap and measured movement-event interface frozen

**From:** Suyash

**To:** Pratik, Abhijan

**Cc:** Ayush, Samik

**Date:** 2026-08-22

**Priority:** P0 integration

## Decision

The additive cell and movement-event interface requested by Abhijan is now frozen in Git.
This message is the handoff; no separate approval or file-by-file confirmation is needed.

Read:

- `codebase/config/rescue_cell_movement_event_extension.v1.json`
- `codebase/docs/RESCUE_CELL_MOVEMENT_EVENT_EXTENSION.md`
- `codebase/rescue/schema.py`
- `codebase/tests/test_rescue_cell_movement_extension.py`

The base envelope remains `veriswarm.rescue.event.v1`. Old count-only evidence remains
readable. All new FactoryCity producers must include the additive fields.

## Pratik — implement now

1. Emit `assignment.cell_ids` with exact IDs from the loaded movement configuration.
2. Emit all three coverage lists: `in_progress_cell_ids`, `completed_cell_ids` and
   `blocked_cell_ids`.
3. Emit `task_reassigned.cell_ids`; its length must equal `cells_count`.
4. Emit `movement_safety` from `<node>.telemetry` for every obstacle/hold/deflection/
   rejoin/block/collision transition using the frozen reason/result mapping.
5. Invoke `evaluate_movement_command` immediately before every mutating CoSys command.
6. Use only measured front depth, `simGetCollisionInfo`, vehicle state, the immutable
   route/safety limits and a fresh authorization lease. Do not read evaluator truth or
   Unreal obstacle actor coordinates.
7. Validate event IDs against the loaded movement configuration before durable enqueue.
8. Keep the existing straight-route contract and its evidence unchanged. Publish the
   sensor-driven obstacle-avoidance implementation as a new additive movement-contract
   revision; do not reinterpret the accepted v1 file.

The frozen simulation timing limits are: 200 ms maximum control period, 250 ms maximum
depth-sample age, 400 ms HOLD dispatch deadline and 500 ms durable transition-enqueue
deadline. A timing miss causes HOLD.

## Abhijan — implement now

1. Project exact cell lists into the heatmap and scoreboard; do not derive colored cells
   from counts.
2. Load cell geometry only from the immutable movement configuration.
3. Reject/flag unknown cell IDs, inconsistent ownership and invalid transitions.
4. Display movement-safety events from retained evidence, distinguishing terminal cell
   blockage from terminal vehicle collision.
5. Prove replay equivalence: replaying the same retained events must reconstruct the same
   ownership, heatmap, blocked cells, reassignment history and movement alerts.
6. Keep the command-centre view read-only for movement, assignment and authorization.

## Shared acceptance sequence

Run these after Pratik publishes the new controller revision:

1. Normal five-drone mission with exact assignment/coverage cells.
2. Measured obstacle causes detection, safety hold, permitted deflection and route rejoin.
3. No-safe-route case marks the cell blocked and reassigns it explicitly.
4. Security HOLD releases no new movement; fresh ALLOW resumes only through the gate.
5. QUARANTINE lands/excludes one vehicle, preserves its person/hazard observations and
   reassigns every unfinished cell.
6. Replay produces identical dashboard state.

No live-autonomy claim is accepted from unit tests alone. Retain JSONL, controller console,
simulator video and post-run evaluation outside Git. Continue working and report progress
through a Git commit/status message; do not wait for a separate Suyash confirmation.
