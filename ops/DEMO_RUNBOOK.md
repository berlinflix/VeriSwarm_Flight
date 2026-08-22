# VeriSwarm model-hash demo — runbook

**Bundle:** `IHQ-20260821-001` · **Source:** `4483d87c` · **Roster:** alpha / bravo / charlie

Everything below is verified on the real hardware. Commands are short on purpose:
type them, don't paste them.

---

## Who does what

| Person | Machine | Job |
|---|---|---|
| **Suyash** | Jetson + Charlie laptop | brings the table up, runs the terminal cases, calls GO/NO-GO |
| **Abhijan** | Mac `.14` | **triggers the attack from the dashboard** — he is the attack operator |
| **Samik** | Bravo laptop | starts Bravo; stops it on cue for the degraded case |

Abhijan triggers attacks. He cannot change the verdict — the API accepts only two
case *names*, never a hash, a path, or an expected outcome. Peers decide
independently and sign their own votes. Worth saying out loud.

---

## Phase 0 — bring the table up (~3 min, before anyone watches)

1. Switch on. Three Ethernet cables. Power the Jetson. Wait ~60 s.
2. **Charlie laptop** → right-click `C:\VeriSwarm\start_charlie.ps1` → *Run with PowerShell*
   → wait for `READY charlie on :51003`. Leave the window open.
3. **Bravo laptop** → right-click `C:\VeriSwarm\start_bravo.ps1` → *Run with PowerShell*
   → wait for `READY bravo on :51001`. Leave the window open.
4. **Suyash** → `ssh akaberlinflix@192.168.50.10`
5. ```
   vs check
   ```
   All PASS. If the clock line fails, the Jetson booted before Charlie — run
   `sudo systemctl restart systemd-timesyncd`, wait 20 s, `vs check` again.

Both peer windows should be visible to the room. They are the other two drones.

---

## Phase 1 — state the claim, then prove it

```
vs matrix
```

> "One witness is enough to refuse a command. It takes two to permit one."

### Case 1 — full quorum

```
vs full
```

| | expected |
|---|---|
| Q-CLEAN | `ACCEPTED` · acks=2 · **semantic_acks=2** · disputes=0 |
| Q-MODEL-SWAP | `REJECTED` · disputes=2 · HOLD |

Point at **`semantic_acks=2`**: both peers actually compared the perception
claim. An `ACCEPTED` with `semantic_acks: 0` is cryptographically sound and
semantically *unverified* — the runner fails such a run rather than passing it.

---

## Phase 2 — Abhijan runs the attack from the dashboard

**Suyash**, in a second Jetson window (or `tmux`):

```
vs dash
```

Leave it running. Ctrl+C here kills Abhijan's session.

**Abhijan**, on his Mac:

```
./start_abhijan_mac.sh
```

Then `http://127.0.0.1:5175` → **Model Hash** → **CLEAN + MODEL HASH**.

He may re-run as often as he likes. Each click gets a fresh sequence and a fresh
create-once evidence file; nothing is overwritten and no result is cached.

The UI runs CLEAN first and **refuses to continue** unless it reaches two
semantic ACKs. That is correct behaviour, not a bug.

### Rescue-dashboard integration

For the combined rescue dashboard, Abhijan instead runs from the rescue-security
worktree:

```bash
./ops/start_abhijan_rescue_mac.sh
```

This keeps the same secure qualifier tunnel while also starting the rescue collector on
the Mac. After the two dashboard cases complete, the UI displays the create-once attack
evidence filename. Abhijan publishes it with:

```bash
./ops/publish_rescue_authorization.sh <filename-shown-by-dashboard>
```

The target is `alpha`: Alpha originated the signed approved/unapproved provenance cases;
Bravo and Charlie independently voted on them. This is not a runtime model hot-load.

---

## Phase 3 — kill a drone, on camera

**Samik**: click the Bravo window → **Ctrl+C once** → wait for
`bravo stopped; port 51001 released`.

**Suyash**:

```
vs peers
```
→ `UP charlie`, `DOWN bravo`

```
vs deg
```

| | expected |
|---|---|
| Q-CLEAN-DEGRADED | `NO_QUORUM` · acks=1 · semantic_acks=1 · missing=1 → **HOLD** |
| Q-MODEL-SWAP-DEGRADED | `REJECTED` · disputes=1 · missing=1 → **HOLD** |

**Say this — it is the best line in the demo:**

> "Charlie **agreed** with the clean command, and it still was not authorised.
> Agreement is not authority; quorum is. But Charlie alone was enough to reject
> the attack. One witness can refuse. It takes two to permit."

