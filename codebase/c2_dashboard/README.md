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

## Current Scope

- Translucent telemetry deck for Alpha, Bravo and Charlie.
- Ethernet-ready real-time stream viewport with a clear disconnected state.
- Live analytics sourced from retained `results/live_events.jsonl` consensus,
  semantic ACK, pose and reputation events.
- A functional clean-plus-model-hash control backed by Alpha's Jetson service.
- Other attacks remain visibly disabled until their real delivery and evidence
  paths are qualified; the physical patch remains blocked on Pratik's captures.
- Evidence strip separating provenance, semantic ACKs, supervisor action and reason code.

The dashboard does not manufacture verdicts. A missing service, failed clean
gate, timeout, mismatched hash or non-HOLD attack result is shown as unavailable
or failed rather than converted into a presentation PASS.
