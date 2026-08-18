# Event schema — the contract between the swarm and the console

**Frozen.** New event *types* may be added. Existing field names must not be
renamed or repurposed. Consumers must ignore unknown types and unknown fields, so
both sides can change without waiting on each other.

This file is the reason M2 is not blocked on M1. The console is written against
this document and `tools/mock_events.py`, never against a running swarm.

## Transport

One JSON object per line, appended to **`results/live_events.jsonl`**.

Producers use `node/events.py` → `EventLog.emit()`. Consumers use
`events.follow()` (tail live) or `events.read_events()` (read all).

Why a file rather than a socket: it works with the network unplugged (a demo beat
in its own right), a crashed console loses nothing and replays from disk, and the
same bytes are both the live feed and the post-mortem record. No port to argue
with the venue firewall about.

## Every event carries

| field | type | meaning |
|---|---|---|
| `seq` | int | monotonic per-log counter, starts at 1. **A gap means you dropped events** — say so on screen rather than rendering an incomplete security claim. |
| `t` | int | wall-clock milliseconds since epoch |
| `type` | string | one of the types below |

---

## `round`

Frames one attestation cycle. Everything between a `start` and its matching `end`
belongs to that round.

```json
{"seq":1,"t":1723600000123,"type":"round","round":6,"phase":"start"}
```

| field | type | notes |
|---|---|---|
| `round` | int | 1-based round index |
| `phase` | string | `"start"` or `"end"` |

## `receipt`

A drone signed an inference result. This is the attested claim under review.

```json
{"type":"receipt","node":"alpha","round":6,"action":[1.0,0.0,0.0],
 "model_hash":"ab12…","frame_hash":"cd34…","backend":"optee","sign_ms":33.97}
```

| field | type | notes |
|---|---|---|
| `node` | string | the originator |
| `action` | `[f, l, v]` | forward / lateral / vertical, each in `[-1, 1]`. **`[1,0,0]` means "clear path, full speed"** — an empty detection set produces exactly this, which is why a patched drone accelerates. |
| `model_hash` | hex | SHA-256 of the weight file actually loaded |
| `frame_hash` | hex | SHA-256 of the frame bytes; binds the receipt to an image |
| `backend` | string | `"software"` or `"optee"`. **Show this** — `optee` is the hardware-root reveal. |
| `sign_ms` | float | signing latency |

## `covisibility`

Why a peer did or did not apply the semantic check. **Render this prominently.**

A gate that abstains silently is the single most dangerous failure mode in the
system: every peer ACKs, the swarm looks perfectly healthy, and the only layer
that catches an adversarial patch is switched off. If the console shows all-green
without showing *why*, it is actively misleading.

```json
{"type":"covisibility","node":"bravo","target":"alpha","covisible":false,
 "method":"low_parallax","iou":0.94,"parallax_deg":1.2,"orb_inliers":null,
 "o_min":0.1,"phi_min":23.0,"detail":"NOT co-visible (redundant viewpoint): …"}
```

| field | type | notes |
|---|---|---|
| `node` | string | the peer deciding |
| `target` | string | the originator being checked |
| `covisible` | bool | whether the semantic clause was applied |
| `method` | string | `geometric` \| `features` \| `low_parallax` \| `none` |
| `iou` | float\|null | measured footprint overlap |
| `parallax_deg` | float\|null | inter-view angle at the shared patch |
| `orb_inliers` | int\|null | set only when the image fallback ran |
| `o_min`, `phi_min` | float | the thresholds in force |
| `detail` | string | pre-formatted one-liner, safe to print as-is |

`method` values worth distinguishing on screen:

- **`geometric`** — footprints overlap. Normal.
- **`features`** — geometry said no and ORB found a shared scene. This can restore
  overlap only when `phi_min` is disabled; image matches alone do not prove
  angular diversity.
- **`features_no_parallax`** — ORB found a shared scene, but no trusted viewpoint
  evidence proves `phi >= phi_min`; the peer abstains.
- **`geometry+features`** — both pose geometry and available image evidence were
  checked. A claimed high IoU cannot bypass conflicting frames.
- **`low_parallax`** — they overlap but view from effectively the same place, so the peer abstains. Its vote would have carried no independent evidence.
- **`none`** — no shared view and no frames to fall back on.

## `vote`

```json
{"type":"vote","node":"bravo","target":"alpha","decision":"DISPUTE",
 "reason":"semantic_disagreement","delta":1.83}
```

| field | type | notes |
|---|---|---|
| `decision` | string | `ACK` or `DISPUTE` |
| `reason` | string | see below |
| `delta` | float\|null | L2 between claimed and observed action |

**Not all ACKs are equal, and the console must not draw them the same.**

| reason | decision | meaning |
|---|---|---|
| `ok` | ACK | co-observed and agreed. **The only reason that is real verification.** |
| `ok_no_covisibility` | ACK | abstained — did not share the view |
| `ok_no_observation` | ACK | abstained — no local perception at all |
| `semantic_disagreement` | DISPUTE | saw the scene, disagrees with the claim |
| `model_hash_not_approved:…` | DISPUTE | provenance — unapproved weights |
| `bad_signature` | DISPUTE | cryptographic |
| `unknown_drone_id:…` | DISPUTE | not on the roster — a rogue node |
| `stale_receipt` | DISPUTE | outside the freshness window — a replay |

Suggested rendering: `ok` solid green; the two abstentions **grey, not green**.
Drawing an abstention as a pass is exactly the confusion the schema exists to
prevent.

## `verdict`

```json
{"type":"verdict","node":"bravo","target":"alpha","outcome":"REJECTED",
 "acks":1,"disputes":2,"semantic_acks":1,"consensus_ms":12.4}
```

