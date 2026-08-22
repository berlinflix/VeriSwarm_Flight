# MESSAGE — SUYASH TO TEAM: JETSON COCO INFRASTRUCTURE BASELINE COMPLETE

Date: 2026-08-23

The Owl Lite camera to Jetson Orin Nano CUDA path has completed its three physical
infrastructure cases using the existing COCO `yolov8n.pt`. This result qualifies the
camera/runtime/test harness only. It does not qualify Samik's rescue detector, survivor
condition, disaster hazards, field accuracy or TensorRT deployment.

## Executed identity

- Model: `yolov8n.pt`
- SHA-256:
  `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36`
- CUDA device reported by Torch: `Orin`
- Camera:
  `/dev/v4l/by-id/usb-Owl_Lite_Owl_Lite_Camera_SN0001-video-index0`
- Each final case: 10 warm-up frames followed by 120 measured frames

## Empty scene

- Result: PASS for this scene
- Target frames: 0/120
- Target detections: 0
- Mean inference: 29.6655 ms
- P95 inference: 31.6466 ms
- End-to-end: 25.0284 FPS

This is one physical negative control, not a dataset-level false-positive rate.

## Partially occluded person

- Presence result: PASS for this scene
- Target frames: 120/120
- Mean inference: 29.8765 ms
- P95 inference: 39.8756 ms
- End-to-end: 29.2522 FPS
- Raw person boxes: 171
- Frames with one person box: 69
- Frames with two strongly overlapping person boxes: 51

The visible person was correctly detected, but raw box count overcounted one physical
person. Therefore presence passed while unique-person counting remains unresolved. Raw
boxes must be retained, overlap ambiguity surfaced, and identity resolved using tracking
and multiview geometry. A survivor alert must not be vetoed because its count is
ambiguous.

## Full-body distance, final `r5`

The accepted frame visibly contains the complete head, body, both legs and both feet in
upright landscape orientation.

- Result: PASS for this scene
- Target frames: 120/120
- Raw target boxes: 120
- Overlap clusters: 120
- Ambiguous target frames: 0
- Maximum raw targets in one frame: 1
- Mean inference: 28.7001 ms
- P95 inference: 29.9104 ms
- End-to-end: 29.1877 FPS

Earlier framing attempts were setup evidence and are not the accepted full-body case.

## Orientation failure retained

A deliberate 90-degree sideways camera attempt showed a complete physical person but
produced `dog 0.32` instead of `person`. This is a real negative finding. The production
camera mount needs a known fixed orientation; portrait mounts require deterministic
pre-inference rotation. Aircraft roll needs gimbal/IMU rectification and measured
rotation augmentation. Raw sideways frames are not accepted as orientation-robust
inference.

## Required repeat with Samik's checkpoint

When Samik publishes the selected rescue checkpoint, repeat the same empty, occluded and
full-body-distance cases with the checkpoint's actual class mapping, image size and
confidence threshold. Then export the accepted checkpoint to ONNX and TensorRT FP16 on
the Jetson, compare outputs on identical frames, and run the sustained camera-to-event
benchmark. The current COCO results must never be presented as rescue-model evidence.
