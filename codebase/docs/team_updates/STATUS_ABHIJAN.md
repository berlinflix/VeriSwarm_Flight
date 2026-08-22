# STATUS — ABHIJAN

Updated: 2026-08-22 IST

Branch: `codex/abhijan-rescue-security`

Integration base in this branch: `origin/suyash/sih26177-rescue-integration` at `0f7cb7f`

Latest upstream reviewed: `0f7cb7f` (Pratik contract accepted; movement tests and
dashboard proof-valid gate requested). The accepted Pratik commits are `d96cf1c`,
`bc5cbd7` and `f2bbd65`.

Functional rescue data-plane parent: `30736f3`

## Completed

- Added a fail-closed adapter from Suyash's create-once qualification dashboard proof to
  `veriswarm.rescue.event.v1` authorization events.
- Kept detailed model hashes, receipts, votes and private identities out of the rescue
  event payload.
- Mapped a verified clean proof to `ALLOW`, a verified model-swap rejection to
  `QUARANTINE`, and missing/failed/inconsistent/no-quorum proof to `HOLD`.
- Required the retained proof to identify the OP-TEE signer backend, manifest hash,
  approved/observed model hashes and consistent receipt hash.
- Added restart-safe persistent delivery state. The exact event is saved before delivery;
  connection failure retains it, and an identical retry preserves event ID, timestamp and
  source sequence.
- Added development-only clean and model-swap proof-shape examples clearly marked as
  non-hardware evidence.
- Completed a process-level collector smoke test:
  - clean proof -> `ALLOW`;
  - model-swap proof -> `QUARANTINE`;
  - projected Alpha state -> `QUARANTINED`;
  - responder alert -> `CRITICAL`, reason `model_hash_not_approved`.
- Full Python validation after the final restart-safety test: `454 passed in 55.56s`.
- Dashboard production build passed with Vite 7.3.6.
- Live visual dashboard check passed against the collector projection: Operation Varuna
  showed ACTIVE, 45% coverage, one person candidate, one mapped hazard, one vehicle and the
  critical Alpha quarantine alert. Browser console warnings/errors: none.
- Added an integrated Mac launcher that opens the secure Alpha tunnel, starts the rescue
  collector and serves the rescue-enabled dashboard at `127.0.0.1:5175`.
- Added a publisher that securely fetches the create-once `*.dashboard.json` proof from
  Alpha, converts it through the fail-closed adapter and posts only the normalized
  authorization event to the Mac collector.
- Validated the publisher with retained hardware-run evidence on Alpha:
  - `dashboard-clean-1787276531963106801.dashboard.json` -> `ALLOW`, sequence 1;
  - `dashboard-model_swap-1787276532093292096.dashboard.json` -> `QUARANTINE`, sequence 2;
  - projected Alpha state -> `QUARANTINED`, with a `CRITICAL` responder alert.
- Completed the physical model-hash reviewer path over Ethernet:
  - clean baseline: semantic acknowledgements `2/2`, disputes `0/2`, accepted;
  - unapproved Alpha model: semantic acknowledgements `0/2`, disputes `2/2`, rejected;
  - released command remained `[0,0,0]` / `HOLD`;
  - normalized rescue authorization was published as `QUARANTINE` with reason
    `model_hash_not_approved` and projected as one quarantined vehicle.
- Split the dashboard control into two explicit reviewer stages so the accepted baseline
  can be demonstrated before the attack:
  - `RUN APPROVED BASELINE` runs only the clean qualification;
  - `RUN MODEL-HASH ATTACK` becomes available only after the clean result;
  - the retained attack evidence remains visible after publication.
- Updated the dashboard handoff, demo runbook and reviewer script to match that staged
  sequence. The production dashboard build passed after the change.
- Read Suyash's P0 movement freeze at `ef56941`. Recorded ownership without changing the
  frozen rescue event schema:
  - Pratik owns nominal CoSys flight, formation, route, telemetry, landing and reset;
  - Pratik and Abhijan jointly own authorization-aware movement safety and reassignment;
  - hidden simulator truth is restricted to Abhijan's post-run evaluator and must not
    enter perception, planning, localization, collision avoidance or authorization;
  - a survivor observation must remain visible after its observing drone is held or
    quarantined.
- Reviewed Pratik's movement handoff at `f2bbd65` (`bc5cbd7` contract and `d96cf1c`
  nominal controller). The published JSON freezes policy text, but the live controller
  does not yet consume authorization, perform cell reassignment or emit the durable rescue
  event stream. The first live A-to-B run is also still open.
- Reviewed Ayush's heatmap, priority, scoreboard and read-only command-centre proposals.
  Accepted the deterministic heatmap/scoreboard as P1 after P0 movement events are stable;
  retained the natural-language query layer as optional and strictly read-only.
- Published `MESSAGE_ABHIJAN_TO_PRATIK_AUTONOMOUS_MOVEMENT_AND_COMMAND_CENTRE_2026-08-22.md`
  to request autonomous sensor-driven obstacle deflection, authorization gating and the
  cell-level event inputs needed for a truthful heatmap.
- Merged Suyash's accepted rescue-integration tip `0f7cb7f` into this branch without
  changing `veriswarm.rescue.event.v1`.
- Corrected the staged dashboard's clean-baseline gate. One shared predicate now requires
  all three conditions before the UI can claim an approved baseline or expose the
  model-hash attack stage:
  - `dashboard_proof.proof_valid === true`;
  - clean outcome `ACCEPTED`;
  - at least two semantic acknowledgements.
- Added a regression for `ACCEPTED + 2 ACK + proof_valid=false`. It fails closed, keeps
  the attack stage locked, shows `VERIFICATION FAILED` / `FAILED CLOSED`, and directs the
  operator to rerun the approved baseline.
