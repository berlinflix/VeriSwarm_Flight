# Ayush — USB webcam + Android DroidCam execution plan

**Date:** 19 August 2026

**Camera-code designer/implementer:** Ayush

**Technical owner, integrator and live operator:** Samik

**Attack-artifact operator:** Abhijan

**Final GO/NO-GO:** Suyash

Read `FIVE_CABLE_EXECUTION_FREEZE_2026-08-19.md` first. It overrides any older instruction
that assumes two USB webcams or a separate Ayush Ethernet endpoint.

## Immediate boundary

Ayush has no demo cable, runtime IP or on-stage terminal. He designs/tests the application
and hands Samik one frozen commit. Samik's Windows P2 at `192.168.50.12` connects the USB
webcam and DroidCam, runs capture/YOLO/co-visibility/evidence locally, releases both sources,
then starts Bravo on port `51001`. Abhijan keeps his own `.14` attack-control Mac.

## Outcome

Deliver a reviewed, platform-neutral camera application that Samik can use for three
repeatable clean to attack to recovery cycles on P2 using one USB webcam and one Android
DroidCam phone. Ayush's deliverable is code/tests/documentation, not live operation.

## Code handoff to Samik

1. Start from `origin/codex/covis-live-ui` at
   `f0582b6d6b471b023ba6c312fd198978e5ebdd6b` on a new
   `ayush/covis-live-final` branch.
2. Modify only `codebase/tools/covis_live.py`, the co-visibility helper, their focused
   tests and `codebase/docs/COVIS_LIVE.md` unless Samik approves a demonstrated dependency.
3. Never commit recordings, generated evidence, weights, private manifests, seeds or phone
   URLs/credentials.
4. Run the focused camera tests, `python -m tools.covis_live --help` and
   `git diff --check`.
5. Push one frozen commit and send Samik its exact hash, base, changed-file list, exact test
   output and proposed Jetson command. Do not send loose source files.
6. Fix review findings on the same branch. Samik reviews the final exact commit in a clean
   worktree; Suyash decides whether it can be merged after real P2 camera validation.

## Camera and software tasks

1. Keep capture sources platform-neutral: Windows integer device index/stable backend for
   Camera A and local DroidCam HTTP/V4L2-compatible URL for Camera B.
2. Preserve exact displayed detections in the decision/evidence path; do not add a second
   display-only inference pass.
3. Keep feature-only `COVISIBLE` distinct from semantic `AGREE`; never compare raw boxes
   from different views.
4. Preserve `c`, `a`, `r`, `s`, `q`, create-once evidence and guaranteed release on normal
   exit, error and interrupt.
5. Document the Windows/P2 setup and exact proposed command without hard-coding phone IP,
   camera index, paths, credentials or model bytes.
6. Give Samik troubleshooting notes for source-open failure, skew, blur and DroidCam loss;
   these conditions must produce `ABSTAIN`, never a manufactured attack verdict.

If DroidCam cannot pass the setup gate within ten minutes on P2, Samik keeps the same
phone/tripod and switches Camera B to the prevalidated local MJPEG/RTSP path. He records the
new transport, uses a new run ID and preserves failed evidence. Ayush may fix a demonstrated
code defect, but does not remotely tune thresholds during an accepted run.

## Samik's oversight

Samik reviews the P2 Windows OpenCV/Torch/Ultralytics state, freezes sources/model hash/
thresholds, distinguishes feature-only output from semantic agreement, rejects camera-
health-only attack cycles, reproduces focused tests, checks evidence/release behavior and
acts as live/recovery operator. He starts no Bravo process until the camera process and
sources are closed.

## Acceptance and stop conditions

Ayush's code acceptance requires focused tests, clean diff, documented Windows source
handling and Samik's review. Hardware acceptance then requires three complete cycles from
one frozen command on P2: clean `AGREE`, attack
`DISPUTE` or a valid feature-overlap `ABSTAIN`, recovery `AGREE`, raw/annotated frames,
JSONL/summary, real displayed YOLO boxes and `release_verified=true`.

Stop without weakening thresholds for ambiguous/same camera sources, unstable clean
claims, wrong model hash, existing evidence paths, attack results caused only by loss/blur/
skew, release failure, or any camera/Alpha overlap.
