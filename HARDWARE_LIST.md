# SIH 2026 — Hardware List (final, revised)

Prices are approximate India retail (Amazon.in / Robu.in), August 2026.
Companion to `SIH_2026_Build_Plan.md`.

---

## Already owned — do not buy

| Item | Note |
|---|---|
| Jetson Orin Nano Super 8 GB Dev Kit | Plus its 19 V adapter (EU variant — confirm plug fits) |
| External HDD 1 TB | Covers backup video + repo copy. Keep it off the demo table; it doesn't like being knocked while spinning |
| USB flash drive | For moving the backup video to a venue machine |
| Teammates' USB-C docks (with ethernet) | **Must be load-tested — see below** |
| Laptop webcams | Cover any peer node that doesn't need a positioned camera |
| Android phone + tripod | Camera B for the internal qualifier through DroidCam |

---

## Buy — essential

| # | Item | ₹ | Why |
|---|---|---|---|
| 1 | **TP-Link TL-SG1008D** 8-port gigabit unmanaged switch | 1,899 | Your own LAN. Confirmed purchase |
| 2 | **USB webcam ×1** — 1080p | 2,500 | Camera A for the internal qualifier; Camera B is the owned Android/DroidCam phone. A second matched USB camera is a later SIH calibration upgrade, not a blocker now |
| 3 | **Camera mount ×1** (mini tripod or gooseneck clamp) | 1,000 | Fixed USB-camera placement; the Android already has a tripod |
| 4 | **Cat6 cables** — 3× 1 m, 3× 3 m | 900 | Four machines plus spares |
| 5 | **TP-Link UE300C ×1** (spare) | 1,100 | See "the one spare that's justified" below |
| 6 | **USB-C → USB-A dongle ×2** | 500 | Webcam into a USB-C-only Mac |
| 7 | **6-socket surge power strip** | 1,000 | Everything from one venue socket |
| 8 | **Extension cable, 5 m** | 500 | Venue sockets are never near the table |
| 9 | **Spare microSD** — **match the size already in the Jetson**, A2/U3 rated | 700–2,500 | Clone your working image onto it. Buy on the **speed rating**, not capacity. ₹0 if you already have a card ≥ the current one |
| 10 | **USB microSD card reader** | 300 | To clone that image |
| 11 | **Jumper wires (F–F) + jumper caps** | 250 | Orin Nano recovery mode jumpers pins 9–10 on the button header. Without this you cannot reflash |
| 12 | **Patch printing** — A3 matte ×3, mounted on foam board | 1,000 | See printing rules below |

**Essential total ≈ ₹11,500–13,500** with the owned Android/tripod.

## Buy — recommended

