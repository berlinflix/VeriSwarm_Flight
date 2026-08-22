# Samik multi-camera to Abhijan C2 dashboard

## What is implemented

Samik's `tools.covis_multicam` process now has two independent, read-only outputs:

1. `GET /status` plus `GET /stream.mjpg` on the camera host for the annotated
   2--5 camera composite. The service binds to loopback by default. A non-loopback
   bind requires a 32+ character `VERISWARM_MULTICAM_TOKEN`.
2. Optional `veriswarm.rescue.event.v1` image-space observations through a
   Samik-only SQLite outbox. The camera process never shares Pratik's telemetry
   source sequence or outbox.

Abhijan's Vite server proxies both camera endpoints. The browser receives neither
the upstream URL nor the token. The C2 panel is placed after the main mission status
and before the existing search heatmap/movement/reassignment panel. It uses an
approximately 70/30 desktop split and stacks on narrower screens.

## Failure behavior

- Five measured cameras: `LIVE`.
- One to four measured cameras: `DEGRADED`; unavailable configured cameras retain
  explicit `CAMERA OFFLINE` tiles and only measured live pairs are assessed.
- No producer: the panel remains in place and says `MULTI-CAMERA FEED OFFLINE`.
- Evidence older than `VITE_MULTICAM_STALE_AFTER_MS` (default 4000 ms): `STALE`;
  the old stream is no longer presented as live.
- A Samik failure does not change Pratik rescue state. A Pratik failure does not
  stop the camera status/stream path.

The stream is display evidence only. Appearance-only evidence stays `REVIEW` or
`ABSTAIN`. It never creates `position_ned`, a confirmed-survivor claim or a flight
command.

## Combined two-computer run

Use exact private Ethernet addresses; do not bind to `0.0.0.0`.

On Abhijan's Mac, set Samik's exact address, Samik's bridge URL and a random shared
camera-bridge token before starting the existing Pratik integration launcher:

```bash
export VERISWARM_SAMIK_IP=192.168.50.REPLACE
export VERISWARM_MULTICAM_URL=http://192.168.50.REPLACE:8780
export VERISWARM_MULTICAM_TOKEN=REPLACE_WITH_RANDOM_64_HEX
./ops/start_abhijan_pratik_mac.sh
```

This keeps the canonical collector on `127.0.0.1:8770`, Pratik's restricted ingress
on `:8771`, and (when `VERISWARM_SAMIK_IP` is set) Samik's separately source-locked
ingress on `:8772`.

On Samik's Windows machine:

1. Copy `config/covis_multicam.example.json` to a local, untracked configuration.
2. Put Samik's exact Ethernet address in `dashboard_bind`.
3. Set `rescue.enabled` to `true` and `rescue.endpoint` to
   `http://<ABHIJAN_ETHERNET_IP>:8772`.
4. Give every configured camera its real rescue node. Add `calibration_id` only for
   a genuinely measured calibration.
5. Set the same `VERISWARM_MULTICAM_TOKEN`. The source-IP ingress does not need a
   rescue bearer token.
6. Run:

```powershell
$env:VERISWARM_MULTICAM_TOKEN = "REPLACE_WITH_THE_SAME_RANDOM_64_HEX"
powershell -ExecutionPolicy Bypass -File tools\launch_covis_multicam_demo.ps1 `
  -CameraConfigPath .\config\covis_multicam.local.json
```

The Python runner also supports direct flags. Rescue mode requires all three of
`--rescue-mission-id`, `--rescue-endpoint`, and `--rescue-outbox`, plus exactly one
`--camera-node CAMERA NODE` per configured camera.

## Event behavior

- Model-thresholded supported boxes are converted to image-space observations.
- A lightweight camera-local IoU tracker emits a new observation only for a new
  track; it does not create a new person marker on every frame.
- Publishing is capped at 3 Hz and defaults to 2 Hz.
- `source_seq` is persisted per `<node>.perception` stream in the dedicated SQLite
  file and survives restart.
- Deterministic event identities make exact offline replay idempotent.
- Frames never enter rescue JSON. MJPEG and JSON remain separate bounded paths.

## Verification truth

Implemented and automated-testable: status derivation, degraded/offline/stale UI,
read-only MJPEG service, event conversion, persistent sequence allocation, exact
replay, collector coexistence with Pratik telemetry, responsive panel and production
build.

Still pending before a live claim: Samik must run the actual configured cameras and
model, Abhijan must retain a screenshot/video from the combined dashboard, and the
team must measure the real network/stream latency. No live hardware verification is
claimed by this document.
