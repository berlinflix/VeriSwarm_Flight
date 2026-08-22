# MESSAGE — SUYASH TO SAMIK: `@960` AND TENSORRT CORRECTION

Date: 2026-08-23

Your decision not to reject `YOLOv8n@960` from a PyTorch estimate is correct. The final
gate requires a static batch-one FP16 TensorRT engine built and executed on the actual
Jetson Orin Nano. TensorRT speedup must be measured rather than assumed.

One numerical correction is required: `155.0038 ms` was the mean from the short
30-frame cold-start infrastructure probe. It included model start-up/warm-up effects and
must not be used as the steady-state 640 latency. The warmed 120-frame PyTorch/CUDA
physical cases measured:

- empty: mean `29.6655 ms`, p95 `31.6466 ms`, end-to-end `25.0284 FPS`;
- occluded: mean `29.8765 ms`, p95 `39.8756 ms`, end-to-end `29.2522 FPS`;
- accepted full-body distance: mean `28.7001 ms`, p95 `29.9104 ms`, end-to-end
  `29.1877 FPS`.

Therefore, neither the approximately `350 ms` PyTorch estimate for `@960` nor a claimed
`2–4x` TensorRT improvement is accepted as evidence. Both are hypotheses. Measure the
selected rescue checkpoint and the executed engine directly.

There is also a resolution constraint: the current Owl acceptance runner captures
`640x480`. Setting inference `imgsz=960` on those frames only upsamples existing pixels
and does not prove better small-person detection. The `@960` candidate must be evaluated
for accuracy on native-resolution aerial/disaster frames with sufficient source detail,
and its Jetson timing test must identify the actual capture/decode resolution.

For each selected candidate:

1. Evaluate the PyTorch checkpoint on the frozen held-out corpus.
2. Export static batch-one ONNX from that exact checkpoint.
3. Build FP16 TensorRT on the target Nano/JetPack runtime.
4. Compare PyTorch, ONNX and TensorRT outputs on identical frames and enforce the frozen
   accuracy-retention limits.
5. Benchmark the exact engine after warm-up with native-resolution inputs for 15 minutes.
6. Report mean, p95 and p99 camera-to-event latency, processed FPS, input/drop counts,
   memory, power mode, clocks and thermal/throttling observations.
7. Bind the executed `.engine` hash and runtime lineage in deployment evidence.

Keep `YOLOv8n@640` and `YOLOv8n@960` as measured candidates. Select from held-out rescue
accuracy plus the sustained Nano gate; do not select from pixel-count scaling or an
assumed TensorRT multiplier.
