# Model-hash demo — operator sheet

**Bundle** `IHQ-20260821-001` · **Alpha key** `20c7be09…f152c0f7` (OP-TEE, Jetson)
**Approved model** `f59b3d83…c83b36` · **Unapproved probe** `c514a021…cbbf5a`

---

## The one line to say

> **One witness is enough to refuse a command. It takes two to permit one.**
> A degraded swarm becomes more conservative, never more permissive.

---

## The matrix — every cell verified

| peers live | CLEAN (approved model) | MODEL-SWAP (unapproved) |
|---|---|---|
| **2** — full | `ACCEPTED` · acks 2 · **semantic 2** | `REJECTED` · disputes 2 → HOLD |
| **1** — degraded | `NO_QUORUM` · acks 1 → **HOLD** | `REJECTED` · disputes 1 → HOLD |
| **0** — isolated | `NO_QUORUM` · acks 0 → HOLD | `NO_QUORUM` → HOLD |

Three things a reviewer should notice:

1. **Top-left** — `semantic 2` means both peers actually compared the perception
   claim. An `ACCEPTED` with `semantic_acks: 0` would be cryptographically sound
   and semantically unverified; the runner fails such a run rather than passing it.
2. **Middle-left** — the honest peer *agreed*, and the command was **still** not
   authorised. Agreement is not authority; quorum is.
3. **Bottom-right** — with no witnesses the attack yields `NO_QUORUM`, not
   `REJECTED`. The swarm does not claim to have caught what it never verified.

---

## Running it — Jetson

```bash
./run_model_hash.sh --matrix      # show the table, run nothing
./run_model_hash.sh --check       # preconditions only, changes nothing
./run_model_hash.sh               # FULL      (needs both peers up)
./run_model_hash.sh --degraded    # DEGRADED  (stop Bravo first)
./run_model_hash.sh --isolated    # ISOLATED  (stop both peers)
```

Each mode **asserts the peer count it names** and stops if it finds a different
one, so a mode can never quietly demonstrate a scenario other than the one it
claims. Every mode also re-runs the OP-TEE preflight, because that needs no peers.

**Suggested live order** (~4 minutes):

1. `--matrix` — state the claim before proving it
2. *(both peers up)* `./run_model_hash.sh` → ACCEPTED + REJECTED
3. **Ctrl+C Bravo**, wait for `bravo stopped; port 51001 released`
4. `./run_model_hash.sh --degraded` → NO_QUORUM + REJECTED ← **the moment**
5. Restart Bravo with `start_bravo.ps1`

---

## Where the predictions live

Full-quorum predictions are in the signed bundle: `public/clean-case.json`,
`public/model-swap-case.json`.

Degraded predictions are derived once, from those same files, into
`scenarios/` — every wire field copied verbatim, only the `expected` block
differs. `public/` and `PUBLIC_SHA256SUMS` are never touched, so the bundle's
identity is unchanged.

There is deliberately **no `--expect-outcome` flag**. An operator who can type
the expected verdict at run time is an operator who can make any result "pass".
A reviewer can diff a scenario against its source case and see exactly what was
predicted, before the run happened.

---

## If a reviewer asks

**"Did you load tampered weights?"**
No. Both model files were hashed when the bundle was built; the resulting hashes
travel in signed replay cases. The claim is: *a receipt whose model provenance is
not on the signed allowlist is rejected.* Nothing is hot-loaded.

**"Does OP-TEE protect the AI?"**
No. OP-TEE protects Alpha's **signing key** — it never leaves secure storage.
Inference, model loading and pose estimation are **not** attested.

**"Are all three drones hardware-secured?"**
No. Only Alpha has an OP-TEE hardware identity. Bravo and Charlie use software
simulation keys, each holding only its own.

**"Can the attack operator fake a pass?"**
No. The dashboard API accepts only two case *names* — no path, no hash, no
expected verdict, no shell command. He triggers; the peers decide and sign their
own votes.

**"What does this NOT cover?"**
The semantic layer. Model-hash exercises **provenance** only — no co-visibility,
no perception comparison. The adversarial-patch case is the separate physical
webcam beat.

---

## Evidence

Every run writes create-once JSON to `evidence/protocol/` tagged
`FULL-…` / `DEGRADED-…` / `ISOLATED-…`, plus an OP-TEE preflight in
`evidence/optee/`. Peer vote reasons land in each peer's JSONL. Nothing is
overwritten; a failed run is evidence too and is never deleted or recoloured.
