# MESSAGE — SUYASH TO SAMIK

Date: 2026-08-23

Subject: Publish the selected rescue checkpoint for the completed Jetson dual-model runtime

The shared-camera Jetson runtime is implemented on `codex/jetson-rescue-live` at
`f4990585a1bae93d30a9f95e2b87e3e9cbfd280a`. It continuously runs the rescue detector
and periodically runs the University-1652 visual-relocalization sidecar from the same
camera frames. No reply or authorization is required; continue training independently.

When the first selected rescue candidate is available, publish one status commit containing:

- candidate ID and training-run ID;
- immutable download location for `best.pt` outside Git;
- `best.pt` byte size and SHA-256;
- exact class map (required output: `person_candidate`);
- input size, confidence threshold and Ultralytics version;
- validation precision, recall, mAP50 and small-person recall;
- exact ONNX export command/status, if completed; and
- whether the artifact is a temporary smoke candidate or final selection.

Do not commit model weights or datasets. Do not wait for Suyash to approve intermediate
training work. The Jetson owner will copy the published artifact, verify it once, run the
camera acceptance cases, build TensorRT FP16 on the target Nano, and execute the same
dual-model application with either the `.pt` or target-built `.engine`.

Until that artifact exists, the existing COCO `yolov8n.pt` remains a runtime plumbing
baseline only and must not be described as the trained disaster-survivor detector.
