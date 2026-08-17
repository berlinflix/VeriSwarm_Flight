# Demo topology — every machine, cable, port, and process

What is physically on the table, what runs where, and what to plug into what.
Written 2026-08-18. Pair with `HARDWARE_LIST.md` (what to buy). `RUNBOOK.md`
is a required M1 deliverable and does not exist yet; do not mistake this topology
for a completed launch procedure.

The demo has **two independent halves** and that is deliberate. If the simulator
will not start, the physical half still tells a complete story on its own; if the
webcams fail, the simulated half does. Neither depends on the other. They run
**sequentially**, not concurrently, because both use the Jetson: the physical
webcam demonstration runs first, then the webcam process stops and the Jetson
becomes node `alpha` with local OP-TEE signing for the simulated swarm.

| | What it shows | Runs on |
|---|---|---|
| **A. Simulated swarm** | 5 drones, adversarial patch, consensus, isolation | CoSys AirSim + 5 node processes |
| **B. Physical co-visibility rig** | the overlap algorithm on two real cameras | Jetson + 2 USB webcams |

Half B is new — its planned implementation guide is `codebase/docs/COVIS_LIVE.md`;
until that file and `codebase/tools/covis_live.py` exist and pass their gate, use
§6 as design requirements rather than runnable instructions.

### Implementation status gate

The OP-TEE backend, Alpha manifest-key binding, `tools/optee_preflight.py`,
`node.server`, and the protocol-only `node.run_phaseb` regression exist. The
following commands shown in this topology are **planned, not implemented** as of
2026-08-18: `tools.covis_live`, `tools.event_collector`, `console/app.py`, and
`tools.run_campaign`. The accepted integrated demonstration is blocked until each
file exists, has tests, and is cold-started from the final `RUNBOOK.md`. A diagram
or command name is not evidence that a subsystem exists.

---

## 1. Machines

| # | Machine | Owner | Role at the demo |
|---|---|---|---|
| **J** | Jetson Orin Nano | Suyash | Stage B: drives both webcams. After a checked handover, Stage A: node **alpha** — YOLO + local OP-TEE signing. These workloads never run concurrently. |
| **P1** | Windows PC, strongest GPU | Pratik | Runs **CoSys AirSim**. Nothing else. |
| **P2** | Windows PC | Samik | Nodes **bravo**, **charlie**. Backup AirSim install. |
| **L1** | Laptop | Suyash | Nodes **delta**, **echo**. Orchestration, RUNBOOK commands. |
| **L2** | Mac | Abhijan | **Console** + attack panel + event collector. Drives the projector. |

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
| `192.168.50.12` | **P2** | gRPC 51001–51002, frames 8081–8082, events 9011 |
| `192.168.50.13` | **L1** | gRPC 51003–51004, frames 8083–8084, events 9013 |
| `192.168.50.14` | **L2** console | **event collector 9000**, console UI 8501 |
| `192.168.50.20` | spare | — |

Subnet `255.255.255.0`. **No gateway, no DNS** — nothing here reaches the
internet, and it must not need to.

### Cabling

```
                    ┌──────────────────────────┐
                    │  8-PORT GIGABIT SWITCH   │
                    │  (unmanaged, no DHCP)    │
                    └─┬───┬───┬───┬───┬────────┘
        Cat6 ─────────┘   │   │   │   └───────── Cat6
          │               │   │   │                │
    ┌─────▼────┐   ┌──────▼┐ ┌▼───────┐  ┌─────────▼──┐
    │ J Jetson │   │ P1    │ │ P2     │  │ L1 laptop  │
    │  .10     │   │ .11   │ │ .12    │  │  .13       │
    └──┬────┬──┘   │AirSim │ │bravo   │  │ delta      │
       │    │      │only   │ │charlie │  │ echo       │
   USB │    │ USB  └───────┘ └────────┘  └────────────┘
   ┌───▼─┐ ┌▼────┐                        ┌────────────┐
   │ CAM │ │ CAM │                        │ L2 Mac .14 │
   │  A  │ │  B  │                        │ console +  │
   └─────┘ └─────┘                        │ collector  │
                                          └──────┬─────┘
                                                 │ HDMI
                                          ┌──────▼─────┐
                                          │ PROJECTOR  │
                                          └────────────┘
```

**5 Cat6 cables minimum, buy 7.** One spare per two in use is the right ratio for
a venue where a crimp can fail and you cannot buy another.

**Do not use Wi-Fi.** A hackathon hall's 2.4/5 GHz is congested to the point where
your measured consensus latency would contradict Table 4.9 live on screen. Wired
is not a preference here, it is what keeps the numbers honest.

**The Mac needs USB-C→Ethernet.** If Abhijan's dock has an Ethernet port, use it,
but load-test it for a full hour beforehand — cheap docks drop the NIC under
sustained load, and it fails at exactly the wrong moment.

---

## 3. Where YOLO runs

**One copy of YOLOv8n per drone, inside that drone's own node process.** There is
no central inference server; that is the whole premise. Five nodes, five
independent detectors.

