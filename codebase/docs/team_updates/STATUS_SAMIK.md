# STATUS — SAMIK

Updated: 2026-08-22 18:59 IST

Branch: `samik/sih26177-perception`

Latest committed work before this update: `9e845e5`

Last central inbox commit seen: `0f7cb7f`

## Completed since the previous update

- Deployed one Secure Cloud RunPod training Pod:
  - Pod ID: `g0t6q14d7ed9is`
  - GPU: NVIDIA GeForce RTX 5090, 32,607 MiB reported by `nvidia-smi`
  - price: `$0.99/hour`
  - data center: `EU-RO-1`
  - pinned image digest:
    `sha256:c7ff5829fb34e42557edf949396d3875a74b3a896f7835268670cc62fdd3e60a`
  - disposable container disk: 30 GB
  - persistent network volume: `e6vat4wz37`, 100 GB STANDARD, mounted at
    `/workspace`
- Verified a real SSH proxy session into the running container. Runtime evidence:
  Python `3.12.3`, image Torch `2.9.1+cu130`, CUDA `13.0`, CUDA available, and
  device name `NVIDIA GeForce RTX 5090`. The pinned training environment is still
  to be created under persistent `/workspace`; the image interpreter is not being
  treated as the qualified environment.
- Registered a dedicated local RunPod SSH public key. The private key remains outside
  Git and is not included in this status.
- Registered the unchanged C2A v2 source archive outside Git at:
  `E:\Model Training\SAMIK_RESCUE_PERSON_MODEL_2026-08-22\dataset-intake\accepted\c2a-v2\archive-registration.v2.json`.
  The archive is `4,903,081,990` bytes with SHA-256
  `cc21b41d7fcd555134117f95f52eab7fd39a96cd694e8edc8728c540e9eae653`.
  It has not been extracted or modified.
- Completed the broad automated rescue-training candidate regression:
  `225 passed, 3 dependency-gated skips in 249.39s`. This is Codex-generated test
  evidence, not model training, dataset qualification, human review or deployment
  qualification.
- Fetched and adopted Suyash's latest central integration update at `0f7cb7f`, including
  the Jetson engine-identity gate, `VideoFileSource`, newest-frame video processing,
  and synthetic/physical/real-aerial evaluation separation.

## Current dataset and training state

- C2A archive transfer to RunPod: **NOT STARTED**.
- Safe/walking dataset is frozen to Kaggle `adilshamim8/people-detection`, version 1.
  Its archive has **NOT BEEN DOWNLOADED**.
- VisDrone DET train/validation archives: **NOT DOWNLOADED**.
- Images converted on RunPod: `0`; qualified labels: `0`; training epochs: `0`.
- Five-epoch `YOLOv8n@640` smoke job: **NOT STARTED**. Command/output: **NONE**.
- Trained metrics, `.pt`, ONNX, TensorRT engine, and Jetson benchmark: **NONE**.
- The authenticated console balance observed after Pod setup was `$19.87`.

## Frozen archive workflow

- Every source ZIP is copied into persistent `/workspace` and retained unchanged.
- Before extraction, the pipeline requires expected byte size, SHA-256 and ZIP integrity.
- Extraction is path-safe and bounded. Automated qualification then checks image/label
  pairing, decode failures, annotation validity, content duplicates and split leakage.
- Conversion/reorganization writes a derived YOLO tree and dataset YAML under
  `/workspace`; training consumes that YAML, never the ZIP.
- Source archives, extracted data, audits, environments, logs and model outputs remain
  on the persistent network volume and outside Git.
- Human review has been explicitly removed. No human-review claim will be made. Dataset
  qualification is an automated, hash-bound, fail-closed receipt over the complete
  extracted dataset.

## Frozen model and deployment contract

- The detector `sar-rgb-person-v1` emits only `person_candidate`.
- A separately identifiable state stage `sar-rgb-person-state-v1` supplies
  `safe_walking` or `disaster_stressed` evidence inside one end-user application.
  High-confidence predicted safe is green; predicted distress is red; missing, stale,
  conflicting or low-confidence state evidence fails closed to red
  `DISASTER / UNVERIFIED`.
- Dataset origin is prohibited as a state label. Automated labels must carry immutable
  weak-supervision labeler/policy hashes, confidence and
  `weak_supervision_unverified` provenance.
- Mandatory detector candidates are `YOLOv8n@640` and `YOLOv8n@960`;
  `YOLOv8s@640` is optional if the budget permits.
- RunPod RTX 5090 is training/validation only. Production is a Jetson Orin Nano 8 GB
  at 15 W: `.pt` -> static batch-1 ONNX -> TensorRT FP16 built on the actual Jetson,
  exact executed engine hash, and a 15-minute full camera-to-event benchmark.
- Windows `.exe` is demonstration-only. Recorded video uses Suyash's
  `VideoFileSource`, source timestamps and newest-frame inference with no stale queue.

## In progress now

- Creating the persistent RunPod directory tree and exact Python 3.12.13 / Torch
  `2.13.0+cu130` / Torchvision `0.28.0+cu130` / Ultralytics `8.4.56` environment.
- Finishing adversarial hardening of sealed selection/deployment evidence, automated
  weak state labels and raw-trace-derived Jetson qualification evidence.
- Preparing immutable ZIP transfer and automated intake commands before starting the
  first dataset download or epoch.

## Blockers

- No shared-interface blocker for dataset intake or detector training.
- Direct TCP SSH has not yet accepted the newly registered key; authenticated RunPod
  proxy SSH works and is being used for setup. This is an access-path issue, not a
  model or shared-interface blocker.
- State training remains gated on independent automated posture/hazard teacher agreement
  with abstention; dataset identity cannot stand in for a safe/distress label.
- Final production qualification requires physical access to the target Jetson Orin
  Nano to build and execute the FP16 engine.

## Shared-interface changes

- NONE. Suyash's `veriswarm.rescue.event.v1`, survivor-first behavior, five-camera
  evidence, security evidence, CoSys ownership and frozen network remain unchanged.
- Green/red state stays a separate display sidecar and cannot veto or relabel the
  `person_candidate` rescue observation.

## Next checkpoint

- Finish the persistent environment gate; move each immutable source ZIP into
  `/workspace`; verify size, SHA-256 and integrity; safely extract and run the complete
  automated audit; create the derived YOLO YAML; then launch the detached five-epoch
  `YOLOv8n@640` smoke job. Publish the exact command, output/log paths, CUDA/VRAM,
  duration, projected cost and preliminary validation metrics after execution.
