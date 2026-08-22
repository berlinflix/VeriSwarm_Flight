# MESSAGE — SUYASH TO TEAM: JETSON RESCUE RUNTIME READY

Date: 2026-08-23

To: Samik, Abhijan, Pratik and Ayush

The physical Jetson Orin Nano and Owl Lite camera path is ready for Samik's selected
rescue detector. This is an infrastructure handoff, not rescue-model qualification.

## Measured result

- JetPack 6.2.1 / L4T 36.4.7 / 15 W mode
- isolated environment: `~/.venv-rescue-jp621`
- NumPy 1.26.4, OpenCV 4.11.0
- Torch 2.8.0, Torchvision 0.23.0, Ultralytics 8.4.56
- CUDA available: true; reported device: `Orin`
- Owl camera opened and returned 640 by 480 frames through its stable by-id path
- verified COCO baseline hash:
  `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36`
- 30 measured camera frames completed with a visually correct `person 0.82` box
- mean inference time: 155.0038 ms
- observed end-to-end rate: 5.5031 FPS

The short baseline includes start-up effects and is not the final latency benchmark.
It proves camera-to-CUDA-to-detector execution only. COCO does not satisfy the rescue
problem statement.

## Samik handoff

When model selection completes, commit a team update containing:

- selected checkpoint filename and one SHA-256;
- exact class-name mapping;
- expected image size and confidence threshold;
- detector validation metrics and evaluation-set identity;
- producing training commit and artifact retrieval location.

No separate Suyash authorization is needed. After the update is visible, the checkpoint
will be copied to the Jetson and run through the empty, occluded and distant/full-body
physical cases in `codebase/docs/JETSON_RESCUE_CAMERA_ACCEPTANCE.md`.

Abhijan and Pratik should continue their direct dashboard/simulation integration; this
message does not change their event or Ethernet contracts.
