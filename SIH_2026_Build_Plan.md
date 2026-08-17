# SIH 2026 — VeriSwarm Live Demo: 4-Member Build Plan

> ### ⚠️ Read [`urgent_new_changes.md`](urgent_new_changes.md) before this file.
> It carries newer decisions and **overrides anything here that contradicts it.** This plan
> is not rewritten on every change — changes land in that file first and are folded in later.

## Context

Smart India Hackathon internal selection at VIT Chennai: ~1000 applicants, few teams
advance. The internal round is judged on a **full working demo** (confirmed), with
**4–8 weeks** available and a team of **4** — one experienced (Suyash, author of the
VeriSwarm protocol and its IEEE paper) and **three beginners**.

The asymmetric advantage is that the hard part is already built and peer-reviewed:
`~/veriswarm/` contains the complete protocol (receipts, Ed25519 signing, three-layer
verification, PBFT tally, reputation weighting, isolation), a working OP-TEE Trusted
Application on a physical Jetson Orin Nano, real gRPC distributed runs at 3–11 nodes,
102 passing tests, and a public repo.

The gap is **legibility**. The protocol has no interface — `eval.run_all` prints CSVs.
Nothing can be watched. In a judged demo that is fatal. So this plan does not add
protocol; it makes the existing protocol **live, visible, and attackable in front of a
judge**.

Framing decision (locked): **drone swarm** (option 1). The fixed-camera path stays
alive as configuration, because the tabletop camera node *is* a fixed node.

Intended outcome: a swarm demo containing simulated flying drones **and** a physical
Jetson-rooted camera node, reaching one consensus verdict, with a judge able to press a
button and watch an attack get caught. Unreal work is now split into independent scenario
packs rather than one shared map, because `.umap` / `.uasset` files are binary and are too
easy to desynchronize during parallel edits.

---

## The qualification thesis

One 40-second sequence wins this, and everything else is supporting evidence:

> **Run A:** drone flies A→B, an adversarial patch makes it report "clear path," it
> flies into the obstacle. **Run B:** same patch, VeriSwarm on — co-observing peers
> disagree, consensus REJECTS, the drone hovers and is isolated, mission continues.
> **Then the reveal:** "none of that security was simulated — that Jetson signed every
> receipt inside its secure element, and this camera is a live node in the same swarm."

Build order is chosen so that sequence exists as early as possible and everything after
it is risk reduction.

---

## Architecture: what actually gets built

Confirmed against the source. The protocol layer is **untouched**.

### Two one-line seam changes to existing files

| File | Current | Change |
|---|---|---|
| `node/client.py` → `Originator.originate()` | `input_bytes=b"\x00"*128` | accept real frame JPEG bytes as a parameter |
| `node/server.py` → `AttestationServicer.SubmitReceipt` | `my_observation=self.observation` (static manifest constant) | call a live observation callable; also pass `my_frame` / `originator_frame` |

`protocol/peer_consensus.py:276` `vote_on()` **already** accepts `my_frame` and
`originator_frame` — the ORB co-visibility fallback needs no protocol work, only
plumbing.

### Five new files (all in `node/`, all M1's)

1. **`node/events.py`** — the event bus. Appends JSONL to `results/live_events.jsonl`
   and broadcasts over a WebSocket. **This is the contract that unblocks everyone.**
2. **`node/frame_source.py`** — `FileSource` (replay, the universal fallback and what
   everyone develops against), `WebcamSource` (cv2), `AirSimSource`, `GazeboSource`.
   Interface: `.read() -> np.ndarray`, `.pose() -> Pose | None`.
3. **`node/live_node.py`** — a `PerceptionWorker` thread holding the latest frame and
   latest action, so `SubmitReceipt` never blocks on YOLO. Feeds the seam above.
4. **`node/mission.py`** — the 2 Hz round loop: grab frame → `frame_to_action()` →
   `originate()` → emit events → apply verdict.
5. **`node/attack.py`** — the judge-facing control surface. A watched JSON file or tiny
   HTTP endpoint with `{"model_swap": [...], "patch": [...], "collude": [...]}`.

### Reused as-is — do not rewrite

