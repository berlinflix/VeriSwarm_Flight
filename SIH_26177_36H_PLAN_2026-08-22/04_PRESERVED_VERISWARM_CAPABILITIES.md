# Preserved VeriSwarm capabilities and extended demo

## Preservation rule

The rescue pivot adds a mission profile; it does not delete or rewrite the accepted
multi-camera, model-provenance, protocol, OP-TEE or prior attack evidence. Preserve source
commits, hashes, commands, videos and create-once results exactly as produced. New rescue
runs use new run IDs and reference—not rename—the prior evidence.

## 1. Five-camera multi-view rescue observation

The existing five-camera work is a useful physical demonstration of why multiple viewpoints
matter in search and rescue. Its accepted claim is bounded:

> Five local cameras observe a shared physical area from different angles. The application
> displays per-camera detections, measured/projected common image regions and whether
> independent views support, dispute or abstain on an observation.

It does **not** prove five airborne drones, calibrated 3-D triangulation or identity across
arbitrary viewpoints unless those functions are separately implemented and measured.

### What to retain

- five camera/source identities and backend mapping;
- the three-panel/projected-overlap UI and existing feature/release evidence;
- per-camera raw boxes, model/hash, frame/capture time and latency;
- overlap/covisibility values, `AGREE`/`DISPUTE`/`ABSTAIN` outcome and reasons;
- create-once summary JSON/JSONL, screenshot, internal recording and camera-release proof;
- current accepted commits and model/environment manifests; and
- one fallback video that can be replayed without representing it as live.

### Rescue adaptation

1. Place a person-shaped target and one supported hazard/placard in the physical common
   viewing area.
2. Show the target from different angles and distances with a box in every view that
   actually detects it.
3. Show the projected common region as geometry distinct from detector boxes.
4. Occlude or turn one camera away. That source must become weak/`ABSTAIN`; the application
   must not fabricate agreement.
5. Present multi-view support as confidence/provenance for a responder, not as permission
   for a flight command.

Samik operates and oversees the accepted runtime. Ayush owns the bounded UI/registry
adaptation and evidence packaging. Suyash accepts the final claim language.

## 2. Preserved cyber-robustness demonstration

Reuse Abhijan's existing attack cases as a second, independent security beat:

- approved model versus model-swap/unapproved hash;
- replayed or stale signed evidence;
- peer timeout/abstention;
- malformed or inconsistent receipt/claim; and
- physical/digital adversarial patch evaluation where the target class and detector are
  valid and the clean/attack evidence is retained.

Expected product behavior is fail-closed: reject or quarantine the affected perception
source, HOLD its command contribution and reassign unfinished search cells. A signature
proves receipt origin/integrity; it does not prove that the inference is semantically true.

Use these claims:

- “The system is adversarially evaluated against these demonstrated cases.”
- “It rejects this unapproved model and contains the affected node.”
- “Independent views/peers expose this disagreement and force HOLD.”

Do not use “adversarial-proof,” “tamper-proof,” “unhackable” or “immune to attacks.” Those
are universal claims that the evidence cannot establish.

Abhijan owns attack execution and expected reason codes. Suyash owns policy integration
and claim/evidence mapping. Samik owns camera/runtime reproducibility. Alpha's existing
OP-TEE demonstration remains evidence for a hardware-protected signing key only.

## 3. Future cross-border profile

The same architecture can later support border surveillance because the reusable pieces
are mission partitioning, multi-view observations, geotagged alerts, offline operation,
model authorization, signed evidence, quarantine and work reassignment. Present this as a
future deployment profile, not as a capability proved by the disaster demo.

A border profile still requires domain-specific datasets/classes, night/thermal validation,
terrain and long-range communications tests, privacy/retention policy, jurisdictional
approval, counter-spoofing evaluation and operational rules. Reusing the platform does not
make the present rescue model a border-intrusion detector.

## 4. Extended presentation order

If the panel permits an additional 60–90 seconds:

1. **Multi-view physical proof:** five angles, projected overlap, object boxes, one occluded
   view abstaining and clean camera release.
2. **Rescue mission:** autonomous coverage, person candidate, obstacle-safe response,
   geotagged dashboard alert and situational report.
3. **Cyber containment:** model swap or adversarial disagreement causes rejection/HOLD and
   deterministic sector reassignment.
4. **Platform roadmap:** disaster response is the current validated mission; border
   surveillance is a future domain pack using the same trusted autonomy core.

The short four-minute rescue sequence remains the fallback. Never risk the coherent rescue
run merely to fit every preserved subsystem into one live process.
