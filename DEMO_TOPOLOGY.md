# Demo topology — every machine, cable, port, and process

What is physically on the table, what runs where, and what to plug into what.
Written 2026-08-18. Pair with `HARDWARE_LIST.md` (what to buy). `RUNBOOK.md`
is a required M1 deliverable and does not exist yet; do not mistake this topology
for a completed launch procedure.

**Internal-qualifier override (2026-08-19):** read
`FIVE_CABLE_EXECUTION_FREEZE_2026-08-19.md` first. Tomorrow uses exactly five cables:
Jetson `.10`, Pratik `.11`, Samik `.12`, Suyash `.13` and Abhijan's Mac `.14`. Ayush
designs the camera software but is not a wired runtime endpoint. The camera pair is one
USB webcam plus one Android DroidCam feed connected to Samik P2.

The demo has **two independent halves** and that is deliberate. If the simulator
will not start, the physical half still tells a complete story on its own; if the
webcams fail, the simulated half does. Neither depends on the other. They run
**sequentially** for a clean stage narrative and because Samik P2 runs the camera
application first and Bravo second. The Jetson remains the Alpha/OP-TEE host.

| | What it shows | Runs on |
|---|---|---|
| **A. Simulated swarm** | 5 drones, adversarial patch, consensus, isolation | CoSys AirSim + 5 node processes |
| **B. Physical co-visibility rig** | co-visibility-gated semantic comparison on two live views | Samik P2 + USB webcam + Android DroidCam |

Half B's implementation and guide are staged on `origin/codex/covis-live-ui` at
`f0582b6d6b471b023ba6c312fd198978e5ebdd6b`. Ayush develops the final camera branch;
Samik reviews its frozen commit and runs the real Windows camera gate.

### Implementation status gate

The OP-TEE backend, Alpha manifest-key binding, `tools/optee_preflight.py`,
`node.server`, and the protocol-only `node.run_phaseb` regression exist.
`tools.covis_live` exists on the staged camera branch but still requires Samik's Windows
review and hardware gate. `tools.event_collector`, `console/app.py`, and
`tools.run_campaign` remain planned. The accepted integrated demonstration is blocked until
every invoked entry point exists, has tests and is cold-started from the final `RUNBOOK.md`.
A diagram or command name is not evidence that a subsystem exists.

---

## 1. Machines

| # | Machine | Owner | Role at the demo |
|---|---|---|---|
| **J** | Jetson Orin Nano | Suyash | Node **alpha** and local OP-TEE signing after the camera beat; never a generic remote signer. |
| **P1** | Windows PC, strongest GPU | Pratik | Runs **CoSys AirSim**. Nothing else. |
| **P2** | Windows PC | Samik | USB/DroidCam application first; then qualification node **bravo**; backup AirSim install. |
| **L1** | Laptop | Suyash | Qualification node **charlie**; orchestration and RUNBOOK commands. |
| **L2** | Mac | Abhijan | Independent attack-control terminal, event/evidence view and projector. |

**Why AirSim is alone on P1.** Unreal will take whatever GPU and RAM it can get.
Sharing that machine with node processes running YOLO means the two compete, frame
rate collapses, and the round timing you rehearsed stops holding. P1 renders; it
does not think.

**Why nodes are spread over three machines.** Because the paper's claim is
distributed consensus over a real network. Five processes on one laptop is a
different experiment, and a judge who asks "is this actually distributed?"
deserves a true answer. The switch makes it true.

---

## 2. Network

**8-port unmanaged gigabit switch. No DHCP server**, so every device gets a
**static IP**. Set these before the day; discovering it at the venue costs an hour.

| IP | Device | Listens on |
|---|---|---|
| `192.168.50.10` | **J** Jetson | gRPC 51000 (alpha), frames 8080, events 9010 |
| `192.168.50.11` | **P1** AirSim | AirSim RPC **41451** |
| `192.168.50.12` | **P2** | qualification Bravo gRPC **51001**, after camera release |
| `192.168.50.13` | **L1** | qualification Charlie gRPC **51003** |
| `192.168.50.14` | **L2** attack terminal | event collector 9000 and console UI 8501 when implemented |
| `192.168.50.20` | spare | — |

Subnet `255.255.255.0`. Ethernet has **no gateway and no DNS**. Wi-Fi may remain available
during setup and the local phone-camera stage, but every demo service uses its explicit
wired endpoint and unrelated Wi-Fi is disabled for the accepted protocol run.

### Cabling