- `perception/yolo_action.py` — `frame_to_action()`, `apply_patch()`, `model_hash()`
- `protocol/peer_consensus.py` — `PeerVerifier`, `ConsensusEngine`, `ReputationStore`
  (α=0.05, β=0.2, r_min=0.1)
- `protocol/covis_features.py` — ORB + RANSAC fallback (`DEFAULT_M_MIN = 15`)
- `protocol/geometry.py` — `Pose`, footprint IoU
- `signing/optee_backend.py` — `OPTEEReceiptSigner`, drop-in
- `node/common.py` — manifest, signer/verifier construction
- Existing weights: `yolov8n.pt` **and** `yolov8n_tampered.pt`

### No model is trained

YOLOv8n is pretrained COCO weights used as-is. `yolov8n_tampered.pt` is a weight-perturbed
copy made by `ensure_tampered()` at `eval/perception_scenarios.py:59` — seconds, no data.
`detections_to_action` is a hand-calibrated deterministic controller. Navigation is
waypoints + PID. The **only** gradient work is the adversarial patch, which optimizes
*input pixels* against a **frozen** network — not training: no dataset, no labels, no epochs.

### The attack panel — six attacks, one control surface

`node/attack.py` watches a JSON file that M2's console writes on a button press. Every
node re-reads it each round (≤500 ms latency). No web server, works fully offline.

```json
{ "model_swap": ["bravo"], "patch": true, "collude": ["delta","echo"],
  "spoof_pose": {"bravo": [100,0,14,0]}, "replay": false, "forge": false }
```

**Design rule: the defense is already built — what we implement is the *delivery*.** Each
attack must reach the node the way a real one would, because delivery is what makes it
read as an attack rather than a flag being flipped.

| # | Attack | Real-world delivery | Our delivery | Layer / reason code |
|---|---|---|---|---|
| 1 | Rogue node injection | Unrostered aircraft or unregistered key joins the swarm | Start a 4th process **not in the manifest**, let it broadcast | Crypto — `unknown_drone_id` |
| 2 | Replay | SDR records the drone-to-drone link, retransmits later | Re-broadcast a stored receipt | Crypto — `stale_receipt` (checked **before** signature) |
| 3 | **Compromised provisioning host** | The ground-crew workstation that flashes drone SD cards is owned. **Every aircraft it has provisioned carries trojaned weights.** Supply-chain class — SolarWinds-shaped | `tools/provision.py` writes weights into `nodes/<id>/`; compromised mode writes the tampered file. Re-provision Bravo **and** Charlie | Provenance — `model_hash_not_approved`, on both |
| 4 | Model swap, mid-flight | Compromised OTA update server or C2 link pushes new weights | Nodes **fetch weights over HTTP** from a "ground station"; attack changes what it serves | Provenance — `model_hash_not_approved` |
| 5 | Adversarial patch | Printed patch on a wall, vehicle, or rooftop. **No access to the drone at all** | Textured plane raised into the 3D scene (b1/b2) **+ a physically printed patch held to the Jetson's USB camera** | Semantic — `semantic_disagreement` |
| 6 | Colluding voters | Several drones compromised — captured units, one firmware bug across a batch, poisoned production run | Flagged peers ACK unconditionally | Reputation weighting; integer quorum holds |
| 7 | GPS spoof (T3) | SDR transmits counterfeit GNSS stronger than the satellites | Pose injected at message level — **transmitting counterfeit GNSS is illegal**, and that is the answer to give a judge | ORB image fallback restores the semantic layer |

**Do NOT demo signature forgery.** A real attacker cannot forge Ed25519 — demoing it
invites *"so you demoed something impossible."* Attack 1 exercises the same crypto layer
with a scenario that actually happens.

**Two implementation changes this requires (both small, both high-value):**

1. **`tools/ground_station.py`** — a ~20-line HTTP server that serves model weights.
   Nodes fetch at mission start and on an update push. The attack button changes what the
   server returns. This makes attack 4 a genuine update-channel compromise, which is the
   only *technically real* mid-flight weight-swap vector: a legitimate OTA flow is what
   triggers the model reload. A file swapped on disk does not take effect until the
   process reloads the model, so the OTA path is not a shortcut — it is the accurate one.
