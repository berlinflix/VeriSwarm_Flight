# Controlled Occlusion 01 - Print and Placement Card

## Scope

This artifact is a controlled camera-input perturbation. It is not an optimized
adversarial patch, and no detector-evasion claim is valid until the retained
measurements support it.

## Print

1. Print `controlled_occlusion_v1.png` as a 180 mm square on matte A4 paper.
2. Disable fit-to-page, sharpening, colour enhancement and borderless scaling.
3. Print two identical copies and record the printer, driver and print settings.
4. Keep `clean_control.svg` on an identical backing sheet.
5. Hash the exact files transferred to Abhijan before printing.

## Placement

1. Lock both cameras, lighting and target position before the clean capture.
2. Frame the supported target at least 0.7 of image height and verify clean
   confidence of at least 0.25 on both cameras using the pinned detector.
3. Apply the artifact to Camera A only. Do not touch Camera B, either camera
   mount, any cable, or detector settings.
4. Record distance, yaw, pitch and placement marks. Do not tune the placement
   after seeing a desired verdict inside a recorded cycle.
5. Remove the artifact and prove recovery with the unchanged setup.

## Three-cycle gate

Run clean -> artifact -> recovery three times. Preserve raw frames, timestamps,
detector output and application logs. Freeze this artifact only if the clean
control is stable and the measured effect repeats in all three cycles. If not,
classify it as an unreliable trial and use an explicitly labelled controlled
occlusion instead.

## CoSys scene use

Use a separate attack-mode world/build. Import the PNG as an opaque texture on
an actual plane or mesh with an unlit material. Record object size, parent,
transform, camera calibration, material hash and synchronized clean/attacked
frame IDs. Never composite the image into returned camera pixels and never
modify the frozen Q-B qualification build.
