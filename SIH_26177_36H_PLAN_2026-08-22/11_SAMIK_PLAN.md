# Samik — dataset, training, evaluation and edge inference plan

## Mission

Deliver the frozen perception pack and an inference adapter that turns RGB/thermal frames
into traceable observations. Training can use local or cloud GPUs; accepted inference is
local/offline.

## H0–H2: inventory and selection

- [ ] Create a clean `samik/sih26177-perception` worktree.
- [ ] Preserve the qualified webcam and semantic environments; create a new training env.
- [ ] Inventory P2 disk, CUDA, VRAM, Python and outbound bandwidth.
- [ ] Choose local or cloud GPU based on a 5-epoch timing probe, not assumption.
- [ ] Read `03_KAGGLE_DATASET_SHORTLIST.md`; download the Kaggle Disaster Response YOLO
      set and Smoke-Fire Detection YOLO set first on two parallel lanes.
- [ ] In parallel, download VisDrone DET train/val for aerial RGB and HIT-UAV for thermal.
- [ ] Start HERIDAL in parallel only if 8.3 GB can finish before H5.
- [ ] Start FloodNet only after one RGB loader passes.
- [ ] Record Kaggle slug/owner/version, visible terms state and archive hash before
      extraction. `TERMS_NOT_DISPLAYED` permits private experiment but not redistribution.

## H1–H5: converters and quality gate

- [ ] Write separate deterministic converters; never combine raw datasets.
- [ ] Convert the Kaggle Disaster Response labels to `person_candidate`, `fire`, `smoke`
      and optional vehicle classes; preserve its original labels and source identifier.
- [ ] Convert the Kaggle Smoke-Fire set as a separate two-class specialist with negatives.
- [ ] Map VisDrone `pedestrian` and `people` to `person_candidate`; ignore other classes in
      the derived one-class set while retaining original annotations.
- [ ] Map only HIT-UAV `Person` to thermal `person_candidate`.
- [ ] Preserve official splits. If a split is absent, group by video/scene before splitting.
- [ ] Emit image/label counts, class counts, corrupt list and duplicate hashes.
- [ ] Render and manually inspect 100 examples per accepted dataset.
- [ ] Commit converters, tests, manifest templates and audit renderer—not data.

H4 gate: a 100-image audit must show correct boxes and no cross-split duplicate. If it
fails, fix the converter before training.

## H4–H12: parallel RGB models

- [ ] Run a five-epoch `sar-alert-rgb-k0` smoke job first to unblock live integration.
- [ ] Run COCO `yolov8n.pt` on the untouched RGB validation subset as baseline.
- [ ] Run a 5-epoch `yolov8n` smoke training.
- [ ] Launch frozen `n` and `s` candidates in parallel only if the smoke run is healthy.
- [ ] Select using validation recall/precision plus P2 latency; do not inspect test to choose.
- [ ] Freeze the confidence/NMS thresholds.
- [ ] Evaluate the winner once on the untouched test subset.
- [ ] Export fixed-shape ONNX and compare it against PyTorch on 100 held-out images.
- [ ] Produce `sar-rgb-person-v1` registry entry and hashes.

## H6–H16: secondary specialist

Priority order after the rapid Kaggle detector begins:

1. fire/smoke specialist from Kaggle on a parallel cloud worker;
2. aerial RGB person model on VisDrone/HERIDAL;
3. thermal person model on HIT-UAV; and
4. flood/access segmentation on FloodNet.

- [ ] Ask Ayush to take one converter/training lane only if Suyash activates the backup.
- [ ] Keep thermal and RGB models separate.
- [ ] If synthetic Cosys frames are used for adaptation, keep them in a separate source
      group and hold out entire target placements/weather variants.
- [ ] Mark every synthetic observation explicitly.

## H10–H18: production inference adapter

- [ ] Implement one load at startup; reject unexpected model/class-map hashes.
- [ ] Accept frame/source/capture-time/modality metadata.
- [ ] Emit the frozen observation schema with the exact boxes used on screen.
- [ ] Include preprocessing and inference latency separately.
- [ ] Fail closed on NaN, wrong tensor shape, unsupported class, stale frame or OOM.
- [ ] Add batch/saved-frame CLI for Pratik and a callable adapter for integration.
- [ ] Add deterministic unit tests with a stub model; keep real-model tests separately marked.
- [ ] Hand Pratik/Suyash the API and one valid sample event by H12–H14.

## H14–H24: edge benchmark

### P2

- [ ] Benchmark 500 frames after warmup and report median/p95 latency, FPS, VRAM and errors.
- [ ] Soak for 10 minutes without memory growth or camera ownership leaks.

### Jetson

- [ ] Do not modify the qualified OP-TEE environment.
- [ ] Inventory JetPack/TensorRT/PyTorch/ONNX Runtime.
- [ ] Create a separate inference environment only when compatible.
- [ ] Copy ONNX plus registry by hash and benchmark with network disabled.
- [ ] If Jetson runtime cannot be completed safely, preserve the blocker and use P2 offline
      inference; do not call that Jetson deployment.

## Preserved five-camera responsibility

- [ ] Keep the accepted five-camera branch, model/environment hashes and evidence intact.
- [ ] Oversee Ayush's bounded rescue UI adaptation; require projected overlap and detector
      boxes to remain visually and semantically distinct.
- [ ] Execute the final five-camera stage on the qualified machine, including an occluded
      view that abstains and complete release verification.
- [ ] Do not call image-plane overlap calibrated 3-D fusion or multi-drone localization.

## H24–H30: evidence and freeze

- [ ] Package only models, registry, class maps, thresholds, evaluation and commands in an
      external content-addressed bundle.
- [ ] Run wrong-model/wrong-class-map/corrupt-model/empty-frame tests.
- [ ] Send Suyash exact commits, hashes, held-out metrics, limitations and runtime commands.
- [ ] Freeze at H30. No retraining after the integrated threshold is accepted.

## Required deliverables

```text
DATASET_MANIFESTS/
LABEL_AUDIT/
TRAINING_MANIFEST.json
EVALUATION.json
ERROR_ANALYSIS.md
MODEL_REGISTRY.json
sar-rgb-person-v1.pt
sar-rgb-person-v1.onnx
optional sar-thermal-person-v1.*
optional sar-flood-access-v1.*
SHA256SUMS
RUN_INFERENCE.txt
```

## Failure fallbacks

- RGB fine-tune misses deadline: use existing COCO person model and label it baseline.
- HERIDAL transfer misses H5: continue with VisDrone; do not wait.
- Thermal model misses H16: demonstrate synthetic thermal sensor view only, without a
  thermal-detection claim.
- Flood segmentation misses H18: map blocked route from metric obstacle occupancy and call
  it route blockage, not AI flood classification.
- Cloud fails: resume from locally downloaded dataset/checkpoint and run the nano model.