2. **Rogue-node runner** — a manifest-less broadcaster. Trivial: reuse `node/client.py`
   with a fresh keypair not in `peer_keys`.
3. **`tools/provision.py`** — the ground-crew flashing tool. Writes weights + node config
   into `nodes/<id>/` (the stand-in for an SD card). Honest mode writes `yolov8n.pt`;
   compromised mode writes `yolov8n_tampered.pt`. ~40 lines.

   **Critical structural detail: `provision.py` must NOT write the allowlist.** The
   `approved_models` list comes from a separate `mission_authority.json`, kept visibly
   distinct on screen. This is the separation of duties that makes attack 3 catchable —
   a compromised provisioner controls the weights but not the list they are checked
   against. It is the §3.1 trust assumption made concrete, and it is the answer when a
   judge asks *"why doesn't the attacker just add his hash to the allowlist?"*

   If the attacker controlled both, provenance falls and it drops to the semantic layer —
   which catches a tampered model whose behavior diverges, and a tampered model whose
   behavior does not diverge is not accomplishing anything. Rehearse that; don't demo it.

### Demo run-of-show — three attacks on stage, seven on the panel

A 5-minute pitch cannot carry seven attacks. Run three, then invite the judge to pick.

**Act 1 — the control comparison (the money shot).** Same scene, same patch, one flag.
- *Run A, VeriSwarm off:* patch raised → YOLO returns nothing → `detections_to_action([])`
  returns full forward → **the drone flies into the obstacle.** "This is what happens today."
- *Run B, VeriSwarm on:* identical up to the verdict → co-observing peers still see the
  obstacle → DISPUTE → REJECTED → Alpha hovers, is isolated, the swarm continues to B.

**Act 2 — the compromised provisioning host, escalating into collusion.** One continuous
story rather than two disconnected button presses, and it carries the provenance *and*
reputation layers together.

> "The laptop your ground crew uses to flash drone SD cards was compromised three weeks
> ago. Every aircraft it has provisioned since carries a trojaned perception model. Today
> it provisioned Bravo and Charlie."

- Re-provision Bravo and Charlie from the owned workstation. Both come up with tampered
  weights. Signature valid, drone rostered, and the action barely moves — measured L2
  0.05–0.11, invisible to any behavioral check. **Provenance catches both**, and catches
  them at every peer regardless of co-visibility.
- **Then escalate:** the same actor owns both aircraft, so they defend each other. Enable
  collusion. At N = 5 (k = 4, f = 1, T_acc = 3, T_rej = 2) two colluding ACKs cannot reach
  the accept threshold while two honest DISPUTEs clear the reject threshold. The integer
  quorum holds untouched, and reputation drives both colluders to the r_min = 0.1 floor
  in about four rounds.
- Closing line: *"One compromised laptop owned two aircraft. The swarm still never accepted
  a single bad decision. And notice what saved us — the approved-model list did not come
  from the machine that provisioned the drones."*

This is the strongest attack in the set: it needs no access to any aircraft, it scales to
the whole fleet, and it makes the reputation layer feel necessary instead of bolted on.

**Then the reveal, and the invitation:** "None of that security was simulated — that Jetson
signed every receipt in its secure element, and this camera is a live node in the same
swarm. There are four more attacks on the panel. Pick one."

The assigned pitch owner owns this script. The invitation is deliberate: judges who press a
button remember you.

### The patch is world-space, in two stages (decision locked)

Not a digital composite. The patch is a physical plane in the 3D scene, so every drone
sees it from its own angle and only the victim is fooled.

- **b1 — parallax occluder (Week 2).** A textured plane placed between Alpha and the
  obstacle, sized to block it from Alpha's position only; peers 3 m and 6 m to the side
  see past it. No optimization, no render gap, works first try. Trigger is
  `simSetObjectPose()` translating the plane up from below the ground plane — the judge
  **watches the patch slide into the scene**, which beats an invisible overlay.
  Avoid `simSpawnObject` / `simSetObjectMaterialFromTexture`: both exist but vary across
  AirSim and Colosseum builds, and a version hunt on the critical path is not affordable.
