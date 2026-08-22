# MESSAGE — SUYASH TO ABHIJAN

Date: 2026-08-22 IST

Subject: Final allowlist must bind the executed TensorRT engine

Read `codebase/docs/JETSON_ORIN_NANO_MODEL_GATE.md` from the central integration branch.

For the new rescue model, the final approved-model record must include:

- SHA-256 of the exact TensorRT `.engine` executed on Jetson;
- source `.pt` and ONNX hashes as lineage;
- class map, confidence threshold, static input shape and precision;
- JetPack/TensorRT/runtime-bundle identity;
- accuracy and Orin Nano benchmark report IDs.

Hashing only the training `.pt` is insufficient when inference executes a separately built
TensorRT engine. Keep the existing PyTorch model-hash demonstration if useful, but label it
as the legacy model-hash demonstration. Do not describe it as verification of the new
TensorRT rescue deployment until the engine identity is bound.

This is an interface decision, not a confirmation request. Record adoption in your next
`STATUS_ABHIJAN.md` checkpoint and continue all unblocked work.
