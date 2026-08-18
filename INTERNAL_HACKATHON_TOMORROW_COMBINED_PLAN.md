# VeriSwarm Internal Hackathon Qualification Plan

**Demo date:** 19 August 2026  
**Purpose:** qualify for the internal hackathon with a small, real, repeatable slice of VeriSwarm.  
**Status:** temporary qualification overlay only. This file does **not** replace `SIH_2026_Build_Plan.md`, `urgent_new_changes.md`, or any individual full execution plan.

## 1. The one claim we will prove tomorrow

VeriSwarm can turn real sensor evidence into a locally hardware-signed receipt, distribute and verify that evidence over an Ethernet-connected team, reject a controlled attack, and separately demonstrate that the target CoSys/AirSim vehicle can complete a deterministic A-to-B flight.

Tomorrow is a qualification slice, not the completed protected-autonomy product. The full SIH execution remains unchanged and will later connect the verified decision to the reviewed CoSys command adapter.

## 2. Minimum credible demo

The demo has three sequential beats:

1. **Physical perception attack:** two webcams connected to the Jetson observe the same scene. A clean observation agrees. Abhijan then introduces a controlled printed adversarial/occlusion artifact to one view. The software shows the measured evidence and an agreement, dispute, or abstention outcome.
2. **OP-TEE receipt and LAN verification:** stop the webcam process and prove both cameras are released. Alpha then runs on the Jetson, signs its canonical receipt locally through OP-TEE, and Ethernet peers verify it. A clean measured claim is accepted; a model swap, replay, or receipt mutation is rejected and produces HOLD at the decision layer.
3. **CoSys A-to-B flight:** Pratik's CoSys/AirSim vehicle takes off, flies a short deterministic route from A to B, hovers, lands, and records zero collisions. This is explicitly a transport smoke test for tomorrow; it is not represented as already controlled by the signed protocol decision.

### Tomorrow's qualification topology

Use a three-node protocol quorum to reduce demo fragility while keeping every computer on the wired network:

| Machine | Address | Tomorrow's role |
|---|---:|---|
| Jetson | `192.168.50.10` | Webcams first; then Alpha originator and local OP-TEE signer |
| Pratik P1 | `192.168.50.11` | CoSys/AirSim world and vehicle; RPC `41451` |
| Samik P2 | `192.168.50.12` | Bravo verification peer |
| Suyash L1 | `192.168.50.13` | Charlie verification peer and qualification orchestrator |
| Abhijan L2/Mac | `192.168.50.14` | Projector, attack operator, evidence viewer |

All five machines connect through the unmanaged Gigabit switch using Ethernet. Wi-Fi must either be disabled during the run or visibly documented as unused. The full five-aircraft topology remains the SIH target; tomorrow's three protocol identities are a qualification subset.

## 3. What must exist before the first rehearsal

The following entries are **MUST BUILD** items and must not be spoken about as already implemented until their acceptance checks pass:

| Deliverable | Owner | Acceptance check |
|---|---|---|
| Minimal `codebase/tools/covis_live.py` | Suyash | Two cameras open concurrently; clean and attacked evidence is shown and saved; both devices release on exit |
| Minimal LAN qualification runner, preferably `codebase/tools/qualification_protocol_demo.py` | Suyash, with Samik integration support | Alpha runs on Jetson and signs locally; Bravo and Charlie are reached over Ethernet; clean measured claim and one rejection case produce a JSON summary |
| `codebase/sim/cosys_smoke_flight.py` | Samik | Connect, API control, arm, takeoff, move A-to-B, hover, land, disarm, release control; every wait has a timeout and failure triggers land/abort |
| Deterministic A/B scene handoff | Pratik | Fixed coordinates, vehicle name, reset steps, RPC address, expected duration, collision check, and one backup recording |
| Physical attack kit and attack card | Abhijan | Printed artifact, fixed placement/distance/lighting, clean control, attack sequence, expected evidence change, and recovery step |

The qualification runner must use protocol-v4 measured `PerceptionClaim` data. An action-only fallback is not evidence of semantic quorum. Alpha's signer must be constructed and invoked on the Jetson; do not create a shared remote signing service and do not route other identities through Alpha's key.

### 16-hour parallel execution model

All four owners may use Codex continuously, but parallel speed is useful only after the interfaces are frozen. Work in four independent lanes:

| Lane | Primary owner | Frozen output contract |
|---|---|---|
| Physical evidence + Alpha trust | Suyash | `covis_live` evidence schema, OP-TEE preflight output, Alpha qualification result |
| LAN peer + vehicle client | Samik | Bravo readiness contract, qualification peer result, `airsim_smoke.json` |
| Attack + presentation evidence | Abhijan | attack manifest/card, expected-outcome oracle, read-only projected summary |
| Simulator server + deterministic scene | Pratik | RPC endpoint, vehicle/A/B handoff, reset and abort contract |