- **b2 — trained adversarial patch (Week 4).** A texture swap on the same plane, same
  geometry, same trigger. Must be trained on **sim renders**, not `bus.jpg`, and the EoT
  in `eval/adv_patch_transfer.py` widened beyond viewpoint to brightness/gamma jitter,
  mild blur, and scale — these stand in for Unreal's lighting, TAA, and mipmapping.

Peer placement becomes load-bearing, not cosmetic: Bravo and Charlie sit at the
**measured** 3 m and 6 m offsets (12° and 23°) from the §4.3 transfer study, so the demo
geometry *is* the experiment's geometry and Figure 6 is the evidence for what the judge
just watched.

`apply_patch()` in `perception/yolo_action.py` drops out of the live loop but stays for
the eval harness — do not delete it.

### Environment track strategy — no shared Unreal map

The team will not co-edit one Unreal environment through Git. Each Unreal owner builds a
separate, self-contained scenario pack and exports frames / video / Cosys-AirSim access for
the same Python verification pipeline.

| Track | Owner | Environment | Purpose | Handoff to M2 |
|---|---|---|---|---|
| **Dark urban** | **Pratik** | Night / dark-mode city scene with buildings, rooftops, alleys, and adversarial patches on rooftops or walls | Primary crash-vs-caught demo; strong visual story for rooftop patch attacks | Live Cosys-AirSim endpoint over LAN plus frame folders for patch lowered/raised |
| **Light terrain** | **Samik** | Daylight hills, mountains, desert, lakes, trees, and open terrain | Robustness scenario showing the same verifier works outside the city map | Recorded frame folders first; live Cosys-AirSim endpoint if ready |

These are **separate demonstrations**, not one stitched multi-world swarm. The same swarm
logic, event contract, YOLO action conversion, consensus, and dashboard are reused across
both. For judging, the safest run order is: Pratik's dark urban scene as the main live demo,
then Samik's light terrain frames as the evidence that the pipeline generalizes.

Each environment handoff must include:

1. Patch lowered frames for every visible drone camera.
2. Patch raised frames for the same drone positions.
3. Pose / vehicle-name metadata, including the mapping to `alpha`, `bravo`, `charlie`, etc.
4. At least one close, frame-dominating COCO-class obstacle or textured object that YOLOv8n
   detects without the patch.
5. A short note saying which drone is intended to be fooled by the patch and which peers
   should still see the obstacle.

### Frame delivery for the ORB fallback (Tier 3, optional)

`SubmitReceipt` carries only the receipt, not the frame. Rather than regenerate protos,
each node serves its latest frame over a tiny HTTP endpoint keyed by hash. **The peer
verifies the fetched frame against the signed `input_hash`**, so a spoofed frame can't
be substituted — cryptographically clean, and a good line for judges.

---

## The event contract — freeze this on Day 1

M1 writes it, M2 reads it, M3 feeds it. Everyone works against this and nobody blocks.

```json
{"t":1723600000123,"type":"receipt","node":"alpha","action":[1.0,0.0,0.0],"model_hash":"ab12…","frame_hash":"cd34…","backend":"optee","sign_ms":4.7}
{"t":…,"type":"vote","voter":"bravo","target":"alpha","decision":"DISPUTE","reason":"semantic_disagreement","delta":1.83,"covisible":true}
{"t":…,"type":"verdict","target":"alpha","outcome":"REJECTED","acks":1,"disputes":3,"consensus_ms":12.4}
{"t":…,"type":"reputation","node":"bravo","value":0.80}
{"t":…,"type":"isolation","node":"alpha","round":6,"rejected_fraction":0.6}
{"t":…,"type":"pose","node":"alpha","xyz":[3.0,0.0,14.0],"covis":{"bravo":0.64}}
{"t":…,"type":"frame","node":"alpha","jpeg_b64":"…"}
```

**M1's Day-1 deliverable is `tools/mock_events.py`** — replays a scripted attack as this
stream at 2 Hz. M2 builds the entire console against it and never waits for the backend.

---

## Tier gates — each tier is independently demoable

