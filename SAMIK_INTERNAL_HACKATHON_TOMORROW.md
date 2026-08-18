# Samik — Internal Hackathon Plan for 19 August 2026

**Scope:** temporary qualification plan. Keep `SAMIK_EXECUTION_PLAN.md` unchanged as the full Internal Hackathon + SIH execution plan.

## Your outcome tomorrow

Make the network and vehicle paths boringly repeatable: Bravo must verify Alpha over Ethernet, and Pratik's CoSys vehicle must execute a bounded A-to-B smoke flight and land safely. You are Suyash's recovery operator.

## Priority order tonight

### Your Codex lane

Use Codex for the smoke-flight state machine, focused tests/mocks, Bravo launch validation and failure-path review. Require it to inspect the installed AirSim/CoSys client version before choosing API calls. Review every diff yourself and validate against Pratik's actual simulator; a mock-only pass does not satisfy the acceptance check. Do not edit Suyash's shared protocol interfaces without an agreed handoff.

### S-T1 — Implement the CoSys/AirSim smoke-flight script

Create `codebase/sim/cosys_smoke_flight.py` as a narrow transport test, not as the final autonomy stack.

Required state machine:

1. Connect to the configured AirSim RPC address and confirm the expected vehicle exists.
2. Enable API control and arm.
3. Take off to the fixed safe altitude.
4. Hold briefly and confirm valid pose/velocity telemetry.
5. Move to the fixed B coordinate using bounded velocity/position limits.
6. Hold at B, verify position tolerance, then land.
7. Confirm landed state, disarm and release API control.

Every asynchronous wait needs an explicit timeout. Any connection, telemetry, position or collision failure must enter the tested abort path: stop motion, land if controllable, disarm and release control. The script must never leave the vehicle armed after exit.

Write `airsim_smoke.json` containing endpoint, vehicle name, A/B coordinates, state transitions, pose errors, collision count, timeouts, landing result and process result. Return non-zero on any failed invariant.

Acceptance: two consecutive reset-to-reset runs with no source/config edits, final pose inside the agreed tolerance, collision count zero, landed and disarmed.

### S-T2 — Own Bravo and LAN readiness

- Configure Samik P2 at `192.168.50.12` on Ethernet.
- Start Bravo with the node-scoped qualification manifest and its own software simulation identity.
- Confirm Suyash/Jetson can reach Bravo's exact port and that Bravo can verify Alpha's pinned public key.
- Do not copy Alpha's private material, OP-TEE client state or a whole seed-bearing manifest to P2.
- Record startup command, process ID, listening port, log path and clean shutdown command.
- Ensure logs show receipt ID, decision and reason codes without dumping secrets.

### S-T3 — Integrate and test the qualification runner

Support Suyash on `qualification_protocol_demo.py` by validating:

- clean protocol-v4 measured claim obtains the expected semantic acknowledgement;
- unapproved/tampered model hash is rejected with the expected reason;
- stale/replayed receipt is never counted as a fresh acknowledgement;
- peer timeout or disconnect produces HOLD, not a permissive fallback;
- the displayed command digest exactly matches the signed receipt binding.

Do not connect this runner to AirSim for tomorrow unless the reviewed adapter already exists and passes fault tests. The panel flight remains a clearly labelled transport smoke test.

### S-T4 — Write the executable run sheet

Produce a one-page ordered list of exact commands for:

- network preflight;
- Bravo/Charlie startup and readiness;
- Jetson webcam start/stop;
- OP-TEE preflight;
- qualification clean/attack cases;
- AirSim connect/reset/smoke flight;
- graceful shutdown and emergency abort.

Mark the machine and working directory for every command. No command may rely on shell history or an unstated environment variable.

## Tomorrow startup checklist

- Arrive with P2 power, Ethernet adapter/cable and the frozen environment.
- Verify wired address, firewall and time offset.
- Start Bravo and leave its READY/log view visible.
- From P2, verify Jetson Alpha, Suyash Charlie and Pratik AirSim endpoints.
- Run the smoke flight once before the panel, reset, and preserve the output.
- Check that the abort/land command is immediately accessible.
- Place the frozen run sheet on both your and Suyash's machines.

## Your on-stage actions

- Keep Bravo running and watch readiness/verification logs.
- During the clean protocol case, confirm Bravo produced a semantic acknowledgement for the measured claim.
- During the attack, point to Bravo's exact rejection reason; do not interpret it as a flight action.
- Start the A-to-B smoke script when Suyash hands over to the CoSys beat.
- Monitor timeout, collision and landed/disarmed status.
- If Suyash's operator terminal fails, execute the frozen run sheet as recovery operator.

## Pass/fail and handoff

Your subsystem is GO only if Bravo remains reachable across two cold rehearsals and the smoke flight completes twice from reset with zero collisions and correct shutdown. Call NO-GO on intermittent telemetry, uncontrolled movement, missing timeouts, hidden Wi-Fi routing or an untested dependency change.

One live retry is allowed. After that, abort/land and show the labelled cold-rehearsal evidence.

## Evidence you own

- Bravo startup/readiness log and port check.
- Clean and attacked peer verdicts/reason codes.
- AirSim smoke-flight JSON for two consecutive runs.
- Abort/land test result.
- Frozen command run sheet.

## Do not claim

The smoke-flight script is not path replanning, GPS-denied navigation, swarm task allocation or protocol-gated control. Those remain in your full execution plan after qualification.
