# Dataset, model and edge-deployment decision

## 1. Model strategy

Do not train one heterogeneous “everything detector.” Separate specialists prevent images
from one dataset silently treating unlabelled classes from another dataset as background.

### P0 model: `sar-rgb-person-v1`

- Task: RGB aerial `person_candidate` detection.
- Starting point: the already supported YOLOv8 family; benchmark `yolov8n.pt` and
  `yolov8s.pt`, then select on held-out recall and edge deadline.
- Fast first dataset: official VisDrone DET train/validation with `pedestrian` and `people`
  remapped to one `person_candidate` class.
- SAR-domain refinement: HERIDAL only if its 8.3 GB transfer and conversion finish by H5.
- Never merge validation/test images into training.

### P0 fast prototype: `sar-alert-rgb-k0`

- Task: local RGB boxes for `person_candidate`, fire and smoke, with optional vehicles.
- Dataset: Kaggle Disaster Response Object Detection Dataset, because it already exposes
  the relevant YOLO classes in one corpus.
- Purpose: get the simulator-to-alert-to-dashboard vertical slice working quickly while
  the official aerial specialists train.
- Acceptance ceiling: `ACCEPTED_DEMO_INTERNAL` until source overlap, duplicate leakage,
  aerial-domain performance and displayed terms are audited. It does not replace
  VisDrone, HERIDAL, HIT-UAV or FloodNet evidence.

### P1 model: `sar-thermal-person-v1`

- Task: thermal aerial `person_candidate` detection.
- Dataset: HIT-UAV, using only the official person annotations and official split or a
  scene-grouped deterministic split if no split is supplied.
- Convert thermal frames deterministically to the model's three-channel input while
  retaining original bytes and modality metadata.
- This model is not an RGB/thermal fusion model. Fusion happens at observation/track level.

### P1 model: `sar-flood-access-v1`

- Task: segment water/flooded road/non-flooded road or derive `road_blocked` from a frozen
  post-processing rule.
- Dataset: FloodNet. Preserve its official split and ten-class masks.
- Prefer a small segmentation architecture already supported by the pinned environment.
  A YOLOv8 segmentation conversion is acceptable only after polygon/mask conversion is
  visually checked on at least 100 images.

### P1 model: `sar-fire-smoke-v1`

Start immediately with the Kaggle Smoke-Fire Detection YOLO set, retaining its negative
images. Use the larger Kaggle Fire/Smoke YOLO v9 set as a fallback or independent
cross-dataset test. FLAME remains the aerial-domain reference. Non-aerial training may
power the live prototype, but aerial and simulator results must be reported separately.

## 2. Primary-source dataset matrix