| Node | Machine | Detector runs on | Signs with |
|---|---|---|---|
| alpha | **J** Jetson | Jetson GPU | **OP-TEE secure element** |
| bravo | **P2** | P2 CPU/GPU | software key |
| charlie | **P2** | P2 CPU/GPU | software key |
| delta | **L1** | L1 CPU/GPU | software key |
| echo | **L1** | L1 CPU/GPU | software key |

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

**1. J — Jetson webcam process only**

```text
python -m tools.covis_live
```

Run the live two-camera overlap, patch, and lens-cover beats. No swarm node or
OP-TEE receipt service runs during this stage. The OP-TEE key remains protected
even while unused.

**2. Checked Jetson handover**

Stop `covis_live` cleanly, verify that it exited and released both camera devices,
flush and hash its evidence, then record a `WEBCAM_STAGE_COMPLETE` event. Do not
reuse its process state or call the swarm ready until the OP-TEE public-key
challenge succeeds. P1 may load the world in the background to avoid stage delay,
but it must not start the accepted mission yet.

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

**New deliverable.** Two USB webcams on the table, both looking at the same
object from different angles, wired into the Jetson.

```
        object on the table
              ▲   ▲
             /     \        ~25-35 cm apart
            /       \       both ~40-60 cm from the object
      ┌────┘         └────┐
   ┌──┴──┐             ┌──┴──┐
   │CAM A│             │CAM B│
   └──┬──┘             └──┬──┘
      └────USB-A──┬───USB-A┘
              ┌───▼────┐
              │ JETSON │  runs tools/covis_live.py
              └────────┘
```

**Why this earns its place.** Everything else on the table is simulated. This is
the co-visibility algorithm — the actual `protocol/covis_features.py`, ORB +
RANSAC, already unit-tested — running on real photons, in front of the judge, on
hardware they can touch. And it is the only part of the demo a judge can
*interfere with directly*: slide a camera, hold up the printed patch, cover a
lens.

**What it shows on screen:** both camera feeds side by side, ORB matches drawn
between them, the live inlier count against `m_min = 15`, and the co-visible
verdict. Then, with YOLO enabled on both feeds, each camera's action vector and
the L2 between them against θ.

**The three judge-operable moments:**

1. **Slide camera B away.** Inliers fall, the verdict flips to *not co-visible*,
   and the semantic layer abstains. *"It refuses to cross-check two cameras that
   are not looking at the same thing — that is why our false-positive rate is
   low."*
2. **Hold the printed patch in front of camera A.** A's detections vanish, its
   action diverges from B's, L2 crosses θ, **DISPUTE**. *"A physical printed
   attack, caught by a second viewpoint."*
3. **Cover camera A entirely.** Detections vanish the same way — but so does the
   scene. Shows that absence of evidence is not evidence of absence, which is
   exactly the distinction the protected controller makes and the baseline does
   not.

Point 3 is worth rehearsing because a sharp judge will ask it, and having the
answer already on screen is far better than explaining it.

**Cameras:** two identical USB webcams, 640×360 is plenty. Identical models keep
the intrinsics comparable, which matters for the overlap claim. The Jetson has
4× USB-A, so both plug straight in — no hub.

**Software:** `tools/covis_live.py`. Owner split — **Suyash** writes it (it calls
protocol code directly), **Abhijan** owns the physical rig, the printed patch, and
the attack choreography, since he owns attack delivery.

---

## 7. Power and physical layout

- **One surge-protected power strip**, minimum 6 sockets: Jetson barrel jack,
  P1, P2, switch, and two laptop chargers.
- The Jetson runs **headless**. Its DisplayPort is not dual-mode, so a passive
  DP→HDMI adapter produces no output — this cost a day already. SSH from L1, or
  NoMachine.
- **Only L2 touches the projector.** One HDMI cable, one thing to go wrong.
- Put the **Jetson and the two webcams at the front of the table**, nearest the
  judges. They are the parts that are real; keep them reachable.
- Label every Cat6 cable at both ends with its destination IP. At teardown you
  will thank yourself, and at setup it turns a 20-minute debug into a glance.

---

## 8. Pre-demo checklist

Run this cold, twice, on two different days. A demo that has never been cold-booted
has not been tested.

- [ ] All five devices ping each other by static IP
- [ ] `41451` reachable **from another machine**, not just P1's localhost
- [ ] `covis_live` is stopped and both webcam devices are released before Alpha starts
- [ ] A fresh random challenge verifies under Alpha's pinned OP-TEE public key
- [ ] Console shows `backend: optee` for Alpha; no software fallback is configured
- [ ] At least one retained Alpha receipt verifies under that same pinned public key
- [ ] All five nodes appear in the console with monotonic `seq`, no gaps
- [ ] Both webcams enumerate on the Jetson (`ls /dev/video*`)
- [ ] `covis_live` shows inliers ≥ 15 in the rehearsed camera placement
- [ ] The printed patch actually suppresses detection at the rehearsed distance —
      **test the exact print**, since paper, scale, and lighting all matter
- [ ] Offline mode (`--offline`) runs the full attack sequence with the switch
      unplugged
- [ ] Backup video plays from L2 without a network
