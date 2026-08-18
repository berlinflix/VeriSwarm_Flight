# Five-cable internal-qualifier execution freeze

**Date:** 19 August 2026

**Status:** ACTIVE for the internal-qualifier setup and demonstration

**Scope:** physical network, camera ownership, code handoff and sequential run order

**Does not replace:** the later five-aircraft SIH architecture

This file is the authoritative answer when an older plan mentions two USB webcams, six
wired operator computers, a separately wired Ayush computer, Ayush at `192.168.50.15`,
or Bravo on port `51002`.

## 1. Exactly five cables and five wired machines

The unmanaged switch has no DHCP role. Each physical Ethernet adapter uses a static `/24`
address with a blank gateway and blank DNS. Do not bridge Wi-Fi and Ethernet and do not
enable Internet Connection Sharing.

| Cable | Machine | Wired address | Internal-qualifier role |
|---:|---|---:|---|
| 1 | Jetson Orin Nano | `192.168.50.10` | Alpha originator and local OP-TEE signer after the camera beat |
| 2 | Pratik P1 | `192.168.50.11` | CoSys/AirSim server, RPC `41451` |
| 3 | Samik P2 | `192.168.50.12` | USB/DroidCam application first; then Bravo peer on gRPC `51001` |
| 4 | Suyash L1 | `192.168.50.13` | Charlie qualification peer, gRPC `51003`, orchestration |
| 5 | Abhijan Mac | `192.168.50.14` | Independent attack-control terminal, event/evidence view and projector |

There is no `192.168.50.15` endpoint. Ayush designs the camera software but has no wired
runtime endpoint. Abhijan keeps his own Mac and attack-control terminal. The Android phone
consumes no Ethernet cable; it is Camera B and reaches Samik's PC through a prevalidated
local DroidCam HTTP/V4L2 path.

Wi-Fi may remain enabled during setup, Git transfer and the DroidCam camera stage. After
the camera evidence is closed and clocks are synchronized, disable unrelated hotspot/Wi-Fi
links for the accepted Ethernet protocol run. All demo RPC/gRPC clients must use the
explicit `192.168.50.x` endpoints; a hotspot address is never accepted as wired proof.

## 2. Camera hardware and operator freeze

- Camera A is one rigidly mounted USB webcam connected directly to Samik's Windows P2.
- Camera B is one Android phone on a tripod, using DroidCam or the already approved local
  OpenCV-compatible phone stream into Samik's PC. There is no second USB webcam.
- `tools.covis_live` executes on Samik's PC and displays locally; it does not run on Jetson.
- Ayush owns camera-software design, implementation and focused development tests only.
- Samik owns review, Windows integration, live operation, evidence, release and recovery.
- Abhijan independently applies/removes the artifact from his own `.14` attack terminal.
- Suyash owns final evidence acceptance and the camera-to-protocol GO/NO-GO.
- The camera application is unarmed and has no signing, flight or actuator interface.

## 3. One-commit Ayush to Samik handoff

Loose Python files are not an accepted handoff.

1. Ayush starts from `origin/codex/covis-live-ui` at
   `f0582b6d6b471b023ba6c312fd198978e5ebdd6b` and works on a dedicated
   `ayush/covis-live-final` branch.
2. He limits the change to the camera implementation, co-visibility helper, focused tests
   and `codebase/docs/COVIS_LIVE.md`; generated evidence, videos, weights, private
   manifests, seeds and phone credentials stay out of Git.
3. He runs `tests/test_covis_features.py`, `tests/test_covis_live.py`,
   `python -m tools.covis_live --help` and `git diff --check`.
4. He pushes once and sends Samik the exact 40-character commit, base commit, changed-file
   list, exact test output and proposed Jetson command.
5. Samik reviews that exact commit in a clean worktree, reproduces the focused tests and
   checks Windows camera-source handling, failure behavior, model-hash enforcement,
   create-once evidence and source release.
6. Only after review does Samik run that exact commit with the physical USB/DroidCam pair
   on P2. Do not copy loose files into the Bravo checkout or modify the frozen commit.
7. Suyash accepts or rejects the real Samik-P2 hardware run. A Mac or mock-only pass is not
   hardware acceptance. The Jetson camera environment is not used in this topology.

Hash the frozen final commit and retained evidence once. Do not repeatedly re-hash unchanged
intermediate drafts.

## 4. Fixed sequential run order

1. Power the switch with its original adapter; connect and label the five cables above.
2. Prove all five wired addresses and the exact host/MAC/address mapping. Prove `41451`,
   `51001` and `51003` only after the corresponding service listens.
3. On Samik P2, run feature-only alignment, then the pinned-model semantic camera run.
4. Samik records three clean to attack to recovery cycles; Abhijan moves only the artifact.
5. Exit normally, retain evidence, close DroidCam and prove the USB source can be reopened.
6. Confirm `tools.covis_live` is gone and no camera source remains owned before Bravo starts
   on that same P2 host.
7. Synchronize clocks, remove unrelated Wi-Fi paths for the accepted protocol run and run
   a fresh OP-TEE preflight to a new evidence filename.
8. Samik starts Bravo on `192.168.50.12:51001`; Suyash starts Charlie on
   `192.168.50.13:51003`; Alpha runs locally on the Jetson and signs locally through OP-TEE.
9. Clean protocol evidence must yield two semantic acknowledgements and `ACCEPTED`.
   The frozen unapproved-model case must yield peer rejection and `HOLD` for the declared
   reason. Save the raw peer reasons.
10. Pratik and Samik run the separately frozen CoSys A-to-B transport proof. A local
    `127.0.0.1:41451` Q-B run remains local simulator evidence; a later
    `192.168.50.11:41451` run receives a new LAN run ID and never overwrites it.

## 5. Stop conditions

Stop and retain the failure without reusing its run ID if any of these occurs:

- an address is duplicated, a wired service resolves through Wi-Fi, or a required port is
  not reachable from its declared peer;
- the camera sources are ambiguous, the pinned model hash differs, the clean condition is
  unstable, or the attack result is only camera loss/blur/skew;
- camera ownership survives normal exit or overlaps Bravo execution on Samik P2;
- Bravo's scoped identity is copied to Ayush or any second active host;
- the protocol produces zero semantic acknowledgements, rejects for the wrong reason, or
  releases anything other than HOLD after rejection;
- the CoSys run changes the frozen route/settings or overwrites prior local evidence.

## 6. Role summary

| Person | Immediate responsibility |
|---|---|
| Suyash | Switch/IP acceptance, Charlie, OP-TEE preflight, final integration and GO/NO-GO |
| Samik | Review/integrate/run `covis_live` on P2, release cameras, then start Bravo; own CoSys client |
| Ayush | Design/test `covis_live` and deliver one frozen reviewed commit; no runtime cable/IP |
| Abhijan | Keep `.14` attack terminal; prepare/apply/remove attack and validate evidence |
| Pratik | Keep the deterministic CoSys world and `.11:41451` endpoint frozen and recoverable |