| field | type | notes |
|---|---|---|
| `node` | string | **who tallied.** Every node tallies independently. |
| `outcome` | string | `ACCEPTED` \| `REJECTED` \| `NO_QUORUM` |
| `acks`, `disputes` | int | vote counts |
| `semantic_acks` | int | ACK votes with reason `ok`; always `0 <= semantic_acks <= acks` |

Two things to build here:

1. You will receive **one verdict per node**, not one per round. That is the
   point — the originator is the drone under scrutiny, so its own tally proves
   nothing. *"4 of 4 nodes independently reached REJECTED"* is the line worth
   putting on screen.
2. `semantic_acks == 0` on an `ACCEPTED` means **nobody checked the claim**. It
   is cryptographically sound and semantically unverified. Do not render it as a
   plain green ACCEPTED.

A verdict may be revised as late votes arrive — expect `NO_QUORUM` then a
decisive outcome for the same target.

## `safe_action`

What the drone actually did about the verdict.

```json
{"type":"safe_action","node":"alpha","action":"SAFE_FALLBACK",
 "outcome":"REJECTED","semantic_acks":1}
```

| `action` | meaning |
|---|---|
| `EXECUTE` | verified accept — the supervisor released the commanded action |
| `HOLD` | **the supervisor refused to release a command.** The most common non-nominal outcome by far — build this state first. |
| `EXECUTE_DEGRADED` | legacy display value; must not authorize motion |
| `SAFE_FALLBACK` | rejected — hover |
| `DEFER` | no quorum or no semantic quorum — cautious hold |

`outcome` is normally the consensus result (`ACCEPTED` / `REJECTED` /
`NO_QUORUM`), but carries **`NO_DECISION`** when the round never reached consensus
at all — a frame that failed to arrive, perception that raised, depth that failed,
or attestation that errored. In that case `reason` names the specific failure
(`frame_unavailable`, `perception_failure`, `depth_failure`,
`attestation_failure`, `invalid_action`).

`safe_action` also carries `reason`, plus `requested` and `released` on a
completed round. **`requested` is what perception asked for; `released` is what
the supervisor allowed.** Showing both side by side is the clearest single view of
the safety boundary doing its job — and when they differ, `reason` says why.

Reasons the supervisor emits: `authorized`, `operator_abort`,
`autopilot_guard_unhealthy`, `state_estimate_unhealthy`, `perception_unhealthy`,
`geofence_blocked`, `consensus_rejected`, `consensus_no_quorum`,
`semantic_quorum_missing`, `forward_clearance_unproven`,
`all_axis_clearance_unproven`, `command_expired`, `command_freshness_unproven`,
`invalid_action`, `invalid_consensus`, `invalid_health_evidence`,
`evidence_receipt_missing`, `consensus_receipt_mismatch`,
`evidence_command_mismatch`, `perception_claim_missing`.

The last four are protocol-v4 additions. A perception certificate authorizes
exactly the command it was earned for; presenting one alongside a command from
a different mission, epoch, round, or frame is refused. Worth surfacing
distinctly on the console — it is a replay of *valid* evidence, which looks
nothing like a signature failure and is far more interesting.

## `reputation`

```json
{"type":"reputation","node":"bravo","value":0.8}
```

`value` in `[0.1, 1.0]`. Floor is `r_min = 0.1`. A sustained liar reaches the
reaches 0.2 after four penalties and the 0.1 floor after five
(α = 0.05 up, β = 0.2 down).

## `isolation`

```json
{"type":"isolation","node":"alpha","round":6,"rho":0.6,"n_active_after":4}
```

`rho` is the fraction of that drone's recent receipts the swarm rejected; it is
isolated above `alpha = 0.5` over a window of `W = 10`. Worth a banner.

## `pose`

```json
{"type":"pose","node":"alpha","xyz":[3.0,0.0,14.0],"yaw":0.0,"pitch":0.0}
```

`xyz` in metres, local ENU, `z` is altitude. `yaw` and `pitch` in **radians**;
`pitch` is camera tilt off nadir (`0` = straight down). Drives the swarm map.

## `frame`

```json
{"type":"frame","node":"alpha","jpeg_b64":"…","w":640,"h":360}
```

Base64 JPEG. Large — throttle to ~1 Hz and only for nodes on screen.

## `depth`

The self-consistency check. **No peers involved** — this is a drone catching its
own attack, and it still works when it is flying alone.

```json
{"type":"depth","node":"alpha","contradicted":true,"nearest_m":7.9,
 "required_m":9.5,"commanded_forward":1.0,"safe_to_proceed":false,
 "reason":"contradiction","detail":"…"}
```

`contradicted: true` means the drone commanded forward motion its own rangefinder
says is not clear. `nearest_m: null` means no valid reading — **draw that as
"unknown", never as clear.**

## `attack`

Which attack the judge has armed.

```json
{"type":"attack","name":"patch","armed":true,"targets":["alpha"]}
```

`name` ∈ `patch`, `model_swap`, `provisioning`, `ota`, `rogue_node`, `replay`,
`collude`, `spoof_pose`.

## `log`

Free text. `{"type":"log","message":"…","level":"info"}`. Levels: `info`,
`warn`, `error`.

---

## Building against this

```bash
python -m tools.mock_events --scenario patch --rate 2
```

Writes a scripted attack to `results/live_events.jsonl` at 2 Hz. Scenarios:
`honest`, `patch`, `model_swap`, `collusion`, `unverified`, `provisioning`.

Read it:

```python
from node.events import follow

for event in follow():
    print(event["type"], event)
```

`--scenario unverified` is the one worth wiring early: it produces an `ACCEPTED`
with `semantic_acks: 0`. If the console renders that identically to a verified
accept, the bug is in the console, and it is the bug most likely to mislead a
judge.
