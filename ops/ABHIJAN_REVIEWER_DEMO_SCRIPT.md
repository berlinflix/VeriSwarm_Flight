# VeriSwarm model-integrity reviewer presentation

## One-sentence message

VeriSwarm keeps an edge-AI rescue mission fail-closed by verifying model provenance
before trusting a drone claim, quarantining Alpha when Bravo and Charlie reject an
unapproved model hash, and exposing that decision to responders without leaking private
security material.

## What the reviewer should understand

1. The clean approved-model baseline succeeds with two independent semantic ACKs.
2. The model-swap evaluation carries an unapproved model hash in a signed Alpha receipt.
3. Bravo and Charlie independently compare that provenance against the approved policy.
4. Both peers dispute the unapproved hash, so the command remains `HOLD [0,0,0]`.
5. Alpha is projected as `QUARANTINED` and a critical responder alert is generated.
6. This is a demonstrated adversarial evaluation, not a claim of being unhackable.

## Four-minute speaking sequence

### 1. Problem and relevance — 30 seconds

> Disaster-response drones must continue operating when cloud connectivity is unavailable,
> so inference happens on the edge. That creates a security question: how can the command
> centre trust a detection if a drone is running an unapproved model? VeriSwarm treats model
> identity as evidence that must be independently verified before a claim can influence the
> mission.

### 2. Physical topology — 35 seconds

> Alpha is the Jetson at 192.168.50.10 and signs the qualification receipt through OP-TEE.
> Bravo at .12 and Charlie at .13 are independent policy verifiers. The Mac at .14 runs the
> rescue collector and reviewer dashboard. The qualification API stays bound to Alpha's
> loopback interface and the Mac reaches it through an SSH tunnel.

Point to the footer and say:

> Only Alpha has the hardware-backed OP-TEE identity in this slice. Bravo and Charlie are
> software-key verification peers, so I will not claim that every node is hardware-attested.

### 3. Establish the clean gate — 35 seconds

Point to **Clean baseline — 2/2 ACK**.

> First, the dashboard runs the frozen clean case. Alpha presents the approved model hash.
> Bravo and Charlie both acknowledge it, producing two out of two semantic ACKs. The attack
> case is not allowed to run unless this clean gate succeeds.

### 4. Demonstrate the model-hash rejection — 55 seconds

Point to **Attack ACKs — 0/2**, **Peer verdict — 2/2 DISPUTE**, and the two hashes.

> Next, Alpha presents the frozen adversarial case carrying a model hash that is not on the
> signed allowlist. This is a safe qualification replay; we do not hot-load an unknown model
> into the flight runtime. Bravo and Charlie independently dispute the provenance. Zero
> attack ACKs is therefore the correct result, while the clean baseline remains two out of
> two.

### 5. Explain the safety consequence — 45 seconds

Point to **Safety response — HOLD + QUARANTINE** and the critical alert.

> Because the model provenance is unapproved, the supervisor releases no movement command:
> HOLD with the zero vector. The retained proof is normalized into the rescue event schema.
> The command centre then marks Alpha as quarantined and raises a critical
> `model_hash_not_approved` alert. Detailed hashes, peer receipts and private identities stay
> in the separate retained security evidence instead of entering the disaster-detection
> stream.

### 6. Connect it to the rescue mission — 35 seconds

> When Pratik's simulator producer is connected, the same dashboard receives mission start,
> coverage, vehicle, link and task-reassignment events. The intended operational response is
> that Alpha's work is reassigned after quarantine. Until those simulator events arrive,
> Operation Varuna correctly remains `NOT_STARTED`; the dashboard does not fabricate rescue
> telemetry.

### 7. Close — 25 seconds

> The result is not that VeriSwarm is attack-proof. The demonstrated claim is narrower and
> testable: an unapproved model-provenance receipt is rejected by independent peers, the
> command remains fail-closed, the affected node is quarantined, and responders receive an
> actionable alert with retained evidence for audit.

## Exact dashboard reading order

1. **Clean baseline:** `2/2 ACK`.
2. **Attack ACKs:** `0/2 ACK`.
3. **Peer verdict:** `2/2 DISPUTE`.
4. **Safety response:** `HOLD + QUARANTINE`.
5. **Retained proof:** `VERIFIED · UNAPPROVED HASH`.
6. **Hash comparison:** approved allowlist hash differs from the hash claimed on Alpha.
7. **Encrypted event log:** Bravo and Charlie both dispute the same target receipt.
8. **Operation Varuna alert:** Alpha quarantined because its model hash is not approved.

## Claims to use

- “The system is adversarially evaluated against an unapproved model-provenance claim.”
- “Bravo and Charlie independently reject the claim.”
- “The supervisor fails closed and releases no motion.”
- “Alpha's hardware-backed signer is OP-TEE configured.”
- “The dashboard separates attack evidence from genuine rescue observations.”
- “Person detections are person candidates requiring human review.”

## Claims to avoid

- “The system is unhackable, tamper-proof, adversarial-proof or immune to attacks.”
- “All three nodes are hardware-attested.”
- “We loaded a malicious model into the live flight runtime.”
- “The clean case has zero ACKs.”
- “The system confirmed a survivor.”
- “The dashboard currently shows live simulator mission telemetry” until Pratik's producer
  is actually connected.

## Before the reviewer arrives

1. Keep Bravo and Charlie peer services ready.
2. On Alpha, run `~/vs peers`, `~/vs check`, then `~/vs dash`.
3. On the Mac, run `./ops/start_abhijan_rescue_mac.sh`.
4. Open `http://127.0.0.1:5175`, run **Approved Baseline**, and pause on `2/2 ACK`.
5. Run **Model-Hash Attack** only after explaining the clean state.
6. Publish the exact retained model-swap filename with
   `./ops/publish_rescue_authorization.sh <filename>`.
7. Confirm one vehicle, Alpha quarantined and the critical model-hash alert.
8. Do not rerun after evidence is retained unless **Reset view** is intentionally selected.
9. Do not install Jetson updates immediately before the demonstration.

## If the live qualifier is unavailable

Do not invent a result. Show the already retained dashboard evidence and say:

> This is the create-once proof retained from the physical Alpha-Bravo-Charlie Ethernet run.
> The live service is unavailable, so I am presenting the retained evidence rather than
> simulating a successful response.
