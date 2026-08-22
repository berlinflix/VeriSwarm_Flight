# Git-based team updates

This directory is the lightweight coordination channel for the SIH 26177 build.
It replaces repeated chat confirmations; it is not an approval system.

## Working rule

- Every owner works independently on their own branch.
- Do not wait for Suyash before normal dataset, training, simulation, UI, test or
  documentation work.
- Commit and push a status update after a meaningful checkpoint, when an interface
  changes, or when a real blocker appears.
- At the start of a work session and before changing a shared interface, fetch and
  inspect the latest team-update commits.
- During an active integration sprint, check the central inbox branch at least every
  30 minutes, before a rehearsal and before pushing an interface change:
  `origin/suyash/sih26177-rescue-integration`.
- Abhijan follows this same cadence. His primary inbox is the Suyash integration branch;
  he also checks `git log --all -- codebase/docs/team_updates` for teammate-owned status
  and message commits.
- A status commit records progress. It does not freeze inputs or require another
  confirmation.

## Files

Each person maintains one branch-owned status file:

```text
codebase/docs/team_updates/STATUS_SUYASH.md
codebase/docs/team_updates/STATUS_SAMIK.md
codebase/docs/team_updates/STATUS_PRATIK.md
codebase/docs/team_updates/STATUS_ABHIJAN.md
codebase/docs/team_updates/STATUS_AYUSH.md
```

Direct requests use a separate, clearly labelled file:

```text
MESSAGE_<FROM>_TO_<TO>_<YYYY-MM-DD>.md
```

Use `STATUS_TEMPLATE.md` for status updates. Append or replace the current status in
your own file; do not edit another person's status file.

## Minimal Git loop

```text
git fetch origin
git log --oneline --all -- codebase/docs/team_updates
```

Check only the central integration inbox:

```text
git log --oneline origin/suyash/sih26177-rescue-integration -- \
  codebase/docs/team_updates
```

Read a message without switching branches:

```text
git show origin/<sender-branch>:codebase/docs/team_updates/<message-file>
```

After meaningful work:

```text
git add codebase/docs/team_updates/STATUS_<NAME>.md <actual-work-files>
git commit -m "status(<name>): <checkpoint>"
git push
```

Each owner records `last_inbox_commit_seen` in their own status file. Reading a routine
message does not require an acknowledgement commit or chat reply. Commit only actual
progress, an interface decision or a real blocker.

## What belongs in Git

Commit code, configuration examples, dataset manifests, metrics, class counts, audit
summaries, interface changes and status notes. Keep raw datasets, virtual environments,
cloud credentials, large weights, generated videos and private evidence outside Git.

Routine work does not require per-file SHA-256 confirmation. Hashes remain useful for
the final selected model weights, protected identities/receipts and frozen release
artifacts.
