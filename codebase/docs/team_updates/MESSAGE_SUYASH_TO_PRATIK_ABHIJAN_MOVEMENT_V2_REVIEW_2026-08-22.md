# MESSAGE — SUYASH TO PRATIK AND ABHIJAN — MOVEMENT V2 REVIEW

Date: 2026-08-22 IST

Integration branch: `suyash/sih26177-rescue-integration`

## Decision

Pratik's sensor-driven movement v2 and Abhijan's retained cell-mission dashboard have
been integrated for development. Unit and replay tests are evidence of software
behavior only; they are not a live CoSys qualification claim.

The independent review found and corrected the following integration/safety defects:

- a transition could report `SAFETY_HOLD` while releasing a deflection in the same
  decision rather than first issuing a real hover;
- `BLOCKED` and `COLLIDED` states were not sticky and could resume after a later clean
  frame;
- invalid directional depth could be interpreted as infinite free space;
- only left/right escape existed even though upward clearance is required in debris;
- route-cell assignment and coverage reused one sector key, allowing four of five drone
  records to overwrite one another in the projection;
- completed/blocked/assigned sets could not support unfinished-cell reassignment;
- the dashboard accepted any historical owner after reassignment, so stale ownership
  could appear valid;
- the movement extension referenced authority hashes that did not match its integration
  branch;
- transition-to-durable-enqueue latency was not checked end to end.

The corrected contract now has a measured braking-distance floor, real hover-before-
deflect sequencing, deterministic `LEFT`/`RIGHT`/`UP` selection, bounded projected
cross-track/vertical motion, sticky terminal states, fail-closed telemetry validation,
unique per-drone sector IDs, exact current-owner validation and durable reassignment
state publication.

## Independent verification

- Movement/security/replay compatibility: `81 passed`.
- Final focused integration selection: `40 passed`.
- Dashboard unit tests: `12 passed`.
- Production dashboard build: PASS.
- Complete Python suite on Windows with the POSIX-only owner-mode assertion deselected:
  `546 passed, 3 skipped, 1 deselected`.
- The unfiltered suite result is `546 passed, 3 skipped, 1 failed`; the one failure is
  the existing `test_state_file_is_owner_only` assertion because Windows reports POSIX
  mode bits as `0666`. It remains required on Linux/Jetson and was not weakened.

## Pratik — continue without waiting for another authorization

1. Pull the latest Suyash integration commit after it is published.
2. Wire the corrected `SensorDrivenMovementSupervisor`, `CellLedger`,
   `DurableMovementEvents` and `GatedCommandDispatcher` into actual CoSys calls.
3. Implement all returned actions, including `HOVER`, `DEFLECT_LEFT`,
   `DEFLECT_RIGHT`, `DEFLECT_UP`, `REJOIN_ROUTE`, `BLOCK_CELL` and
   `TERMINATE_VEHICLE`. Never translate `HOLD` into nominal motion.
4. Keep evaluation-only obstacle coordinates and survivor truth outside controller
   inputs.
5. Publish retained evidence for: unobstructed A-to-B, left deflection, right
   deflection, upward deflection, no-safe-path block, stale-depth hold, authorization
   hold/quarantine and collision termination.
6. Produce the original no-person CoSys RGB negative-control clip outside Git.
7. Report live results and the exact commit in `STATUS_PRATIK.md`; do not edit the
   review message or reuse failed run identities.

## Abhijan — continue without waiting for another authorization

1. Pull the latest Suyash integration commit after it is published.
2. Consume the current-owner dashboard rules; an old owner is invalid immediately after
   an accepted reassignment.
3. Replay task reassignment followed by the two current assignments and two current
   coverage records. A partial replay must remain visibly conflicted/fail-closed.
4. Verify and show `OBSTACLE_DETECTED`, real `SAFETY_HOLD`, deflection,
   `ROUTE_REJOINED`, terminal cell blockage and terminal vehicle collision separately.
5. Keep the survivor alert independent from the movement/security state: one valid
   positive survivor observation remains visible even when its drone is held or
   quarantined.
6. Publish a replay screenshot/video and test/build results in `STATUS_ABHIJAN.md`.

## Shared stop conditions

- Missing/stale authorization, stale depth, invalid telemetry, timing miss or projection
  conflict means `HOLD`/conflict, never route continuation.
- A blocked cell transfers only if unfinished. Completed cells are never reassigned.
- No unit test, dashboard animation or synthetic fixture may be described as a retained
  live five-drone CoSys run.
