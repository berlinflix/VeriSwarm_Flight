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

                target_frame_count += int(target_count > 0)
                target_detection_count += target_count
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
