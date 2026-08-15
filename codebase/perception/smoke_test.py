"""
Smoke test: validate the perception -> action bridge with a real YOLOv8 model.

Run (after `pip install ultralytics`):
    python3 -m perception.smoke_test [image_path]

With no path it downloads a sample image. It prints the action for the clean
frame, then places an occlusion patch over the largest detected object and shows
that the detector now misses it and the action diverges past the semantic
threshold (the adversarial-perception attack, made real). It also prints the
model_hash provenance value used by the model-swap check.
"""

from __future__ import annotations

import sys

from perception.yolo_action import (
    apply_patch,
    detections_to_action,
    frame_to_detections,
    model_hash,
)


def main() -> None:
    import cv2  # provided by the ultralytics install
    import numpy as np
    from ultralytics import YOLO

    if len(sys.argv) > 1:
        img_path = sys.argv[1]
    else:
        from ultralytics.utils import ASSETS
        img_path = str(ASSETS / "bus.jpg")
        print(f"using bundled sample image -> {img_path}")

    weights = "yolov8n.pt"
    model = YOLO(weights)  # auto-downloads on first run
    frame = cv2.imread(img_path)
    if frame is None:
        print(f"could not read {img_path}")
        return

    dets = frame_to_detections(frame, model)
    action_clean = detections_to_action(dets)
    print(f"clean   : {len(dets)} detections -> action "
          f"{tuple(round(v, 3) for v in action_clean)}")

    if dets:
        h, w = frame.shape[:2]
        big = max(dets, key=lambda d: d.w * d.h)
        pw, ph = int(big.w * w * 1.1) or 8, int(big.h * h * 1.1) or 8
        c0 = max(0, int(big.x * w) - pw // 2)
        r0 = max(0, int(big.y * h) - ph // 2)
        patch = np.full((ph, pw, 3), 127, dtype=frame.dtype)  # gray occlusion
        patched = apply_patch(frame, patch, (r0, c0))
        dets_p = frame_to_detections(patched, model)
        action_patch = detections_to_action(dets_p)
        l2 = sum((a - b) ** 2 for a, b in zip(action_clean, action_patch)) ** 0.5
        print(f"patched : {len(dets_p)} detections -> action "
              f"{tuple(round(v, 3) for v in action_patch)}")
        print(f"action L2 divergence: {l2:.3f}  "
              f"-> caught at theta=0.5: {l2 > 0.5}")

    print(f"model_hash({weights}) = {model_hash(weights)}")


if __name__ == "__main__":
    main()
