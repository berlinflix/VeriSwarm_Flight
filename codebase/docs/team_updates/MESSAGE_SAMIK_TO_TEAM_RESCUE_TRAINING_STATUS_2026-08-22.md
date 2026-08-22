# MESSAGE — SAMIK TO TEAM

Date: 2026-08-22 18:59 IST

To: Suyash, Pratik, Abhijan and Ayush

Subject: RTX 5090 rescue-training environment is live; zero epochs run

Samik's Secure Cloud RTX 5090 Pod is now running in `EU-RO-1` with the 100 GB
persistent network volume `e6vat4wz37` mounted at `/workspace`. The actual container
reports an RTX 5090 with 32,607 MiB, CUDA 13.0 and working CUDA Torch. The pinned
training environment is being created under `/workspace`; no dataset has been copied
to the Pod and no training epoch has run yet.

The unchanged C2A v2 archive remains at `E:\dataset\C2A_Dataset.zip` and is registered
outside Git as `4,903,081,990` bytes with SHA-256
`cc21b41d7fcd555134117f95f52eab7fd39a96cd694e8edc8728c540e9eae653`.
The Kaggle safe/walking and VisDrone archives are not downloaded yet.

The frozen cloud intake order is: copy the source ZIP to persistent `/workspace`;
verify expected size, SHA-256 and ZIP integrity; safely extract; automatically check
image/label pairing, corrupt files, annotation validity, duplicate content and split
leakage; build a derived YOLO tree and YAML; then train from the YAML. Source ZIPs are
immutable evidence. Human review has been removed by project direction, so no human
acceptance claim will be made; qualification is automated, full-dataset, hash-bound and
fail closed.

The broad Codex-generated pipeline regression currently stands at `225 passed, 3
dependency-gated skips`. This is implementation evidence only. Current model artifacts,
metrics, `.pt`, ONNX, TensorRT engine and Jetson qualification are all **NONE**.

There is no shared-interface change: the detector still emits only
`person_candidate`; green/red safe/distress state stays separately identified and
cannot veto survivor-first evidence; `veriswarm.rescue.event.v1`, five-camera evidence,
security evidence and the frozen network are untouched. RunPod is training/validation
only. Final production remains TensorRT FP16 built and benchmarked on the actual Jetson
Orin Nano 8 GB at 15 W, with the executed engine hash and a 15-minute full-pipeline run.

No reply is needed unless this conflicts with a shared interface. The next status will
publish immutable archive intake receipts and the exact detached five-epoch
`YOLOv8n@640` smoke command/log path after those automated gates pass.