```
                    ┌──────────────────────────┐
                    │  8-PORT GIGABIT SWITCH   │
                    │  (unmanaged, no DHCP)    │
                    └─┬────┬────┬────┬─────────┘
                      │    │    │    │
        ┌─────────────┘    │    │    └──────────────┐
        │                  │    │                   │
  ┌─────▼────┐      ┌──────▼┐ ┌─▼─────────┐  ┌──────▼──────┐
  │ J Jetson │      │ P1    │ │ P2 Samik │  │ L1 Suyash  │
  │ .10 Alpha│      │ .11   │ │ .12       │  │ .13 Charlie│
  │ OP-TEE   │      │AirSim │ │camera,then│  └─────────────┘
  └──────────┘      └───────┘ │Bravo      │
                              └─┬────────┬─┘  ┌─────────────┐
                            USB │        │Wi-Fi│ L2 Abhijan │
                         ┌──────▼┐  ┌────▼────▼┐│ .14 attack │
                         │USB CAM│  │ANDROID   ││ terminal   │
                         │ A     │  │DroidCam B│└──────┬─────┘
                         └───────┘  └──────────┘       │ HDMI
                                                ┌──────▼─────┐
                                                │ PROJECTOR  │
                                                └────────────┘
```

**Exactly 5 Cat6 cables are used.** A sixth spare is desirable later but is not required to
start the frozen internal-qualifier topology.

**Do not use Wi-Fi for protocol or simulator RPC.** The local Android feed may use a
prevalidated phone/P2 hotspot during the camera stage. Disable unrelated Wi-Fi after
camera release so measured protocol latency follows the wired route.

**The Mac needs USB-C→Ethernet.** If Abhijan's dock has an Ethernet port, use it,
but load-test it for a full hour beforehand — cheap docks drop the NIC under
sustained load, and it fails at exactly the wrong moment.

---

## 3. Where YOLO runs

For the internal qualifier, the physical camera YOLO executes on Samik P2 before Bravo.
The later SIH fleet retains one approved detector per drone; the camera demonstrator is not
represented as one of those flight nodes.

| Node | Machine | Detector runs on | Signs with |
|---|---|---|---|
| alpha | **J** Jetson | Jetson GPU | **OP-TEE secure element** |
| bravo | **P2** | P2 CPU/GPU after camera release | software key |
| charlie | **L1** | L1 CPU/GPU | software key |

Delta and Echo remain later SIH identities; they are not falsely presented as running in
tomorrow's three-node qualification quorum.

AirSim on **P1** renders the world and serves each node its own camera view over
RPC 41451. P1 runs no detector.

**Frames go over the network, inference does not.** Each node pulls its own RGB +
depth from P1, runs YOLO locally, and signs locally. If inference happened
centrally there would be nothing to cross-verify — one machine's opinion is one
opinion however many drones you draw on screen.

**OP-TEE is retained, but it does not sign for every identity.** After the webcam
stage, every receipt originated by `alpha` and every peer vote emitted by `alpha`
is signed locally through `OPTEEReceiptSigner`; its Ed25519 private key never
leaves secure world. `bravo`, `charlie`, `delta`, and `echo` keep distinct
software-backed keys for this simulation. Do not send all five nodes' unsigned
receipts to one generic Jetson signing endpoint: the current Trusted Application
has one persistent key, so that design would collapse per-node identity, create a
single point of failure, and let an insufficiently authenticated caller request a
signature for another drone. A future all-TEE design requires one protected key
slot per node plus mutually authenticated callers and a TA-enforced
`caller identity == receipt.drone_id` check.

The precise claim is: **Alpha's signing key is hardware-isolated and Alpha's
canonical receipt bytes are signed by OP-TEE.** It is not a claim that YOLO or the
other nodes execute inside the TEE. If the Alpha signer is unavailable or misses
its signing deadline, Alpha emits no accepted receipt and the system
holds/reassigns; there is no silent software-key fallback.

---

## 4. What each machine runs, in stage order

Final commands will belong in the planned `RUNBOOK.md`; this section fixes process
placement and stage order but does not claim that every planned entry point exists.

### Stage B — physical co-visibility first

**1. P2 — Samik camera process only**

```text
python -m tools.covis_live
```

Run the live two-camera overlap, patch and recovery beats. No Bravo process runs on P2
during this stage. Alpha/OP-TEE remains separate on the Jetson; physical-camera output is
not represented as an OP-TEE receipt.

**2. Checked P2 camera-to-Bravo handover**

