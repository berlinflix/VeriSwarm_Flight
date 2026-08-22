# MESSAGE — SUYASH TO SAMIK AND PRATIK

Date: 2026-08-22

Topic: Offline rescue-event delivery interface is ready

Implementation commit: `78d5df0`

The durable rescue producer outbox is complete and independently usable. It persists a
validated event before any network attempt, preserves FIFO order, retries idempotent event
posts with a bound, accepts exact collector duplicates, and retains conflicts/corruption
for inspection. Link, service and authentication failures remain pending and do not delete
events.

Continue your existing work without waiting for confirmation. Adopt this interface when
your first producer output is ready.

## Read the interface

```text
git fetch origin
git show origin/suyash/sih26177-rescue-integration:codebase/docs/RESCUE_DATA_PLANE.md
git show --stat 78d5df0
```

Relevant implementation:

```text
codebase/rescue/outbox.py
codebase/tools/rescue_event_sender.py
codebase/tests/test_rescue_outbox.py
```

## Samik

Cloud training remains independent and should continue. When the inference adapter emits
its first genuine `observation`, enqueue that exact event through one perception-node
outbox and publish the resulting collector integration test in your normal work commit.
The outbox is not a prerequisite for dataset download, label auditing or training.

## Pratik

Use one outbox per simulated producer/node for mission, coverage, vehicle and link events.
Do not share one SQLite file across node identities. A temporarily unavailable collector
must leave telemetry pending; recovery must drain it in source-sequence order.

## Status response

Record adoption, test output or a real integration conflict in your branch-owned status
file and commit it. Do not send a confirmation-only response and do not stop current work
while waiting for Suyash.
