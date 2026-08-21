# Acceptance matrix and final demonstration

## 1. Capability-to-evidence matrix

| Capability | Minimum accepted evidence | Failure meaning |
|---|---|---|
| autonomous coverage | deterministic sector/path manifest plus completed visited cells | no autonomy claim |
| obstacle safety | fresh metric depth/LiDAR, stopping calculation and HOLD/bypass before collision | mission FAIL |
| RGB person candidate | held-out metrics, frozen model/hash and traceable live/simulator observation | use COCO baseline label or omit claim |
| thermal person candidate | real thermal held-out evaluation plus executed local runtime | show synthetic thermal view only; no thermal-AI claim |
| flood/access hazard | held-out segmentation metric and traceable mask, or metric route-blocked event | say route blockage, not AI flood detection |
| geolocation | known-target error distribution and uncertainty field | show image-space alert only |
| multi-drone coordination | assignment manifest, no duplicate ownership and deterministic reassignment | fall back to two drones |
| offline resilience | network removed during inference/mission plus buffered/replayed events | no offline claim |
| command dashboard | all displayed state reconstructs from retained event log | dashboard rejected |
| model trust | approved case releases; wrong hash rejects for exact reason and HOLDs | security beat rejected |
| situational report | report matches event projection and truth evaluation | manual summary only |
| on-device inference | retained P2/Jetson benchmark with network disabled | call it offline workstation inference or roadmap |

## 2. Required tests

### Unit/contract

- schema rejects unknown/missing fields and non-finite values;
- dataset converters preserve counts and boxes;
- model registry rejects wrong hashes/classes/preprocessing;
- planner output is deterministic and within geofence;
- geolocation refuses stale/missing pose/depth;
- dedup retains provenance;
- command adapter rejects expired/unauthorized commands;
- event replay is idempotent.

### Simulator integration

- cold boot/reset twice;
- one-drone route and landing;
- two-drone sector separation;
- five-drone preferred mission;
- obstacle HOLD/bypass;
- person observation to map alert;
- quarantine and work reassignment;
- dashboard unavailable then reconnect;
- clean landing/disarm/API release/reset.

### Adversarial/failure

- corrupt or unapproved model;
- camera/image unavailable;
- stale depth and stale pose;
- event sequence replay/duplicate;
- RPC timeout;
- impossible route;
- one drone unavailable;
- dashboard link loss;
- existing evidence directory.

## 3. Demo sequence — 4 minutes

### 0:00–0:25 — responder problem

Show the frozen disaster map and state: “Five autonomous scouts must search a flooded,
partially blocked urban area while connectivity is limited. The goal is faster person
discovery and safer responder access—not autonomous medical diagnosis.”

### 0:25–0:55 — launch and allocation

Start the create-once campaign. Show all drones, sector assignment, model identities,
offline edge status and coverage beginning.

### 0:55–1:35 — person discovery

One drone detects a person candidate. Show the exact camera box, track, map point,
uncertainty, model hash and P2/Jetson latency. The dashboard raises a prioritized alert and
deduplicates a second observation.

### 1:35–2:10 — hazard-aware autonomy

A route meets rubble or a blocked road. Show fresh depth/occupancy evidence, HOLD or bounded
replan, and zero collision. If flood segmentation passed, overlay the flood mask; otherwise
describe only the metric route blockage.

### 2:10–2:45 — resilient swarm/trust beat

Trigger the existing unapproved-model case. State exactly: “The model bytes are outside
the approved mission policy.” Show rejection/HOLD and deterministic reassignment of the
quarantined drone's remaining cells. Do not say the signature proved the AI correct.

### 2:45–3:25 — completion and report

Show coverage, vehicle states, person/hazard markers and the generated situational report.
Land/disarm/reset cleanly.

### 3:25–4:00 — measured results and roadmap

Show held-out precision/recall, latency, coverage/time-to-detection, collision count and
limitations. State that GPS-denied VIO, real thermal hardware and Snapdragon QNN profiling
are next verified gates.

## 4. Exact presenter language

Use:

- “person candidate detected by the frozen RGB/thermal model”;
- “metric depth identified an unsafe corridor”;
- “the planner held/recomputed a bounded route”;
- “the observation was geolocated with this uncertainty”;
- “inference continued locally with the network unavailable”;
- “the unapproved model identity was rejected and the command was held”;
- “simulated sensor” and “synthetic thermal” where applicable.

Avoid:

- “confirmed survivor”;
- “all hazards detected”;
- “GPS-denied” if simulator pose/GPS is still used;
- “thermal camera” for a recolored RGB or segmentation view;
- “military-grade,” “certified,” “perfect” or “unhackable”;
- “Byzantine tolerant” for three nodes; and
- “OP-TEE secures the whole AI pipeline.”

## 5. Cold-run GO/NO-GO checklist

- [ ] exact clean commits and no dirty worktrees;
- [ ] dataset/model/world/settings/config hashes match;
- [ ] correct five host/IP identities when the wired trust stage is used;
- [ ] clocks synchronized and default routes unchanged;
- [ ] simulator and endpoint listener healthy;
- [ ] RGB/depth/pose freshness healthy;
- [ ] model loads, class map matches and offline inference passes;
- [ ] event collector/dashboard empty and ready;
- [ ] evidence/run directories do not already exist;
- [ ] reset and emergency HOLD/land commands printed;
- [ ] screen/video recording active;
- [ ] fallback video and two-drone command available.

## 6. PASS criteria for each cold run

- process exit code zero and summary `pass: true`;
- all launched drones remain inside geofence/altitude/separation bounds;
- zero unapproved collisions;
- at least the P0 person candidate is detected and traceable;
- obstacle interaction produces safe HOLD/bypass;
- no duplicate final person marker for the same target;
- wrong-model case rejects for the expected reason and releases HOLD;
- reassignment covers the quarantined drone's unfinished required cells;
- event log chain/replay verifies;
- report and dashboard match the retained event log;
- all vehicles land/disarm/release control and reset;
- no camera/simulator/client process remains unexpectedly alive.

If a run fails, preserve it unchanged, stop and use a new run ID only after review. Never
overwrite, relabel or silently repeat a failed campaign.

## 7. Suggested final artifacts

```text
OP-VARUNA-001/
  FROZEN_INPUTS.json
  SHA256SUMS
  scenario/
  models/
  routes/
  evidence/cold-01/
  evidence/cold-02/
  reports/
  video/
  RUNBOOK.md
  RESET_ABORT_CARD.md
  CLAIM_EVIDENCE_MATRIX.md
```