Stop `covis_live` cleanly, verify that it exited and released both camera sources, flush
and hash its evidence, then record a `WEBCAM_STAGE_COMPLETE` event. Start Bravo on P2 only
after the release probe passes. Independently run the fresh Jetson OP-TEE challenge before
Alpha. P1 may load the world in the background, but it must not start the accepted mission.

### Stage A — simulated swarm with OP-TEE-backed Alpha

**3. P1 — AirSim** (load early; it takes longest)
```
CoSysAirSim.exe   →   world loaded, 5 vehicles, RPC on 41451
```
Verify from another machine before continuing: a node that cannot reach 41451
fails in a way that looks like a protocol bug and is not one.

**4. L2 — collector and console** (before the nodes, so nothing is missed)
```
python -m tools.event_collector --bind 0.0.0.0:9000
streamlit run console/app.py
```

**5. J — OP-TEE handover preflight**

```bash
export VERISWARM_OPTEE_CA=/absolute/path/to/veriswarm_optee_ca
export VERISWARM_ALPHA_PUBKEY=<64-hex-key-pinned-outside-the-runtime-manifest>
python -m tools.optee_preflight \
  --mission-id contested-border-001 --mission-epoch 1 \
  --out results/handover/optee-preflight-001.json
```

This signs a fresh canonical receipt, verifies it against Alpha's independently
pinned public key and writes a non-overwriting evidence file. A key mismatch,
missing `/dev/tee0`, missing Client Application, timeout, invalid signature or
existing output file fails the handover.

**6. J — Jetson, node alpha**
```
VERISWARM_OPTEE_CA=... python -m node.server --manifest demo5.json --id alpha
```
Before accepting mission traffic, issue a random preflight challenge, sign it
through the OP-TEE Client Application, and verify it against Alpha's pinned
public key. Then confirm the console shows `backend: optee` for Alpha. The field
alone is not evidence; retain the challenge, public key, signature, verification
result, TA/CA hashes and measured signing latency. If the backend says `software`
or the challenge fails, Alpha does not participate and the protected mission
must hold or reallocate according to the signed mission policy. Signer construction
also verifies that the live OP-TEE public key equals Alpha's manifest identity.

**7. P2 and L1 — the peer nodes**
```
python -m node.server --manifest demo5.json --id bravo    # …charlie, delta, echo
```

**8. J — Alpha originator mission, operated by Samik over SSH**
```
# Planned accepted entry point; Samik must implement this before the Cosys gate:
python -m tools.run_campaign --manifest demo5.json --originator alpha --scenario contested-border
```

`node.mission` is currently a library and has no command-line entry point; the old
`python -m node.mission ...` line was non-executable and is removed. The accepted
Cosys stage is blocked until `tools.run_campaign` exists and wires the real source,
autonomy, supervisor and command sink. That process must execute on the Jetson:
`Originator` constructs the signer in its own process, so running Alpha on L1 searches
for `/dev/tee0` on L1 and fails. Frames travel from P1 to Alpha; the unsigned canonical
receipt does not leave Alpha before the local OP-TEE call. Samik owns and triggers the
autonomy run, while Suyash owns the Jetson/OP-TEE preflight and abort authority.

Until the campaign runner exists, the executable hardware-key protocol regression is
`VERISWARM_OPTEE_CA=... python -m node.run_phaseb` on the Jetson. It is evidence for
local OP-TEE signing only; its loopback/static observations are not accepted as the
Cosys autonomy demonstration.

**Fallback at any point:** every node takes `--offline`, which swaps the AirSim
source for `FileSource` replaying Pratik's captured frames. The protocol, the
consensus, the attacks, and the console are all unchanged. Only the pretty 3-D
window is lost. **Rehearse the demo in offline mode at least once** — it is the
configuration you will actually use if P1 misbehaves.

---

## 5. How events reach the console

Each node writes its own `results/live_events.jsonl` **locally** and also POSTs
each event to the collector on `L2:9000`.

- **The local file is the durable record.** It survives a network drop, a console
  crash, and the demo itself — it is the post-mortem artifact.
- **The POST is the live feed.** Best-effort; a failed POST never blocks a
  consensus decision.

The collector merges by `(node, seq)`. Because `seq` is monotonic per node, a
missing event is *detectable*: the console can say "alpha: 3 events dropped"
instead of quietly drawing an incomplete picture. That matters when the thing on
screen is a security claim.

**This is also a demo beat.** Unplug the switch mid-run: the console freezes, the
swarm keeps deciding, plug it back in and the backlog fills. "The swarm does not
need the ground station to stay safe."

---

## 6. Half B — the physical co-visibility rig