| Tier | By end of | Content | If everything after this fails |
|---|---|---|---|
| **T0** | Week 2 | 3 nodes, one laptop, replayed/webcam frames, live console, patch caught | **This + the deck qualifies you.** |
| **T1** | Week 3 | Jetson = Alpha, real OP-TEE signing, over LAN | The hardware reveal works |
| **T2** | Week 4 | Pratik dark-urban Cosys-AirSim A→B, crash-vs-caught | The money shot exists |
| **T2b** | Week 4 | Samik light-terrain recorded/live frames through the same Python verifier | Robustness evidence exists |
| **T3** | stretch | GPS-spoof + ORB fallback scene | Bonus only |

**Week 4 is a hard feature freeze.** Weeks 5–6 are rehearsal and backup only. Do not
negotiate this — a demo that works and is rehearsed beats a better demo that isn't.

---

## Member plans

| | Who | Platform | Owns |
|---|---|---|---|
| **M1** | **Suyash** | WSL + Jetson | Protocol integration, live mission loop, event bus, attack surface, OP-TEE, cross-machine networking |
| **M2** | **Abhijan** | **Mac** | Console (Streamlit), Cosys-AirSim RPC client, YOLO verification, and dashboard presentation for both environment tracks |
| **M3** | **Pratik** | **Windows** | Dark urban Unreal/Cosys-AirSim environment, rooftop/wall patches, primary live demo operations |
| **M4** | **Samik** | **Windows / Unreal-capable PC** | Light terrain Unreal/Cosys-AirSim environment: hills, mountains, desert, lakes, trees, and scenario export |

The Mac/Unreal split is not preference, it is a constraint: **Abhijan's Mac does not run
Unreal.** Unreal owners work in separate environment tracks and hand off images / live
Cosys-AirSim endpoints to the same platform-agnostic Python verifier.

### M1 — Suyash: Core, Integration, Hardware
Owns everything on the critical path. Assume the others' output may arrive late or broken.

**Week 1**
1. Freeze the event schema above; commit it as `docs/EVENT_SCHEMA.md`. Non-negotiable after Day 1.
2. Write `node/events.py` (JSONL + WebSocket broadcast).
3. Write `tools/mock_events.py` and hand it to M2 **by end of Day 2**. This is your highest-leverage hour of the whole project.
4. Write `node/frame_source.py` with `FileSource` + `WebcamSource`.
5. Record ~200 frames of an obstacle scene into `sim/demo_frames/` so everyone has data before AirSim exists.
- *Gate:* M2 can render a full attack sequence from mock events by Day 5.

**Week 2**
1. `node/live_node.py` — `PerceptionWorker` thread; wire live observation into `server.py`.
2. Change `client.py:originate()` to take real `input_bytes`; pass JPEG bytes.
3. `node/mission.py` — 2 Hz round loop emitting events.
4. `node/attack.py` — the seven-attack JSON control surface. `approved_models` comes from `mission_authority.json`, **never** from the provisioner.
5. `tools/provision.py` (attack 3, incl. the fleet-wide re-provision of Bravo+Charlie) and `tools/ground_station.py` (attack 4, HTTP weights + mid-flight OTA push). Ship attacks 1, 2, 3, 6 this week — the defenses already exist, only delivery is new.
6. Run 3 nodes on one laptop end to end with the console attached.
7. **Ship the `--no-veriswarm` control flag** — applies the action without waiting on a verdict. This is Act 1's Run A and it is a first-class deliverable, not a debug switch.
- *Gate (T0):* judge presses "patch Charlie" → console shows DISPUTE → REJECTED → isolated, within 3 seconds. **This is the qualification floor. Hit it in Week 2.**

**Week 3**
1. Cross-machine manifest: Alpha on Jetson, peers on teammates' PCs. Reuse the pattern in `node/run_crossmachine.py`.
2. Jetson: `backend: "optee"` for Alpha; verify the secure element signs live receipts and peers verify them.
3. Frame streaming PC → Jetson (JPEG, 2 Hz).
4. Add a `--offline` flag that runs everything from `FileSource` with no network.
- *Gate (T1):* console shows `backend: optee` and ~4.7 ms sign time on Alpha, live.

**Week 4**
1. `AirSimSource` against M3's wrapper.
2. **Verdict → flight feedback**: ACCEPTED applies the action as a velocity command; REJECTED hovers. This is the crash-vs-caught mechanism and it is yours, not M3's, because it touches consensus output.
3. Run the two-run comparison end to end.
4. **FREEZE.**
- *Gate (T2):* crash-vs-caught runs twice in a row from cold boot.

