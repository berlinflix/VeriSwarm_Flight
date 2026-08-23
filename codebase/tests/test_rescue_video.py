from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from tools.rescue_video import (
    RescueVideoRuntime,
    extract_image_upload,
    extract_video_upload,
    rescue_video_html,
)


class RescueVideoTests(unittest.TestCase):
    def test_extracts_allow_listed_image_and_strips_client_path(self) -> None:
        boundary = "rescue-image-boundary"
        payload = b"png-bytes"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image"; filename="../../flood.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
        value, filename = extract_image_upload(
            body, f"multipart/form-data; boundary={boundary}"
        )
        self.assertEqual(value, payload)
        self.assertEqual(filename, "flood.png")

    def test_extracts_allow_listed_video_and_strips_client_path(self) -> None:
        boundary = "rescue-boundary"
        payload = b"video-bytes"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="video"; filename="../../case.mp4"\r\n'
            "Content-Type: video/mp4\r\n\r\n"
        ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
        value, filename = extract_video_upload(
            body, f"multipart/form-data; boundary={boundary}"
        )
        self.assertEqual(value, payload)
        self.assertEqual(filename, "case.mp4")

    def test_rejects_non_video_extension(self) -> None:
        boundary = "rescue-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="video"; filename="payload.svg"\r\n\r\n'
            "not-video\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        with self.assertRaises(ValueError):
            extract_video_upload(body, f"multipart/form-data; boundary={boundary}")

    def test_page_has_unified_navigation_and_reporting_policy(self) -> None:
        runtime = SimpleNamespace(
            weights_sha256="f" * 64,
            signer=SimpleNamespace(backend="software"),
            stride=10,
        )
        page = rescue_video_html(runtime).decode("utf-8")  # type: ignore[arg-type]
        self.assertIn("/rescue", page)
        self.assertIn("/hazards", page)
        self.assertIn("detections are never gated by consensus", page)
        self.assertIn("ffffffffffffffff", page)
        self.assertIn("/api/rescue/image", page)
        self.assertIn("PERSON_CANDIDATE", page)
        self.assertIn("DISASTER / UNVERIFIED", page)

    def test_runtime_verifies_identity_without_loading_detector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weights = root / "model.pt"
            weights.write_bytes(b"frozen-model-bytes")
            expected = hashlib.sha256(weights.read_bytes()).hexdigest()
            runtime = RescueVideoRuntime(
                weights=weights,
                expected_weights_sha256=expected,
                confidence=0.35,
                person_class=0,
                image_size=640,
                stride=10,
                max_frames=20,
                work_dir=root / "work",
                evidence_dir=root / "evidence",
                optee_ca=None,
                gpu_lock=threading.Lock(),
                before_run=lambda: None,
            )
            self.assertFalse(runtime.status()["model_loaded"])
            self.assertEqual(runtime.status()["backend"], "cold")
            self.assertEqual(runtime.status()["model_sha256"], expected)

    def test_still_image_analysis_returns_signed_unverified_candidate_evidence(self) -> None:
        class FakeDetector:
            backend = "fake-detector"
            sha256 = "d" * 64

            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def detect(self, _frame):
                return [
                    {
                        "class_id": 0,
                        "confidence": 0.873,
                        "xyxy": [10, 20, 100, 180],
                    }
                ]

        fake_cv2 = SimpleNamespace(
            IMREAD_COLOR=1,
            imdecode=lambda _encoded, _mode: np.zeros((48, 64, 3), dtype=np.uint8),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weights = root / "model.pt"
            weights.write_bytes(b"frozen-model-bytes")
            runtime = RescueVideoRuntime(
                weights=weights,
                expected_weights_sha256=hashlib.sha256(weights.read_bytes()).hexdigest(),
                confidence=0.35,
                person_class=0,
                image_size=960,
                stride=10,
                max_frames=20,
                work_dir=root / "work",
                evidence_dir=root / "evidence",
                optee_ca=None,
                gpu_lock=threading.Lock(),
                before_run=lambda: None,
            )
            runtime.signer = SimpleNamespace(
                backend="optee",
                receipt=lambda claim: {
                    "backend": "optee",
                    "digest": hashlib.sha256(str(claim).encode()).hexdigest(),
                    "signature": "signed",
                },
            )
            with (
                patch("tools.rescue_video.cv2", fake_cv2),
                patch("tools.rescue_video.Detector", FakeDetector),
                patch(
                    "tools.rescue_video.annotate_frame",
                    return_value="data:image/jpeg;base64,annotated",
                ),
            ):
                result = runtime.analyze_image(b"image-bytes", "../../collapsed.png")

            self.assertEqual(result["person_candidate_count"], 1)
            self.assertEqual(result["classification"], "DISASTER / UNVERIFIED")
            self.assertEqual(result["source_filename"], "collapsed.png")
            self.assertEqual(result["receipt"]["backend"], "optee")
            self.assertTrue(result["annotated_image"].startswith("data:image/jpeg"))
            evidence = root / "evidence" / result["evidence_file"]
            self.assertTrue(evidence.is_file())
            self.assertNotIn("annotated_image", evidence.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
