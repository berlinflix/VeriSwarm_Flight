"""Convert measured multi-camera detections into durable rescue observations.

This adapter deliberately does not promote 2-D appearance or homography overlap to
3-D localization.  It emits image-space ``person_candidate``/hazard observations
only, through a perception-owned source and a producer-specific SQLite outbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from rescue.outbox import RescueOutbox
from rescue.schema import HAZARD_CLASSES, RESCUE_SCHEMA, validate_rescue_event


CLASS_MAP = {
    "person": "person_candidate",
    "fire": "fire",
    "smoke": "smoke",
    "debris": "debris",
    "landslide": "landslide",
}


class MultiCameraAdapterError(ValueError):
    """The multi-camera observation cannot be published safely."""


def _pairs(values: Sequence[Sequence[str]], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in values:
        if len(row) != 2 or not all(isinstance(item, str) and item for item in row):
            raise MultiCameraAdapterError(f"{label} requires CAMERA VALUE pairs")
        camera, value = row
        if camera in result:
            raise MultiCameraAdapterError(f"duplicate {label} camera: {camera}")
        result[camera] = value
    return result


def _bbox(detection: Mapping[str, Any]) -> list[float]:
    try:
        x = float(detection["x"])
        y = float(detection["y"])
        width = float(detection["w"])
        height = float(detection["h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MultiCameraAdapterError("detection requires numeric normalized x/y/w/h") from exc
    x1 = max(0.0, min(1.0, x - width / 2.0))
    y1 = max(0.0, min(1.0, y - height / 2.0))
    x2 = max(0.0, min(1.0, x + width / 2.0))
    y2 = max(0.0, min(1.0, y + height / 2.0))
    if x2 <= x1 or y2 <= y1:
        raise MultiCameraAdapterError("detection produces an empty normalized box")
    return [x1, y1, x2, y2]


def _iou(left: Sequence[float], right: Sequence[float]) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


@dataclass
class _Track:
    observation_id: str
    bbox: list[float]
    last_seen_ms: int


class MultiCameraObservationAdapter:
    """Stateful, throttled and durable multi-camera observation producer."""

    def __init__(
        self,
        *,
        mission_id: str,
        outbox_path: str | Path,
        camera_nodes: Mapping[str, str],
        camera_calibrations: Mapping[str, str] | None,
        model_id: str,
        publish_hz: float = 2.0,
        track_ttl_ms: int = 5_000,
        track_iou: float = 0.55,
    ):
        if not camera_nodes:
            raise MultiCameraAdapterError("at least one camera-node mapping is required")
        if not 0 < publish_hz <= 3:
            raise MultiCameraAdapterError("publish_hz must be inside (0, 3]")
        if track_ttl_ms < 1 or not 0 < track_iou <= 1:
            raise MultiCameraAdapterError("track bounds are invalid")
        self.mission_id = mission_id
        self.path = Path(outbox_path)
        self.camera_nodes = dict(camera_nodes)
        self.camera_calibrations = dict(camera_calibrations or {})
        unknown_calibrations = set(self.camera_calibrations) - set(self.camera_nodes)
        if unknown_calibrations:
            raise MultiCameraAdapterError("camera calibration has no camera-node mapping")
        self.model_id = model_id
        self.publish_interval_ms = max(1, round(1_000 / publish_hz))
        self.track_ttl_ms = track_ttl_ms
        self.track_iou = track_iou
        self.last_publish_ms: int | None = None
        self.tracks: dict[tuple[str, str], list[_Track]] = {}
        self.outbox = RescueOutbox(mission_id, self.path)
        self._initialize_sequence_store()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize_sequence_store(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS covis_multicam_source_sequence (
                    source TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL CHECK(last_sequence >= 0)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS covis_multicam_event_identity (
                    event_id TEXT PRIMARY KEY,
                    canonical_json TEXT NOT NULL
                )
                """
            )

    def _existing_event(self, event_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT canonical_json FROM covis_multicam_event_identity WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def _remember_event(self, event: Mapping[str, Any]) -> None:
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO covis_multicam_event_identity(event_id, canonical_json) VALUES (?, ?)",
                (event["event_id"], canonical),
            )

    def _next_sequence(self, source: str) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT last_sequence FROM covis_multicam_source_sequence WHERE source = ?",
                (source,),
            ).fetchone()
            sequence = (int(row[0]) if row else 0) + 1
            connection.execute(
                """
                INSERT INTO covis_multicam_source_sequence(source, last_sequence)
                VALUES (?, ?)
                ON CONFLICT(source) DO UPDATE SET last_sequence=excluded.last_sequence
                """,
                (source, sequence),
            )
            connection.commit()
            return sequence

    def _match_or_create_track(
        self,
        camera: str,
        class_id: str,
        bbox: list[float],
        now_ms: int,
        seed: str,
    ) -> tuple[_Track, bool]:
        key = (camera, class_id)
        active = [track for track in self.tracks.get(key, []) if now_ms - track.last_seen_ms <= self.track_ttl_ms]
        best = max(active, key=lambda track: _iou(track.bbox, bbox), default=None)
        if best is not None and _iou(best.bbox, bbox) >= self.track_iou:
            best.bbox = bbox
            best.last_seen_ms = now_ms
            self.tracks[key] = active
            return best, False
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
        track = _Track(f"{camera}-track-{digest}", bbox, now_ms)
        active.append(track)
        self.tracks[key] = active
        return track, True

    def convert(self, record: Mapping[str, Any], *, now_ms: int | None = None) -> list[dict[str, Any]]:
        if record.get("schema") not in {
            "veriswarm.covis_multicam.v1",
            "veriswarm.covis_multicam.v2",
            "veriswarm.covis_multicam.v3",
        }:
            raise MultiCameraAdapterError("unsupported multi-camera record schema")
        cameras = record.get("cameras")
        if not isinstance(cameras, Mapping):
            raise MultiCameraAdapterError("multi-camera record requires cameras")
        timestamp_ms = int(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
        if self.last_publish_ms is not None and timestamp_ms - self.last_publish_ms < self.publish_interval_ms:
            return []
        self.last_publish_ms = timestamp_ms
        run_id = str(record.get("run_id") or "multicam")
        model_hash = record.get("model_sha256")
        output: list[dict[str, Any]] = []
        for camera, node in self.camera_nodes.items():
            value = cameras.get(camera)
            if not isinstance(value, Mapping):
                continue
            detections = value.get("detections", [])
            class_names_raw = value.get("class_names", [])
            class_names = {
                int(row[0]): str(row[1]).casefold()
                for row in class_names_raw
                if isinstance(row, (list, tuple)) and len(row) == 2
            } if isinstance(class_names_raw, list) else {}
            if not isinstance(detections, list):
                continue
            frame_sequence = value.get("frame_sequence")
            frame_id = f"{camera}-frame-{frame_sequence}"
            received_ns = value.get("received_wall_ns")
            observed_at_ms = int(received_ns) // 1_000_000 if isinstance(received_ns, int) and received_ns > 0 else timestamp_ms
            for index, detection in enumerate(detections):
                if not isinstance(detection, Mapping):
                    continue
                raw_class = class_names.get(detection.get("cls"), "person" if detection.get("cls") == 0 else "")
                rescue_class = CLASS_MAP.get(raw_class)
                if rescue_class is None or rescue_class not in {"person_candidate", *HAZARD_CLASSES}:
                    continue
                bbox = _bbox(detection)
                seed = f"{run_id}:{camera}:{frame_sequence}:{index}:{rescue_class}"
                track, created = self._match_or_create_track(camera, rescue_class, bbox, timestamp_ms, seed)
                if not created:
                    continue
                source = f"{node}.perception"
                event_id = "mc-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]
                existing = self._existing_event(event_id)
                if existing is not None:
                    output.append(existing)
                    continue
                sequence = self._next_sequence(source)
                payload: dict[str, Any] = {
                    "node": node,
                    "observation_id": track.observation_id,
                    "class_id": rescue_class,
                    "confidence": float(detection.get("conf", 0.0)),
                    "frame_id": frame_id,
                    "modality": "rgb",
                    "model_id": self.model_id,
                    "model_sha256": model_hash,
                    "bbox_norm": bbox,
                    "evidence_security": "UNVERIFIED",
                    "security_reasons": ["image_space_multicamera_candidate"],
                }
                calibration = self.camera_calibrations.get(camera)
                if calibration:
                    payload["camera_id"] = camera
                    payload["camera_calibration_id"] = calibration
                event = validate_rescue_event({
                    "schema": RESCUE_SCHEMA,
                    "mission_id": self.mission_id,
                    "event_id": event_id,
                    "source": source,
                    "source_seq": sequence,
                    "observed_at_ms": observed_at_ms,
                    "kind": "observation",
                    "payload": payload,
                }, expected_mission_id=self.mission_id)
                self._remember_event(event)
                output.append(event)
        return output

    def enqueue(self, record: Mapping[str, Any], *, now_ms: int | None = None) -> list[dict[str, Any]]:
        events = self.convert(record, now_ms=now_ms)
        for event in events:
            self.outbox.enqueue(event)
        return events

    def flush(self, endpoint: str, *, token: str = "") -> dict[str, Any]:
        return self.outbox.flush(endpoint, token=token, timeout_s=1.5, max_events=25, transient_retries=0).to_dict()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="existing veriswarm.covis_multicam JSONL")
    parser.add_argument("--mission-id", required=True)
    parser.add_argument("--outbox", type=Path, required=True)
    parser.add_argument("--camera-node", action="append", nargs=2, metavar=("CAMERA", "NODE"), required=True)
    parser.add_argument("--camera-calibration", action="append", nargs=2, metavar=("CAMERA", "CALIBRATION"), default=[])
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--publish-hz", type=float, default=2.0)
    parser.add_argument("--endpoint")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        adapter = MultiCameraObservationAdapter(
            mission_id=args.mission_id,
            outbox_path=args.outbox,
            camera_nodes=_pairs(args.camera_node, "camera-node"),
            camera_calibrations=_pairs(args.camera_calibration, "camera-calibration"),
            model_id=args.model_id,
            publish_hz=args.publish_hz,
        )
        converted = 0
        with args.input.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    converted += len(adapter.enqueue(json.loads(line)))
                except (json.JSONDecodeError, MultiCameraAdapterError, ValueError) as exc:
                    raise MultiCameraAdapterError(f"line {line_number}: {exc}") from exc
        result = adapter.flush(args.endpoint, token=os.environ.get("VERISWARM_RESCUE_TOKEN", "")) if args.endpoint else adapter.outbox.status()
        print(json.dumps({"ok": True, "converted": converted, "outbox": result}, sort_keys=True))
        return 0
    except (OSError, MultiCameraAdapterError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
