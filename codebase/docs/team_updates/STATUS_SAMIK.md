# STATUS — SAMIK

Updated: 2026-08-22 IST

Branch: `samik/sih26177-perception`

Latest work commit before this status update: `db73384`

## Completed since the previous update

- Installed and authenticated the official RunPod integration and verified a funded
  account balance of `$20.00`.
- Prepared, but did not deploy, one Secure RTX 5090 configuration named
  `samik-rescue-person-training`: one RTX 5090, 30 GB temporary container disk, 100 GB
  network volume mounted at `/workspace`, Jupyter/SSH enabled and a pinned container
  image digest. The current displayed rate is approximately `$1.00/hour`.
- Verified local C2A intake at `E:\dataset\C2A_Dataset.zip`: `4,903,081,990` bytes,
  SHA-256 `CC21B41D7FCD555134117F95F52EAB7FD39A96CD694E8EDC8728C540E9EAE653`,
  archive listing exit `0`, 10,215 PNG entries, 20,431 TXT entries and three official
  split JSON entries. This is Codex-generated unreviewed intake evidence, not Samik
  dataset acceptance.
- Read and adopted Suyash's latest Jetson gate and synthetic-POV handoffs from
  `origin/suyash/sih26177-rescue-integration` through commit `60317d3`.
- Updated the external implementation plan to distinguish cloud training, onboard Jetson
  production deployment and the secondary Windows live/video demonstration wrapper.
- Re-ran the focused rescue perception-adapter tests with an external pytest temporary
  directory: `10 passed in 0.12s`. This is Codex verification, not Samik acceptance or
  model-training evidence.

## In progress now

- Production target is the Jetson Orin Nano 8GB installed onboard the drone, using the
  available 15 W power profile and one live RGB stream. RunPod RTX 5090 is training and
  validation infrastructure only.
- Frozen candidate order is mandatory `YOLOv8n@640`, mandatory `YOLOv8n@960`, then
  optional `YOLOv8s@640` if budget remains. The selected model must pass the Nano gate.
- Frozen artifact chain is cloud `.pt` -> static batch-1 ONNX -> TensorRT FP16 engine
  built on the actual target Nano. An RTX-built TensorRT engine will not be transferred
  to Jetson.
- The Windows `.exe` is demonstration-only and will expose Live Camera and Video File
  modes. Recorded-video integration will adopt Suyash's `VideoFileSource`, preserve source
  timestamps and use newest-frame sampling without a stale-frame queue.

## Outputs available

- External implementation plan:
  `C:\projects\VeriSwarm\My work\SAMIK_RESCUE_PERSON_MODEL_2026-08-22\IMPLEMENTATION_PLAN.md`.
- Frozen detector output class: `person_candidate` only.
- VisDrone input mapping: `pedestrian` and `people` -> `person_candidate`.
- Verified VisDrone images downloaded: `0`; verified annotations downloaded: `0`.
- Converted images: `0`; converted labels: `0`.
- Five-epoch smoke job: **NOT STARTED**.
- Latest model metrics: **NONE**.
- Trained `.pt`, ONNX, TensorRT engine and Nano benchmark: **NONE**.
- Executable training pipeline: **NOT GENERATED**. The current artifact is a reviewed
  implementation plan, not completed training code.

## Suyash interface adoption

- The most accurate held-out candidate that passes the Orin Nano 8GB at 15 W gate is the
  production choice; speed alone does not select the model.
- The one-drone Nano gate covers one RGB stream and the complete capture-to-event/outbox
  path. The five-camera tripod demonstration remains separate evidence.
- The release allowlist binds the exact executed TensorRT `.engine` SHA-256, source `.pt`
  and ONNX lineage, class map, threshold, input shape, precision, runtime identity and
  accuracy/benchmark reports.
- Generated disaster POV results are labelled `synthetic_rgb` and kept separate from
  real-aerial held-out metrics.
- The detector proves only RGB `person_candidate`; it does not prove thermal or disaster
  hazard detection. Green/red safe-disaster triage remains a separate classifier/display
  capability and does not change `veriswarm.rescue.event.v1`.
- No schema, five-camera, security or frozen network evidence was changed.

## Blockers

- The billable RunPod Pod has not been deployed. Training cannot start until compute is
  created and the immutable environment gate passes.
- Safe/disaster state classification still requires reviewed per-person labels; the
  `karthika95/pedestrian-detection` dataset is not accepted as a `safe_walking` label
  source.
- Physical Jetson Orin Nano access is required later to build and qualify the final
  TensorRT engine.

## Next checkpoint

- Deploy the prepared RunPod configuration, verify its actual GPU/datacenter/image and
  persistent volume, create the pinned environment, download and audit VisDrone, then run
  the five-epoch `YOLOv8n@640` smoke gate. Publish measured duration, cost projection,
  CUDA/VRAM evidence and preliminary validation metrics before full candidates.
