# Demo topology — every machine, cable, port, and process

What is physically on the table, what runs where, and what to plug into what.
Written 2026-08-18. Pair with `HARDWARE_LIST.md` (what to buy) and
`RUNBOOK.md` (the order to start things in).

The demo has **two independent halves** and that is deliberate. If the simulator
will not start, the physical half still tells a complete story on its own; if the
webcams fail, the simulated half does. Neither depends on the other.

| | What it shows | Runs on |
|---|---|---|
| **A. Simulated swarm** | 5 drones, adversarial patch, consensus, isolation | CoSys AirSim + 5 node processes |
| **B. Physical co-visibility rig** | the overlap algorithm on two real cameras | Jetson + 2 USB webcams |

Half B is new — see `docs/COVIS_LIVE.md` and §6.

---

## 1. Machines

| # | Machine | Owner | Role at the demo |
|---|---|---|---|
| **J** | Jetson Orin Nano | Suyash | Node **alpha** — YOLO + OP-TEE signing. Also drives both webcams for half B. |
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

---

## 4. What each machine runs, in start order

Full commands belong in `RUNBOOK.md`; this is the shape.

**1. P1 — AirSim** (first, it takes longest to load)
```
CoSysAirSim.exe   →   world loaded, 5 vehicles, RPC on 41451
```
Verify from another machine before continuing: a node that cannot reach 41451
fails in a way that looks like a protocol bug and is not one.

**2. L2 — collector and console** (before the nodes, so nothing is missed)
```
python -m tools.event_collector --bind 0.0.0.0:9000
streamlit run console/app.py
```

**3. J — Jetson, node alpha**
```
VERISWARM_OPTEE_CA=... python -m node.server --manifest demo5.json --id alpha
```
Confirm the console shows `backend: optee` for alpha. That single field is the
hardware reveal; if it says `software`, the TA did not load and the reveal is
gone.

**4. P2 and L1 — the peer nodes**
```
python -m node.server --manifest demo5.json --id bravo    # …charlie, delta, echo
```

**5. L1 — the mission**
```
python -m node.mission --manifest demo5.json --id alpha --rounds 200
```

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
- [ ] Console shows `backend: optee` for alpha
- [ ] All five nodes appear in the console with monotonic `seq`, no gaps
- [ ] Both webcams enumerate on the Jetson (`ls /dev/video*`)
- [ ] `covis_live` shows inliers ≥ 15 in the rehearsed camera placement
- [ ] The printed patch actually suppresses detection at the rehearsed distance —
      **test the exact print**, since paper, scale, and lighting all matter
- [ ] Offline mode (`--offline`) runs the full attack sequence with the switch
      unplugged
- [ ] Backup video plays from L2 without a network