**Weeks 5–6**
Bug fixes only. Cold-boot the whole system daily. Write `RUNBOOK.md` — exact startup
order, ports, IPs, and what to do when each piece fails. Support rehearsals.

---

### M2 — Abhijan (Mac): Console + the Python half of the simulator

Owns everything pure-Python and platform-agnostic. **Does not install Unreal.** AirSim's
macOS support was never first-class, Microsoft archived it in 2022, and on Apple Silicon
it becomes a multi-day plugin-compilation problem with no community trail to follow.

He doesn't need it: the `cosysairsim` / `airsim` pip package is **just an RPC client**, pure
Python, works fine on macOS. He drives Pratik's and Samik's sims **over the LAN on port
41451** when live access exists, and otherwise runs the same verifier over exported frame
folders.

#### Console — the main build (Streamlit, not React)
Python, no JS, readable output in days. Ugly is fine; **legible from three metres is not.**

**Week 1** — Install Streamlit, work the tutorial. Static layout with fake numbers: header
strip (swarm size, round #, verdict banner), left panel (node cards), centre (live receipt
stream), right (vote matrix). Point it at `tools/mock_events.py`. *Deliverable: layout
renders and refreshes at 2 Hz.*

**Week 2** — Every panel live from the event stream: receipt rows appear as they arrive;
vote matrix cells turn green/red on ACK/DISPUTE; **verdict banner huge** — ACCEPTED green,
REJECTED red, NO_QUORUM amber; reputation bars per node. **Plus the seven attack buttons**,
which write `attack.json` for the nodes to pick up. *Deliverable: full attack sequence
legible end to end from mock events.*

**Week 3** — Swarm map (2D top-down from `pose` events, co-visibility links drawn between
overlapping nodes), latency ticker, isolation banner. Then the **3-metre test**: put it on
a TV, stand 3 m back, have someone who has never seen it narrate what's happening. Fix
whatever they can't read. Fonts ≥ 24 pt.

**Week 4** — Live frame panel (from `frame` events) so judges see what each drone sees.
Integrate against M1's real backend. **Freeze.**

**Weeks 5–6** — Colour and contrast polish only. No new panels.

#### Simulator Python side — secondary, ~200 lines total, Weeks 2–4
Written against Pratik's and Samik's sims over the LAN, or against recorded frames when
they are down.

- **Waypoint flight** — `moveToPositionAsync()`. Fifteen minutes of API calls. Not a research problem, and nobody is judging your navigation.
- **Frame capture** — `simGetImages()` per vehicle at 2 Hz, 640×360, written to `sim/demo_frames/<environment>/<node>/` or an equivalent per-environment folder.
- **Attack trigger** — `simSetObjectPose()` raising the b1 patch plane into the scene.
- **Week 4** — the `get_frame(vehicle)` / `set_velocity(vehicle, action)` wrapper M1 consumes.
- **Environment verification** — for each handoff, run YOLO on patch-lowered and
  patch-raised frames, record detections/actions, and confirm the victim diverges while
  peers still detect the obstacle.

> **Verify in Week 1, not Week 3:** that AirSim's RPC accepts a connection from another
> machine on the switch. Depending on the build it may bind to localhost only. Ten minutes
> now versus a blocked week later.

*Fallback if the console stalls:* M1 ships a `rich` terminal console — coloured tables, ~2
hours, honestly still readable. Decide at the end of Week 2.

---

### M3 — Pratik (Windows): Dark Urban Unreal Environment + Demo Operations

Owns the primary **dark urban** environment: buildings, rooftops, alleys, a close
YOLO-detectable obstacle, and adversarial patches placed on rooftops / walls. His output
is either a live Cosys-AirSim endpoint for Abhijan or a folder of recorded frames that M1
replays through `FileSource`. If live AirSim never works, nobody else is blocked.

**Check two prerequisites before anything else:**
- **Visual Studio 2022, C++ desktop workload** (~10 GB) — required to build the AirSim plugin on Windows. Not optional.
- **100 GB+ free disk** — Epic Launcher + UE5 + a built plugin. Finding this out mid-install turns a 2-day timebox into a week.

