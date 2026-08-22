# MESSAGE — SUYASH TO SAMIK: OCCLUSION DUPLICATE-BOX FINDING

Date: 2026-08-23

The physical Jetson COCO-baseline occlusion case detected a visible person in all 120
measured frames at 29.25 end-to-end FPS. Presence recall therefore passed for this one
scene. It also exposed a counting defect that the rescue runtime must address.

- 69 frames contained one `person` box.
- 51 frames contained two `person` boxes.
- Total raw target boxes were 171 for one physical person.
- Example confidences were approximately 0.33 and 0.32.
- Example nested boxes had IoU above 0.60 and smaller-box containment above 0.98.
- The saved frame also contained a non-target COCO `refrigerator` false positive on the
  occluding doorway. It did not affect the person-presence decision.

Do not use raw detector-box count as victim count. Preserve raw candidates, group strong
same-frame overlaps as ambiguous clusters, then use temporal association and multiview
geometry to resolve identity. Never discard the survivor-presence alert merely because
its count is ambiguous. If identity cannot be resolved, report a survivor candidate and
a conservative lower-bound count.

Suyash's Jetson acceptance runner now records raw target count, overlap-cluster count,
ambiguous-frame count and cluster membership. This is evaluation evidence; integrate an
equivalent raw-versus-associated distinction into the selected rescue runtime before the
dashboard presents a victim count.