| Dataset | What it truly provides | Size/labels | License noted by source | 36-hour decision |
|---|---|---|---|---|
| [VisDrone](https://github.com/VisDrone/VisDrone-Dataset) | RGB UAV people/pedestrian and vehicle boxes | 10,209 still images; DET train about 1.44 GB | record displayed terms; unresolved terms limit release, not private evaluation | P0 aerial RGB source |
| [HERIDAL via Accenture AIR](https://github.com/Accenture/AIR) / [Zenodo](https://zenodo.org/records/5662351) | land-SAR aerial human boxes | 3,338 files, 8.3 GB | AIR documents HERIDAL as CC BY 3.0 | P0 refinement only if downloaded by H5 |
| [HIT-UAV](https://github.com/suojiashun/hit-uav-infrared-thermal-dataset) | high-altitude thermal Person/Bicycle/Car/OtherVehicle boxes | 2,898 images from 43,470 frames | CC BY 4.0 | P1 thermal model |
| [FloodNet](https://github.com/BinaLab/FloodNet-Supervised_v1.0) | post-flood UAV segmentation: flooded buildings/roads, water and related classes | 2,343 images | CDLA-Permissive | P1 flood/access model |
| [RescueNet](https://github.com/BinaLab/RescueNet-A-High-Resolution-Post-Disaster-UAV-Dataset-for-Semantic-Segmentation) | UAV damage segmentation: water, four building-damage levels, blocked road | 4,494 images | CC BY-NC-ND | reference/evaluation only until derivative-weight legality is reviewed |
| [SeaDronesSee](https://github.com/Ben93kie/SeaDronesSee) | people/objects in open water and tracking | 8,930 OD-v2 train images | dataset CC0 1.0 | optional flood-rescue augmentation; do not relabel maritime results as urban flood proof |
| [FLAME](https://github.com/AlirezaShamsoshoara/Fire-Detection-UAV-Aerial-Image-Classification-Segmentation-UnmannedAerialVehicle) | aerial fire classification/segmentation | IEEE DataPort components | academic/non-commercial stated | stretch only |
| [SAREnv](https://github.com/namurproject/SAREnv) | search paths, lost-person distributions and coverage metrics—not camera detections | generated geospatial scenarios | MIT | planning reference/benchmark, not perception training |

The 145.6 MB [Search-and-Rescue From Drones](https://zenodo.org/records/3924925)
dataset is explicitly described by its authors as testing/evaluation only. Do not use it as
the primary training corpus.

The fast Kaggle sources, limitations and job order are frozen in
`03_KAGGLE_DATASET_SHORTLIST.md`. Kaggle does not replace the primary-source evaluation
anchors above.

## 3. Dataset intake and acceptance gate

Licence ambiguity is no longer a download or private-experiment blocker. It remains a
redistribution/release blocker. For every downloaded dataset, create
`datasets/<id>/SOURCE.json` outside Git and commit only a redacted manifest template. It
must contain:

```text
source URL and DOI/repository
retrieval time UTC
terms/licence exactly as displayed, copied terms filename, or TERMS_NOT_DISPLAYED
acceptance state: ACCEPTED_DEMO_INTERNAL, ACCEPTED_RELEASE or EXPLORATORY_ONLY
archive filename, byte size and SHA-256
official split identity
original classes
frozen class remapping
converter commit and command
image/annotation counts before and after conversion
rejected/corrupt sample count and reasons
```

`ACCEPTED_DEMO_INTERNAL` data may be processed privately for the hackathon when its source,
version and integrity are recorded. Do not commit, redistribute or present raw samples from
unclear/restrictive sources. Only `ACCEPTED_RELEASE` data may support a distributable model
release. Dataset terms never waive privacy, biometric, export-control or platform rules.

Before training, render a stratified 100-image label audit. Fail if boxes are off-image,
empty labels are misinterpreted, person subclasses are mapped inconsistently, images are
duplicated across splits, or sequence-adjacent frames leak between train and validation.

## 4. Training sequence

Use the already frozen Samik P2 Python 3.12/CUDA environment or a separately hashed cloud
environment. Do not upgrade the accepted webcam environment in place.

Illustrative pinned-environment commands; Samik must substitute exact manifest paths and
record the fully resolved command:

```powershell
yolo detect train model=yolov8n.pt data=configs/sar_rgb_person.yaml `
  imgsz=960 epochs=40 batch=-1 device=0 workers=8 seed=26177 `
  deterministic=True cache=disk patience=10 `
  project=artifacts/training name=sar-rgb-person-n

yolo detect train model=yolov8s.pt data=configs/sar_rgb_person.yaml `
  imgsz=960 epochs=40 batch=-1 device=0 workers=8 seed=26177 `
  deterministic=True cache=disk patience=10 `
  project=artifacts/training name=sar-rgb-person-s

yolo detect val model=artifacts/training/sar-rgb-person-s/weights/best.pt `
  data=configs/sar_rgb_person.yaml split=test imgsz=960 `
  project=artifacts/evaluation name=sar-rgb-person-heldout
```

Run a 5-epoch smoke training first. Stop immediately on broken labels, exploding loss,
zero recall or CUDA instability. Do not wait 40 epochs to discover a converter defect.

## 5. Cloud training contract

Cloud GPU use is allowed for training and evaluation, never required for accepted runtime
inference. Prefer Kaggle notebooks for Kaggle-hosted data so the source stays on its host.
For other data, record where it was uploaded and keep unclear/restricted sources in a
private workspace. Do not publish notebooks containing data or credentials.

Every cloud run must export:

- notebook/script bytes and SHA-256;
- base image or `pip freeze`/`pip inspect` record;
- GPU type and framework/CUDA versions;
- exact dataset manifest and split hashes;
- command/config, seed and start/end timestamps;
- complete stdout/stderr and metrics;
- `last.pt`, `best.pt`, optimizer/training metadata and hashes; and
- a locally repeated validation on the downloaded `best.pt`.

Cloud checkpoints are untrusted until their hash is recorded, the model loads in the local
frozen environment, class names match, and local held-out validation reproduces within the
declared tolerance. Cloud inference is prohibited during the demo.

Parallel cloud runs should explore only the predeclared `n` versus `s` comparison. Do not
conduct uncontrolled test-set-driven hyperparameter search.

## 6. Selection metrics

Life-safety scoring prioritizes recall but cannot ignore false alarms. Freeze thresholds on
validation data and report on untouched test data:

- precision, recall, F1, AP50 and mAP50–95 per class;
- false negatives per image and false alerts per image/minute;
- small/medium/large target breakdown;
- daylight/night or scene breakdown when metadata exists;
- simulator held-out recall as a separate synthetic-domain metric;
- P2 and Jetson median, p95 latency, FPS, memory and model size; and
- threshold and NMS IoU used by the actual demo.

There is no universal numerical PASS threshold without operational study. For this
prototype, Suyash freezes a demo gate before test evaluation; suggested minimums are
person recall >= 0.80 on the selected held-out subset, precision >= 0.60, zero crash over a
10-minute inference soak and p95 latency <= the configured frame period. If not met,
present measured results honestly and keep the COCO baseline as fallback.

## 7. Model registry and export

Each model receives an immutable registry record:

```json
{
  "schema": "veriswarm.model.v1",
  "model_id": "sar-rgb-person-v1",
  "task": "detect",
  "modality": "rgb",
  "classes": ["person_candidate"],
  "weights_sha256": "...",
  "training_manifest_sha256": "...",
  "thresholds_sha256": "...",
  "framework": "ultralytics-pinned-version",
  "input": {"width": 960, "height": 960, "color": "RGB"},
  "status": "candidate"
}
```

Export the winner to fixed-shape ONNX and compare PyTorch versus ONNX outputs on at least
100 held-out images. Record maximum coordinate, confidence and detection-count differences.
Ultralytics supports ONNX export; do not silently change to the current upstream model
generation or package version during this sprint.

## 8. Edge deployment

### P2

P2 trains and validates. Benchmark camera and saved simulator frames using the exact
production adapter, not the Ultralytics demo renderer.

### Jetson

Keep the qualified OP-TEE Python environment untouched. Inventory JetPack, TensorRT,
PyTorch and ONNX Runtime first. Create a separate rescue-inference environment. Preferred
order:

1. use an already compatible JetPack/TensorRT path;
2. otherwise run fixed ONNX with an available local runtime;
3. otherwise show P2 inference and state that Jetson deployment is blocked—do not install
   an incompatible generic Torch wheel.

An on-device claim requires a retained Jetson benchmark with network disabled. Training
may be cloud-based; inference may not.

### Qualcomm roadmap

Produce a standard ONNX artifact and input contract. Qualcomm AI Hub can compile PyTorch
or ONNX to LiteRT, ONNX/QNN or QNN artifacts, but without a supported Snapdragon target
and an executed profile this is a deployment roadmap, not a measured result.
