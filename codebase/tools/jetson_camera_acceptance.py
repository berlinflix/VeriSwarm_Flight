"""Run repeatable real-camera acceptance cases for a rescue detector on Jetson.

The tool intentionally keeps the detector contract small: a model is accepted for a
case only from measured target-class detections.  It does not infer survivor state,
hazards, geolocation, or mission authorization from a bounding box.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


SCHEMA = "veriswarm.jetson.camera_acceptance.v1"
POSITIVE_CASES = frozenset({"occluded", "full-body-distance"})
ALL_CASES = ("empty", *sorted(POSITIVE_CASES))
DEFAULT_CAMERA = (
    "/dev/v4l/by-id/"
    "usb-Owl_Lite_Owl_Lite_Camera_SN0001-video-index0"
)


@dataclass(frozen=True)
class CaseDecision:
    passed: bool
    reason: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_targets(values: Iterable[str]) -> frozenset[str]:
    targets = frozenset(value.strip().casefold() for value in values if value.strip())
    if not targets:
        raise ValueError("at least one non-empty target class is required")
    return targets


def class_name(names: Mapping[int, str] | Sequence[str], class_id: int) -> str:
    if isinstance(names, Mapping):
        return str(names.get(class_id, class_id))
    if 0 <= class_id < len(names):
        return str(names[class_id])
    return str(class_id)


def decide_case(
    *, case: str, target_frame_count: int, frame_count: int, minimum_rate: float
) -> CaseDecision:
    if case not in ALL_CASES:
        raise ValueError(f"unsupported case: {case}")
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")
    if not 0.0 <= minimum_rate <= 1.0:
        raise ValueError("minimum_rate must be within [0, 1]")
    if not 0 <= target_frame_count <= frame_count:
        raise ValueError("target_frame_count must be within [0, frame_count]")

    rate = target_frame_count / frame_count
    if case == "empty":
        if target_frame_count == 0:
            return CaseDecision(True, "no target-class false alert")
        return CaseDecision(False, f"target class appeared in {target_frame_count} frames")
    if rate >= minimum_rate:
        return CaseDecision(True, f"target-frame rate {rate:.4f} met {minimum_rate:.4f}")
    return CaseDecision(False, f"target-frame rate {rate:.4f} below {minimum_rate:.4f}")


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be within [0, 1]")
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return float(ordered[index])


def _box_area(box: Sequence[float]) -> float:
    if len(box) != 4:
        raise ValueError("box must contain exactly four coordinates")
    return max(0.0, float(box[2]) - float(box[0])) * max(
        0.0, float(box[3]) - float(box[1])
    )


def box_overlap(box_a: Sequence[float], box_b: Sequence[float]) -> tuple[float, float]:
    """Return IoU and smaller-box containment without claiming object identity."""

    area_a = _box_area(box_a)
    area_b = _box_area(box_b)
    left = max(float(box_a[0]), float(box_b[0]))
    top = max(float(box_a[1]), float(box_b[1]))
    right = min(float(box_a[2]), float(box_b[2]))
    bottom = min(float(box_a[3]), float(box_b[3]))
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = area_a + area_b - intersection
    smaller = min(area_a, area_b)
    iou = intersection / union if union > 0.0 else 0.0
    containment = intersection / smaller if smaller > 0.0 else 0.0
    return iou, containment


def overlap_clusters(
    boxes: Sequence[Sequence[float]],
    *,
    iou_threshold: float = 0.60,
    containment_threshold: float = 0.85,
) -> list[tuple[int, ...]]:
    """Group strongly overlapping boxes while retaining every raw observation.

    A cluster is an ambiguity diagnostic, not a unique-person assertion. Two real people
    can overlap, so downstream tracking and multiview geometry must resolve identity.
    """

    if not 0.0 <= iou_threshold <= 1.0:
        raise ValueError("iou_threshold must be within [0, 1]")
    if not 0.0 <= containment_threshold <= 1.0:
        raise ValueError("containment_threshold must be within [0, 1]")

    parents = list(range(len(boxes)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(boxes)):
        for right in range(left + 1, len(boxes)):
            iou, containment = box_overlap(boxes[left], boxes[right])
            if iou >= iou_threshold or containment >= containment_threshold:
                union(left, right)

    grouped: dict[int, list[int]] = {}
    for index in range(len(boxes)):
        grouped.setdefault(find(index), []).append(index)
    return [tuple(indices) for indices in grouped.values()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=ALL_CASES)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--camera", default=DEFAULT_CAMERA)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--warmup-frames", type=int, default=10)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--minimum-positive-rate", type=float, default=0.80)
    parser.add_argument(
        "--target-class",
        action="append",
        default=[],
        help="Accepted target class; repeat as needed. Defaults to person variants.",
    )
    args = parser.parse_args()
    if args.frames <= 0 or args.warmup_frames < 0:
        parser.error("--frames must be positive and --warmup-frames non-negative")
    if not 0.0 <= args.confidence <= 1.0:
        parser.error("--confidence must be within [0, 1]")
    if not 0.0 <= args.minimum_positive_rate <= 1.0:
        parser.error("--minimum-positive-rate must be within [0, 1]")
    return args


def main() -> int:
    args = _parse_args()

    # Keep heavyweight deployment dependencies out of module import so contracts and
    # decision logic remain testable on development machines without CUDA.
    import cv2  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]
    from ultralytics import YOLO  # type: ignore[import-not-found]

    if not args.model.is_file():
        raise SystemExit(f"model not found: {args.model}")
    if not torch.cuda.is_available():
        raise SystemExit("CUDA unavailable; refusing CPU-only acceptance")

    targets = normalize_targets(
        args.target_class or ("person", "person_candidate", "survivor")
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = args.out_dir / f"{args.case}.frames.jsonl"
    summary_path = args.out_dir / f"{args.case}.summary.json"
    annotated_path = args.out_dir / f"{args.case}.annotated.jpg"

    model = YOLO(str(args.model))
    camera = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    if not camera.isOpened():
        raise SystemExit(f"camera open failed: {args.camera}")

    target_frame_count = 0
    target_detection_count = 0
    target_overlap_cluster_count = 0
    ambiguous_target_frame_count = 0
    maximum_raw_targets_per_frame = 0
    inference_ms: list[float] = []
    last_annotated = None
    first_target_annotated = None
    measured_started: float | None = None

    with trace_path.open("w", encoding="utf-8") as trace_stream:
        try:
            for frame_index in range(args.warmup_frames + args.frames):
                if frame_index == args.warmup_frames:
                    measured_started = time.perf_counter()
                ok, frame = camera.read()
                if not ok or frame is None:
                    raise RuntimeError(f"camera read failed at frame {frame_index}")

                inference_started = time.perf_counter()
                result = model.predict(
                    source=frame,
                    device=0,
                    imgsz=args.imgsz,
                    conf=args.confidence,
                    verbose=False,
                )[0]
                elapsed_ms = (time.perf_counter() - inference_started) * 1000.0
                last_annotated = result.plot()

                if frame_index < args.warmup_frames:
                    continue

                detections: list[dict[str, object]] = []
                target_count = 0
                if result.boxes is not None:
                    class_ids = result.boxes.cls.detach().cpu().tolist()
                    confidences = result.boxes.conf.detach().cpu().tolist()
                    coordinates = result.boxes.xyxy.detach().cpu().tolist()
                    for raw_id, confidence, xyxy in zip(
                        class_ids, confidences, coordinates, strict=True
                    ):
                        resolved_name = class_name(result.names, int(raw_id))
                        is_target = resolved_name.casefold() in targets
                        target_count += int(is_target)
                        detections.append(
                            {
                                "class_id": int(raw_id),
                                "class_name": resolved_name,
                                "confidence": float(confidence),
                                "xyxy": [float(value) for value in xyxy],
                                "is_target": is_target,
                            }
                        )

                target_indices = [
                    index
                    for index, detection in enumerate(detections)
                    if detection["is_target"]
                ]
                target_boxes = [
                    detections[index]["xyxy"] for index in target_indices
                ]
                clusters = overlap_clusters(target_boxes)  # type: ignore[arg-type]
                for cluster_index, members in enumerate(clusters):
                    for target_member in members:
                        detections[target_indices[target_member]][
                            "overlap_cluster"
                        ] = cluster_index

                target_frame_count += int(target_count > 0)
                target_detection_count += target_count
                target_overlap_cluster_count += len(clusters)
                ambiguous_target_frame_count += int(
                    any(len(cluster) > 1 for cluster in clusters)
                )
                maximum_raw_targets_per_frame = max(
                    maximum_raw_targets_per_frame, target_count
                )
                inference_ms.append(elapsed_ms)
                if target_count > 0 and first_target_annotated is None:
                    first_target_annotated = last_annotated.copy()
                trace_stream.write(
                    json.dumps(
                        {
                            "frame_index": frame_index - args.warmup_frames,
                            "captured_ns": time.time_ns(),
                            "inference_ms": elapsed_ms,
                            "detections": detections,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
        finally:
            camera.release()

    if measured_started is None:
        raise RuntimeError("measured interval did not start")
    duration_s = time.perf_counter() - measured_started
    evidence_frame = (
        first_target_annotated
        if first_target_annotated is not None
        else last_annotated
    )
    if evidence_frame is None or not cv2.imwrite(str(annotated_path), evidence_frame):
        raise RuntimeError(f"annotated-frame write failed: {annotated_path}")

    decision = decide_case(
        case=args.case,
        target_frame_count=target_frame_count,
        frame_count=args.frames,
        minimum_rate=args.minimum_positive_rate,
    )
    summary = {
        "schema": SCHEMA,
        "case": args.case,
        "pass": decision.passed,
        "reason": decision.reason,
        "model": str(args.model.resolve()),
        "model_sha256": sha256_file(args.model),
        "camera": args.camera,
        "cuda_device": torch.cuda.get_device_name(0),
        "target_classes": sorted(targets),
        "frame_count": args.frames,
        "warmup_frames": args.warmup_frames,
        "target_frame_count": target_frame_count,
        "target_frame_rate": target_frame_count / args.frames,
        "target_detection_count": target_detection_count,
        "target_overlap_cluster_count": target_overlap_cluster_count,
        "ambiguous_target_frame_count": ambiguous_target_frame_count,
        "maximum_raw_targets_per_frame": maximum_raw_targets_per_frame,
        "overlap_cluster_policy": {
            "iou_threshold": 0.60,
            "smaller_box_containment_threshold": 0.85,
            "meaning": "ambiguity diagnostic; not a unique-person count",
        },
        "minimum_positive_rate": args.minimum_positive_rate,
        "mean_inference_ms": statistics.fmean(inference_ms),
        "p95_inference_ms": percentile(inference_ms, 0.95),
        "end_to_end_fps": args.frames / duration_s,
        "trace": str(trace_path),
        "annotated_frame": str(annotated_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if decision.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
