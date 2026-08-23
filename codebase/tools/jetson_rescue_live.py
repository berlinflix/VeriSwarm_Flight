"""Run a model-agnostic live rescue-perception viewer on a Jetson.

The viewer deliberately labels detections as *person candidates*. A detector box alone
does not prove survivor status, unique identity, geolocation, or mission authorization.
The same runner accepts PyTorch ``.pt`` and target-built TensorRT ``.engine`` models so
the UI and evidence contract remain unchanged across deployment formats.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence


SCHEMA = "veriswarm.jetson.rescue_live.v1"
DEFAULT_CAMERA = (
    "/dev/v4l/by-id/"
    "usb-Owl_Lite_Owl_Lite_Camera_SN0001-video-index0"
)


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


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be within [0, 1]")
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return float(ordered[index])


def _box_area(box: Sequence[float]) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(
        0.0, float(box[3]) - float(box[1])
    )


def overlap_clusters(
    boxes: Sequence[Sequence[float]],
    *,
    iou_threshold: float = 0.60,
    containment_threshold: float = 0.85,
) -> list[tuple[int, ...]]:
    """Group strongly overlapping boxes as an ambiguity diagnostic.

    A cluster is not a unique-person assertion. Tracking and multiview geometry remain
    responsible for identity resolution.
    """

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
            box_left = boxes[left]
            box_right = boxes[right]
            area_left = _box_area(box_left)
            area_right = _box_area(box_right)
            x1 = max(float(box_left[0]), float(box_right[0]))
            y1 = max(float(box_left[1]), float(box_right[1]))
            x2 = min(float(box_left[2]), float(box_right[2]))
            y2 = min(float(box_left[3]), float(box_right[3]))
            intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            union_area = area_left + area_right - intersection
            smaller = min(area_left, area_right)
            iou = intersection / union_area if union_area > 0.0 else 0.0
            containment = intersection / smaller if smaller > 0.0 else 0.0
            if iou >= iou_threshold or containment >= containment_threshold:
                union(left, right)

    grouped: dict[int, list[int]] = {}
    for index in range(len(boxes)):
        grouped.setdefault(find(index), []).append(index)
    return [tuple(indices) for indices in grouped.values()]


@dataclass
class AlertLatch:
    """Latch a candidate promptly and clear only after a bounded empty interval."""

    enter_frames: int = 1
    exit_frames: int = 15
    active: bool = False
    consecutive_positive: int = 0
    consecutive_empty: int = 0

    def __post_init__(self) -> None:
        if self.enter_frames <= 0:
            raise ValueError("enter_frames must be positive")
        if self.exit_frames <= 0:
            raise ValueError("exit_frames must be positive")

    def update(self, observed: bool) -> tuple[bool, str | None]:
        transition: str | None = None
        if observed:
            self.consecutive_positive += 1
            self.consecutive_empty = 0
            if not self.active and self.consecutive_positive >= self.enter_frames:
                self.active = True
                transition = "ALERT_ENTER"
        else:
            self.consecutive_positive = 0
            self.consecutive_empty += 1
            if self.active and self.consecutive_empty >= self.exit_frames:
                self.active = False
                transition = "ALERT_CLEAR"
        return self.active, transition


@dataclass
class GeolocationVoteLatch:
    """Require repeated appearance hypotheses before exposing a fix candidate."""

    required_votes: int = 3
    recent: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.required_votes <= 0:
            raise ValueError("required_votes must be positive")

    def update(self, location: str | None, appearance_passed: bool) -> bool:
        if location is None or not appearance_passed:
            self.recent.clear()
            return False
        self.recent.append(location)
        self.recent = self.recent[-self.required_votes :]
        return (
            len(self.recent) == self.required_votes
            and len(set(self.recent)) == 1
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--camera", default=DEFAULT_CAMERA)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--camera-fps", type=float, default=30.0)
    parser.add_argument("--record-fps", type=float, default=25.0)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--device", default="0")
    parser.add_argument("--warmup-frames", type=int, default=10)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--alert-enter-frames", type=int, default=1)
    parser.add_argument("--alert-exit-frames", type=int, default=15)
    parser.add_argument("--no-display", action="store_true")
    parser.add_argument("--no-record", action="store_true")
    parser.add_argument("--geolocation-checkpoint", type=Path)
    parser.add_argument("--geolocation-expected-sha256")
    parser.add_argument("--geolocation-gallery-dir", type=Path)
    parser.add_argument("--geolocation-gallery-cache", type=Path)
    parser.add_argument("--geolocation-interval-frames", type=int, default=90)
    parser.add_argument("--geolocation-votes", type=int, default=3)
    parser.add_argument("--geolocation-top-k", type=int, default=5)
    parser.add_argument("--geolocation-minimum-similarity", type=float)
    parser.add_argument("--geolocation-minimum-margin", type=float)
    parser.add_argument(
        "--target-class",
        action="append",
        default=[],
        help="Target class to render; repeat as needed.",
    )
    args = parser.parse_args()
    if args.width <= 0 or args.height <= 0:
        parser.error("--width and --height must be positive")
    if args.camera_fps <= 0.0 or args.record_fps <= 0.0:
        parser.error("--camera-fps and --record-fps must be positive")
    if args.imgsz <= 0 or args.warmup_frames < 0 or args.max_frames < 0:
        parser.error("invalid image, warm-up, or maximum-frame count")
    if not 0.0 <= args.confidence <= 1.0:
        parser.error("--confidence must be within [0, 1]")
    geolocation_values = (
        args.geolocation_expected_sha256,
        args.geolocation_gallery_dir,
        args.geolocation_gallery_cache,
    )
    if args.geolocation_checkpoint is not None and not all(geolocation_values):
        parser.error(
            "geolocation mode requires checkpoint hash, gallery directory and cache"
        )
    if args.geolocation_checkpoint is None and any(geolocation_values):
        parser.error("--geolocation-checkpoint is required for geolocation mode")
    if args.geolocation_interval_frames <= 0 or args.geolocation_votes <= 0:
        parser.error("geolocation interval and vote count must be positive")
    if args.geolocation_top_k < 2:
        parser.error("--geolocation-top-k must be at least 2")
    if (args.geolocation_minimum_similarity is None) != (
        args.geolocation_minimum_margin is None
    ):
        parser.error("configure both geolocation thresholds or neither")
    return args


def _draw_text(cv2: object, frame: object, text: str, origin: tuple[int, int], color: tuple[int, int, int], scale: float = 0.62) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(frame, text, origin, font, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, text, origin, font, scale, color, 2, cv2.LINE_AA)


def _camera_source(value: str) -> int | str:
    return int(value) if value.isdecimal() else value


def _torch_device(value: str) -> str:
    """Translate Ultralytics' numeric CUDA selector to a torch device string."""

    return f"cuda:{value}" if value.isdecimal() else value


