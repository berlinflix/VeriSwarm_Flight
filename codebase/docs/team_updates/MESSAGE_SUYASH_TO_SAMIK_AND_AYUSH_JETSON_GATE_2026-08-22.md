# MESSAGE — SUYASH TO SAMIK AND AYUSH

Date: 2026-08-22 IST

Subject: Orin Nano is the mandatory rescue-model deployment floor

This is not an authorization gate. Continue cloud setup, dataset audit and training now.
Read and adopt:

```text
codebase/docs/JETSON_ORIN_NANO_MODEL_GATE.md
codebase/examples/rescue_model_deployment_manifest.example.json
```

## Required stance

The final selected RGB person model is the most accurate candidate that passes the full
pipeline on Jetson Orin Nano 8GB at 15 W. We are not optimizing merely for maximum FPS.

Current state is **NOT QUALIFIED**: the latest status has no training run, model metric,
TensorRT engine or Nano benchmark.

## Samik

1. Keep the planned YOLOv8n cloud smoke run; do not wait for Suyash.
2. Produce `v8n@640` and `v8n@960`; compare `v8s@640` only after those complete.
3. Select by held-out survivor accuracy subject to the Nano gate, not by speed alone.
4. Export static batch-1 ONNX, then build TensorRT FP16 on the actual Nano.
5. Run `.pt` versus engine accuracy equivalence and the 15-minute full-pipeline benchmark.
6. Treat INT8 as optional; accept it only after representative calibration and recall
   retention.
7. Publish the populated deployment manifest, accuracy summary, latency percentiles,
   memory/thermal report and exact commands. Do not commit the engine or raw datasets.
8. Use the final executed `.engine` hash in the approved-model handoff, not only the
   source `.pt` hash.

## Ayush

Act as independent deployment backup:

1. review Samik's export and benchmark commands;
2. verify input shape, class map and confidence threshold are identical across `.pt`,
   ONNX and engine;
3. independently check the accuracy deltas and benchmark report;
4. help with TensorRT/JetPack compatibility, but do not install a generic desktop Torch
   build on Jetson;
5. record findings in `STATUS_AYUSH.md` on your own branch.

## Claim separation

- The one-drone Nano gate covers one RGB stream and the complete event pipeline.
- The five-camera tripod display remains a separate ground demonstration unless all five
  streams are actually benchmarked on Nano.
- This model proves only RGB `person_candidate`. It does not prove thermal or disaster
  hazard detection.
- Larger Jetsons require their own rebuilt TensorRT engine and smoke test even after the
  Nano baseline passes.

No per-step confirmation or hash approval is required. Hash and freeze only the final
selected deployment artifacts.
