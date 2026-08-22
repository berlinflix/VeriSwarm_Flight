# STATUS — ABHIJAN

Updated: 2026-08-22 IST

Branch: `codex/abhijan-rescue-security`

Integration base in this branch: `origin/suyash/sih26177-rescue-integration` at `e939121`

Latest upstream reviewed: `ef56941` (movement freeze); branch tip `437c329` was also
checked and contains a Samik-only video-evaluation handoff with no replacement Abhijan
instruction.

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
codebase/docs/ABHIJAN_AUTHORIZATION_ADAPTER.md
codebase/docs/team_updates/STATUS_ABHIJAN.md
codebase/examples/qualification_clean_dashboard_sample.json
codebase/examples/qualification_model_swap_dashboard_sample.json
codebase/c2_dashboard/src/App.jsx
ops/start_abhijan_rescue_mac.sh
ops/publish_rescue_authorization.sh
ops/ABHIJAN_DASHBOARD_HANDOFF.md
ops/ABHIJAN_REVIEWER_DEMO_SCRIPT.md
ops/DEMO_RUNBOOK.md
```

## Current blockers

- The joint configuration-driven movement contract requested by Suyash at `ef56941` is
  not yet available on this branch. Value-specific security movement tests cannot be
  finalized until Pratik publishes the exact Point A/Point B, five-drone roster and poses,
  sectors/cells, route, limits, obstacles, command lifecycle and repeatable reset details.
- Pratik's CoSys rescue-event producer/handoff has not yet been reviewed in this branch.
  Until that handoff is supplied, Abhijan will not invent coordinates, simulator truth,
  movement state or reassignment events.
- A physical model-hash rerun still operationally requires Alpha, Bravo and Charlie to be
  reachable, Charlie's peer service to be running, and `vs dash` to be started on Alpha.
  This is not a code blocker for the already retained evidence or dashboard.

## Next checkpoint

- Review Pratik's exact branch/commit handoff when supplied; do not redesign or merge it
  blindly.
- Add compatible movement-security tests on this branch covering `ALLOW`, transient
  `HOLD`, `QUARANTINE`, abort/hover/land behavior, and unfinished-cell reassignment using
  the published configuration rather than hard-coded or invented simulator facts.
- Verify the quarantined-drone case end to end: no unauthorized motion, Alpha unavailable,
  unfinished cells reassigned, survivor observations preserved, and authorization,
  vehicle-state and reassignment events projected through the frozen rescue data plane.
- Keep attack evidence separate from genuine mission observations and use hidden simulator
  truth only for post-run scoring.
- Publish code, tests and any new blockers here; keep raw videos and runtime evidence out
  of Git.
