"""Run the YOLO -> action perception path on captured sim frames (Stage 2)."""

from __future__ import annotations

import glob
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import cv2
from ultralytics import YOLO

from perception.yolo_action import detections_to_action, frame_to_detections

pattern = sys.argv[1] if len(sys.argv) > 1 else "/tmp/vsframes/*.jpg"
frames = sorted(glob.glob(pattern))
print(f"found {len(frames)} frames matching {pattern}")
if not frames:
    raise SystemExit(0)

model = YOLO("yolov8n.pt")
for f in frames[-4:]:
    fr = cv2.imread(f)
    if fr is None:
        print(f"{f}: unreadable")
        continue
    dets = frame_to_detections(fr, model)
    action = detections_to_action(dets)
    classes = sorted({int(d.cls) for d in dets})
    print(f"{f}  shape={fr.shape}  detections={len(dets)} classes={classes}  "
          f"action={tuple(round(v, 3) for v in action)}")
