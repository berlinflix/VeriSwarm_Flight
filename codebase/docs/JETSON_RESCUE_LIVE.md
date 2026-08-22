# Jetson rescue live viewer

`tools.jetson_rescue_live` is the model-agnostic physical-camera demonstration for the
SIH rescue mission. It displays only target-class boxes, measured FPS and latency, an
immediate person-candidate alert, model identity and overlap ambiguity. It records an
annotated AVI, per-frame JSONL and a final session summary.

The UI intentionally says **PERSON CANDIDATE**. A detector box alone does not establish
survivor status, unique identity, geolocation or movement authorization.

## Jetson command

Run from the repository `codebase` directory in the JetPack-qualified environment:

```bash
source ~/.venv-rescue-jp621/bin/activate

python -m tools.jetson_rescue_live \
  --model ~/VeriSwarm_Models/incoming/rescue-best.pt \
  --camera /dev/v4l/by-id/usb-Owl_Lite_Owl_Lite_Camera_SN0001-video-index0 \
  --out-dir ~/VeriSwarm_Jetson_Evidence/live/rescue-pt-01 \
  --width 1280 \
  --height 720 \
  --camera-fps 30 \
  --record-fps 25 \
  --imgsz 640 \
  --confidence 0.25 \
  --device 0
```

Press `q`, Escape or close the window to stop. Every run requires a new output directory.
The runner reopens and reads the camera after shutdown; a failed release probe returns a
nonzero exit code.

For a bounded headless check, add `--no-display --max-frames 120`.

## TensorRT swap

After the selected static batch-one ONNX has been converted on the target Orin Nano, use
the same command and replace only the model and output-directory arguments:

```bash
--model ~/VeriSwarm_Models/engines/rescue-best-640-fp16.engine
--out-dir ~/VeriSwarm_Jetson_Evidence/live/rescue-fp16-01
```

Ultralytics loads the target-built TensorRT engine while the display, target filtering,
alert behavior and evidence schema remain unchanged. Compare PyTorch and TensorRT on the
frozen videos before accepting the engine for the live viewer.

## Alert semantics

- A target-class observation enters the candidate alert immediately by default.
- The alert clears after 15 consecutive empty frames, avoiding display flicker.
- Raw boxes and overlap groups are both displayed.
- Overlap groups are ambiguity diagnostics, not unique-person counts.
- PBFT/model-hash qualification may gate vehicle movement, but it must not erase a
  retained positive person observation from the command-center record.