**New deliverable.** One USB webcam and one Android DroidCam phone observe the same object
from different angles and feed Samik P2.

```
        object on the table
              ▲   ▲
             /     \        ~25-35 cm apart
            /       \       both ~40-60 cm from the object
      ┌────┘         └────┐
   ┌──┴─────┐          ┌──┴─────────┐
   │USB CAM │          │ANDROID     │
   │ A      │          │DroidCam B  │
   └──┬─────┘          └─────┬──────┘
      │ USB                 Wi-Fi/local URL
      └──────────────┬────────┘
                 ┌───▼────┐
                 │SAMIK P2│  runs tools/covis_live.py, then Bravo
                 └────────┘
```

**Why this earns its place.** Everything else on the table is simulated. This is
the co-visibility algorithm — the actual `protocol/covis_features.py`, ORB +
RANSAC, already unit-tested — running on real photons, in front of the judge, on
hardware they can touch. And it is the only part of the demo a judge can
*interfere with directly*: slide a camera, hold up the printed patch, cover a
lens.

**What it shows on screen:** both feeds side by side with the exact YOLO boxes used by the
decision, camera health/skew, ORB/RANSAC inliers, projected `view_IoU`, same-class projected
`box_IoU` when available, measured claims and `AGREE`/`DISPUTE`/`ABSTAIN`. These are planar
demo measurements, not calibrated stereo or raw cross-view box IoU.

**The three judge-operable moments:**

1. **Slide camera B away.** Inliers fall, the verdict flips to *not co-visible*,
   and the semantic layer abstains. *"It refuses to cross-check two cameras that
   are not looking at the same thing — that is why our false-positive rate is
   low."*
2. **Hold the printed patch in front of camera A.** A reproducible class/presence mismatch
   produces **DISPUTE**, or valid preserved co-visibility evidence produces an explicitly
   labelled semantic **ABSTAIN**. A camera-health failure does not count as attack evidence.
3. **Cover camera A entirely.** Detections vanish the same way — but so does the
   scene. Shows that absence of evidence is not evidence of absence, which is
   exactly the distinction the protected controller makes and the baseline does
   not.

Point 3 is worth rehearsing because a sharp judge will ask it, and having the
answer already on screen is far better than explaining it.

**Cameras:** one USB webcam plus one Android phone on a rigid tripod. Heterogeneous cameras
mean the demo reports health and host receive-time skew honestly; it does not claim hardware
synchronization or calibrated stereo. Use 640×360 at 15 FPS as the initial gate.

**Software:** `tools/covis_live.py`. **Ayush** designs and tests it; **Samik** reviews,
integrates and operates it on P2; **Abhijan** owns the printed attack and independent attack
terminal; **Suyash** accepts the evidence and stage transition.

---

## 7. Power and physical layout

- **One surge-protected power strip**, minimum 6 sockets: Jetson barrel jack,
  P1, P2, switch, and two laptop chargers.
- The Jetson runs **headless**. Its DisplayPort is not dual-mode, so a passive
  DP→HDMI adapter produces no output — this cost a day already. SSH from L1, or
  NoMachine.
- **Only L2 touches the projector.** One HDMI cable, one thing to go wrong.
- Put the **USB camera, Android tripod and target at the front of the table**, with their
  cables routed safely back to Samik P2.
- Label every Cat6 cable at both ends with its destination IP. At teardown you
  will thank yourself, and at setup it turns a 20-minute debug into a glance.

---

## 8. Pre-demo checklist

Run this cold, twice, on two different days. A demo that has never been cold-booted
has not been tested.

- [ ] All five devices ping each other by static IP
- [ ] `41451` reachable **from another machine**, not just P1's localhost
- [ ] `covis_live` is stopped and both camera sources are released before Bravo starts
- [ ] A fresh random challenge verifies under Alpha's pinned OP-TEE public key
- [ ] Console shows `backend: optee` for Alpha; no software fallback is configured
- [ ] At least one retained Alpha receipt verifies under that same pinned public key
- [ ] Alpha, Bravo and Charlie appear in qualification evidence with monotonic sequence
- [ ] USB webcam and DroidCam open on Samik P2 as two distinct sources
- [ ] `covis_live` shows inliers ≥ 15 in the rehearsed camera placement
- [ ] The printed patch actually suppresses detection at the rehearsed distance —
      **test the exact print**, since paper, scale, and lighting all matter
- [ ] Offline mode (`--offline`) runs the full attack sequence with the switch
      unplugged
- [ ] Backup video plays from L2 without a network
