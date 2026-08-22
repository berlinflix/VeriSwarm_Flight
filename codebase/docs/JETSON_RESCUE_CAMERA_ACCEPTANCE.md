# Jetson rescue-camera acceptance

This gate measures the selected detector on the actual Jetson Orin Nano and Owl
camera. It is intentionally narrower than final system qualification: a pass proves
only the stated visual case and does not prove survivor condition, hazard class,
geolocation, navigation safety, adversarial robustness or field readiness.

## Frozen execution environment

- JetPack 6.2.1 / L4T 36.4.7 / 15 W mode
- Python 3.10 isolated environment: `~/.venv-rescue-jp621`
- Torch 2.8.0 with CUDA available
- Torchvision 0.23.0
- Ultralytics 8.4.56
- OpenCV 4.11.0
- Stable camera path:
  `/dev/v4l/by-id/usb-Owl_Lite_Owl_Lite_Camera_SN0001-video-index0`

Do not install into the OP-TEE/protocol environment at `~/.venv`.

## Model handoff

Samik supplies the selected checkpoint, its class-name mapping, expected image size,
validation metrics and the Git commit that produced the artifact. One SHA-256 is kept
for the selected model because the executable model identity is safety-relevant. Normal
iterative files do not require per-file approval or Suyash authorization.

## Physical cases

Use the same camera position, model, image size and confidence threshold for all three
cases. Each case uses ten warm-up frames followed by 120 measured frames.

1. `empty`: no person is visible. Any target-class detection fails the case.
2. `occluded`: a person remains partly hidden behind a doorway or large object. A target
   must appear in at least 80 percent of measured frames.
3. `full-body-distance`: the complete person is three to five metres from the camera. A
   target must appear in at least 80 percent of measured frames.

These are engineering smoke thresholds, not claims of population-level accuracy.
Dataset-level precision, recall and false-alarm performance remain separate.

## Commands

Run from the repository's `codebase` directory after activating the Jetson environment.
Replace `/path/to/rescue-best.pt` with Samik's selected checkpoint.

```bash
source ~/.venv-rescue-jp621/bin/activate
cd ~/VeriSwarm_SIH_codebase

python -m tools.jetson_camera_acceptance \
  --case empty \
  --model /path/to/rescue-best.pt \
  --out-dir ~/VeriSwarm_Jetson_Evidence/empty

python -m tools.jetson_camera_acceptance \
  --case occluded \
  --model /path/to/rescue-best.pt \
  --out-dir ~/VeriSwarm_Jetson_Evidence/occluded

python -m tools.jetson_camera_acceptance \
  --case full-body-distance \
  --model /path/to/rescue-best.pt \
  --out-dir ~/VeriSwarm_Jetson_Evidence/full-body-distance
```

For a detector whose target class is named differently, repeat `--target-class`, for
example `--target-class person_candidate`. Each run writes a summary, per-frame JSONL
trace and annotated frame and exits nonzero when the stated case fails.

The summary reports raw target boxes separately from strongly overlapping box clusters.
An overlap cluster is an ambiguity diagnostic, never a proven unique-person count. Keep
all raw boxes for audit, use a temporal tracker and multiview geometry to resolve identity,
and present uncertain counts as a lower bound rather than counting every box as a victim.

## Deployment sequence

First execute the selected PyTorch checkpoint in these cases. Only after functional
acceptance should the same selected checkpoint be exported to static batch-one ONNX and
then TensorRT FP16 on the Jetson. Compare outputs on identical frames before accepting
the engine. The existing COCO `yolov8n.pt` baseline is infrastructure evidence only and
must not be presented as the rescue model.
