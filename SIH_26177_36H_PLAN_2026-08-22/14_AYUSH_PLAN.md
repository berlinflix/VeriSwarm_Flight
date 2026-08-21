# Ayush — perception backup and multi-camera adaptation plan

## Mission

Remain available as Samik's backup without duplicating his work. Your default deliverable
is a configurable rescue-model adapter for the existing camera interface and independent
dataset/converter verification. Suyash activates heavier training assistance only when a
trigger below occurs.

## H0–H4: independent readiness

- [ ] Create a clean `ayush/sih26177-perception-support` worktree.
- [ ] Do not edit Samik's converter/model files.
- [ ] Review the frozen ontology, model registry and observation schema.
- [ ] Prepare a dataset-audit script that checks image/label pairing, normalized boxes,
      class IDs, duplicate hashes and split leakage.
- [ ] Prepare a 100-image label montage tool.
- [ ] Review Samik's first converter output independently and report pass/fail.

## Backup activation triggers

Suyash may activate exactly one of these lanes:

- **A — download/conversion:** Samik's primary download or converter is blocked at H3.
- **B — thermal training:** RGB is healthy by H6 and a second GPU/cloud slot is available.
- **C — model fallback:** RGB fine-tune fails; package and benchmark the existing COCO
  person baseline through the production adapter.
- **D — integration:** camera application cannot load the frozen rescue model/registry.

Do not start a second uncontrolled version of Samik's RGB training.

## Multi-camera changes

The current multi-camera tool is valuable as a redundancy/co-visibility display, but it is
not the disaster map and not a five-drone simulator. Make only these bounded changes:

- [ ] Load a model from the immutable registry rather than assuming COCO class names.
- [ ] Display `person_candidate` and supported hazards with modality/model ID.
- [ ] Preserve the existing 2-D homography-projected overlap language.
- [ ] Add track IDs and show when multiple cameras support the same observation.
- [ ] Never merge observations solely because boxes overlap in two image planes.
- [ ] Preserve `AGREE`, `DISPUTE`, `ABSTAIN`, create-once evidence and release verification.
- [ ] Keep all sources local; no internet relay.
- [ ] Do not claim the tripod represents calibrated multi-drone 3-D mapping.

The five-camera rig can become a separate stage called **multi-view rescue observation
validation**: place one person target and one supported hazard/placard in the shared area,
show exact per-camera detections and the projected common regions, then show that a weak or
blocked view abstains rather than manufacturing agreement.

## H8–H18: integration help

- [ ] Verify the model hash, class map, preprocessing and thresholds on P2.
- [ ] Run saved-frame inference and compare UI boxes to emitted observation boxes.
- [ ] Add tests for missing registry, wrong hash, unsupported class and empty detections.
- [ ] Confirm camera output never directly controls a drone.
- [ ] Produce one sample rescue observation JSONL file for Suyash's dashboard tests.

## H18–H30: review and fallback packaging

- [ ] Independently reproduce Samik's selected model load and held-out metric command.
- [ ] Benchmark the COCO baseline and selected model on the same P2 sample.
- [ ] Verify the ONNX/PyTorch comparison report.
- [ ] Package the multi-camera fallback command and a retained accepted video.
- [ ] Freeze at H30; do not change camera transports during rehearsal.

## Required report

```text
reviewed commit and base
files owned/changed
focused and full test output
dataset audit result
model/registry hashes checked
camera source mapping
semantic or feature-only status
release proof
known limitations
```
