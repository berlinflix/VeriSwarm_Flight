"""Lazy, Jetson-local disaster-video detection for the unified web console."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

try:
    import cv2  # type: ignore[import-not-found]
except ImportError:  # Geolocation/atlas remain available when video extras are absent.
    cv2 = None  # type: ignore[assignment]


VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm"})
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_video_upload(body: bytes, content_type: str) -> tuple[bytes, str]:
    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
    if not match:
        raise ValueError("expected a multipart video upload")
    boundary = ("--" + (match.group(1) or match.group(2)).strip()).encode()
    for part in body.split(boundary):
        split_at = part.find(b"\r\n\r\n")
        if split_at < 0:
            continue
        headers = part[:split_at].decode("utf-8", "replace")
        filename_match = re.search(r'filename="([^"]*)"', headers)
        if not filename_match:
            continue
        filename = Path(filename_match.group(1)).name or "upload.mp4"
        suffix = Path(filename).suffix.casefold()
        if suffix not in VIDEO_SUFFIXES:
            raise ValueError("video must be MP4, MOV, AVI, MKV or WebM")
        payload = part[split_at + 4 :]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        if payload:
            return payload, filename
    raise ValueError("upload contains no video file")


def extract_image_upload(body: bytes, content_type: str) -> tuple[bytes, str]:
    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
    if not match:
        raise ValueError("expected a multipart image upload")
    boundary = ("--" + (match.group(1) or match.group(2)).strip()).encode()
    for part in body.split(boundary):
        split_at = part.find(b"\r\n\r\n")
        if split_at < 0:
            continue
        headers = part[:split_at].decode("utf-8", "replace")
        filename_match = re.search(r'filename="([^"]*)"', headers)
        if not filename_match:
            continue
        filename = Path(filename_match.group(1)).name or "upload.jpg"
        if Path(filename).suffix.casefold() not in IMAGE_SUFFIXES:
            raise ValueError("image must be JPEG, PNG or WebP")
        payload = part[split_at + 4 :]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        if payload:
            return payload, filename
    raise ValueError("upload contains no image file")


class Detector:
    def __init__(self, weights: Path, confidence: float, person_class: int, image_size: int):
        if cv2 is None:
            raise RuntimeError("OpenCV is required for rescue-video decoding")
        self.weights = weights
        self.confidence = confidence
        self.person_class = person_class
        self.image_size = image_size
        self.sha256 = sha256_file(weights)
        self.backend, self.impl = self._load()

    def _load(self) -> tuple[str, Any]:
        if self.weights.suffix.casefold() == ".onnx":
            return "opencv-dnn", cv2.dnn.readNetFromONNX(str(self.weights))
        from ultralytics import YOLO  # type: ignore[import-not-found]

        return "ultralytics", YOLO(str(self.weights))

    def detect(self, frame: np.ndarray) -> list[dict[str, object]]:
        if self.backend == "ultralytics":
            result = self.impl.predict(
                frame,
                conf=self.confidence,
                imgsz=self.image_size,
                classes=[self.person_class],
                verbose=False,
            )[0]
            return [
                {
                    "class_id": int(box.cls[0]),
                    "confidence": round(float(box.conf[0]), 3),
                    "xyxy": [round(float(value)) for value in box.xyxy[0]],
                }
                for box in result.boxes
            ]
        return self._detect_onnx(frame)

    def _detect_onnx(self, frame: np.ndarray) -> list[dict[str, object]]:
        height, width = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            frame,
            1 / 255.0,
            (self.image_size, self.image_size),
            swapRB=True,
            crop=False,
        )
        self.impl.setInput(blob)
        prediction = np.squeeze(self.impl.forward()).T
        scores = prediction[:, 4 + self.person_class]
        keep = scores > self.confidence
        prediction, scores = prediction[keep], scores[keep]
        if not len(prediction):
            return []
        gain = min(self.image_size / width, self.image_size / height)
        pad_x = (self.image_size - width * gain) / 2
        pad_y = (self.image_size - height * gain) / 2
        boxes: list[list[float]] = []
        for center_x, center_y, box_width, box_height in prediction[:, :4]:
            boxes.append(
                [
                    (center_x - box_width / 2 - pad_x) / gain,
                    (center_y - box_height / 2 - pad_y) / gain,
                    box_width / gain,
                    box_height / gain,
                ]
            )
        indices = cv2.dnn.NMSBoxes(boxes, scores.tolist(), self.confidence, 0.45)
        result = []
        for index in np.array(indices).flatten():
            x, y, box_width, box_height = boxes[int(index)]
            result.append(
                {
                    "class_id": self.person_class,
                    "confidence": round(float(scores[int(index)]), 3),
                    "xyxy": [round(x), round(y), round(x + box_width), round(y + box_height)],
                }
            )
        return result


class Signer:
    """Sign sighting claims; never mislabel the software fallback as OP-TEE."""

    def __init__(self, ca_path: Path | None):
        self.backend = "unsigned"
        self.ca_path: Path | None = None
        self.software_key = None
        if ca_path and ca_path.is_file():
            try:
                subprocess.run(
                    [str(ca_path), "getpub"],
                    capture_output=True,
                    check=True,
                    timeout=5,
                )
                self.backend = "optee"
                self.ca_path = ca_path
                return
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            import nacl.signing  # type: ignore[import-not-found]

            self.software_key = nacl.signing.SigningKey.generate()
            self.backend = "software"
        except ImportError:
            pass

    def receipt(self, claim: dict[str, object]) -> dict[str, object]:
        canonical = json.dumps(claim, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(canonical).hexdigest()
        signature: str | None = None
        try:
            if self.backend == "optee" and self.ca_path:
                completed = subprocess.run(
                    [str(self.ca_path), "sign", canonical.hex()],
                    capture_output=True,
                    check=True,
                    text=True,
                    timeout=10,
                )
                signature = completed.stdout.strip()
            elif self.backend == "software" and self.software_key:
                signature = self.software_key.sign(canonical).signature.hex()
        except (OSError, subprocess.SubprocessError):
            signature = None
        return {"digest": digest, "backend": self.backend, "signature": signature}


class RescueVideoRuntime:
    """Run one video at a time and share one GPU lock with visual geolocation."""

    def __init__(
        self,
        *,
        weights: Path,
        expected_weights_sha256: str | None,
        confidence: float,
        person_class: int,
        image_size: int,
        stride: int,
        max_frames: int,
        work_dir: Path,
        evidence_dir: Path,
        optee_ca: Path | None,
        gpu_lock: threading.Lock,
        before_run: Callable[[], object],
        live_camera: str = (
            "/dev/v4l/by-id/"
            "usb-Owl_Lite_Owl_Lite_Camera_SN0001-video-index0"
        ),
        live_width: int = 640,
        live_height: int = 480,
        live_fps_limit: float = 8.0,
    ) -> None:
        self.weights = weights.resolve(strict=True)
        self.weights_sha256 = sha256_file(self.weights)
        if expected_weights_sha256:
            expected = expected_weights_sha256.strip().casefold()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("expected rescue model SHA-256 must contain 64 hex characters")
            if expected != self.weights_sha256:
                raise ValueError(
                    f"rescue model hash mismatch: expected {expected}, got {self.weights_sha256}"
                )
        self.confidence = confidence
        self.person_class = person_class
        self.image_size = image_size
        self.stride = stride
        self.max_frames = max_frames
        self.work_dir = work_dir.resolve()
        self.evidence_dir = evidence_dir.resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.signer = Signer(optee_ca)
        self.gpu_lock = gpu_lock
        self.before_run = before_run
        self.detector: Detector | None = None
        self.jobs: dict[str, dict[str, object]] = {}
        self.jobs_lock = threading.Lock()
        self.detector_lock = threading.Lock()
        self.active_job: str | None = None
        self.live_camera = live_camera
        self.live_width = live_width
        self.live_height = live_height
        self.live_fps_limit = live_fps_limit
        self.live_lock = threading.Lock()
        self.live_stop_event = threading.Event()
        self.live_thread: threading.Thread | None = None
        self.live_state = "stopped"
        self.live_error: str | None = None
        self.live_camera_opened = False
        self.live_release_verified: bool | None = None
        self.live_latest_jpeg: bytes | None = None
        self.live_frame_index = 0
        self.live_person_candidates = 0
        self.live_maximum_confidence = 0.0
        self.live_inference_ms = 0.0
        self.live_processing_fps = 0.0
        self.live_started_utc: str | None = None
        self.live_stopped_utc: str | None = None
        self.live_last_receipt: dict[str, object] | None = None
        self.live_session_id: str | None = None

    def status(self) -> dict[str, object]:
        return {
            "ok": True,
            "available": True,
            "model_loaded": self.detector is not None,
            "model_sha256": self.weights_sha256,
            "backend": self.detector.backend if self.detector else "cold",
            "signer": self.signer.backend,
            "active_job": self.active_job,
            "live_camera_state": self.live_status()["state"],
            "policy": "survivor_reporting_is_not_consensus_gated",
        }

    def live_active(self) -> bool:
        with self.live_lock:
            return self.live_state in {"starting", "running", "stopping"}

    def live_status(self) -> dict[str, object]:
        with self.live_lock:
            return {
                "ok": self.live_state != "error",
                "state": self.live_state,
                "camera": self.live_camera,
                "camera_opened": self.live_camera_opened,
                "release_verified": self.live_release_verified,
                "model_loaded": self.detector is not None,
                "model_sha256": self.weights_sha256,
                "detector": self.detector.backend if self.detector else "cold",
                "signer": self.signer.backend,
                "frame_index": self.live_frame_index,
                "person_candidate_count": self.live_person_candidates,
                "maximum_confidence": self.live_maximum_confidence,
                "inference_ms": self.live_inference_ms,
                "processing_fps": self.live_processing_fps,
                "started_utc": self.live_started_utc,
                "stopped_utc": self.live_stopped_utc,
                "last_receipt": self.live_last_receipt,
                "session_id": self.live_session_id,
                "error": self.live_error,
                "policy": "live_detections_are_unverified_person_candidates",
            }

    def start_live(self) -> dict[str, object]:
        if cv2 is None:
            raise RuntimeError("OpenCV is required for live-camera capture")
        if self.active_job is not None:
            raise ValueError("a rescue-video job is active; wait for it to finish")
        with self.live_lock:
            if self.live_state in {"starting", "running", "stopping"}:
                raise ValueError(f"live camera is already {self.live_state}")
            self.live_state = "starting"
            self.live_error = None
            self.live_camera_opened = False
            self.live_release_verified = None
            self.live_latest_jpeg = None
            self.live_frame_index = 0
            self.live_person_candidates = 0
            self.live_maximum_confidence = 0.0
            self.live_inference_ms = 0.0
            self.live_processing_fps = 0.0
            self.live_started_utc = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            self.live_stopped_utc = None
            self.live_last_receipt = None
            self.live_session_id = str(uuid.uuid4())
            self.live_stop_event.clear()
            self.live_thread = threading.Thread(
                target=self._live_loop,
                name="veriswarm-live-camera",
                daemon=True,
            )
            self.live_thread.start()
        return self.live_status()

    def stop_live(self, timeout: float = 12.0) -> dict[str, object]:
        with self.live_lock:
            thread = self.live_thread
            if not thread or not thread.is_alive():
                if self.live_state != "error":
                    self.live_state = "stopped"
                return self.live_status_unlocked()
            self.live_state = "stopping"
            self.live_stop_event.set()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise ValueError("live camera did not stop within the release deadline")
        return self.live_status()

    def live_status_unlocked(self) -> dict[str, object]:
        """Return status while the caller already owns live_lock."""
        return {
            "ok": self.live_state != "error",
            "state": self.live_state,
            "camera": self.live_camera,
            "camera_opened": self.live_camera_opened,
            "release_verified": self.live_release_verified,
            "model_loaded": self.detector is not None,
            "model_sha256": self.weights_sha256,
            "detector": self.detector.backend if self.detector else "cold",
            "signer": self.signer.backend,
            "frame_index": self.live_frame_index,
            "person_candidate_count": self.live_person_candidates,
            "maximum_confidence": self.live_maximum_confidence,
            "inference_ms": self.live_inference_ms,
            "processing_fps": self.live_processing_fps,
            "started_utc": self.live_started_utc,
            "stopped_utc": self.live_stopped_utc,
            "last_receipt": self.live_last_receipt,
            "session_id": self.live_session_id,
            "error": self.live_error,
            "policy": "live_detections_are_unverified_person_candidates",
        }

    def latest_live_frame(self) -> bytes | None:
        with self.live_lock:
            return self.live_latest_jpeg

    def unload(self) -> dict[str, object]:
        if self.active_job is not None:
            raise ValueError("cannot unload while a rescue-video job is active")
        if self.live_active():
            raise ValueError("cannot unload while live-camera detection is active")
        with self.detector_lock:
            self.detector = None
            try:
                import torch  # type: ignore[import-not-found]

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
        return self.status()

    def start(self, payload: bytes, filename: str) -> str:
        if self.live_active():
            raise ValueError("live-camera detection is active; stop it first")
        with self.jobs_lock:
            if self.active_job is not None:
                raise ValueError("a rescue-video job is already active")
            job_id = str(uuid.uuid4())
            suffix = Path(filename).suffix.casefold()
            video_path = self.work_dir / f"{job_id}{suffix}"
            with video_path.open("xb") as stream:
                stream.write(payload)
            self.jobs[job_id] = {
                "id": job_id,
                "state": "queued",
                "filename": Path(filename).name,
                "size_mib": round(len(payload) / 1048576, 2),
                "progress": 0.0,
                "frames_analyzed": 0,
                "people_detected": 0,
                "sightings": [],
                "model_sha256": self.weights_sha256,
                "signer": self.signer.backend,
            }
            self.active_job = job_id
        thread = threading.Thread(
            target=self._process,
            args=(job_id, video_path),
            daemon=True,
        )
        thread.start()
        return job_id

    def job(self, job_id: str) -> dict[str, object] | None:
        with self.jobs_lock:
            value = self.jobs.get(job_id)
            return dict(value) if value else None

    def analyze_image(self, payload: bytes, filename: str) -> dict[str, object]:
        """Analyze one still image synchronously and preserve create-once evidence."""
        if cv2 is None:
            raise RuntimeError("OpenCV is required for rescue-image decoding")
        if self.active_job is not None:
            raise ValueError("a rescue-video job is active; wait for it to finish")
        if self.live_active():
            raise ValueError("live-camera detection is active; stop it first")
        encoded = np.frombuffer(payload, dtype=np.uint8)
        frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            raise ValueError("could not decode image; choose a valid JPEG, PNG or WebP")

        started = time.perf_counter()
        with self.gpu_lock:
            if self.active_job is not None:
                raise ValueError("a rescue-video job is active; wait for it to finish")
            self.before_run()
            with self.detector_lock:
                if self.detector is None:
                    self.detector = Detector(
                        self.weights,
                        self.confidence,
                        self.person_class,
                        self.image_size,
                    )
                detector = self.detector
            detections = detector.detect(frame)

        analysis_id = str(uuid.uuid4())
        maximum_confidence = max(
            (float(item["confidence"]) for item in detections), default=0.0
        )
        claim: dict[str, object] = {
            "schema": "veriswarm.rescue.image_claim.v1",
            "analysis_id": analysis_id,
            "source_filename": Path(filename).name,
            "person_candidate_count": len(detections),
            "maximum_confidence": round(maximum_confidence, 3),
            "model_sha256": detector.sha256,
            "detector": detector.backend,
            "classification": "DISASTER / UNVERIFIED",
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        receipt = self.signer.receipt(claim)
        evidence: dict[str, object] = {
            **claim,
            "boxes": detections,
            "receipt": receipt,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        evidence_path = self.evidence_dir / f"image-{analysis_id}.json"
        with evidence_path.open("x", encoding="utf-8") as stream:
            json.dump(evidence, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        return {
            **evidence,
            "annotated_image": annotate_frame(frame, detections),
            "evidence_file": evidence_path.name,
        }

    def _set(self, job_id: str, **values: object) -> None:
        with self.jobs_lock:
            self.jobs[job_id].update(values)

    def _process(self, job_id: str, video_path: Path) -> None:
        started = time.perf_counter()
        try:
            with self.gpu_lock:
                self.before_run()
                with self.detector_lock:
                    if self.detector is None:
                        self.detector = Detector(
                            self.weights,
                            self.confidence,
                            self.person_class,
                            self.image_size,
                        )
                    detector = self.detector
                self._process_video(job_id, video_path, detector)
        except Exception as error:
            self._set(
                job_id,
                state="error",
                error=f"{type(error).__name__}: {error}",
                elapsed_seconds=round(time.perf_counter() - started, 2),
            )
        finally:
            try:
                video_path.unlink()
            except OSError:
                pass
            with self.jobs_lock:
                self.active_job = None
                snapshot = dict(self.jobs[job_id])
            evidence_path = self.evidence_dir / f"{job_id}.json"
            try:
                with evidence_path.open("x", encoding="utf-8") as stream:
                    json.dump(snapshot, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
            except OSError:
                pass

    def _process_video(self, job_id: str, path: Path, detector: Detector) -> None:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise ValueError("could not decode video; check the codec")
        source_fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        planned = min(self.max_frames, max(1, (total_frames + self.stride - 1) // self.stride))
        self._set(
            job_id,
            state="running",
            detector=detector.backend,
            source_fps=round(source_fps, 2),
            total_frames=total_frames,
            planned_frames=planned,
        )
        source_index = analyzed = people = 0
        started = time.perf_counter()
        while analyzed < self.max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            source_index += 1
            if (source_index - 1) % self.stride:
                continue
            detections = detector.detect(frame)
            analyzed += 1
            if detections:
                people += len(detections)
                claim: dict[str, object] = {
                    "schema": "veriswarm.rescue.sighting.v1",
                    "frame_index": source_index,
                    "timestamp_seconds": round(source_index / source_fps, 2),
                    "person_count": len(detections),
                    "maximum_confidence": max(
                        float(item["confidence"]) for item in detections
                    ),
                    "model_sha256": detector.sha256,
                    "detector": detector.backend,
                    "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                sighting = {
                    **claim,
                    "boxes": detections,
                    "annotated_frame": annotate_frame(frame, detections),
                    "receipt": self.signer.receipt(claim),
                }
                with self.jobs_lock:
                    sightings = self.jobs[job_id]["sightings"]
                    assert isinstance(sightings, list)
                    sightings.append(sighting)
            self._set(
                job_id,
                frames_analyzed=analyzed,
                people_detected=people,
                progress=round(min(100.0, 100 * analyzed / planned), 1),
            )
        capture.release()
        elapsed = time.perf_counter() - started
        self._set(
            job_id,
            state="done",
            progress=100.0,
            elapsed_seconds=round(elapsed, 2),
            processing_fps=round(analyzed / max(elapsed, 1e-6), 2),
        )

    def _live_loop(self) -> None:
        capture = None
        evidence_stream = None
        try:
            capture = self._open_live_capture()
            if not capture.isOpened():
                raise RuntimeError(f"could not open camera {self.live_camera}")

            with self.live_lock:
                self.live_camera_opened = True
                self.live_state = "running"

            session_id = self.live_session_id or str(uuid.uuid4())
            evidence_path = self.evidence_dir / f"live-{session_id}.jsonl"
            evidence_stream = evidence_path.open("x", encoding="utf-8")
            processed = 0
            failed_reads = 0
            loop_started = time.perf_counter()
            last_signed_at = 0.0

            with self.gpu_lock:
                if self.active_job is not None:
                    raise RuntimeError("rescue-video job became active during camera start")
                self.before_run()
                with self.detector_lock:
                    if self.detector is None:
                        self.detector = Detector(
                            self.weights,
                            self.confidence,
                            self.person_class,
                            self.image_size,
                        )
                    detector = self.detector

                while not self.live_stop_event.is_set():
                    cycle_started = time.perf_counter()
                    ok, frame = capture.read()
                    if not ok or frame is None or frame.size == 0:
                        failed_reads += 1
                        if failed_reads >= 5:
                            raise RuntimeError("camera returned five consecutive empty frames")
                        time.sleep(0.05)
                        continue
                    failed_reads = 0
                    inference_started = time.perf_counter()
                    detections = detector.detect(frame)
                    inference_ms = (time.perf_counter() - inference_started) * 1000.0
                    encoded = annotate_frame_jpeg(frame, detections)
                    if not encoded:
                        raise RuntimeError("could not encode annotated camera frame")
                    processed += 1
                    maximum_confidence = max(
                        (float(item["confidence"]) for item in detections),
                        default=0.0,
                    )
                    receipt = None
                    now = time.monotonic()
                    if detections and now - last_signed_at >= 1.0:
                        claim: dict[str, object] = {
                            "schema": "veriswarm.rescue.live_sighting.v1",
                            "session_id": session_id,
                            "frame_index": processed,
                            "person_candidate_count": len(detections),
                            "maximum_confidence": round(maximum_confidence, 3),
                            "model_sha256": detector.sha256,
                            "detector": detector.backend,
                            "camera": self.live_camera,
                            "created_utc": datetime.now(timezone.utc).isoformat(
                                timespec="seconds"
                            ),
                        }
                        receipt = self.signer.receipt(claim)
                        evidence_stream.write(
                            json.dumps(
                                {**claim, "boxes": detections, "receipt": receipt},
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        evidence_stream.flush()
                        last_signed_at = now
                    elapsed = time.perf_counter() - loop_started
                    with self.live_lock:
                        self.live_latest_jpeg = encoded
                        self.live_frame_index = processed
                        self.live_person_candidates = len(detections)
                        self.live_maximum_confidence = round(maximum_confidence, 3)
                        self.live_inference_ms = round(inference_ms, 1)
                        self.live_processing_fps = round(
                            processed / max(elapsed, 1e-6), 2
                        )
                        if receipt is not None:
                            self.live_last_receipt = receipt

                    if self.live_fps_limit > 0:
                        remaining = (1.0 / self.live_fps_limit) - (
                            time.perf_counter() - cycle_started
                        )
                        if remaining > 0:
                            self.live_stop_event.wait(remaining)
        except Exception as error:
            with self.live_lock:
                self.live_state = "error"
                self.live_error = f"{type(error).__name__}: {error}"
        finally:
            if evidence_stream is not None:
                evidence_stream.close()
            if capture is not None:
                capture.release()
                release_verified = self._verify_live_camera_reopen(
                    initial_release=not capture.isOpened()
                )
            else:
                release_verified = True
            with self.live_lock:
                self.live_camera_opened = False
                self.live_release_verified = release_verified
                self.live_stopped_utc = datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                )
                if self.live_state != "error":
                    self.live_state = "stopped"
                self.live_thread = None

    def _open_live_capture(self):
        backend = getattr(cv2, "CAP_V4L2", None)
        if backend is not None and self.live_camera.startswith("/dev/"):
            capture = cv2.VideoCapture(self.live_camera, backend)
        else:
            source: str | int = (
                int(self.live_camera)
                if self.live_camera.isdecimal()
                else self.live_camera
            )
            capture = cv2.VideoCapture(source)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.live_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.live_height)
        return capture

    def _verify_live_camera_reopen(self, *, initial_release: bool) -> bool:
        """Prove another owner can reopen/read/release the camera after stop."""
        if not initial_release:
            return False
        for attempt in range(3):
            probe = self._open_live_capture()
            try:
                opened = probe.isOpened()
                read_ok, frame = probe.read() if opened else (False, None)
            finally:
                probe.release()
            if opened and read_ok and frame is not None and not probe.isOpened():
                return True
            if attempt < 2:
                time.sleep(0.1)
        return False


def annotate_frame_jpeg(
    frame: np.ndarray, detections: list[dict[str, object]]
) -> bytes:
    """Return a compact annotated JPEG for still-image and live-camera views."""
    image = frame.copy()
    for detection in detections:
        x1, y1, x2, y2 = (int(value) for value in detection["xyxy"])  # type: ignore[index]
        confidence = float(detection["confidence"])
        cv2.rectangle(image, (x1, y1), (x2, y2), (54, 54, 255), 3)
        cv2.putText(
            image,
            f"PERSON_CANDIDATE | UNVERIFIED {confidence:.2f}",
            (x1, max(18, y1 - 7)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (54, 54, 255),
            2,
        )
    height, width = image.shape[:2]
    output_width = min(960, width)
    output_height = max(1, round(height * output_width / width))
    image = cv2.resize(image, (output_width, output_height), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 78])
    return encoded.tobytes() if ok else b""


def annotate_frame(frame: np.ndarray, detections: list[dict[str, object]]) -> str:
    encoded = annotate_frame_jpeg(frame, detections)
    return (
        "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")
        if encoded
        else ""
    )


def rescue_video_html(runtime: RescueVideoRuntime) -> bytes:
    replacements = {
        "__MODEL_HASH__": html.escape(runtime.weights_sha256[:16]),
        "__SIGNER__": html.escape(runtime.signer.backend),
        "__STRIDE__": str(runtime.stride),
    }
    value = RESCUE_IMAGE_TEMPLATE
    for marker, replacement in replacements.items():
        value = value.replace(marker, replacement)
    return value.encode("utf-8")


def live_camera_html(runtime: RescueVideoRuntime) -> bytes:
    replacements = {
        "__MODEL_HASH__": html.escape(runtime.weights_sha256[:16]),
        "__SIGNER__": html.escape(runtime.signer.backend),
        "__CAMERA__": html.escape(runtime.live_camera),
    }
    value = LIVE_CAMERA_TEMPLATE
    for marker, replacement in replacements.items():
        value = value.replace(marker, replacement)
    return value.encode("utf-8")


LIVE_CAMERA_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VeriSwarm Live Edge Camera</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#06100f;--panel:#0c1817;--line:#243b38;--text:#eef8f5;--muted:#8da9a3;--mint:#4de8ad;--cyan:#66d9ef;--amber:#ffba57;--red:#ff5757}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 12% -10%,#173d34 0,transparent 35%),var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}.shell{max-width:1260px;margin:auto;padding:22px 28px 64px}header{display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--line);padding:8px 0 22px}.brand{font-size:20px;font-weight:800}.brand b{color:var(--mint)}nav{display:flex;gap:6px;margin-left:auto}nav a{padding:7px 10px;border:1px solid var(--line);border-radius:6px;color:var(--muted);text-decoration:none;font:11px JetBrains Mono,monospace}nav a.active{color:var(--mint);border-color:#297d60}.hero{padding:34px 0 20px}.eyebrow{font:600 11px JetBrains Mono,monospace;color:var(--mint);letter-spacing:.12em}.hero h1{font-size:clamp(34px,5vw,58px);letter-spacing:-.055em;line-height:1.02;margin:10px 0}.hero p{font-size:16px;color:var(--muted);max-width:830px}.chips{display:flex;gap:7px;flex-wrap:wrap}.chip{border:1px solid var(--line);padding:6px 9px;border-radius:5px;color:var(--muted);font:10px JetBrains Mono,monospace}.panel{border:1px solid var(--line);background:linear-gradient(155deg,#10221f,#081211);border-radius:11px;overflow:hidden}.panel-title{display:flex;align-items:center;gap:9px;padding:12px 15px;border-bottom:1px solid var(--line);color:var(--muted);font:600 11px JetBrains Mono,monospace;letter-spacing:.09em}.dot{width:7px;height:7px;border-radius:50%;background:var(--muted)}.dot.live{background:var(--mint);box-shadow:0 0 13px var(--mint)}.viewer{position:relative;min-height:520px;background:#020605;display:grid;place-items:center}.viewer img{display:none;width:100%;max-height:720px;object-fit:contain}.placeholder{text-align:center;color:var(--muted);font:12px JetBrains Mono,monospace}.placeholder b{display:block;color:var(--text);font:600 18px Inter,sans-serif;margin-bottom:8px}.controls{display:flex;gap:9px;padding:14px 15px;border-top:1px solid var(--line);flex-wrap:wrap}.btn{border:1px solid var(--line);background:#10231f;color:var(--text);border-radius:6px;padding:10px 14px;font-weight:600;cursor:pointer}.btn.primary{background:var(--mint);border-color:var(--mint);color:#04110d}.btn.danger{border-color:#7e3538;color:#ffb4b4}.btn:disabled{opacity:.42;cursor:not-allowed}.stats{display:grid;grid-template-columns:repeat(6,1fr);border-top:1px solid var(--line)}.stat{padding:13px 14px;border-right:1px solid var(--line);min-width:0}.stat:last-child{border-right:0}.stat label{display:block;color:var(--muted);font:10px JetBrains Mono,monospace}.stat strong{display:block;font:700 16px JetBrains Mono,monospace;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.receipt{margin:16px 0 0;padding:13px 15px;border-left:3px solid var(--mint);background:#0b1f19;color:#bfead9;font:11px JetBrains Mono,monospace}.warning{margin:16px 0;border-left:3px solid var(--amber);background:#21180d;color:#e8cca5;padding:12px 14px}.error{display:none;margin:16px 0;border-left:3px solid var(--red);background:#251012;color:#ffb9b9;padding:12px 14px}@media(max-width:900px){.shell{padding:15px}.stats{grid-template-columns:1fr 1fr 1fr}.viewer{min-height:360px}header{align-items:flex-start;flex-wrap:wrap}nav{margin-left:0}}@media(max-width:560px){.stats{grid-template-columns:1fr 1fr}}
</style></head><body><div class="shell">
<header><div class="brand"><b>VERI</b>SWARM / LIVE EDGE CAMERA</div><nav><a href="/rescue">Rescue analysis</a><a class="active" href="/live">Live camera</a><a href="/">Geolocation</a><a href="/hazards">Hazard atlas</a></nav></header>
<section class="hero"><div class="eyebrow">USB CAMERA · JETSON · ON-DEVICE AI</div><h1>Live camera in.<br>Person candidates out.</h1><p>The Owl USB camera is opened by the Jetson. The frozen rescue model draws live PERSON_CANDIDATE boxes locally and signs bounded sighting evidence through the configured signer.</p><div class="chips"><span class="chip">CAMERA __CAMERA__</span><span class="chip">MODEL __MODEL_HASH__</span><span class="chip">SIGNER __SIGNER__</span></div></section>
<section class="panel"><div class="panel-title"><span class="dot" id="dot"></span><span id="panelState">CAMERA STOPPED</span></div><div class="viewer"><div class="placeholder" id="placeholder"><b>USB camera is configured</b>Press Start live detection to open it on the Jetson.</div><img id="feed" alt="Jetson USB camera with unverified person-candidate boxes"></div><div class="controls"><button class="btn primary" id="start">Start live detection</button><button class="btn danger" id="stop" disabled>Stop and release camera</button><button class="btn" id="unload">Unload detector</button></div><div class="stats"><div class="stat"><label>Camera</label><strong id="camera">CLOSED</strong></div><div class="stat"><label>Detector</label><strong id="detector">COLD</strong></div><div class="stat"><label>Processing</label><strong id="fps">0 FPS</strong></div><div class="stat"><label>Inference</label><strong id="inference">0 MS</strong></div><div class="stat"><label>Person candidates</label><strong id="people">0</strong></div><div class="stat"><label>Confidence</label><strong id="confidence">0.000</strong></div></div></section>
<div class="receipt" id="receipt">SIGNED RECEIPT: waiting for a person-candidate sighting</div><div class="warning"><b>UNVERIFIED:</b> A live box is a model-generated person candidate, not confirmation of survivor condition. Reporting is not consensus-gated; movement remains separately authorized.</div><div class="error" id="error"></div>
</div><script>
const $=id=>document.getElementById(id);let frameTimer=null,busy=false;
function fail(message){$('error').textContent=message;$('error').style.display='block'}
async function post(path){const response=await fetch(path,{method:'POST'}),value=await response.json();if(!response.ok)throw new Error(value.error||`server ${response.status}`);return value}
function render(s){const active=['starting','running','stopping'].includes(s.state);$('panelState').textContent=`CAMERA ${s.state.toUpperCase()}`;$('dot').classList.toggle('live',s.state==='running');$('camera').textContent=s.camera_opened?'CONNECTED':(s.release_verified?'RELEASED':'CLOSED');$('detector').textContent=(s.detector||'cold').toUpperCase();$('fps').textContent=`${Number(s.processing_fps||0).toFixed(1)} FPS`;$('inference').textContent=`${Number(s.inference_ms||0).toFixed(0)} MS`;$('people').textContent=s.person_candidate_count||0;$('confidence').textContent=Number(s.maximum_confidence||0).toFixed(3);$('start').disabled=active||busy;$('stop').disabled=!active||busy;$('unload').disabled=active||busy;if(s.last_receipt){$('receipt').textContent=`SIGNED RECEIPT: ${String(s.last_receipt.backend).toUpperCase()} · ${String(s.last_receipt.digest).slice(0,24)}…`}if(s.error)fail(s.error);if(s.frame_index>0){$('feed').style.display='block';$('placeholder').style.display='none'}else if(!active){$('feed').style.display='none';$('placeholder').style.display='block'}}
async function refresh(){try{const response=await fetch('/api/live/status',{cache:'no-store'}),s=await response.json();render(s);if(s.state==='running'&&s.frame_index>0)$('feed').src=`/api/live/frame?frame=${s.frame_index}&t=${Date.now()}`}catch(error){fail(error.message)}}
$('start').onclick=async()=>{busy=true;$('error').style.display='none';try{render(await post('/api/live/start'))}catch(error){fail(error.message)}finally{busy=false;refresh()}};
$('stop').onclick=async()=>{busy=true;try{render(await post('/api/live/stop'))}catch(error){fail(error.message)}finally{busy=false;refresh()}};
$('unload').onclick=async()=>{busy=true;try{await post('/api/rescue/unload');await refresh()}catch(error){fail(error.message)}finally{busy=false}};
refresh();frameTimer=setInterval(refresh,400);window.addEventListener('beforeunload',()=>clearInterval(frameTimer));
</script></body></html>'''


RESCUE_IMAGE_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VeriSwarm Rescue Perception</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#06100f;--panel:#0c1817;--line:#243b38;--text:#eef8f5;--muted:#8da9a3;--mint:#4de8ad;--amber:#ffba57;--red:#ff5757}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 12% -10%,#173d34 0,transparent 35%),var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}.shell{max-width:1240px;margin:auto;padding:22px 28px 64px}header{display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--line);padding:8px 0 22px}.brand{font-size:20px;font-weight:800}.brand b{color:var(--mint)}nav{display:flex;gap:6px;margin-left:auto}nav a{padding:7px 10px;border:1px solid var(--line);border-radius:6px;color:var(--muted);text-decoration:none;font:11px JetBrains Mono,monospace}nav a.active{color:var(--mint);border-color:#297d60}.hero{padding:38px 0 22px}.eyebrow{font:600 11px JetBrains Mono,monospace;color:var(--mint);letter-spacing:.12em}.hero h1{font-size:clamp(34px,5vw,60px);letter-spacing:-.055em;line-height:1.02;margin:12px 0}.hero p{font-size:16px;color:var(--muted);max-width:820px}.chips{display:flex;gap:7px;flex-wrap:wrap}.chip{border:1px solid var(--line);padding:6px 9px;border-radius:5px;color:var(--muted);font:10px JetBrains Mono,monospace}.mode-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.panel{border:1px solid var(--line);background:linear-gradient(155deg,#10221f,#081211);border-radius:11px;overflow:hidden}.panel-title{padding:12px 15px;border-bottom:1px solid var(--line);color:var(--muted);font:600 11px JetBrains Mono,monospace;letter-spacing:.09em}.drop{margin:16px;border:1px dashed #365b55;border-radius:9px;min-height:210px;display:grid;place-items:center;text-align:center;padding:24px;cursor:pointer}.drop.hot{border-color:var(--mint);background:#0d251f}.drop h2{margin:0 0 8px}.drop p{margin:0;color:var(--muted)}.tag{display:inline-block;margin-bottom:10px;padding:4px 7px;border:1px solid #40534f;border-radius:4px;color:var(--mint);font:10px JetBrains Mono,monospace}.bar{height:3px;background:#162724;display:none}.bar i{display:block;height:100%;background:var(--mint);width:0;transition:.2s}.stats{display:none;grid-template-columns:repeat(5,1fr);border-top:1px solid var(--line);margin-top:16px}.stat{padding:13px 15px;border-right:1px solid var(--line)}.stat label{display:block;color:var(--muted);font:10px JetBrains Mono,monospace}.stat strong{display:block;font:700 19px JetBrains Mono,monospace;margin-top:4px}.policy{border-left:3px solid var(--amber);background:#21180d;color:#e8cca5;padding:13px 15px;margin:18px 0}.warning{border-left:3px solid var(--red);background:#231011;color:#ffbcbc;padding:12px 14px;margin:16px 0}.sighting{border:1px solid var(--line);background:#0b1716;border-radius:9px;overflow:hidden;margin-top:14px}.sighting img{width:100%;max-height:600px;object-fit:contain;background:#030706;display:block}.meta{display:flex;gap:18px;align-items:center;padding:12px 14px;flex-wrap:wrap}.meta span{font:11px JetBrains Mono,monospace}.receipt{margin-left:auto;color:var(--mint)}.photo-result{display:none;margin-top:16px}.photo-result.show{display:block}.error{display:none;border-left:3px solid var(--red);background:#251012;color:#ffb9b9;padding:12px 14px;margin:16px}@media(max-width:820px){.mode-grid{grid-template-columns:1fr}.shell{padding:15px}.stats{grid-template-columns:1fr 1fr}.stat{border-bottom:1px solid var(--line)}header{align-items:flex-start;flex-wrap:wrap}nav{margin-left:0}}
</style></head><body><div class="shell">
<header><div class="brand"><b>VERI</b>SWARM / RESCUE PERCEPTION</div><nav><a class="active" href="/rescue">Rescue analysis</a><a href="/live">Live camera</a><a href="/">Geolocation</a><a href="/hazards">Hazard atlas</a></nav></header>
<section class="hero"><div class="eyebrow">ON-DEVICE · JETSON · SIGNED ATTRIBUTION</div><h1>Disaster imagery in.<br>Person candidates out.</h1><p>Analyze a still disaster image or video with the frozen rescue detector. Processing remains local on the Jetson. Red boxes are unverified <b>PERSON_CANDIDATE</b> outputs—not confirmed survivors and not disaster-type classifications.</p><div class="chips"><span class="chip">MODEL __MODEL_HASH__</span><span class="chip">SIGNER __SIGNER__</span><span class="chip">VIDEO SAMPLE EVERY __STRIDE__TH FRAME</span></div></section>
<div class="mode-grid">
<section class="panel"><div class="panel-title">01 · DISASTER PHOTO ANALYSIS</div><div id="photoDrop" class="drop"><div><span class="tag">RESCUE PERCEPTION</span><h2>Drop a disaster-scene image</h2><p>JPEG · PNG · WebP</p></div><input id="photoFile" type="file" accept="image/jpeg,image/png,image/webp" hidden></div><div id="photoError" class="error"></div></section>
<section class="panel"><div class="panel-title">02 · DISASTER VIDEO ANALYSIS</div><div id="videoDrop" class="drop"><div><span class="tag">TEMPORAL SCAN</span><h2>Drop disaster footage</h2><p>MP4 · MOV · AVI · MKV · WebM</p></div><input id="videoFile" type="file" accept="video/*" hidden></div><div id="bar" class="bar"><i></i></div><div id="videoError" class="error"></div></section>
</div>
<div class="warning"><b>DISASTER / UNVERIFIED:</b> A red box is a model-generated person candidate. A zero-result frame does not prove that an area is clear.</div>
<section id="photoResult" class="photo-result"><article class="sighting"><img id="photoAnnotated" alt="annotated disaster image"><div class="meta"><span>PERSON CANDIDATES <b id="photoPeople">0</b></span><span>MAX CONF <b id="photoConf">—</b></span><span>INFERENCE <b id="photoTime">—</b></span><span>MODEL <b id="photoModel">—</b></span><span id="photoReceipt" class="receipt"></span></div></article></section>
<div id="stats" class="stats"><div class="stat"><label>PERSON CANDIDATES</label><strong id="people">0</strong></div><div class="stat"><label>FRAMES ANALYZED</label><strong id="frames">0</strong></div><div class="stat"><label>SIGHTINGS</label><strong id="count">0</strong></div><div class="stat"><label>PROCESSING FPS</label><strong id="fps">—</strong></div><div class="stat"><label>STATE</label><strong id="state">IDLE</strong></div></div>
<div class="policy"><b>Reporting policy:</b> detections are never gated by consensus. Peer quorum may authorize movement; one blinded or occluded drone must never suppress a person-candidate report.</div><div id="sightings"></div>
</div><script>
const $=id=>document.getElementById(id);let timer=null,seen=0;
function wireDrop(dropId,fileId,callback){const drop=$(dropId),file=$(fileId);drop.onclick=()=>file.click();drop.ondragover=e=>{e.preventDefault();drop.classList.add('hot')};drop.ondragleave=()=>drop.classList.remove('hot');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('hot');if(e.dataTransfer.files[0])callback(e.dataTransfer.files[0])};file.onchange=()=>file.files[0]&&callback(file.files[0])}
function showError(id,message){const box=$(id);box.textContent=message;box.style.display='block'}
async function analyzePhoto(image){$('photoError').style.display='none';$('photoResult').classList.remove('show');const form=new FormData();form.append('image',image);try{const response=await fetch('/api/rescue/image',{method:'POST',body:form});const value=await response.json();if(!response.ok)throw new Error(value.error||`server ${response.status}`);$('photoAnnotated').src=value.annotated_image;$('photoPeople').textContent=value.person_candidate_count;$('photoConf').textContent=value.person_candidate_count?Number(value.maximum_confidence).toFixed(3):'NONE';$('photoTime').textContent=Number(value.elapsed_ms).toFixed(0)+' ms';$('photoModel').textContent=value.model_sha256.slice(0,12)+'…';$('photoReceipt').textContent=value.receipt.backend.toUpperCase()+' · '+value.receipt.digest.slice(0,16)+'…';$('photoResult').classList.add('show');$('photoResult').scrollIntoView({behavior:'smooth',block:'center'})}catch(error){showError('photoError',error.message)}}
async function uploadVideo(video){$('videoError').style.display='none';$('sightings').innerHTML='';seen=0;$('stats').style.display='grid';$('bar').style.display='block';$('bar').querySelector('i').style.width='3%';$('state').textContent='UPLOADING';const form=new FormData();form.append('video',video);try{const response=await fetch('/api/rescue/analyze',{method:'POST',body:form});const value=await response.json();if(!response.ok)throw new Error(value.error||`server ${response.status}`);clearInterval(timer);timer=setInterval(()=>poll(value.job_id),650)}catch(error){showError('videoError',error.message);$('bar').style.display='none';$('state').textContent='ERROR'}}
async function poll(id){try{const response=await fetch(`/api/rescue/jobs/${id}`),job=await response.json();if(!response.ok)throw new Error(job.error);$('people').textContent=job.people_detected||0;$('frames').textContent=job.frames_analyzed||0;$('count').textContent=(job.sightings||[]).length;$('fps').textContent=job.processing_fps||'—';$('state').textContent=(job.state||'').toUpperCase();$('bar').querySelector('i').style.width=`${Math.max(3,job.progress||0)}%`;for(const hit of (job.sightings||[]).slice(seen)){const card=document.createElement('article');card.className='sighting';const image=document.createElement('img');image.src=hit.annotated_frame;image.alt='unverified person candidate';const meta=document.createElement('div');meta.className='meta';meta.textContent=`FRAME ${hit.frame_index} · T+ ${hit.timestamp_seconds}s · CANDIDATES ${hit.person_count} · CONF ${Number(hit.maximum_confidence).toFixed(3)} · ${hit.receipt.backend.toUpperCase()} ${hit.receipt.digest.slice(0,16)}…`;card.append(image,meta);$('sightings').appendChild(card)}seen=(job.sightings||[]).length;if(job.state==='done'||job.state==='error'){clearInterval(timer);$('bar').style.display='none';if(job.state==='error')showError('videoError',job.error||'processing failed')}}catch(error){clearInterval(timer);showError('videoError',error.message)}}
wireDrop('photoDrop','photoFile',analyzePhoto);wireDrop('videoDrop','videoFile',uploadVideo);
</script></body></html>'''


RESCUE_TEMPLATE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VeriSwarm Survivor Console</title><link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"><style>
:root{--bg:#06100f;--panel:#0c1817;--line:#243b38;--text:#eef8f5;--muted:#8da9a3;--mint:#4de8ad;--amber:#ffba57;--red:#ff6b6b}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 12% -10%,#173d34 0,transparent 35%),var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}.shell{max-width:1200px;margin:auto;padding:22px 28px 64px}header{display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--line);padding:8px 0 22px}.brand{font-size:20px;font-weight:800}.brand b{color:var(--mint)}nav{display:flex;gap:6px;margin-left:auto}nav a{padding:7px 10px;border:1px solid var(--line);border-radius:6px;color:var(--muted);text-decoration:none;font:11px JetBrains Mono,monospace}nav a.active{color:var(--mint);border-color:#297d60}.hero{padding:42px 0 24px}.eyebrow{font:600 11px JetBrains Mono,monospace;color:var(--mint);letter-spacing:.12em}.hero h1{font-size:clamp(36px,5vw,64px);letter-spacing:-.055em;line-height:1.02;margin:12px 0}.hero p{font-size:17px;color:var(--muted);max-width:760px}.chips{display:flex;gap:7px;flex-wrap:wrap}.chip{border:1px solid var(--line);padding:6px 9px;border-radius:5px;color:var(--muted);font:10px JetBrains Mono,monospace}.panel{border:1px solid var(--line);background:linear-gradient(155deg,#10221f,#081211);border-radius:11px;overflow:hidden}.drop{margin:16px;border:1px dashed #365b55;border-radius:9px;min-height:260px;display:grid;place-items:center;text-align:center;padding:24px;cursor:pointer}.drop.hot{border-color:var(--mint);background:#0d251f}.drop h2{margin:0 0 8px}.drop p{margin:0;color:var(--muted)}.bar{height:3px;background:#162724;display:none}.bar i{display:block;height:100%;background:var(--mint);width:0;transition:.2s}.stats{display:none;grid-template-columns:repeat(5,1fr);border-top:1px solid var(--line)}.stat{padding:13px 15px;border-right:1px solid var(--line)}.stat label{display:block;color:var(--muted);font:10px JetBrains Mono,monospace}.stat strong{display:block;font:700 20px JetBrains Mono,monospace;margin-top:4px}.policy{border-left:3px solid var(--amber);background:#21180d;color:#e8cca5;padding:13px 15px;margin:18px 0}.sighting{border:1px solid var(--line);background:#0b1716;border-radius:9px;overflow:hidden;margin-top:14px}.sighting img{width:100%;max-height:540px;object-fit:contain;background:#030706;display:block}.meta{display:flex;gap:18px;align-items:center;padding:12px 14px;flex-wrap:wrap}.meta span{font:11px JetBrains Mono,monospace}.receipt{margin-left:auto;color:var(--mint)}.error{display:none;border-left:3px solid var(--red);background:#251012;color:#ffb9b9;padding:12px 14px;margin:16px}@media(max-width:720px){.shell{padding:15px}.stats{grid-template-columns:1fr 1fr}.stat{border-bottom:1px solid var(--line)}header{align-items:flex-start;flex-wrap:wrap}nav{margin-left:0}}
</style></head><body><div class="shell"><header><div class="brand"><b>VERI</b>SWARM / SURVIVOR CONSOLE</div><nav><a class="active" href="/rescue">Rescue analysis</a><a href="/live">Live camera</a><a href="/">Geolocation</a><a href="/hazards">Hazard atlas</a></nav></header><section class="hero"><div class="eyebrow">ON-DEVICE RESCUE PERCEPTION</div><h1>Footage in.<br>Actionable sightings out.</h1><p>Upload disaster footage from any operator laptop. Frames are decoded and analyzed locally on the Jetson; survivor candidates appear with model identity and signed attribution receipts.</p><div class="chips"><span class="chip">MODEL __MODEL_HASH__</span><span class="chip">SIGNER __SIGNER__</span><span class="chip">EVERY __STRIDE__TH FRAME</span></div></section><section class="panel"><div id="drop" class="drop"><div><h2>Drop flood, earthquake or landslide footage</h2><p>MP4 · MOV · AVI · MKV · WebM</p></div><input id="file" type="file" accept="video/*" hidden></div><div id="bar" class="bar"><i></i></div><div id="error" class="error"></div><div id="stats" class="stats"><div class="stat"><label>SURVIVOR BOXES</label><strong id="people">0</strong></div><div class="stat"><label>FRAMES ANALYZED</label><strong id="frames">0</strong></div><div class="stat"><label>SIGHTINGS</label><strong id="count">0</strong></div><div class="stat"><label>PROCESSING FPS</label><strong id="fps">—</strong></div><div class="stat"><label>STATE</label><strong id="state">IDLE</strong></div></div></section><div class="policy"><b>Reporting policy:</b> detections are never gated by consensus. Peer quorum may authorize movement; one blinded or occluded drone must never suppress a survivor sighting.</div><div id="sightings"></div></div><script>
const $=id=>document.getElementById(id),drop=$('drop'),file=$('file'),bar=$('bar'),barFill=bar.querySelector('i'),error=$('error'),sightings=$('sightings');let timer=null,seen=0;drop.onclick=()=>file.click();drop.ondragover=e=>{e.preventDefault();drop.classList.add('hot')};drop.ondragleave=()=>drop.classList.remove('hot');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('hot');if(e.dataTransfer.files[0])upload(e.dataTransfer.files[0])};file.onchange=()=>file.files[0]&&upload(file.files[0]);function fail(message){error.textContent=message;error.style.display='block';bar.style.display='none';$('state').textContent='ERROR'}async function upload(video){error.style.display='none';sightings.innerHTML='';seen=0;$('stats').style.display='grid';bar.style.display='block';barFill.style.width='3%';$('state').textContent='UPLOADING';const form=new FormData();form.append('video',video);try{const response=await fetch('/api/rescue/analyze',{method:'POST',body:form});const value=await response.json();if(!response.ok)throw new Error(value.error||`server ${response.status}`);clearInterval(timer);timer=setInterval(()=>poll(value.job_id),650)}catch(e){fail(e.message)}}async function poll(id){try{const response=await fetch(`/api/rescue/jobs/${id}`),job=await response.json();if(!response.ok)throw new Error(job.error);$('people').textContent=job.people_detected||0;$('frames').textContent=job.frames_analyzed||0;$('count').textContent=(job.sightings||[]).length;$('fps').textContent=job.processing_fps||'—';$('state').textContent=(job.state||'').toUpperCase();barFill.style.width=`${Math.max(3,job.progress||0)}%`;for(const hit of (job.sightings||[]).slice(seen)){const card=document.createElement('article');card.className='sighting';card.innerHTML=`<img src="${hit.annotated_frame}" alt="survivor sighting"><div class="meta"><span>FRAME <b>${hit.frame_index}</b></span><span>T+ <b>${hit.timestamp_seconds}s</b></span><span>PEOPLE <b>${hit.person_count}</b></span><span>CONF <b>${hit.maximum_confidence.toFixed(3)}</b></span><span class="receipt">${hit.receipt.backend.toUpperCase()} · ${hit.receipt.digest.slice(0,16)}…</span></div>`;sightings.appendChild(card)}seen=(job.sightings||[]).length;if(job.state==='done'||job.state==='error'){clearInterval(timer);bar.style.display='none';if(job.state==='error')fail(job.error||'processing failed')}}catch(e){fail(e.message)}}
</script></body></html>'''
