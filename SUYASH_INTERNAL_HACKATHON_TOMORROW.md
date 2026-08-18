# Suyash — Internal Hackathon Plan for 19 August 2026

**Scope:** temporary qualification plan. Keep `SUYASH_EXECUTION_PLAN.md` unchanged as the full Internal Hackathon + SIH execution plan.

## Your outcome tomorrow

Own the demo boundary and prove that Alpha's real measured receipt is signed locally by the Jetson's OP-TEE key, verified by Ethernet peers, and rejected for a controlled protocol attack. You are also the final GO/NO-GO owner.

Your blocking handoff is now specified in `SUYASH_TO_SAMIK_QUALIFICATION_HANDOFF.md`. Complete its H0 checklist before asking Samik to validate Bravo; complete H1 before the full rehearsal.

## Priority order tonight

### Your Codex lane

Run separate bounded Codex tasks for the qualification protocol runner and final diff/test review. Ayush operates `covis_live` under Samik's technical oversight; Suyash independently accepts its evidence and remains the sole integrator for shared protocol and manifest files. Before accepting any generated change, inspect its diff, run its focused tests, run the real Jetson preflight where applicable, and record the evidence path. Reject interface redesigns after the T-16 h contract freeze unless they fix a demonstrated blocker.

### Y-T1 — Review and accept the physical webcam demonstrator

Ayush operates the USB-webcam/Android-DroidCam rig and Samik owns implementation, integration and recovery. Suyash reviews `codebase/tools/covis_live.py`, its focused tests and its retained evidence. Keep it unarmed: it must have no flight or actuator connection.

It must:

- Open Camera A and Camera B concurrently and show their stable IDs.
- Capture timestamped frames with a bounded skew check.
- Show camera health, model hash and measured `PerceptionClaim` fields: `detections_present`, class set, occupancy and observation time.
- Show clean agreement and attack-time dispute/abstention with explicit reason codes.
- Save raw/annotated evidence to a fresh run directory.
- Release both camera handles on normal exit, error, and Ctrl+C.
- Distinguish low features, blur, stale/skewed frames and camera loss from a confirmed adversarial mismatch.

Acceptance: three consecutive clean→attack→recovery cycles without restarting the process, followed by proof that both cameras can be reopened.

### Y-T2 — Build the LAN qualification protocol runner

Implement a narrow `codebase/tools/qualification_protocol_demo.py` plus `codebase/tools/qualification_peer.py`; do not expand them into the final campaign runner tonight. The peer launcher is mandatory because the existing `node.server` CLI supplies no measured snapshot and would yield zero semantic acknowledgements.

Required behavior:

- Alpha executes on Jetson and constructs its OP-TEE signer locally.
- It contacts Bravo on Samik's machine and Charlie on Suyash L1 over Ethernet.
- It originates protocol-v4 receipts containing explicit measured claims; action-only fallback is not accepted as semantic proof.
- It runs exactly two panel cases: clean expected ACCEPT and one deterministic attack expected HOLD.
- Live attack is the unapproved/tampered model hash. Offline checks distinguish stale receipt, command expiry and idempotent exact retransmission.
- It prints one compact table with expected decision, actual decision, semantic acknowledgements and reason codes.
- It writes canonical JSON with the canonical receipt digest, vote target hashes, exact receipt-output/request binding and peer results. Protocol v4 has no authoritative standalone command digest.
- It returns non-zero if an expected outcome, key binding, semantic quorum or evidence write fails.

Never create a Jetson signing API. Never sign Bravo or Charlie with Alpha's key. Never load a software Alpha fallback under the OP-TEE label.

### Y-T3 — Freeze manifests and Alpha identity

- Make a three-node qualification manifest derived from the existing topology without changing the full SIH manifest.
- Pin Alpha's public-key fingerprint and approved model hash.
- Keep each peer identity/key distinct.
- Record the exact manifest hash, repository commit and uncommitted-diff hash in the run evidence.
- Run `tools/optee_preflight.py` on the physical Jetson using a new output path.
- Generate public plus Alpha/Bravo/Charlie node-scoped manifests. Samik receives only Bravo's seed; Alpha has no seed; no node receives another peer's seed.
- Freeze the clean detector-derived replay fixtures and expected v4 claims before Samik starts protocol validation.

### Y-T4 — Build the evidence bundle and operator screen

Do not depend on the planned Streamlit console or event collector. For tomorrow, use a compact terminal/table or minimal existing display that shows:

- case name;
- expected versus actual decision;
- Alpha signer fingerprint;
- model hash status;
- measured claim summary;
- semantic acknowledgement count;
- rejection/HOLD reason;
- saved evidence path.

Hash the completed evidence bundle. Copy one read-only backup to Abhijan's projection machine.

## Tomorrow startup checklist

- Verify switch, wired IPs, ports, time offsets and firewall profile with Samik.
- Verify webcams, OP-TEE device, CA library, TA and pinned Alpha public key.
- Create a fresh run ID and evidence directory.
- Start the webcam demo only; do not start Alpha while the cameras are in use.
- After the physical demo, stop the process gracefully and prove the cameras are released.
- Run fresh OP-TEE preflight, then start Alpha locally on the Jetson.
- Confirm Bravo and Charlie READY without changing quorum.
- Confirm Pratik's A/B route has already passed an independent smoke check.

## Your on-stage actions

1. Deliver the 30-second problem statement and the qualification boundary.
2. Point out measured evidence during the clean webcam condition.
3. Let Abhijan place/remove the attack; explain the visible reason code.
4. Stop the webcam process and demonstrate resource release.
5. Run the fresh OP-TEE challenge and state that Alpha signs locally.
6. Run clean and attacked LAN cases; show ACCEPT then HOLD.
7. Hand the display to Pratik for the A-to-B flight.
8. Close with the evidence summary and the next reviewed integration gate.

Use these exact safety statements:

- “The attack changes sensor evidence; it does not set the verdict.”
- “Alpha's receipt is signed locally on Jetson; the key is not a fleet-wide shared signer.”
- “The CoSys flight is today's transport proof. The protected command adapter remains an explicit SIH integration gate.”

## Pass/fail and handoff

GO only after two cold full rehearsals. Call NO-GO if OP-TEE cannot verify live, the measured clean case lacks semantic quorum, the attack outcome is nondeterministic, or the drone cannot reliably land.

If a subsystem fails live, allow one bounded restart and then use the clearly labelled cold-rehearsal recording plus its saved evidence. Do not weaken quorum, switch Alpha to a software key, or edit source during the panel run.

## Evidence you own

- Network and clock preflight.
- Clean/attack webcam evidence.
- OP-TEE preflight JSON and Alpha fingerprint.
- Clean/attack protocol summary.
- Complete run manifest and `SHA256SUMS`.
- Final go/no-go sheet and exact demo commands.

## Do not claim

Do not say military-ready, protected end-to-end flight, full swarm autonomy, trusted inference or five protected hardware identities. Tomorrow proves a real and valuable slice; the retained full plan completes the integrated system.

## If the base demo is finished early

Preserve the accepted one-drone path, then coordinate the separate three-drone transport extension in the combined plan. Your job is to keep protocol identities and displayed vehicle names aligned, validate the separate output bundle and refuse the extension unless it passes twice before freeze. Do not connect protocol decisions to the multi-drone actuator path without the missing reviewed adapter.
