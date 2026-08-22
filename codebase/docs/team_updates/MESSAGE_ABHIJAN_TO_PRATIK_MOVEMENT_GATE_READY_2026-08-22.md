# MESSAGE — Configuration-driven movement gate ready for CoSys wiring

**From:** Abhijan  
**To:** Pratik, Suyash  
**Date:** 2026-08-22  
**Priority:** P0 integration

## Ready on Abhijan's branch

Branch: `codex/abhijan-rescue-security`

The accepted contract at
`codebase/sim/cosys/factorycity/factorycity_joint_movement_contract.development.json`
is now consumed by:

- `codebase/rescue/movement_security.py`;
- `codebase/tests/test_movement_security.py`.

No rescue event schema was changed.

## Pratik integration boundary

Load the contract once with `load_movement_contract(...)`. Immediately before every new
CoSys route-command dispatch, call `evaluate_movement_command(...)` with:

- the target vehicle ID;
- the latest normalized authorization for that same vehicle;
- the authorization event's top-level `observed_at_ms` as the authorization timestamp;
- the current time in milliseconds;
- whether the vehicle is in flight;
- the pending command's velocity, acceleration, pairwise separation and target NED values.

Enforce the returned result exactly:

- `RELEASE`: dispatch the already validated pending command;
- `DO_NOT_DISPATCH`: keep the pending command blocked;
- `HOVER`: cancel new route motion and hold the current safe flood-relative altitude;
- `ABORT_HOVER_LAND`: refuse resumed nominal motion, hover, then perform the configured
  controlled safety landing.

Do not convert a missing, stale, malformed, future-dated or wrong-node authorization into
`ALLOW`; the policy returns `HOLD`.

On `QUARANTINE`, call `reassign_unfinished_cells(...)` with the real completed-cell set and
currently healthy roster. Publish one durable `task_reassigned` event for each returned
cell transition, plus the normalized `authorization` and `vehicle_state` events through
`veriswarm.rescue.event.v1`.

## Data boundary

The controller may consume configured route geometry, measured vehicle state, normalized
authorization and the pending command. It must not read `evaluation_truth`, survivor
locations or evaluation-only obstacle locations. Positive observations already emitted by
Alpha must remain in the mission projection after Alpha enters `HOLD` or `QUARANTINED`.

## Current proof

- Focused movement/rescue integration: `70 passed`.
- Complete Python suite with loopback listeners: `521 passed in 58.95s`.
- Dashboard proof-valid regression suite: `4 passed`.
- Dashboard production build: passed.

This proves the deterministic policy/configuration layer. It does not prove live CoSys
command cancellation, hover, landing, reassignment or event durability. Those require the
retained simulator runs after Pratik completes the runtime call sites.
