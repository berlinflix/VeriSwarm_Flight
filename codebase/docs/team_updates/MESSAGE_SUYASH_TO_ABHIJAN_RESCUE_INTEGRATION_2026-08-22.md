# MESSAGE — SUYASH TO ABHIJAN

Date: 2026-08-22 IST

Subject: Handoff received; Git is now your standing coordination inbox

Your `ABHIJAN_RESCUE_INTEGRATION_HANDOFF.md` has been reviewed. This record resolves its
open questions and supersedes its stale branch-tip information. It is a work instruction,
not an approval gate. Continue independently and report meaningful progress or a real
blocker through Git only.

## Standing Git inbox rule

Your primary inbox is:

```text
origin/suyash/sih26177-rescue-integration
```

Check it:

1. at the start of every work session;
2. at least every 30 minutes during active integration;
3. before changing a shared interface;
4. before a rehearsal or evidence run;
5. before pushing your next checkpoint.

Use:

```text
git fetch origin
git log --oneline origin/suyash/sih26177-rescue-integration -- \
  codebase/docs/team_updates
git log --oneline --all -- codebase/docs/team_updates
```

The first log is the central Suyash inbox. The second includes messages and status commits
from Samik, Pratik and Ayush on their own branches. Read new `MESSAGE_*` files and relevant
`STATUS_*` files with `git show`.

Maintain your own branch, preferably:

```text
abhijan/sih26177-rescue-integration
```

Create and maintain only your own `STATUS_ABHIJAN.md`. Include:

```text
last_inbox_commit_seen: <full commit>
current_work_commit: <full commit or NONE>
completed: <measured checkpoint>
next: <next task>
blockers: <NONE or one concrete blocker>
```

Do not add chat acknowledgements or per-file confirmation requests. Reading a routine Git
message requires no response. Commit only completed work, an interface proposal or a real
blocker.

## Authoritative integration state

```text
branch: suyash/sih26177-rescue-integration
functional code commit: 0030d296c0a0188f1fcd0d4cbe50ca46e4e2b7ef
mission_id: OP-VARUNA-001
schema: veriswarm.rescue.event.v1
collector port: 8770
authorization decision: ALLOW (not ACCEPT)
security source: abhijan-security
full regression: 469 passed, 3 dependency-gated skips
```

The historical `30736f3` remains the original data-plane commit, but it is no longer the
integration target. Since then the branch has added the durable offline outbox,
survivor-first multi-view fusion, the consensus-security bridge and independent producer
stream identities.

The schema identifier remains `veriswarm.rescue.event.v1`. New multi-view fields are
optional and old valid events remain accepted.

## Resolved source and sequencing contract

Different processes must not share one sequence counter accidentally. Use:

```text
alpha.perception  — Samik person/hazard observations
alpha.telemetry   — Pratik coverage/vehicle/link telemetry
alpha.fusion      — localized hazard promotion
abhijan-security  — authorization decisions targeting any node
```

Repeat the pattern for `bravo`, `charlie`, `delta` and `echo`. Each source owns its own
monotonic `source_seq`. A role stream may speak only for the matching node. The legacy
exact-node source remains valid only for a single combined producer.

## Authorization decision contract

Use these mappings:

| Evidence result | Event decision | Reason |
|---|---|---|
| Approved model; valid receipt/runtime/consensus | `ALLOW` | `model_hash_approved` |
| Verification timeout | `HOLD` | `model_hash_verification_timeout` |
| Model identity absent | `HOLD` | `model_identity_missing` |
| No semantic quorum | `HOLD` | `semantic_cross_check_insufficient` or `consensus_no_quorum` |
| Confirmed unapproved model hash | `QUARANTINE` | `model_hash_not_approved` |
| Confirmed receipt/digest integrity failure | `QUARANTINE` | `receipt_integrity_failure` |

Unknown or incomplete evidence is a HOLD, not proof of malicious activity. Use the
presentation wording: **unapproved perception model rejected**.

Model swap and power-off remain separate demonstrations exactly as your handoff states.

## Hazard promotion ownership

The ownership decision is:

- Samik emits image-space hazard `observation` events from `<node>.perception`.
- Pratik supplies genuine pose/depth/range and uncertainty from `<node>.telemetry` or the
  in-process simulator adapter.
- Samik owns the fusion/promotion code that emits a localized `hazard` event as
  `<node>.fusion` only after genuine geometry is available.
- Abhijan owns hidden post-run truth and evaluates the promoted hazard; truth is never an
  input to perception, localization or promotion.

No position means no mapped `hazard` event. Keep the observation visible as
`OBSERVATION_ONLY` rather than inventing coordinates.

## Your active tasks

1. Build the model-hash/consensus-to-`authorization` producer using source
   `abhijan-security` and the durable rescue outbox.
2. Run the collector and dashboard on the Mac with a non-committed 32+ character token.
3. Maintain the hidden Operation Varuna evaluator and keep truth outside autonomy inputs.
4. Add the one-view adversarial-patch evaluation from the latest team message:
   an unaffected positive remains HIGH, the suspicious miss raises
   `PERCEPTION_SECURITY_REVIEW`, and control may HOLD without deleting the person alert.
5. Validate accepted/duplicate/rejected HTTP behavior and the final `/state` and `/report`.
6. Publish launch commands, sample authorization events, tests and measured status on your
   own branch. Do not commit tokens, weights, raw datasets, videos or private truth.

## No remaining confirmation blocker

You do not need another confirmation for branch, mission ID, schema, `ALLOW`, source
identity, reason mappings, hazard ownership, port or normal implementation work. If a
genuine interface conflict appears, record it once in `STATUS_ABHIJAN.md` with the exact
file/API involved and continue every unblocked task.
