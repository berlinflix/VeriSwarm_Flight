# STATUS — SAMIK

Updated: 2026-08-22 IST

Branch: `samik/sih26177-perception`

Latest committed work before this status update: `ef2f5c1`

## Current facts

- The official RunPod integration is installed and authenticated. No Pod or network
  volume has been created, so the cloud environment is **NOT READY** and has produced
  no runtime/GPU evidence. The intended target remains one Secure Cloud RTX 5090 with
  persistent storage mounted at `/workspace`.
- Samik reports `$20.00` of RunPod credit. This is user-reported funding, not a balance
  independently exposed by the current API, and no RunPod usage charge has been
  observed yet.
- Disaster candidate media is `E:\dataset\C2A_Dataset.zip` (C2A v2):
  `4,903,081,990` bytes and SHA-256
  `cc21b41d7fcd555134117f95f52eab7fd39a96cd694e8edc8728c540e9eae653`.
- Codex read-only validation found 10,215 decodable images, 10,215 matching YOLO label
  files, 360,467 valid class-0 boxes, and the official 6,129/2,043/2,043 image split.
  This is generated verification, not Samik dataset acceptance or a human label audit.
- C2A's publisher split is not scene-group independent: 1,877 of 2,043 five-view
  groups cross publisher split boundaries. It will be preserved as provenance but a
  deterministic group-safe split is required for qualification.
- Safe/walking candidate media is frozen as Kaggle
  `adilshamim8/people-detection`, version 1. Its archive has **NOT BEEN DOWNLOADED**.
  Dataset origin will never be accepted as the state label.
- VisDrone DET train/validation images downloaded: `0`; converted images/labels: `0`.
- First 100-image rendered label audit: **NOT CREATED** and **NOT HUMAN ACCEPTED**.
- Five-epoch `YOLOv8n@640` smoke job: **NOT STARTED**. Command/output path: **NONE**.
- Latest trained-model metrics, `.pt`, ONNX, TensorRT engine, and Jetson benchmark:
  **NONE**.

## Frozen model/application contract

- One delivered offline application accepts a live camera, recorded video, or image.
- The detector `sar-rgb-person-v1` emits only `person_candidate` observations.
- A separately identifiable state stage `sar-rgb-person-state-v1` supplies
  `safe_walking` or `disaster_stressed` display evidence. Verified safe is green;
  verified distress is red; missing, stale, conflicting, or low-confidence state
  evidence fails closed to red `NEEDS_REVIEW`.
- The two stages are one end-user bundle but retain separate model hashes, thresholds,
  evidence, and qualification. This avoids learning dataset identity as the state and
  does not change Suyash's frozen `veriswarm.rescue.event.v1` schema.
- Mandatory detector candidates are `YOLOv8n@640` and `YOLOv8n@960`;
  `YOLOv8s@640` is optional if the budget permits.
- RunPod RTX 5090 is training/validation only. Production is one onboard Jetson Orin
  Nano 8GB at 15 W. The deployment chain is `.pt` -> static batch-1 ONNX -> TensorRT
  FP16 built on the actual Jetson, with the exact executed engine hash and a mandatory
  15-minute complete camera-to-event benchmark.
- The Windows `.exe` is demonstration-only and supports Live Camera and Video File.
  Recorded video adopts Suyash's `VideoFileSource`, source timestamps, and
  newest-frame processing without a growing stale-frame queue.

## Codex-generated implementation in progress

- Uncommitted training/evaluation code now covers deterministic VisDrone conversion,
  C2A structure and group-leakage validation, external dataset intake, human-review
  gates, exact-hash cloud/runtime evidence, actual model-driven validation inference,
  validation-only selection, untouched-test controls, static ONNX inspection, Jetson
  deployment evidence, and fail-closed green/red sidecar decisions.
- Current completed focused detector/runtime regression:
  `153 passed, 1 dependency-gated skip in 54.02s`.
- Separate in-progress state-classifier and intake work has focused agent test evidence,
  but the final combined full-suite result has not been run yet.
- These are Codex-generated, unreviewed implementation candidates. They are not model
  training, Samik acceptance, dataset acceptance, or deployment qualification.

## Suyash interface adoption

- Latest team update read from
  `origin/suyash/sih26177-rescue-integration` at `437c329`.
- Synthetic/physical rescue videos are external evaluation/demo inputs, never training,
  validation, or threshold-selection data.
- Video outputs must preserve source order/timestamps, separate synthetic from physical
  and real-aerial metrics, report decoded/inferred/dropped counts and latency, and never
  invent pose, depth, range, GPS, or NED from uncalibrated clips.
- The release event continues to carry `person_candidate`; green/red state is a display
  sidecar and cannot veto survivor-first detection evidence.
- No schema, five-camera, security, CoSys, or frozen network evidence was changed.

## Actual blockers

- Detector cloud work has no shared-interface blocker and can continue after the Pod
  environment gate passes.
- State-classifier training cannot start until the candidate media receives genuine
  per-person human state review. Codex-generated labels will not be reported as human
  review.
- Final production qualification later requires physical access to the target Jetson
  Orin Nano to build and execute the FP16 engine.

## Next checkpoint

- Finish and verify the implementation candidate, provision the persistent RTX 5090
  workspace, register/download the frozen archives with hashes, generate the first
  VisDrone audit montage, obtain human acceptance, and then start the five-epoch
  `YOLOv8n@640` smoke job. Report the exact command, output path, measured CUDA/VRAM,
  duration, projected cost, and preliminary validation metrics after execution.
