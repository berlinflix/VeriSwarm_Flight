# Abhijan model-hash authorization adapter

This adapter converts Suyash's retained qualification dashboard proof into the frozen
`veriswarm.rescue.event.v1` authorization contract. It does not copy model hashes,
receipts, votes, manifests or private keys into the rescue event log.

## Decision mapping

| Verified qualification result | Rescue decision | Reason |
|---|---|---|
| Clean proof, approved hash, accepted with two semantic ACKs | `ALLOW` | `model_hash_approved` |
| Model-swap proof, unapproved hash, rejected and zero action released | `QUARANTINE` | `model_hash_not_approved` |
| Missing, failed, inconsistent or no-quorum proof | `HOLD` | fail-closed reason |

`ALLOW` approves only the demonstrated model identity. It does not independently authorize
flight; mission, obstacle, link and vehicle-health gates remain Pratik's responsibility.

## Dry run

From `codebase/`:

```bash
python -m tools.rescue_authorization_adapter \
  --evidence /path/to/create-once-run.dashboard.json \
  --node alpha \
  --dry-run
```

Confirm that `--node` names the drone whose model identity was qualified. In the existing
qualification bundle the receipt originator is Alpha; do not label Bravo as the attacked
node merely because Bravo is one of the verification peers.

Development-only proof shapes are available at:

```text
examples/qualification_clean_dashboard_sample.json
examples/qualification_model_swap_dashboard_sample.json
```

Their repeated placeholder hashes are not real qualification evidence and must not be
shown as a hardware result.

## Live delivery

Start the rescue collector on Abhijan's Mac with a 32+ character token, then run the
adapter beside the retained qualification evidence:

```bash
export VERISWARM_RESCUE_TOKEN='<same-private-token-as-the-collector>'

python -m tools.rescue_authorization_adapter \
  --evidence /path/to/create-once-run.dashboard.json \
  --node alpha \
  --collector-url http://192.168.50.14:8770 \
  --state results/abhijan_authorization_state.json
```

The state file is written with owner-only permissions and retains an exact pending event
before network delivery. If delivery fails, rerun the identical command. The pending event
keeps its original ID, timestamp and sequence, allowing the collector to treat a lost
response as an idempotent duplicate.

Do not delete the state file while the corresponding rescue event log is retained. A new
state file restarts at sequence 1, which the collector correctly rejects after earlier
events from `abhijan-security`.

## Expected output

```text
PASS event_id=... decision=ALLOW duplicate=false
```

or:

```text
PASS event_id=... decision=QUARANTINE duplicate=false
```

Any invalid evidence, schema error, authentication failure, sequence conflict or network
failure prints `FAIL ...`, returns exit code 2 and retains a pending event when one was
prepared. Do not convert such a failure into `ALLOW`.
