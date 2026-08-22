"""Read-only HTTP bridge for the live multi-camera operator composite.

The camera process owns this service.  It keeps the latest annotated JPEG and a
small status document in memory; it never accepts commands and never shares the
rescue collector token with a browser.  Abhijan's Vite server proxies these
endpoints to the dashboard.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Sequence


DASHBOARD_SCHEMA = "veriswarm.covis_multicam.dashboard.v1"
MJPEG_BOUNDARY = "veriswarm-multicam-frame"
MAX_EXPECTED_CAMERAS = 5


class MultiCameraBridgeError(ValueError):
    """The dashboard bridge received unsafe or malformed state."""


def _camera_names(values: Sequence[str]) -> tuple[str, ...]:
    names = tuple(values)
    if not 2 <= len(names) <= MAX_EXPECTED_CAMERAS:
        raise MultiCameraBridgeError("expected camera count must be inside 2..5")
    if len(set(names)) != len(names) or any(not isinstance(name, str) or not name for name in names):
        raise MultiCameraBridgeError("expected camera names must be unique non-empty strings")
    return names


def dashboard_status_from_event(
    event: Mapping[str, Any],
    expected_camera_names: Sequence[str],
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Build bounded dashboard status from one measured multi-camera event."""
    names = _camera_names(expected_camera_names)
    if event.get("schema") not in {
        "veriswarm.covis_multicam.v1",
        "veriswarm.covis_multicam.v2",
        "veriswarm.covis_multicam.v3",
    }:
        raise MultiCameraBridgeError("unsupported multi-camera event schema")
    cameras = event.get("cameras")
    pairs = event.get("pairs")
    if not isinstance(cameras, Mapping) or not isinstance(pairs, list):
        raise MultiCameraBridgeError("multi-camera event requires camera and pair records")

    camera_rows: list[dict[str, Any]] = []
    person_candidates = 0
    newest_received_ms: int | None = None
    for name in names:
        camera = cameras.get(name)
        connected = isinstance(camera, Mapping) and isinstance(camera.get("frame_sequence"), int)
        detections = camera.get("detections", []) if isinstance(camera, Mapping) else []
        class_names_raw = camera.get("class_names", []) if isinstance(camera, Mapping) else []
        class_names: dict[int, str] = {}
        if isinstance(class_names_raw, list):
            for row in class_names_raw:
                if isinstance(row, (list, tuple)) and len(row) == 2 and isinstance(row[0], int):
                    class_names[row[0]] = str(row[1]).casefold()
        detection_count = len(detections) if isinstance(detections, list) else 0
        if isinstance(detections, list):
            for detection in detections:
                if not isinstance(detection, Mapping):
                    continue
                class_id = detection.get("cls")
                class_name = class_names.get(class_id, "person" if class_id == 0 else "")
                if class_name == "person":
                    person_candidates += 1
        received_wall_ns = camera.get("received_wall_ns") if isinstance(camera, Mapping) else None
        received_ms = (
            int(received_wall_ns) // 1_000_000
            if isinstance(received_wall_ns, int) and received_wall_ns > 0
            else None
        )
        if received_ms is not None:
            newest_received_ms = received_ms if newest_received_ms is None else max(newest_received_ms, received_ms)
        camera_rows.append({
            "name": name,
            "status": "LIVE" if connected else "OFFLINE",
            "frame_sequence": camera.get("frame_sequence") if connected else None,
            "detections": detection_count,
            "detector_error": camera.get("detector_error") if isinstance(camera, Mapping) else "camera_missing",
        })

    connected_count = sum(row["status"] == "LIVE" for row in camera_rows)
    valid_pairs = disputes = abstained = reviews = 0
    for pair in pairs:
        if not isinstance(pair, Mapping):
            continue
        decision = str(pair.get("displayed_decision", "ABSTAIN")).upper()
        valid_pairs += int(pair.get("overlap_available") is True)
        disputes += int(decision == "DISPUTE")
        abstained += int(decision == "ABSTAIN")
        assumption = pair.get("appearance_assumption")
        reviews += int(
            isinstance(assumption, Mapping)
            and assumption.get("identity_proven") is not True
        )

    total_pairs = len(names) * (len(names) - 1) // 2
    state = "LIVE" if connected_count == len(names) else "DEGRADED" if connected_count else "OFFLINE"
    reason = (
        "all_configured_cameras_reporting"
        if state == "LIVE"
        else "one_or_more_cameras_unavailable"
        if state == "DEGRADED"
        else "waiting_for_camera_producer"
    )
    updated_at_ms = newest_received_ms or now_ms or time.time_ns() // 1_000_000
    return {
        "schema": DASHBOARD_SCHEMA,
        "run_id": event.get("run_id"),
        "feed_state": state,
        "reason": reason,
        "updated_at_ms": updated_at_ms,
        "expected_cameras": len(names),
        "connected_cameras": connected_count,
        "cameras": camera_rows,
        "total_pairs": total_pairs,
        "reported_pairs": len(pairs),
        "valid_pairs": valid_pairs,
        "person_candidates": person_candidates,
        "disputes": disputes,
        "abstained": abstained,
        "reviews": reviews,
        "model_id": event.get("model_id") or ("covis-yolo" if event.get("model_sha256") else "FEATURE_ONLY"),
        "model_sha256": event.get("model_sha256"),
        "read_only": True,
        "limitations": [
            "2-D homography-projected evidence; not calibrated stereo or 3-D",
            "appearance-only evidence remains REVIEW or ABSTAIN",
            "person candidates require responder confirmation",
        ],
    }


