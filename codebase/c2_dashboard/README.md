# VeriSwarm C2 Dashboard

Production-style React/Vite dashboard for the internal hackathon projection view.
The model-integrity control consumes the real Alpha/Bravo/Charlie qualification
runner; it does not write a pretend verdict to `attack.json`.

## Run

```bash
cd /Volumes/HyperDrive/Development/VERISWARM_SIH/codebase/c2_dashboard
npm install
npm run dev -- --port 5175
```

Open:

```text
http://localhost:5175
```

## Live Ethernet Video Feed

The real-time viewport accepts an MJPEG or browser-compatible video/image stream
through Vite's environment configuration. When Pratik's Cosys-AirSim bridge is
available, start the dashboard with its stream URL:

```bash
VITE_DRONE_STREAM_URL=http://<bridge-ip>:<port>/stream.mjpg npm run dev -- --port 5175
```

Cosys-AirSim itself uses RPC port `41451`; a small bridge must expose captured
frames as a browser-readable stream because the browser cannot consume the RPC
camera API directly. Until configured, the viewport stays in an explicit
`AWAITING FEED` state and does not show fabricated drone footage.

## Build Check

```bash
npm run build
```

## Live Model-Hash Qualification

Prerequisites:

1. Suyash's immutable qualification bundle is distributed to the correct nodes.
2. Bravo (`192.168.50.12:51001`) and Charlie (`192.168.50.13:51003`) are READY.
3. Alpha's fresh OP-TEE preflight passes on the Jetson.
4. The Jetson has only its Alpha-scoped manifest; the dashboard Mac receives no
   private manifest or node seed.

Generate a random service token and start the narrow bridge on the Jetson:

```bash
cd /path/to/VeriSwarm_SIH/codebase
export VERISWARM_QUALIFIER_TOKEN="REPLACE_WITH_A_RANDOM_64_HEX_TOKEN"
python -m tools.qualification_dashboard_service \
  --bind 192.168.50.10 --port 8765 \
  --manifest /path/to/IHQ/alpha-private/alpha.manifest.json \
  --public-dir /path/to/IHQ/public \
  --evidence-dir /path/to/IHQ/evidence/protocol
```

Allow TCP `8765` only from Abhijan's wired address `192.168.50.14`. Then start
the dashboard on the Mac. These variables stay in the Vite server process and
are never exposed as `VITE_*` browser variables:

```bash
cd /path/to/VeriSwarm_SIH/codebase/c2_dashboard
VERISWARM_QUALIFIER_URL=http://192.168.50.10:8765 \
VERISWARM_QUALIFIER_TOKEN="THE_SAME_RANDOM_TOKEN" \
npm run dev -- --port 5175
```

`RUN CLEAN + MODEL HASH` executes the approved-model case first. The attack is
not attempted unless that baseline reaches `ACCEPTED` with two semantic ACKs.
It then runs the unapproved model-hash case and displays the actual receipt hash,
peer decisions, consensus outcome and released HOLD command from retained JSON.
Every run uses a new create-once evidence filename.

## Rescue Mission State

Start the local rescue collector from `codebase/`:

```bash
python -m tools.rescue_event_collector serve \
  --bind 127.0.0.1 --port 8770 \
  --mission-id OP-VARUNA-001 \
  --log results/rescue_events.dev.jsonl
```

Then start Vite with the server-side bridge configured:

```bash
VERISWARM_RESCUE_URL=http://127.0.0.1:8770 npm run dev -- --port 5175
```

The browser polls only Vite's `/api/rescue/state`; it never receives the LAN bearer token.
For a non-loopback collector bind, set the same 32+ character
`VERISWARM_RESCUE_TOKEN` in the collector and Vite processes. The rescue strip shows only
collector state—when unavailable, it explicitly says that no rescue state is being
fabricated.

The producer schema and exact Samik/Pratik handoff are in
`../docs/RESCUE_DATA_PLANE.md`.

For Pratik's direct wired feed, do not expose this collector or the Vite server to the LAN.
Run `ops/start_abhijan_pratik_mac.sh` for the simulation-only path. It keeps both
loopback-only and starts a separate `:8771` ingress restricted to Pratik's exact Ethernet
source IP; it does not contact the Jetson/model-hash peers. Pratik runs
`ops/start_pratik_rescue_sender.ps1`; no rescue bearer token is shared. See
`../docs/team_updates/MESSAGE_ABHIJAN_TO_PRATIK_DIRECT_ETHERNET_DATA_HANDOFF_2026-08-22.md`.

For Pratik's authorization-aware movement-v2 runner, use the two-way launcher instead:

```bash
./ops/start_abhijan_movement_v2_mac.sh
```

Pratik must first start `ops/start_pratik_authorization_receiver.ps1`. The Mac then
publishes a complete snapshot containing fresh canonical authorization events for Alpha,
Bravo, Charlie, Delta and Echo every 500 ms. The dashboard proxies its controls only to
the loopback policy service at `127.0.0.1:8773`; the browser never talks directly to
Windows. Missing policy, receiver failure, stale snapshots, invalid data and replay all
fail closed. The panel reports `LEASE LINK LIVE` only after Windows has accepted a recent
snapshot. See
`../docs/team_updates/MESSAGE_ABHIJAN_TO_PRATIK_SUYASH_MOVEMENT_V2_AUTHORIZATION_LINK_2026-08-23.md`.

The dashboard labels the rescue source explicitly and fails closed when it is not set:

- `LIVE_PRATIK` is set by the two Abhijan launchers and means the collector is receiving
  Pratik's direct Ethernet data plane.
- `REFERENCE_REPLAY` is only for retained sample/rehearsal events; the UI says replay and
  never labels that state live.
- an unset or unknown `VITE_RESCUE_DATA_MODE` is shown as `SOURCE UNVERIFIED`.
- `VITE_RESCUE_STALE_AFTER_MS` optionally changes the telemetry freshness threshold from
  its 8000 ms default.

Person and hazard symbols are drawn on the NED map only when their validated rescue
events contain a genuine `position_ned`. The dashboard never invents a marker position.
`EXPORT REPORT` downloads the collector's current `/api/rescue/report` projection.

## Current Scope

- Translucent telemetry deck for Pratik's Alpha, Bravo, Charlie, Delta and Echo vehicles,
  including link state, event age and fail-closed freshness labels.
- Ethernet-ready NED coverage map with exact cell states, five vehicle positions and
  genuine positioned person/hazard markers.
- Live analytics sourced from retained `results/live_events.jsonl` consensus,
  semantic ACK, pose and reputation events.
- A functional clean-plus-model-hash control backed by Alpha's Jetson service.
- Live rescue coverage, person-candidate, mapped-hazard, vehicle and prioritized-alert
  state backed by the local rescue collector.
- Other attacks remain visibly disabled until their real delivery and evidence
  paths are qualified; the physical patch remains blocked on Pratik's captures.
- Evidence strip separating provenance, semantic ACKs, supervisor action and reason code.

The dashboard does not manufacture verdicts. A missing service, failed clean
gate, timeout, mismatched hash or non-HOLD attack result is shown as unavailable
or failed rather than converted into a presentation PASS.