- Added `rescue.movement_security`, a configuration-driven policy layer over Pratik's
  frozen `veriswarm.factorycity.joint_movement_contract.v1`. It does not issue CoSys
  commands; it returns deterministic instructions for the live controller to enforce:
  - fresh `ALLOW` plus all movement limits -> `RELEASE`;
  - missing, stale, future-dated, malformed or wrong-node authorization -> `HOLD`;
  - in-flight `HOLD` -> cancel new route motion and `HOVER`;
  - `QUARANTINE` -> refuse resumed nominal motion and `ABORT_HOVER_LAND`;
  - unfinished cells of an unavailable vehicle -> lexical, rotating reassignment across
    the healthy roster.
- Added configuration-driven movement tests against the accepted Pratik JSON contract.
  They cover valid release, movement-limit rejection, fail-closed authorization, in-flight
  hover, quarantine abort, completed-cell exclusion, deterministic Alpha reassignment,
  preservation of Alpha's positive observation, frozen-schema event projection and the
  prohibition on importing hidden simulator truth into control.
- Final validation after the Suyash gate work:
  - focused movement/rescue integration: `70 passed`;
  - complete Python suite with temporary loopback HTTP/gRPC listeners: `521 passed in
    58.95s`;
  - dashboard gate regressions: `4 passed`;
  - Vite production build: passed (`1980` modules transformed).

## Network observed from Abhijan's Mac

- Mac Ethernet: `192.168.50.14` on `en7`.
- Jetson Alpha `192.168.50.10`: ping passes; SSH port 22 accepts connections.
- Suyash/Charlie `192.168.50.13`: ping passes; peer port 51003 accepts connections.
- Bravo `192.168.50.12`: ping passes; peer port 51001 accepts connections.
- Alpha's deployed `~/vs peers` reports Bravo and Charlie `UP`.
- Alpha's deployed `~/vs check` passes OP-TEE device, TA, CA, frozen bundle, seed
  isolation and clock synchronization checks. Observed offsets were 7.2 ms for Bravo and
  10.9 ms for Charlie against the 250 ms bound.
- Alpha's `~/vs` helper was missing its executable bit; `chmod u+x ~/vs` was applied and
  the documented checks now run normally.

No IP address, route, peer configuration or firewall setting was changed.

## Files owned in this checkpoint

```text
codebase/tools/rescue_authorization_adapter.py
codebase/tests/test_rescue_authorization_adapter.py
codebase/rescue/movement_security.py
codebase/tests/test_movement_security.py
codebase/docs/ABHIJAN_AUTHORIZATION_ADAPTER.md
codebase/docs/team_updates/STATUS_ABHIJAN.md
codebase/docs/team_updates/MESSAGE_ABHIJAN_TO_PRATIK_AUTONOMOUS_MOVEMENT_AND_COMMAND_CENTRE_2026-08-22.md
codebase/docs/team_updates/MESSAGE_ABHIJAN_TO_PRATIK_MOVEMENT_GATE_READY_2026-08-22.md
codebase/examples/qualification_clean_dashboard_sample.json
codebase/examples/qualification_model_swap_dashboard_sample.json
codebase/c2_dashboard/src/App.jsx
codebase/c2_dashboard/src/modelHashQualification.js
codebase/c2_dashboard/src/modelHashQualification.test.js
codebase/c2_dashboard/package.json
ops/start_abhijan_rescue_mac.sh
ops/publish_rescue_authorization.sh
ops/ABHIJAN_DASHBOARD_HANDOFF.md
ops/ABHIJAN_REVIEWER_DEMO_SCRIPT.md
ops/DEMO_RUNBOOK.md
```

## Current blockers

- The accepted contract still freezes one straight route with no deviation and makes
  depth evaluation-only. Autonomous obstacle deflection requires a coordinated contract
  revision; it cannot be claimed from this checkpoint.
- The policy gate and configuration-driven tests are complete, but Pratik's live CoSys
  controller must still call the gate at every command boundary, execute the returned
  hover/abort instruction, perform the returned cell reassignments and durably publish the
  corresponding rescue events. No live authorization-aware flight is claimed yet.
- The first retained live A-to-B RPC run, transient-HOLD flight and quarantined-Alpha
  flight remain open simulator qualifications.
- A cell-level heatmap cannot be reconstructed from the current count-only assignment,
  coverage and reassignment payloads. Suyash must freeze additive cell-ID/state fields
  before Pratik and Abhijan depend on them.
- A physical model-hash rerun still operationally requires Alpha, Bravo and Charlie to be
  reachable, Charlie's peer service to be running, and `vs dash` to be started on Alpha.
  This is not a code blocker for the already retained evidence or dashboard.

## Next checkpoint

- Hand `rescue.movement_security` to Pratik for the live CoSys integration. The controller
  must supply only normalized authorization plus measured/configured command inputs and
  must not read `evaluation_truth`.
- Verify the quarantined-drone case live: no unauthorized motion, Alpha unavailable,
  unfinished cells reassigned, survivor observations preserved, and authorization,
  vehicle-state and reassignment events projected through the frozen rescue data plane.
- Retain proof for the normal A-to-B, transient-HOLD and quarantined-Alpha simulator runs;
  report measured outcomes without claiming autonomous obstacle avoidance.
- Keep attack evidence separate from genuine mission observations and use hidden simulator
  truth only for post-run scoring.
- Add the deterministic heatmap and compact scoreboard after cell-level producers pass;
  keep the read-only command-centre query layer optional until the P0 thin slice is stable.
- Publish code, tests and any new blockers here; keep raw videos and runtime evidence out
  of Git.
