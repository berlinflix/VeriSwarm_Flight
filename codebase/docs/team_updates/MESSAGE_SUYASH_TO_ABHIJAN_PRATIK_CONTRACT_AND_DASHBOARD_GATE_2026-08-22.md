# MESSAGE — Pratik contract accepted; dashboard proof gate requires correction

**From:** Suyash
**To:** Abhijan
**Date:** 2026-08-22
**Priority:** P0 integration

## Pratik handoff is ready

Fetch `origin/pratik/sih26177-disaster-cosys` and review:

- nominal five-drone A-to-B implementation: `d96cf1c`;
- joint movement/security contract: `bc5cbd7`;
- direct handoff and remaining gates: `f2bbd65`.

Suyash independently reproduced `18/18` focused FactoryCity tests and
`379 passed, 3 skipped` for the complete Pratik branch. The artifacts are accepted for
development integration. This is not a claim that live A-to-B flight, authorization-aware
movement, durable event delivery or reassignment has passed.

Begin configuration-driven tests without waiting for another acknowledgement:

1. `ALLOW` releases only a valid pending command after movement limits pass.
2. Missing, stale or malformed authorization fails closed to `HOLD`.
3. In-flight `HOLD` prevents new route motion and produces the configured safe hover.
4. `QUARANTINE` prevents resumed nominal motion and initiates the configured safe abort.
5. Every unfinished Alpha route cell is deterministically reassigned to healthy vehicles.
6. Positive survivor observations from Alpha remain visible after Alpha is held or
   quarantined.
7. Authorization, vehicle-state and task-reassignment events project through
   `veriswarm.rescue.event.v1` without importing hidden simulator truth into control.

## Required staged-dashboard correction

Review commit `dd70244`. Both staged-dashboard definitions of `cleanPassed` currently use
only the clean outcome and semantic acknowledgement count. That can display
`APPROVED BASELINE VERIFIED` and enable the attack stage even when
`cleanEvidence.dashboard_proof.proof_valid` is false.

The clean-stage predicate must require all of:

```text
cleanEvidence.dashboard_proof.proof_valid === true
cleanEvidence.actual.outcome === "ACCEPTED"
Number(cleanEvidence.actual.semantic_acks) >= 2
```

Use one shared predicate for the state transition and the displayed status. Add a
regression case where the outcome is `ACCEPTED` with two semantic acknowledgements but
`proof_valid=false`; the UI must show failure, must not claim an approved baseline and
must not enable the model-hash attack stage.

Do not change the frozen rescue event schema. Commit the correction and movement tests to
`codex/abhijan-rescue-security`, update `STATUS_ABHIJAN.md`, and publish only genuine
blockers or shared-interface changes. No separate acknowledgement is required.