Rules for Codex-assisted work:

- At T-16 h, freeze file ownership, CLI arguments, JSON fields, IPs, ports, vehicle name and A/B coordinates. Put interface changes in the team channel immediately.
- Give each Codex task one bounded deliverable plus its acceptance tests. Do not ask four agents to redesign the architecture independently.
- Every owner reviews generated code, reads the diff and runs the acceptance check on the actual target machine. “Codex says it works” is not evidence.
- Avoid overlapping edits. Suyash is the only integrator for shared protocol/manifests; Samik owns the smoke-flight script; Abhijan owns attack assets/oracles; Pratik owns the scene/configuration.
- Commit small passing units with owner and evidence path in the message. Suyash integrates only reviewed commits; do not exchange source with USB copies or chat snippets after integration starts.
- Pin dependencies and model files. No package upgrades, new model families or large downloads after T-6 h.
- When Codex discovers an architectural issue, report it to Suyash before changing an interface. When it discovers a local implementation bug within the frozen contract, fix and test it directly.
- Use Codex for a final adversarial review of timeouts, cleanup, evidence truthfulness and failure states, not for adding presentation features.

## 4. Network and hardware preflight

Run this before every rehearsal and again before the panel enters:

- Connect all five computers to the same switch with labelled cables.
- Confirm the static addresses in the table and record the active interface for each machine.
- Confirm bidirectional ping between every participant and Jetson/P1.
- Confirm TCP reachability for Alpha/Bravo/Charlie protocol ports and AirSim RPC `41451`.
- Record clock offsets. Use one time source where possible; otherwise record the measured offset in the evidence bundle.
- Confirm the firewall allows only the required demo ports on the wired profile.
- Confirm the two webcams enumerate on Jetson and no other process owns them.
- Confirm `/dev/tee0`, the pinned Alpha public key, the expected TA, and the OP-TEE client library on Jetson.
- Confirm CoSys/AirSim reports the expected vehicle name and a reset returns it to A.
- Confirm projector readability from the back of the room.

No preflight item may be silently skipped. Mark each PASS/FAIL with operator initials and time.

## 5. Required evidence and success criteria

Create a fresh directory such as `results/internal_qualifier/<run-id>/`; never overwrite an earlier run. Save:

- `preflight/network.txt`: IPs, ping results, required-port checks, clock offsets, software commit, and operator names.
- `webcam/clean.json` and `webcam/attack.json`: timestamps, camera IDs, model hash, measured claims, agreement state, and reason codes.
- `webcam/`: representative raw frames or a synchronized short recording for the clean and attacked conditions.
- `optee_preflight.json`: public-key fingerprint, challenge result, verification result, and signing latency.
- `protocol_summary.json`: clean and attacked decisions, peer reason codes, receipt digest, command digest, model hash, and replay/sequence result.
- `airsim_smoke.json`: A/B coordinates, vehicle name, start/end pose, timestamps, timeout state, collision count, landing and disarm result.
- `SHA256SUMS`: hashes for the complete evidence set.

The demo passes only if all of these are true:

- Clean physical views produce usable evidence and the expected agreement.
- The controlled physical attack changes measured evidence and produces dispute or explicit abstention; a camera failure is never mislabeled as an adversarial attack.
- The OP-TEE preflight verifies under the pinned Alpha public key using a fresh output path.
- A clean measured protocol-v4 claim reaches the required semantic quorum over Ethernet.
- At least one deterministic attack—model hash swap, exact-receipt mutation, or replay—is rejected with a visible reason and HOLD at the protocol decision layer.
- The CoSys vehicle completes A-to-B and lands with collision count zero.
- A second cold run succeeds from reset without modifying source code or evidence files.

## 6. On-stage sequence — target 5 minutes

### 0:00–0:30 — The problem and the boundary

Suyash: “A drone can navigate correctly and still be unsafe if the sensor evidence or model behind its command is compromised. VeriSwarm makes that evidence explicit, signed and independently checked.”

Also say: “This is the qualification slice. We will show a physical perception attack, hardware-backed receipt verification over Ethernet, and a real simulator flight. The reviewed command adapter that joins those paths remains an explicit SIH gate.”

### 0:30–1:25 — Clean physical evidence

- Show both live webcam views on the Jetson/projector.
- Point to synchronized timestamps, camera health, model hash, detected classes/occupancy and the clean agreement state.
- Avoid a dense dashboard. Keep the result and its evidence on one screen.

### 1:25–2:10 — Abhijan's attack

- Abhijan places the controlled artifact exactly as recorded in the attack card.
- Show the evidence change and the dispute/abstention reason.
- Remove it and show recovery to the clean state.
- State: “The attack changes sensor evidence; it does not write its own verdict.”