**Week 1** — **Timeboxed 2-day AirSim/Colosseum spike.** Install Unreal + AirSim, run the
Blocks environment, get *one* frame out via `simGetImage()`. If it isn't working by the end
of Day 2, **stop and switch to Gazebo** — it already flies 3 drones with measured
co-visibility (`sim/multi_drone.py`). Report the decision at the Week-1 sync; do not
silently keep debugging.

**Week 2** — Build the dark urban scene: **one large, close, frame-dominating obstacle**
wearing a COCO-class photo texture (bus or truck — the trick that already worked in Gazebo;
YOLOv8n does not detect a gray Unreal cube). Place visible adversarial patches on rooftops
or walls. Then **b1**: the parallax occluder plane, parked below the ground plane, ready to
be raised. **Hand Abhijan a running sim to connect to** — that handoff is the week's real
deliverable, alongside a folder of captured frames for M1.

> **The obstacle must dominate the frame.** In the Gazebo run the patch attack silently
> failed on the ring-of-cubes scene: small distant obstacles → weak avoidance action →
> occluding one moved it only L2 ≈ 0.1, far below θ = 0.5, so there was nothing for the
> semantic layer to catch. Scene design, not code, is the most likely cause of demo failure.

**Week 3** — `settings.json` for 5 vehicles in formation with **overlapping fields of
view**. Peers at the measured 3 m and 6 m offsets. Verify `o(i,j)` actually clears
`o_min = 0.1` with the tilted camera — `Pose` carries yaw only, so the footprint model
assumes nadir, and if the gate silently fails the semantic layer switches off and the
patch demo dies quietly. Run in SimpleFlight mode — no PX4.

**Week 4** — Attempt **b2** (trained patch textured onto the b1 plane). Then **record the
backup video**: full crash-vs-caught run, screen-captured, edited, 90 seconds. *This video
is a deliverable, not a nice-to-have.* If b2 misbehaves, b1 ships — it is a complete
world-space attack on its own.

**Weeks 5–6 — his Unreal load drops here, so he takes demo operations.** Re-record the
backup after freeze, test on the actual venue machine, **drive the primary dark urban demo
while the pitch owner narrates**, and own cable setup and teardown against `RUNBOOK.md`.

*Hard rule:* graphics settings low, 640×360, 2 Hz. Nobody scores you on frame rate, and
slower is more legible to a judge anyway.

---

### M4 — Samik: Light Terrain Unreal Environment

Owns the secondary **light terrain** environment: hills, mountains, desert, lakes, trees,
open terrain, and bright daylight scenes. This is not merged into Pratik's map. It is a
separate scenario pack that proves the same Python verifier and dashboard work outside the
dark urban setting.

**Week 1**
1. Set up Unreal/Cosys-AirSim or align with Pratik's working setup.
2. Create a minimal daylight terrain map with one close, YOLO-detectable obstacle.
3. Export the first patch-lowered frame folder for Abhijan, even if the map is ugly.
4. Write down vehicle names, camera names, and expected node mapping.

**Week 2** — Expand the terrain scene with hills / mountains / desert / lake / trees, but
keep the demo geometry simple: victim drone sees a patch-obscured obstacle, peers see past
it. Export patch-lowered and patch-raised frame folders for all vehicles. Do not wait for
the map to look final before handing images to Abhijan.

**Week 3** — If live Cosys-AirSim access is ready, expose port `41451` on the LAN so
Abhijan can run the same smoke test and YOLO verifier. If not, recorded frames remain the
official handoff. Tune lighting so YOLO still detects the intended object in the clean
case.

**Week 4** — Record the backup video for the light terrain scenario and deliver final frame
packs. This is evidence / robustness material, not the primary live demo.

**Weeks 5–6** — Freeze map changes except visual polish. Help M2 and the pitch owner
collect screenshots, short clips, and failure/success comparisons for the deck.

**Pitch and deck ownership:** with Samik now owning a full environment track, the deck and
judge Q&A become a shared weekly-sync responsibility unless the team assigns another named
owner. Do not let the environment split consume the narration work.

---

## Weekly sync — 30 minutes, same day each week

