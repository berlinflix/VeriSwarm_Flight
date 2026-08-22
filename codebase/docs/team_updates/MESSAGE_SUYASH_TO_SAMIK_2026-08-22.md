# MESSAGE — SUYASH TO SAMIK

Date: 2026-08-22

Topic: Rescue-perception current status and autonomous continuation

Read with integration base: `suyash/sih26177-rescue-integration`

Samik, continue the rescue-perception work independently. You do not need a new
authorization or confirmation before creating the cloud environment, downloading the
chosen datasets, auditing labels, training, evaluating or benchmarking. Use the cloud
GPU environment you judge appropriate and record its reproducible runtime details.

## Commit your current status now

Create or update:

```text
codebase/docs/team_updates/STATUS_SAMIK.md
```

Commit and push it on `samik/sih26177-perception` with a message such as:

```text
status(samik): report rescue dataset and cloud training progress
```

Report only the current facts:

1. Cloud environment provider/runtime, GPU and whether it is ready.
2. Dataset sources selected and download progress.
3. Images and annotations downloaded or converted so far.
4. Frozen rescue classes currently being used.
5. Corrupt-file and duplicate-check progress.
6. Path/status of the first 100-image rendered label audit.
7. Whether the five-epoch smoke job has started; include the command and output path if
   it has.
8. Latest measured metrics, even when incomplete.
9. Actual blockers, or `NONE`.
10. Latest code commit and any shared-interface change.

Continue working after pushing the status commit; do not wait for a reply unless you have
a genuine shared-interface conflict or need data/hardware owned by another teammate.

## Adapter follow-up during normal work

Candidate `15d1af6` is a useful development base. Before final model integration, extend
it during your normal implementation to bind inference to the expected model ID/hash and
class-map identity, reject stale frames, and handle empty frames/runtime failures. This is
not a prerequisite for starting cloud training.

Keep raw datasets, cloud credentials, environments and large weights outside Git. Commit
manifests, code, audit summaries, metrics and status updates.