def main() -> int:
    args = _parse_args()

    # Heavy deployment imports remain inside main so unit tests do not require CUDA.
    import cv2  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]
    from ultralytics import YOLO  # type: ignore[import-not-found]

    model_path = args.model.resolve()
    output_dir = args.out_dir.resolve()
    if not model_path.is_file():
        raise SystemExit(f"model not found: {model_path}")
    if output_dir.exists():
        raise SystemExit(f"output directory already exists: {output_dir}")
    if not torch.cuda.is_available():
        raise SystemExit("CUDA unavailable; refusing CPU-only live demo")
    output_dir.mkdir(parents=True)

    targets = normalize_targets(
        args.target_class or ("person", "person_candidate", "survivor")
    )
    model_hash = sha256_file(model_path)
    model = YOLO(str(model_path))

    geolocation: dict[str, object] | None = None
    geolocation_latch = GeolocationVoteLatch(args.geolocation_votes)
    geolocation_latest: dict[str, object] | None = None
    geolocation_query_count = 0
    geolocation_candidate_count = 0
    if args.geolocation_checkpoint is not None:
        from PIL import Image  # type: ignore[import-not-found]

        try:
            from tools.university1652_retrieval import (
                _load_cache,
                acceptance_decision,
                build_encoder,
                encode_pil_images,
                gallery_images,
                gallery_manifest_sha256,
                normalized_sha256,
                rank_top_k,
            )
        except ModuleNotFoundError:
            from university1652_retrieval import (  # type: ignore[no-redef]
                _load_cache,
                acceptance_decision,
                build_encoder,
                encode_pil_images,
                gallery_images,
                gallery_manifest_sha256,
                normalized_sha256,
                rank_top_k,
            )

        geolocation_checkpoint = args.geolocation_checkpoint.resolve()
        geolocation_gallery = args.geolocation_gallery_dir.resolve()
        geolocation_cache = args.geolocation_gallery_cache.resolve()
        if not geolocation_checkpoint.is_file():
            raise SystemExit(
                f"geolocation checkpoint not found: {geolocation_checkpoint}"
            )
        if not geolocation_cache.is_file():
            raise SystemExit(
                "geolocation gallery cache must be built before live execution"
            )
        geolocation_hash = sha256_file(geolocation_checkpoint)
        expected_geolocation_hash = normalized_sha256(
            args.geolocation_expected_sha256,
            "geolocation_expected_sha256",
        )
        if geolocation_hash != expected_geolocation_hash:
            raise SystemExit(
                "geolocation checkpoint hash mismatch: "
                f"expected {expected_geolocation_hash}, got {geolocation_hash}"
            )
        geolocation_paths = gallery_images(geolocation_gallery)
        geolocation_manifest = gallery_manifest_sha256(
            geolocation_paths, geolocation_gallery
        )
        geolocation_device = _torch_device(args.device)
        geolocation_encoder = build_encoder(
            geolocation_checkpoint, geolocation_device
        )
        geo_features, geo_labels, geo_paths = _load_cache(
            geolocation_cache, geolocation_hash, geolocation_manifest
        )
        if args.geolocation_top_k > len(geo_labels):
            raise SystemExit("geolocation top-k exceeds gallery size")
        geolocation = {
            "checkpoint": str(geolocation_checkpoint),
            "checkpoint_sha256": geolocation_hash,
            "gallery": str(geolocation_gallery),
            "gallery_manifest_sha256": geolocation_manifest,
            "gallery_cache": str(geolocation_cache),
            "gallery_size": len(geo_labels),
            "encoder": geolocation_encoder,
            "features": geo_features,
            "labels": geo_labels,
            "paths": geo_paths,
            "Image": Image,
            "encode": encode_pil_images,
            "rank": rank_top_k,
            "decide": acceptance_decision,
        }
    source = _camera_source(args.camera)
    camera = cv2.VideoCapture(source, cv2.CAP_V4L2)
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    camera.set(cv2.CAP_PROP_FPS, args.camera_fps)
    if not camera.isOpened():
        raise SystemExit(f"camera open failed: {args.camera}")

    actual_width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
    reported_fps = float(camera.get(cv2.CAP_PROP_FPS))
    if (actual_width, actual_height) != (args.width, args.height):
        camera.release()
        raise SystemExit(
            f"camera mode mismatch: got {actual_width}x{actual_height}, "
            f"expected {args.width}x{args.height}"
        )

    trace_path = output_dir / "session.frames.jsonl"
    summary_path = output_dir / "session.summary.json"
    video_path = output_dir / "session.annotated.avi"
    writer = None
    if not args.no_record:
        writer = cv2.VideoWriter(
            str(video_path),
            cv2.VideoWriter_fourcc(*"MJPG"),
            args.record_fps,
            (actual_width, actual_height),
        )
        if not writer.isOpened():
            camera.release()
            raise SystemExit(f"annotated video open failed: {video_path}")

    window = "VERISWARM — RESCUE EDGE AI"
    if not args.no_display:
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window, min(actual_width, 1280), min(actual_height, 720))

    latch = AlertLatch(args.alert_enter_frames, args.alert_exit_frames)
    frame_count = 0
    target_frame_count = 0
    alert_entries = 0
    ambiguous_frames = 0
    inference_ms: list[float] = []
    wall_ms: list[float] = []
    display_fps: float | None = None
    previous_finished: float | None = None
    stop_reason = "operator_q"
    fatal_error: str | None = None
    release_verified = False
    started_ns = time.time_ns()

    try:
        ok, frame = camera.read()
        if not ok or frame is None:
            raise RuntimeError("initial camera read failed")
        for _ in range(args.warmup_frames):
            model.predict(
                source=frame,
                device=args.device,
                imgsz=args.imgsz,
                conf=args.confidence,
                verbose=False,
            )

        with trace_path.open("x", encoding="utf-8") as trace:
            while True:
                ok, frame = camera.read()
                if not ok or frame is None:
                    stop_reason = "camera_read_failure"
                    raise RuntimeError(f"camera read failed at frame {frame_count}")

                predicted_at = time.perf_counter()
                result = model.predict(
                    source=frame,
                    device=args.device,
                    imgsz=args.imgsz,
                    conf=args.confidence,
                    verbose=False,
                )[0]
                wall_elapsed_ms = (time.perf_counter() - predicted_at) * 1000.0
                core_inference_ms = float(result.speed.get("inference", math.nan))

                if (
                    geolocation is not None
                    and frame_count % args.geolocation_interval_frames == 0
                ):
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil_image = geolocation["Image"].fromarray(rgb)
                    try:
                        geo_feature = geolocation["encode"](
                            geolocation["encoder"], [pil_image], geolocation_device
                        )[0]
                    finally:
                        pil_image.close()
                    geo_hypotheses = geolocation["rank"](
                        geo_feature,
                        geolocation["features"],
                        geolocation["labels"],
                        geolocation["paths"],
                        args.geolocation_top_k,
                    )
                    appearance_passed, geo_reason, geo_margin = geolocation["decide"](
                        geo_hypotheses,
                        minimum_similarity=args.geolocation_minimum_similarity,
                        minimum_margin=args.geolocation_minimum_margin,
                    )
                    top_location = str(geo_hypotheses[0]["location_id"])
                    temporally_stable = geolocation_latch.update(
                        top_location, appearance_passed
                    )
                    geolocation_query_count += 1
                    geolocation_candidate_count += int(temporally_stable)
                    geolocation_latest = {
                        "sampled_frame": frame_count,
                        "top_k": geo_hypotheses,
                        "appearance_passed": appearance_passed,
                        "appearance_reason": geo_reason,
                        "appearance_margin": geo_margin,
                        "temporal_votes": list(geolocation_latch.recent),
                        "temporally_stable": temporally_stable,
                        "vio_confirmation_required": True,
                        "position_correction_authorized": False,
                    }

                detections: list[dict[str, object]] = []
                if result.boxes is not None:
                    class_ids = result.boxes.cls.detach().cpu().tolist()
                    confidences = result.boxes.conf.detach().cpu().tolist()
                    coordinates = result.boxes.xyxy.detach().cpu().tolist()
                    for raw_id, confidence, xyxy in zip(
                        class_ids, confidences, coordinates, strict=True
                    ):
                        resolved = class_name(result.names, int(raw_id))
                        if resolved.casefold() not in targets:
                            continue
                        detections.append(
                            {
                                "class_id": int(raw_id),
                                "class_name": resolved,
                                "confidence": float(confidence),
                                "xyxy": [float(value) for value in xyxy],
                            }
                        )

                clusters = overlap_clusters(
                    [detection["xyxy"] for detection in detections]  # type: ignore[misc]
                )
                ambiguous = any(len(cluster) > 1 for cluster in clusters)
                observed = bool(detections)
                alert_active, transition = latch.update(observed)
                if transition == "ALERT_ENTER":
                    alert_entries += 1
                target_frame_count += int(observed)
                ambiguous_frames += int(ambiguous)

                for cluster_index, cluster in enumerate(clusters):
                    for member in cluster:
                        detections[member]["overlap_cluster"] = cluster_index

                for detection in detections:
                    x1, y1, x2, y2 = (
                        int(round(value)) for value in detection["xyxy"]  # type: ignore[union-attr]
                    )
                    confidence = float(detection["confidence"])
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (20, 220, 255), 3)
                    _draw_text(
                        cv2,
                        frame,
                        f"PERSON CANDIDATE {confidence:.2f}",
                        (x1, max(22, y1 - 8)),
                        (20, 220, 255),
                        0.58,
                    )

                now = time.perf_counter()
                if previous_finished is not None and now > previous_finished:
                    instantaneous_fps = 1.0 / (now - previous_finished)
                    display_fps = (
                        instantaneous_fps
                        if display_fps is None
                        else 0.90 * display_fps + 0.10 * instantaneous_fps
                    )
                previous_finished = now
                if math.isfinite(core_inference_ms):
                    inference_ms.append(core_inference_ms)
                wall_ms.append(wall_elapsed_ms)

                overlay = frame.copy()
                cv2.rectangle(overlay, (0, 0), (actual_width, 82), (10, 18, 30), -1)
                cv2.addWeighted(overlay, 0.78, frame, 0.22, 0.0, frame)
                status = (
                    "PERSON CANDIDATE ALERT"
                    if alert_active
                    else "SEARCHING — NO PERSON CANDIDATE"
                )
                status_color = (20, 220, 255) if alert_active else (130, 255, 130)
                _draw_text(cv2, frame, "VERISWARM  |  ON-DEVICE RESCUE PERCEPTION", (18, 27), (255, 255, 255), 0.65)
                _draw_text(cv2, frame, status, (18, 59), status_color, 0.67)
                metrics = (
                    f"FPS {display_fps or 0.0:4.1f}  |  inference {core_inference_ms:5.1f} ms"
                    f"  |  raw boxes {len(detections)}  |  overlap groups {len(clusters)}"
                )
                _draw_text(cv2, frame, metrics, (max(18, actual_width - 740), 59), (230, 230, 230), 0.50)
                _draw_text(cv2, frame, f"model {model_hash[:16]}  |  {args.imgsz}px  |  conf {args.confidence:.2f}", (18, actual_height - 18), (230, 230, 230), 0.48)
                if geolocation_latest is not None:
                    geo_top = geolocation_latest["top_k"][0]
                    geo_text = (
                        f"VISUAL LOCATION {geo_top['location_id']} "
                        f"score {geo_top['cosine_similarity']:.3f} | "
                        "VIO CONFIRMATION REQUIRED"
                    )
                    _draw_text(
                        cv2,
                        frame,
                        geo_text,
                        (18, 84),
                        (255, 180, 40),
                        0.52,
                    )
                if ambiguous:
                    _draw_text(cv2, frame, "OVERLAPPING BOXES: IDENTITY NOT YET RESOLVED", (18, 108), (30, 170, 255), 0.60)

                trace.write(
                    json.dumps(
                        {
                            "schema": "veriswarm.jetson.rescue_live.frame.v1",
                            "frame_index": frame_count,
                            "captured_ns": time.time_ns(),
                            "observed": observed,
                            "alert_active": alert_active,
                            "transition": transition,
                            "raw_target_count": len(detections),
                            "overlap_group_count": len(clusters),
                            "ambiguous": ambiguous,
                            "detections": detections,
                            "core_inference_ms": core_inference_ms,
                            "wall_ms": wall_elapsed_ms,
                            "display_fps": display_fps,
                            "geolocation": geolocation_latest,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                trace.flush()

                if writer is not None:
                    writer.write(frame)
                frame_count += 1
                if not args.no_display:
                    cv2.imshow(window, frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        stop_reason = "operator_q"
                        break
                    if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                        stop_reason = "window_closed"
                        break

                if args.max_frames and frame_count >= args.max_frames:
                    stop_reason = "max_frames"
                    break
    except Exception as exc:  # Retain evidence and report the exact runtime failure.
        fatal_error = f"{type(exc).__name__}: {exc}"
    finally:
        camera.release()
        if writer is not None:
            writer.release()
        if not args.no_display:
            cv2.destroyAllWindows()

        time.sleep(0.25)
        probe = cv2.VideoCapture(source, cv2.CAP_V4L2)
        try:
            release_verified = bool(probe.isOpened() and probe.read()[0])
        finally:
            probe.release()

    summary = {
        "schema": SCHEMA,
        "session_complete": fatal_error is None,
        "stop_reason": stop_reason,
        "fatal_error": fatal_error,
        "started_ns": started_ns,
        "finished_ns": time.time_ns(),
        "model": str(model_path),
        "model_sha256": model_hash,
        "device": torch.cuda.get_device_name(0),
        "camera": args.camera,
        "requested_mode": {
            "width": args.width,
            "height": args.height,
            "fps": args.camera_fps,
            "fourcc": "MJPG",
        },
        "actual_mode": {
            "width": actual_width,
            "height": actual_height,
            "reported_fps": reported_fps,
        },
        "imgsz": args.imgsz,
        "confidence": args.confidence,
        "target_classes": sorted(targets),
        "frame_count": frame_count,
        "target_frame_count": target_frame_count,
        "target_frame_rate": target_frame_count / frame_count if frame_count else None,
        "alert_entries": alert_entries,
        "ambiguous_frame_count": ambiguous_frames,
        "mean_core_inference_ms": statistics.fmean(inference_ms) if inference_ms else None,
        "p95_core_inference_ms": percentile(inference_ms, 0.95) if inference_ms else None,
        "mean_wall_ms": statistics.fmean(wall_ms) if wall_ms else None,
        "p95_wall_ms": percentile(wall_ms, 0.95) if wall_ms else None,
        "release_verified": release_verified,
        "geolocation": (
            {
                "enabled": True,
                "checkpoint": geolocation["checkpoint"],
                "checkpoint_sha256": geolocation["checkpoint_sha256"],
                "gallery_manifest_sha256": geolocation[
                    "gallery_manifest_sha256"
                ],
                "gallery_size": geolocation["gallery_size"],
                "interval_frames": args.geolocation_interval_frames,
                "required_votes": args.geolocation_votes,
                "minimum_similarity": args.geolocation_minimum_similarity,
                "minimum_margin": args.geolocation_minimum_margin,
                "query_count": geolocation_query_count,
                "candidate_count": geolocation_candidate_count,
                "latest": geolocation_latest,
                "position_correction_authorized": False,
            }
            if geolocation is not None
            else {"enabled": False}
        ),
        "trace": str(trace_path),
        "annotated_video": str(video_path) if writer is not None else None,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if fatal_error is not None or not release_verified:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