class MultiCameraDashboardState:
    """Thread-safe latest-frame state shared by HTTP request handlers."""

    def __init__(self, expected_camera_names: Sequence[str]):
        self.expected_camera_names = _camera_names(expected_camera_names)
        self._condition = threading.Condition()
        self._sequence = 0
        self._jpeg: bytes | None = None
        self._status = {
            "schema": DASHBOARD_SCHEMA,
            "run_id": None,
            "feed_state": "OFFLINE",
            "reason": "waiting_for_camera_producer",
            "updated_at_ms": None,
            "expected_cameras": len(self.expected_camera_names),
            "connected_cameras": 0,
            "cameras": [
                {"name": name, "status": "OFFLINE", "frame_sequence": None, "detections": 0, "detector_error": "camera_missing"}
                for name in self.expected_camera_names
            ],
            "total_pairs": len(self.expected_camera_names) * (len(self.expected_camera_names) - 1) // 2,
            "reported_pairs": 0,
            "valid_pairs": 0,
            "person_candidates": 0,
            "disputes": 0,
            "abstained": 0,
            "reviews": 0,
            "model_id": None,
            "model_sha256": None,
            "read_only": True,
            "limitations": [],
        }
        self._closed = False

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return json.loads(json.dumps(self._status))

    def publish_jpeg(self, jpeg: bytes, event: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(jpeg, bytes) or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise MultiCameraBridgeError("dashboard frame must be a complete JPEG")
        status = dashboard_status_from_event(event, self.expected_camera_names)
        with self._condition:
            if self._closed:
                raise MultiCameraBridgeError("dashboard state is closed")
            self._sequence += 1
            self._jpeg = jpeg
            self._status = status
            self._condition.notify_all()
        return status

    def wait_frame(self, after_sequence: int, timeout_s: float = 2.0) -> tuple[int, bytes | None, bool]:
        with self._condition:
            if self._sequence <= after_sequence and not self._closed:
                self._condition.wait(timeout_s)
            return self._sequence, self._jpeg, self._closed

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


def _authorized(request: BaseHTTPRequestHandler, token: str) -> bool:
    if not token:
        return True
    return request.headers.get("Authorization") == f"Bearer {token}" or request.headers.get("X-VeriSwarm-Token") == token


def _handler_factory(state: MultiCameraDashboardState, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "VeriSwarmMultiCamera/1"

        def _json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if not _authorized(self, token):
                self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                return
            path = self.path.split("?", 1)[0]
            if path in {"/health", "/status"}:
                snapshot = state.snapshot()
                self._json(HTTPStatus.OK, {"ok": True, "status": snapshot})
                return
            if path != "/stream.mjpg":
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            sequence = 0
            try:
                while True:
                    sequence, jpeg, closed = state.wait_frame(sequence)
                    if closed:
                        return
                    if jpeg is None:
                        continue
                    self.wfile.write(f"--{MJPEG_BOUNDARY}\r\n".encode("ascii"))
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return Handler


class MultiCameraDashboardServer:
    """Bounded read-only HTTP server for status and an MJPEG stream."""

    def __init__(self, bind: str, port: int, expected_camera_names: Sequence[str]):
        if not 0 <= port <= 65535:
            raise MultiCameraBridgeError("dashboard port must be inside 0..65535")
        token = os.environ.get("VERISWARM_MULTICAM_TOKEN", "")
        if bind not in {"127.0.0.1", "localhost", "::1"} and len(token) < 32:
            raise MultiCameraBridgeError("non-loopback dashboard bind requires VERISWARM_MULTICAM_TOKEN with 32+ characters")
        self.state = MultiCameraDashboardState(expected_camera_names)
        self.server = ThreadingHTTPServer((bind, port), _handler_factory(self.state, token))
        self.thread = threading.Thread(target=self.server.serve_forever, name="covis-multicam-dashboard", daemon=True)

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        self.thread.start()

    def publish(self, frame: Any, event: Mapping[str, Any], cv2_module: Any, *, quality: int = 84) -> dict[str, Any]:
        ok, encoded = cv2_module.imencode(".jpg", frame, [int(cv2_module.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            raise MultiCameraBridgeError("OpenCV could not encode dashboard JPEG")
        return self.state.publish_jpeg(bytes(encoded), event)

    def close(self) -> None:
        self.state.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3.0)
