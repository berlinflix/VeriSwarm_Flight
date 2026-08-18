# Pratik — Internal Hackathon Plan for 19 August 2026

**Scope:** temporary qualification plan. Keep `PRATIK_EXECUTION_PLAN.md` unchanged as the full Internal Hackathon + SIH execution plan.

## Your outcome tomorrow

Provide a visually clear, deterministic CoSys/AirSim scene in which one drone starts at A, completes a short safe flight to B, lands, and can be reset immediately. Own the simulator world and vehicle, not the security verdict or autonomy code.

## Priority order tonight

### Your Codex lane

Use Codex to audit CoSys/AirSim settings, RPC reachability checks, reset instructions and evidence collection. Do not let it redesign Samik's flight client or add scene complexity. Validate every generated setting against the running world, freeze the exact configuration handed to Samik, and report coordinate or vehicle-name changes before either side continues.

### P-T1 — Freeze the smallest reliable scene

Use one vehicle and one short route. Do not add swarm choreography, moving attackers, weather changes or complex terrain tonight.

Record a handoff containing:

- CoSys/AirSim build/version and world name;
- P1 wired address `192.168.50.11` and RPC port `41451`;
- vehicle name exactly as returned by AirSim;
- spawn pose A, target pose B and coordinate convention;
- takeoff altitude, speed, position tolerance and expected duration;
- camera/view to project;
- collision objects and expected collision count zero;
- exact reset procedure and time-to-ready;
- tested abort/land behavior.

Choose A and B so the movement is unmistakable to the panel but completes comfortably within one minute. Keep wide clearance from buildings, trees and the ground; obstacle avoidance is not tomorrow's claim.

### P-T2 — Make remote API control reliable

- Bind AirSim RPC to the wired interface needed by Samik's smoke script.
- Verify Samik P2 can connect to P1 without Wi-Fi.
- Confirm the expected vehicle appears after every reset.
- Confirm manual/game controls cannot fight API control during the run.
- Prevent sleep, updates, popups, recording overlays and performance-heavy background tasks.
- Freeze simulator settings after the first full integration pass.

### P-T3 — Validate Samik's smoke flight in your world

Run `codebase/sim/cosys_smoke_flight.py` with Samik and verify:

- API control, arm and takeoff succeed;
- movement is toward the agreed B in the correct coordinate frame;
- the vehicle reaches the tolerance without oscillation;
- collisions remain zero;
- land, disarm and API release are confirmed;
- reset returns to the same A;
- a timeout invokes abort/land instead of hanging.

Do two consecutive cold runs without changing the world or script. Save both outputs and record the second run as the fallback video.

### P-T4 — Prepare the panel view

Use a camera angle that keeps A, B or a clear direction marker visible. Add simple non-misleading labels for A and B if available in the world. Keep the collision counter and compact flight state visible, but remove distracting editor panels.

The world can visually suggest a contested border or damaged urban area, but tomorrow's route must remain simple and safe. The SIH plan will add obstacles, replanning, GPS-denied estimation and swarm behavior after the qualification slice.

## Tomorrow startup checklist

- Connect P1 to the switch by Ethernet and verify `.11:41451` from Samik P2.
- Launch the frozen world and confirm the vehicle at A.
- Run one private smoke flight, save evidence, then reset to A.
- Verify frame rate/telemetry stability and collision count reset.
- Keep the abort/land control immediately available.
- Start screen recording before the panel, but do not cover telemetry.
- Do not edit the world after the final preflight.

## Your on-stage actions

- Keep the world loaded in the background while the webcam and OP-TEE beats run.
- When cued, show the vehicle at A and state that this is the transport smoke test.
- Let Samik's script control the vehicle; do not steer it manually.
- Show takeoff, A-to-B motion, hover, landing and disarm.
- Point to collision count zero and the saved run result.
- If the script aborts, keep hands off until the tested abort/land path finishes.

## Pass/fail and handoff

GO only if two reset-to-reset runs succeed with zero collisions, correct coordinate motion, landing and disarm. Call NO-GO for intermittent RPC, wrong vehicle identity, coordinate mismatch, unstable frame rate, or an untested scene edit.

One live retry is allowed after a clean reset. If it fails again, use the labelled cold-rehearsal recording and its matching `airsim_smoke.json`.

## Evidence you own

- Frozen scene/settings checksum or archived configuration.
- A/B coordinate handoff.
- Two successful smoke-flight JSON outputs.
- Collision and landed/disarmed proof.
- Backup video and reset/abort card.

## Do not claim

Do not call the A-to-B smoke flight autonomous replanning, GPS-denied navigation, swarm behavior or protocol-gated flight. It proves the simulator transport path that the retained SIH execution plan will integrate with VeriSwarm.
