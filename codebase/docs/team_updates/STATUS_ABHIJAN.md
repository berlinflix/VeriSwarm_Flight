# STATUS — ABHIJAN

Updated: 2026-08-22 IST

Branch: `codex/abhijan-rescue-security`

Integration base: `origin/suyash/sih26177-rescue-integration` at `e939121`

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
codebase/examples/qualification_clean_dashboard_sample.json
codebase/examples/qualification_model_swap_dashboard_sample.json
codebase/docs/team_updates/STATUS_ABHIJAN.md
ops/start_abhijan_rescue_mac.sh
ops/publish_rescue_authorization.sh
ops/ABHIJAN_DASHBOARD_HANDOFF.md
ops/DEMO_RUNBOOK.md
```

## Current blockers

- Pratik's simulator event producer is not yet available; this does not block the
  authorization adapter.
- Suyash must keep Charlie running and start `vs dash` on Alpha before Abhijan launches
  the integrated Mac dashboard. This is an operational start step, not a code blocker.

## Next checkpoint

- Suyash keeps Charlie ready and runs `vs dash` on Alpha.
- Abhijan runs `./ops/start_abhijan_rescue_mac.sh` on the Mac.
- Run the physical CLEAN + MODEL HASH cases and publish each displayed retained evidence
  filename with `./ops/publish_rescue_authorization.sh <filename>`.
- Ingest Pratik's simulator events through the same frozen rescue collector when his
  producer is delivered; do not mix attack evidence with survivor/hazard observations.