### 2:10–2:35 — Safe Jetson handover

- Stop `covis_live` gracefully.
- Show that both camera handles are released.
- Run the fresh OP-TEE preflight and show the pinned Alpha fingerprint and verified signature.
- State: “Alpha signs locally on the Jetson. Its non-exportable key is not shared with the other nodes.”

### 2:35–3:35 — Ethernet protocol attack

- Show Alpha, Bravo and Charlie as READY with their wired addresses.
- Run the clean measured case; show semantic acknowledgements and ACCEPT.
- Run one deterministic model-swap or replay case; show the exact peer reason and HOLD.
- Do not show more than two attack cases live. Additional cases belong in the evidence bundle.

### 3:35–4:35 — Pratik's drone flight

- Reset the world, start the smoke-flight script and display the vehicle view plus compact telemetry.
- The drone must take off, fly A-to-B, hover briefly, land and disarm.
- State: “This is today's target-vehicle transport proof. The signed-decision-to-command adapter is the next reviewed integration gate; we are not pretending it is already present.”

### 4:35–5:00 — Close

Show one simple architecture slide and the saved run summary. Close with: “Tomorrow's evidence proves the attack, trust and vehicle paths individually on the real deployment topology. The retained SIH plan joins them, adds autonomous replanning and expands the quorum without weakening identity or safety boundaries.”

## 7. Rehearsal and freeze schedule

Use the full 16 hours, but protect integration and rehearsal time:

| Gate | Required outcome |
|---|---|
| T-16 h | Scope and all cross-lane interfaces frozen; cables, cameras, Jetson, switch and printed attacks present |
| T-15 h | Four Codex-assisted lanes active with non-overlapping files and explicit acceptance checks |
| T-12 h | Each lane executes independently on its target machine and emits its contracted evidence |
| T-10 h | First wired integration begins; fix interfaces before adding any feature |
| T-8 h | Full three-beat demo passes once; all commands copied into the run sheet |
| T-6 h | Feature/dependency/model freeze; only demonstrated qualification blockers may be changed |
| T-4 h | Cold rehearsal 1 from powered/reset state; record duration and every failure |
| T-3 h | Fix only qualification blockers; no UI or architecture expansion |
| T-2 h | Code/config freeze; cold rehearsal 2; create the verified backup recording |
| T-1 h | Charge/power check, fresh evidence directory, projector/font check, printed fallback sheet |
| T-15 min | Final preflight only; no dependency upgrades, model changes or source edits |

Two people must be able to execute the complete run sheet. Suyash is primary operator; Samik is recovery operator.

## 8. Failure policy and fallback

- **Physical view fails:** retry device discovery once. If still unavailable, show the unedited backup recording and saved raw evidence, clearly labelled “recorded rehearsal evidence.”
- **OP-TEE fails:** do not fall back to a software Alpha key and call it equivalent. Show the saved fresh hardware preflight from the cold rehearsal and label the live hardware failure.
- **LAN peer fails:** show which peer is absent. Do not lower quorum during the panel run. Use the saved complete evidence after one bounded restart.
- **CoSys flight fails:** invoke the tested abort/land path. Never keep retrying a moving vehicle on stage. Show the cold-rehearsal recording and `airsim_smoke.json`.
- **Projector/dashboard fails:** use terminals plus the printed one-page result sheet. The evidence must not depend on a planned console that does not exist.

## 9. Claims we will not make tomorrow

- “Military-ready,” “military-certified,” “100% secure,” “unhackable,” or “failsafe.”
- Completed protected A-to-B control before the CoSys adapter and campaign runner exist and pass fault-injection tests.
- Full swarm autonomy, GPS-denied navigation, trusted inference, or five independent hardware-protected aircraft identities.
- That one Alpha OP-TEE key protects or represents every simulated aircraft.
- That a printed patch is a universal attack; it is one controlled, reproducible attack demonstration.

Technical honesty is part of the security demonstration. The panel should see working evidence, explicit rejection reasons, and a credible path from tomorrow's slice to the retained SIH execution plan.

## 10. Final go/no-go owner checklist

| Area | Owner | GO condition |
|---|---|---|
| Overall scope, Alpha, OP-TEE, evidence | Suyash | Two cold runs and fresh verified hardware preflight |
| Qualification runner, peer network, smoke-flight script | Samik | Deterministic commands, bounded timeouts and recorded outputs |
| Attack artifacts, expected outcomes, projected evidence | Abhijan | Clean control and attack both reproducible; operator cannot forge verdict |
| CoSys world, A/B route and recovery | Pratik | Reset-to-reset flight succeeds twice with zero collisions |

Suyash alone calls final GO. Any owner may call NO-GO for their subsystem. A NO-GO invokes the labelled recorded fallback; it never authorizes an untested workaround.
