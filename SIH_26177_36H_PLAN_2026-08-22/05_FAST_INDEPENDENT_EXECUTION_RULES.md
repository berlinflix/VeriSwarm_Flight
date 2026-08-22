# Fast independent execution rules

## Default mode: build without waiting

Every owner may design, implement, test, retrain, rerun and replace intermediate outputs
inside their assigned branch and file surface without asking Suyash to confirm each file.
Notify the team at milestones; do not request permission for routine in-scope work.

During development, use:

- Git branch and commit for code/config/document traceability;
- readable artifact names and a short `STATUS.md` for model/data/simulator work;
- `scratch/<owner>/<task>/latest/` for overwriteable development output; and
- automated tests and interface examples as the handoff proof.

Intermediate screenshots, datasets, converted labels, checkpoints, simulator frames,
configs and console logs do **not** need individual SHA-256 files or Suyash approval.

## When SHA-256 is actually required

Generate hashes automatically only for:

1. the final selected model and class map used by VeriSwarm's model-identity policy;
2. the final external demo bundle at feature freeze, using one generated `SHA256SUMS`;
3. any already-preserved evidence whose existing identity must remain unchanged; and
4. a file transferred through an unreliable channel when corruption is actually suspected.

One final manifest is enough. Do not manually hash the same file repeatedly on each
machine. Do not make another owner wait merely to reconfirm a matching hash.

Dataset duplicate detection may still use internal hashes as an automated quality tool;
it does not require human approvals or per-file reports.

## Development runs versus release runs

### Development runs

- may reuse `scratch/.../latest`;
- may overwrite failed experimental outputs;
- need only concise logs and the current Git commit;
- may rerun immediately after a fix; and
- do not need create-once IDs, frozen manifests or approval messages.

### Final demo/release runs

- begin only after the hour-30 feature freeze;
- use a new run directory and retain the final evidence;
- automatically generate one manifest/checksum file; and
- are not overwritten after being presented as acceptance evidence.

Failed historical qualification campaigns remain preserved, but their strict procedure is
not imposed on ordinary rescue development.

## Handoffs

A normal handoff contains only:

```text
owner and branch
commit
what works
exact run/test command
interface/sample output
known blocker or limitation
```

The receiver may integrate immediately. They do not wait for Suyash to approve every file
or hash. If an interface is compatible and tests pass, continue.

## Approval is required only for

- changing a shared schema in a backward-incompatible way;
- editing another owner's files without coordination;
- merging into the integration/release branch;
- destructive data/repository operations;
- exposing credentials, publishing restricted data or spending paid cloud resources;
- physical actions that can damage hardware or endanger people; and
- the final feature-freeze and live-demo GO/NO-GO decision.

Everything else inside the assigned lane is owner-authorized.

## Reporting rhythm

Post one short update every two hours or at a real handoff—not after every command. Use:

```text
DONE:
RUNNING:
NEXT:
BLOCKER: NONE or one concrete blocker
BRANCH/COMMIT:
```

Silence between milestones is normal. Suyash coordinates interfaces and integration; he
does not act as a checksum approval queue.
