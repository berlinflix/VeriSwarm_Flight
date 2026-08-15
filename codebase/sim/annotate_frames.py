"""
Save YOLO-annotated copies of frames (bounding boxes + class labels drawn on)
for visual inspection. Lets you *see* what the detector saw.

Run:  python3 sim/annotate_frames.py [glob ...]   (default: the pinned sim frames)
Outputs to sim/annotated/.
"""

from __future__ import annotations

import glob
import pathlib
import sys

import cv2
from ultralytics import YOLO

patterns = sys.argv[1:] or ["sim/sim_frames/*.jpg"]
out = pathlib.Path("sim/annotated")
out.mkdir(parents=True, exist_ok=True)

model = YOLO("yolov8n.pt")
files = sorted({f for p in patterns for f in glob.glob(p)})
if not files:
    print(f"no files matched {patterns}")
    raise SystemExit(0)

for f in files:
    res = model(f, verbose=False)[0]
    annotated = res.plot()  # BGR image with boxes + labels
    name = "ann_" + pathlib.Path(f).name
    cv2.imwrite(str(out / name), annotated)
    labels = sorted({model.names[int(b.cls)] for b in res.boxes})
    print(f"wrote sim/annotated/{name}  ({len(res.boxes)} boxes: {labels})")

print("\nview them:  cd ~/veriswarm/sim/annotated && explorer.exe .")