| # | Item | ₹ | Why |
|---|---|---|---|
| 13 | HDMI cable, 3 m | 400 | Laptop → venue projector |
| 14 | HDMI → VGA adapter | 400 | Some college projectors are still VGA |
| 15 | Printed obstacle texture (A3 bus/truck on board) | 400 | Repeatable COCO-class object for the tabletop node. A teammate in frame also works — `person` is a COCO class — but a board is repeatable |
| 16 | Small screwdriver set (PH#0/#1) | 400 | M.2 screws, cases |
| 17 | Velcro ties + label tape | 300 | Label cables and nodes **Alpha / Bravo / Charlie**, plus their static IPs. A tidy table reads as a serious team |

**Recommended total ≈ ₹1,900**

## Optional

| Item | ₹ | Verdict |
|---|---|---|
| **ACTIVE** DisplayPort → HDMI adapter | 1,200–2,500 | Only if you want a screen on the Jetson. See the DP trap below |
| Portable monitor 15.6" USB-C | 10,000 | Only if you don't trust the venue to provide a screen |

---

## Explicitly dropped (and why)

| Item | Why not |
|---|---|
| **NVMe SSD** | Demo is not disk-bound. Once YOLO is in memory the loop is inference + Ed25519 + gRPC, near-zero disk I/O. Dev comfort, not a demo requirement — and SSD prices are up |
| **Router** | The switch covers it. A router's Wi-Fi in a hall of hundreds of devices would wreck your latency numbers — see below |
| **External SSD / flash drive** | Already owned |
| **2nd + 3rd UE300C** | Teammates' docks already have ethernet. Test them; buy only the one spare |
| **USB-C docks** | Already owned. Would not have bought them: a dock fails as one unit, taking network *and* camera down together. Separate dongles fail independently |
| **CSI camera** | Better latency, guaranteed gstreamer/`nvarguscamerasrc` time sink. USB webcam works with `cv2.VideoCapture` immediately |
| **Second Jetson** | One hardware-rooted node *is* the claim. A second proves nothing new |
| **256 GB microSD** | Size was never the point — the *spare* is. Match whatever is in the Jetson now |

---

## Gotchas that will otherwise cost you a day

### The switch has no DHCP
An unmanaged switch does not hand out IP addresses. Assign **static IPs** — which you want anyway, because the manifest hardcodes `host:port` and a router's DHCP leases can move between boots.

```
192.168.50.10   Alpha    (Jetson)
192.168.50.11   Sim PC
192.168.50.12   Bravo    (Mac)
192.168.50.13   Charlie  (Mac) + console
```
Subnet `255.255.255.0`, router field blank. On macOS: System Settings → Network → adapter → Details → TCP/IP → Configure IPv4: **Manually**. Write these on the cable labels and into `RUNBOOK.md`.

### The Jetson's DisplayPort is not DP++
The Orin Nano's DP does **not** support dual-mode, so a **passive** DP→HDMI cable produces no output — this is why the earlier attempt failed and NoMachine was needed. If you want a screen, buy an adapter explicitly labelled **ACTIVE** (has a protocol converter chip).

**But you probably don't need one.** The Jetson runs headless; judges watch the console on a laptop. Better fallback: keep one Cat6 to plug the Jetson **directly into a laptop** and SSH point-to-point — no switch, no venue network, no monitor, no cost.

### Guest Wi-Fi may block device-to-device traffic
Client isolation is standard on institutional and event networks. Every machine gets internet and **none of them can reach each other**, which would kill the demo while looking like a bug in your code. This risk alone justifies the switch.

### Wi-Fi would contradict your own paper
Table 4.9 reports 6.2–19.3 ms consensus over wired LAN. Congested hall Wi-Fi turns that into 50–150 ms with spikes — a judge who read your abstract watches your console disagree with it. Wired reproduces the published conditions.

### Print the patch MATTE, never glossy
Glossy throws specular highlights under venue lighting, changing the pixels the detector sees and weakening or killing the attack. Same reason it mounts on **rigid foam board** — a curled sheet changes geometry between runs and makes results non-reproducible.

Home inkjets shift colour enough to weaken an optimised patch. Print professionally, at **2–3 sizes** (patch-to-object scale matters), and **test each print against the detector before demo day**. Keep the one that measurably suppresses.

---

## Test the docks before relying on them

The failure mode is not "doesn't work." It is **works for twenty minutes, then drops the link once warm** — which is exactly the length of a demo.

1. Both Macs + Jetson on the switch, static IPs
2. Ping between all three
3. Run the real node processes **for at least an hour**
4. Watch for link drops or gRPC reconnects, especially past the 20–30 minute mark

Also confirm macOS sees the dock's ethernet without asking to install anything — some dock chipsets want a driver.

If both docks hold a stable link for an hour under real traffic, use them and buy nothing more.

## The one spare that is justified

**1× TP-Link UE300C, ₹1,100.**

The network is the single point of failure for the entire demo. Every other component has a fallback — the sim falls back to recorded frames, the console falls back to a terminal, AirSim falls back to Gazebo. The network's only fallback is another network device.

The UE300C uses the **Realtek RTL8153**, natively supported on macOS, Windows *and* Linux, so this one spare works on either Mac, the sim PC, or the Jetson. It is a universal part.

---

## Totals

| | ₹ |
|---|---|
| Essential | 15,000–17,000 |
| Recommended | 1,900 |
| **Buy now** | **≈ 17,000–19,000** |
| Optional (active DP adapter, portable monitor) | up to 12,500 |

Down from an initial ≈ ₹25,000 after cutting the SSD, flash drive, NVMe, router, second and third ethernet adapters, and right-sizing the microSD.
