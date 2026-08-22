# Pratik–Abhijan direct coordination

**Effective:** 2026-08-22

Pratik and Abhijan communicate directly through their Git branches for the joint CoSys,
movement-security, event-producer and dashboard integration. Suyash is not the relay for
routine questions, handoffs, test results or blockers.

This rule supersedes older coordination text that called the Suyash branch Abhijan's
primary inbox. Historical messages remain preserved, but they do not override this file.

## Current boundary

- Pratik branch: `pratik/sih26177-disaster-cosys`
- Abhijan branch: `codex/abhijan-rescue-security`
- Pratik owns live CoSys control, measured simulator inputs, vehicle/cell state and durable
  movement-event production.
- Abhijan owns normalized authorization input, fail-closed policy behavior, event replay,
  dashboard projection and post-run evaluation.
- Both jointly own the exact command-boundary integration and retained normal, HOLD,
  QUARANTINE, reassignment and blocked-cell runs.

Latest facts reviewed when this rule was created:

- Pratik `883df7e` reports the nominal five-drone A-to-B run as PASS. That result does not
  qualify authorization-aware movement or obstacle deflection.
- Abhijan `debf335` adds the five-drone live coverage view.
- The next joint deliverable is live movement-v2 wiring: authorization is evaluated before
  every mutating CoSys dispatch, returned HOLD/QUARANTINE instructions are executed, cell
  changes are durably emitted, and the dashboard replay matches the retained events.

These commit IDs are observations, not permanent branch pins. Always fetch before work.

## Direct Git loop

At the start of joint work and before changing their shared interface, each owner runs:

```text
git fetch origin --prune
git log --oneline -n 10 origin/pratik/sih26177-disaster-cosys -- \
  codebase/docs/team_updates
git log --oneline -n 10 origin/codex/abhijan-rescue-security -- \
  codebase/docs/team_updates
```

Read the peer's status without switching branches:

```text
git show origin/pratik/sih26177-disaster-cosys:codebase/docs/team_updates/STATUS_PRATIK.md
git show origin/codex/abhijan-rescue-security:codebase/docs/team_updates/STATUS_ABHIJAN.md
```

Direct requests are committed on the sender's branch as:

```text
MESSAGE_PRATIK_TO_ABHIJAN_<TOPIC>_<YYYY-MM-DD>.md
MESSAGE_ABHIJAN_TO_PRATIK_<TOPIC>_<YYYY-MM-DD>.md
```

The receiver records `last_peer_commit_seen` in their own status file when publishing
their next meaningful work checkpoint. A separate acknowledgement commit, chat message,
checksum confirmation or Suyash approval is not required.

## Work without waiting

Pratik and Abhijan may directly:

- clarify measured fields, commands, ports, reset behavior and event examples;
- propose and implement backward-compatible additions within their owned surfaces;
- exchange sample JSON/events and deterministic fixtures;
- run development simulations, replays, tests and dashboard builds;
- fix compatible producer/consumer defects and publish exact test results; and
- integrate a peer commit into their own branch after reviewing the diff and tests.

They must not merge either full branch blindly. Each owner retains their existing branch
ancestry and ports only reviewed compatible commits/files.

## Escalate to Suyash only when a decision is actually needed

Suyash is required only for:

- a backward-incompatible shared schema or reason-code change;
- an unresolved producer/consumer conflict that changes product behavior;
- a safety/claim decision or expansion of accepted evidence;
- merge into the Suyash integration/release branch;
- destructive repository/data operations, paid spending or credential exposure; or
- physical-hardware activity.

An escalation contains one concise file with: the conflict, both proposed choices,
measured evidence, affected tests and the recommended choice. Work that is independent of
the disputed decision continues.

## Direct definition of done

For every joint checkpoint, the producing owner publishes:

```text
branch and commit
peer commit consumed
files/interfaces changed
exact test or run command
measured result
remaining blocker, or NONE
```

Raw videos, simulator projects, credentials and runtime evidence remain outside Git.
Their paths and summaries may be recorded in status. Suyash reviews the consolidated
integration result, not every intermediate exchange.
