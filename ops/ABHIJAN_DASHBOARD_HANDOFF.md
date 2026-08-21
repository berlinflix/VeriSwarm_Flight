# Abhijan — running the model-hash demo from the dashboard

**Your machine:** Mac, wired at `192.168.50.14`
**Bundle:** `IHQ-20260821-001`
**Frozen commit:** `4483d87c2e222421d9b413ee997f4ddae89cf8ab`

You need **one command**. Everything else is Suyash's job.

---

## What you are demonstrating

You trigger two pre-frozen cases and the swarm decides the outcome independently:

| Case | Model hash | Expected result |
|---|---|---|
| **CLEAN** | `f59b3d83…` — on the signed allowlist | `ACCEPTED`, 2 semantic ACKs |
| **MODEL HASH** | `c514a021…` — *not* on the allowlist | `REJECTED`, 2 disputes, `HOLD [0,0,0]` |

**You cannot influence the verdict.** The API accepts only the two case *names* —
no file path, no hash, no expected outcome, no shell command. Alpha signs with its
OP-TEE hardware key on the Jetson; Bravo and Charlie verify independently and sign
their own votes. That is the property worth saying out loud: *the attack operator
cannot write the verdict.*

**Say this:** "The swarm rejects a receipt whose model provenance is not on the
signed allowlist."
**Do not say:** "We loaded tampered weights onto the drone and it was caught."
Nothing is hot-loaded — the hashes were measured when the bundle was built.

---

## One-time setup (2 minutes, do it once — then it is passwordless forever)

```bash
[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
ssh-copy-id akaberlinflix@192.168.50.10
```

The second command asks for the Jetson password **once**. After that you are
never prompted again. Skip this and everything still works — you will just type
the password once per session.

---

## Before you start, Suyash must have

1. Charlie and Bravo peers running (`READY` on `:51003` / `:51001`)
2. `~/run_model_hash.sh` passed on the Jetson
3. `~/start_dashboard.sh` running

**You need nothing from him.** No token, no header, no config. Your launcher
fetches the session token over the SSH connection it already opens.

---

## Run it

```bash
cd /Volumes/HyperDrive/Development/VERISWARM_SIH_MODEL_SWAP
./ops/start_abhijan_mac.sh
```

If the repo lives elsewhere:

```bash
VERISWARM_REPO=/your/path ./ops/start_abhijan_mac.sh
```

It verifies the frozen commit, pings Alpha, opens the tunnel, fetches the token,
checks the backend, and starts the dashboard.

Then open **http://127.0.0.1:5175**, choose **Model Hash**, and run
**CLEAN + MODEL HASH**.

Press **Ctrl+C** in that terminal when finished — it closes the tunnel too.

The token is minted fresh each session, lives only in memory on your Mac, and is
deleted from the Jetson the moment Suyash stops the service.

---

## Rules while running

- **Do not double-click** the run button, and do not refresh mid-run.
- The UI runs CLEAN first and **refuses to continue** if it does not reach two
  semantic ACKs. That is correct behaviour, not a bug — a clean case that passes
  with zero semantic acknowledgements has proven nothing.
- You may re-run as many times as you like. Each run gets a fresh sequence and a
  fresh create-once evidence file; nothing is overwritten.
- **Never edit, rename, delete or recolour an evidence file.** A failed run is
  evidence too.

---

## If something breaks

| What you see | Cause | Do this |
|---|---|---|
| `cannot reach 192.168.50.10` | cable, or your IP is not `192.168.50.14` | check the cable and `ifconfig` |
| `tunnel failed` | wrong password, or Suyash has not started the dashboard | ask Suyash |
| `no token on Alpha` | his dashboard service is not running | ask Suyash to run `~/start_dashboard.sh` |
| `API did not answer` | his service stopped | ask Suyash to restart it |
| `wrong commit` / `worktree dirty` | wrong checkout | **stop** — tell Suyash, do not `git reset` |
| Port 8765 busy | old tunnel | the script closes it automatically; re-run |
| Browser blank | Vite or browser state | read the terminal output; never open `index.html` via `file://` |
| CLEAN gets `semantic_acks: 1` | a peer's clock drifted | **stop** — Suyash re-syncs; do not re-run to "get a better result" |

Anything unexpected: **preserve it and report it.** Never retry to obtain a nicer
number, and never ask for a threshold or quorum to be relaxed.

---

## What this does *not* prove

Be ready to say this if a judge pushes:

- OP-TEE protects Alpha's **signing key**. It does **not** attest YOLO inference,
  model loading, or pose estimation.
- This is a **hash-policy** test over **detector-derived replay** evidence — not
  live three-camera co-observation, and not a runtime weight swap.
- Only Alpha has hardware-protected identity. Bravo and Charlie use software
  simulation keys. Do not imply three hardware roots.