Each member answers exactly three questions: what shipped, what's blocked, what's the
fallback. **M1 decides go/no-go on each tier at the sync.** A tier that isn't working by
its gate date gets cut, not extended.

---

## Verification

**Per-tier acceptance (must pass twice from cold boot):**

- **T0:** `python -m node.mission --manifest demo3.json --offline` + console. Judge
  triggers patch → DISPUTE visible → REJECTED → isolation banner, under 3 s.
- **T0b:** trigger model swap → rejected with reason `model_hash_not_approved`.
- **T0c:** trigger collusion at N=5 → reputation bars decay to the 0.1 floor in ~4 rounds, accept still blocked.
- **T0d:** rogue node broadcasts → `unknown_drone_id`; replay a stored receipt → `stale_receipt`.
- **T0e:** ground station serves tampered weights mid-mission → node reloads → `model_hash_not_approved` on the next round.
- **T0f (Act 2 chain):** compromised `provision.py` re-provisions Bravo **and** Charlie → both rejected on their first receipt → enable collusion between them → accept still blocked, both reputations reach the 0.1 floor within ~4 rounds. Must run as one continuous sequence without operator intervention.
- **Control:** the same scenario with `--no-veriswarm` reaches the opposite outcome. Both runs recorded side by side.
- **b1/b2 per-drone check (before wiring into the mission loop):** with the patch raised,
  Alpha's YOLO must miss the obstacle **and** Bravo/Charlie's must still detect it. If all
  three miss, the attack is transferring and the demo is dead — widen the peer offsets or
  weaken the patch. Verify this standalone, not through the protocol.
- **T1:** Alpha on Jetson, console shows `backend: optee`, sign ≈ 4.7 ms, peers verify
  over LAN.
- **T2:** run A (VeriSwarm off) drone hits the obstacle; run B (on) drone hovers and is
  isolated. Both recorded in Pratik's dark urban scenario.
- **T2b:** Samik's light terrain scenario produces patch-lowered and patch-raised frame
  folders that run through the same M2 YOLO verifier and console path. It may be recorded
  rather than live.
- **Regression:** `python -m pytest` — all 102 existing tests still pass after the
  `client.py` / `server.py` seam changes. **Run this before every commit.**
- **Venue drill:** unplug the network mid-demo — swarm keeps deciding (this is a demo
  beat, and it verifies `--offline`).

---

## Risks and fallbacks

| Risk | Fallback | Decide by |
|---|---|---|
| AirSim won't install | Gazebo (already flies 3 drones) | Week 1 Day 2 |
| b2 patch dies in the render (lighting/TAA/mipmap) | b1 parallax occluder — complete world-space attack, zero render gap | Week 4 |
| Obstacle too small → action too weak → nothing to catch | Redesign scene: one close, frame-dominating obstacle | Week 2 |
| M2 can't build the console | M1 ships a `rich` terminal console (~2 h) | End of Week 2 |
| M3 falls behind entirely | `FileSource` replays recorded frames; demo unaffected | Week 3 sync |
| Unreal Git collaboration becomes noisy | Keep Pratik and Samik on separate scenario packs; integrate only exported frames/videos and Python-facing metadata | Immediate |
| Samik's light terrain scene falls behind | Use it as recorded robustness evidence only; do not block the primary Pratik live demo | Week 4 |
| Venue network fails | `--offline` mode, all nodes on one machine | Built Week 3 |
| Everything fails on the day | Backup video + live Jetson signing on the table | Week 4 |
| Judge asks "did you build this in the hackathon?" | Disclose first: published protocol + paper is ours; the application, console, and hardware integration are the SIH build | Rehearsed Week 2 |

---

## Two flags

**Team size.** SIH has historically required **6 members including at least one female
member** for the finale. Four is fine for the internal round if your college allows it,
but verify the official 2026 rules now — discovering this after qualifying is a bad
week. Plan the two extra slots as console-support and demo-ops.

**The paper must not slip.** `VeriSwarm_IEEE_v11.docx` is one formatting pass from
submission, and a submitted paper is both the more durable asset and what makes the SIH
pitch credible ("this is peer-reviewed work, here's the hardware"). Carve out a fixed
slot each week for it. It is not SIH work and must not be traded against SIH work.