### Restore

**Samik**: run `start_bravo.ps1` again → `READY bravo on :51001`.

No need to restart Charlie. Sequences are timestamp-seeded, so switching modes
and re-running never produces `duplicate_sequence`.

---

## Phase 4 — optional closer: total isolation

**Samik and Suyash**: Ctrl+C **both** peer windows.

```
vs iso
```

| | expected |
|---|---|
| Q-CLEAN-ISOLATED | `NO_QUORUM` → HOLD |
| Q-MODEL-SWAP-ISOLATED | `NO_QUORUM` → HOLD |

> "With no witnesses the attack is **not** reported as rejected. The swarm does
> not claim to have caught what it never verified."

Reviewers tend to like this one — most systems fail loud and wrong here.

---

## The complete matrix

| peers live | CLEAN | MODEL-SWAP |
|---|---|---|
| **2** `vs full` | `ACCEPTED` acks=2 semantic=2 | `REJECTED` disputes=2 |
| **1** `vs deg` | `NO_QUORUM` → HOLD | `REJECTED` disputes=1 |
| **0** `vs iso` | `NO_QUORUM` → HOLD | `NO_QUORUM` → HOLD |

Every cell verified on real hardware. Every run writes create-once JSON evidence
plus per-peer JSONL vote logs.

---

## Questions a reviewer will ask

**"Only one drone disputed — is that really consensus?"**
The thresholds are derived, not chosen: `T_acc = ⌊2k/3⌋+1`, `T_rej = ⌊k/3⌋+1`,
and `T_acc + T_rej > k` so ACCEPT and REJECT are mutually exclusive. The
asymmetry is deliberate: authorising motion is dangerous, refusing is safe, so
strong evidence is required to act and weak evidence to hold. And note that
`REJECTED` and `NO_QUORUM` both release HOLD — the asymmetry changes only how
honestly the refusal is described. `model_hash_not_approved` is objectively
verifiable, not an opinion: anyone can recompute the hash.

**"How many malicious drones can you tolerate?"**
At N=3, `f = ⌊(k−1)/3⌋ = 0` — **zero**. Be direct about it. But a traitor still
cannot force motion: `T_acc = 2` needs both peers, so malicious ACK + honest
DISPUTE = 1 ack → `NO_QUORUM` → HOLD. The worst it achieves is grounding the
drone. Thresholds scale with roster size; at N=5, `T_rej` becomes 2 and `f = 1`.

**"Did you actually load tampered weights?"**
No. Both weight files were hashed when the bundle was built; the hashes travel
inside signed replay cases. The correct claim is *"the swarm rejects a receipt
whose model provenance is not on the signed allowlist."* Not *"we hot-loaded
malicious weights and caught them."*

**"Does OP-TEE prove the AI is trustworthy?"**
No. OP-TEE protects Alpha's **signing key** — it never leaves secure storage.
It does not attest YOLO inference, model loading, or pose estimation. Only Alpha
has hardware-backed identity; Bravo and Charlie use software simulation keys.

**"Can the attack operator fake a result?"**
No. The API accepts two case names only. Evidence files are create-once and the
service refuses to overwrite. Each peer signs its own vote with its own key, and
those keys exist on no other machine.

---

## If something breaks

| Symptom | Cause | Fix |
|---|---|---|
| `clock NOT synchronized` | Jetson booted before Charlie (no RTC battery) | `sudo systemctl restart systemd-timesyncd`, wait 20 s |
| `DEGRADED needs exactly 1 peer, found 0/2` | wrong peer count | `vs peers`, start or stop one |
| `semantic_acks: 1` on a full run | a peer's clock drifted | **stop** — resync; do not re-run for a nicer number |
| `ok_no_observation` in a peer log | clock skew > 250 ms | resync that peer |
| peer will not start, port busy | it is already running | use that window, or Ctrl+C and relaunch |
| `no token on Alpha` (Abhijan) | `vs dash` not running | start it |

**Never** relax a threshold, widen the freshness window, or re-run to obtain a
better number. Preserve failed evidence — a failed run is evidence too.

---

## Claims not to make

Military-ready · certified · unhackable · full swarm autonomy · trusted
inference · three hardware roots · protected end-to-end flight.

What this demonstrates is narrow and real: **a hardware-signed receipt, verified
independently by peers holding separate keys, rejected on provenance, with the
refusal releasing a hold instead of a command — and correct behaviour preserved
as the swarm degrades.**
