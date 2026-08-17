# VeriSwarm verification gates

This file defines what “works” means. A green console or one successful flight
is not acceptance evidence. A gate is complete only when every listed assertion
is automated, repeatable from a clean environment, and its artifacts are kept.

## S0 — deterministic protocol and safety simulation

Status: implemented.

```bash
python3 -m pytest -q
python3 -m sim.closed_loop
```

Required invariants:

- Honest, semantically verified motion with proven clearance is released.
- Rejected, no-quorum, crypto-only, unhealthy, stale, replayed, malformed, or
  clearance-unknown commands release `(0, 0, 0)`.
- An exact replay inside the freshness window is rejected.
- A duplicate sequence with different signed content is rejected.
- Conflicting signed votes are attributed as equivocation and count on neither
  side.
- Reputation cannot accept or reject below the integer threshold.
- Event sequence and hash-chain verification pass.
- Every mock verdict satisfies `0 <= semantic_acks <= acks`.

## S1 — perception replay

Status: partial; not an acceptance gate yet.

Use synchronized RGB, metric depth, camera calibration, pose, and ground-truth
labels captured from the intended simulator scene. Do not inject expected action
vectors. Run the actual detector and controller for every drone.

Acceptance requirements:

- Separate train/tune/test scenes and seeds.
- Day/night, blur, glare, rain/fog, partial lens obstruction and dropped frames.
- Small/thin obstacles and obstacles occupying less than 5% of the depth ROI.
- Physical 3-D viewpoint transformations, occlusion and non-planar scenes; a
  whole-image 2-D rotation is not patch-transfer evidence.
- Report confidence intervals and all failures, not only successful examples.
- Detector silence must remain HOLD unless independent free-space evidence is
  positive.
- RGB, depth, action and pose must come from one atomic timestamped snapshot;
  separately sampled "latest" values are not admissible evidence.

## S2 — distributed network simulation

Status: protocol transport exists; adversarial campaign incomplete.

Run separate node processes with node-scoped manifests. Exercise latency,
reordering, duplication, loss, partitions, peer restarts, clock jumps, malformed
protobufs, vote equivocation, cache flooding and certificate failures.

Acceptance requirements:

- Bounded memory and thread use under a sustained unauthenticated/invalid load.
- No motion authorization during a partition or after command expiry.
- All honest nodes produce the same finalized certificate, or all remain
  undecided. The present independent tally is not final Byzantine agreement;
  this requirement remains open until a round/finality protocol is implemented.
- Measure time to peer convergence, not only originator reply collection.
- Count vote broadcasts and acknowledgements in overhead results.

## S3 — PX4/Gazebo closed loop

Status: pose measurement exists; the complete loop is not wired to PX4 yet.

`node.mission.MissionRunner` is the required integration seam. A PX4 adapter must
provide synchronized frame/depth/pose/health and accept only the supervisor's
released command. Static observations and dummy input bytes are forbidden.
Originator and verifier poses need an independently trusted simulator or
autopilot source; a host-signed pose claim alone does not prove its accuracy.

Acceptance requirements:

- Software-in-the-loop only, inside a bounded world with no physical actuator.
- Explicit arm preconditions and automatic LAND/KILL recovery procedure.
- Independent geofence and collision prevention remain active when VeriSwarm
  crashes or stalls.
- Measured worst-case timing at the configured camera and control rates.
- Repeated campaigns for obstacle, packet-loss, process-crash, stale-pose,
  model-swap and adversarial-scene cases.
- Zero simulated collisions in all protected campaigns. Any collision is a test
  failure and is retained as an artifact.

## H0 — hardware-in-the-loop

Status: not started.

Use the exact flight computer, autopilot, cameras, depth/lidar, power system and
firmware. Motors remain disabled or propellers removed. Inject sensor loss,
thermal throttling, low voltage, process death, link failure and reboot/rollback.

## F0 — restrained flight test

Status: prohibited until S0–S3 and H0 pass and an independent safety review is
complete.

Required before flight includes a controlled range, containment, safety pilot,
physical kill switch, regulatory approval, written hazard analysis, calibrated
stopping envelope, signed build/configuration, per-device keys, mutual TLS,
secure boot/rollback protection, recovery procedure and witnessed abort tests.

## Non-claims

- OP-TEE currently protects a signing key; it does not prove trusted inference.
- A receipt signature authenticates the originator's pose claim; it does not
  make a compromised host's pose truthful. Production needs independently
  authenticated estimator state or cross-checked visual geometry.
- Hash-chained events detect ordinary modification; without a signed external
  anchor, an attacker who rewrites the entire file can recompute the chain.
- A model/runtime hash proves byte identity against an authority allowlist, not
  correctness or absence of backdoors.
- No fixed angle, IoU or L2 threshold is a universal adversarial-ML guarantee.
- “Military grade” and “perfect” are not testable requirements. Each mission
  needs explicit threats, invariants, evidence, and accepted residual risk.

## Assurance baseline for the next phase

- Use [MIL-STD-882E Change 1](https://quicksearch.dla.mil/qsDocDetails.aspx?ident_number=36027.)
  to maintain a system hazard log, severity/probability classification,
  mitigations and named risk-acceptance authority. Passing cyber tests does not
  close a flight-safety hazard.
- Map the build/release process to [NIST SP 800-218 SSDF](https://csrc.nist.gov/pubs/sp/800/218/final):
  reviewed changes, isolated builds, dependency provenance/SBOM, vulnerability
  response, signed artifacts and reproducible release evidence.
- Apply protect/detect/recover controls from [NIST SP 800-193](https://csrc.nist.gov/pubs/sp/800/193/final)
  to boot firmware and rollback recovery; a runtime hash after an untrusted boot
  is not a root of trust.
- Structure adversarial-perception campaigns using [NIST AI 100-2](https://csrc.nist.gov/pubs/ai/100/2/e2023/final)
  and govern residual AI risk using [NIST AI RMF 1.0](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10).
- Pin one PX4 release and archive every parameter. Exercise its documented
  offboard-loss, position-loss, data-link, geofence, battery and termination
  failsafes in SITL/HITL. Simulator defaults are not acceptance settings.
- Enable MAVLink 2 signing with persistent monotonic link timestamps and reject
  unsigned control messages. MAVLink signing authenticates messages but does not
  encrypt them, so protect the link separately where confidentiality matters.
