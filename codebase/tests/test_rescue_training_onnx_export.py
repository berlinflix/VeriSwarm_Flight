from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rescue_training.contracts import TrainingPlan
from rescue_training.onnx_export import OnnxExportError, export_static_onnx


PLAN_SHA256 = "a" * 64


def _plan() -> TrainingPlan:
    return TrainingPlan.load(
        Path(__file__).resolve().parents[1] / "config" / "sar_rgb_person_training.json"
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _graph(imgsz: int) -> dict:
    return {
        "input_name": "images",
        "input_dtype": "float32",
        "input_shape_nchw": [1, 3, imgsz, imgsz],
        "output_count": 1,
        "output_batch": 1,
        "class_count": 1,
        "task": "detect",
    }


class FakeModel:
    names = {0: "person_candidate"}
    task = "detect"

    def __init__(self, path: str, calls: list):
        self.path = Path(path)
        self.calls = calls

    def export(self, **kwargs):
        self.calls.append(kwargs)
        target = self.path.with_suffix(".onnx")
        target.write_bytes(b"static-onnx")
        return str(target)


def test_export_is_static_batch_one_and_hashes_pt_and_onnx(tmp_path):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"
    workspace.mkdir()
    repository.mkdir()
    source = workspace / "best.pt"
    source.write_bytes(b"trained-person-model")
    calls = []
    report_path = export_static_onnx(
        candidate=_plan().candidate("yolov8n-960"),
        source_checkpoint=source,
        expected_checkpoint_sha256=_sha(source),
        training_plan_sha256=PLAN_SHA256,
        confidence_threshold=0.37,
        report_id="onnx-yolov8n-960-001",
        export_directory=workspace / "exports" / "yolov8n-960",
        workspace_root=workspace,
        repository_root=repository,
        yolo_factory=lambda path: FakeModel(path, calls),
        graph_inspector=lambda path: _graph(960),
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert calls == [
        {
            "format": "onnx",
            "imgsz": 960,
            "batch": 1,
            "dynamic": False,
            "half": False,
            "simplify": False,
            "opset": 17,
            "nms": False,
            "device": "cpu",
        }
    ]
    assert report["input_shape_nchw"] == [1, 3, 960, 960]
    assert report["candidate"] == "yolov8n-960"
    assert report["architecture"] == "yolov8n.pt"
    assert report["training_plan_sha256"] == PLAN_SHA256
    assert report["graph_contract"] == _graph(960)
    assert report["artifacts"]["source_pt"]["sha256"] == _sha(source)
    assert report["artifacts"]["onnx"]["sha256"] == _sha(
        Path(report["artifacts"]["onnx"]["path"])
    )
    assert report["passed"] is True


def test_dynamic_or_wrong_shape_fails_without_passing_report(tmp_path):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"
    workspace.mkdir()
    repository.mkdir()
    source = workspace / "best.pt"
    source.write_bytes(b"model")
    output = workspace / "exports" / "bad"
    with pytest.raises(OnnxExportError, match="graph contract mismatch"):
        export_static_onnx(
            candidate=_plan().candidate("yolov8n-640"),
            source_checkpoint=source,
            expected_checkpoint_sha256=_sha(source),
            training_plan_sha256=PLAN_SHA256,
            confidence_threshold=0.4,
            report_id="onnx-bad-shape",
            export_directory=output,
            workspace_root=workspace,
            repository_root=repository,
            yolo_factory=lambda path: FakeModel(path, []),
            graph_inspector=lambda path: {
                **_graph(640),
                "input_shape_nchw": [0, 3, 640, 640],
            },
        )
    assert not (output / "onnx_export_report.json").exists()


def test_smoke_or_tampered_checkpoint_cannot_export(tmp_path):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"
    workspace.mkdir()
    repository.mkdir()
    source = workspace / "last.pt"
    source.write_bytes(b"model")
    with pytest.raises(OnnxExportError, match="smoke checkpoints"):
        export_static_onnx(
            candidate=_plan().candidate("yolov8n-640-smoke"),
            source_checkpoint=source,
            expected_checkpoint_sha256=_sha(source),
            training_plan_sha256=PLAN_SHA256,
            confidence_threshold=0.4,
            report_id="smoke",
            export_directory=workspace / "export",
            workspace_root=workspace,
            repository_root=repository,
        )

    with pytest.raises(OnnxExportError, match="SHA-256 mismatch"):
        export_static_onnx(
            candidate=_plan().candidate("yolov8n-640"),
            source_checkpoint=source,
            expected_checkpoint_sha256="0" * 64,
            training_plan_sha256=PLAN_SHA256,
            confidence_threshold=0.4,
            report_id="tampered",
            export_directory=workspace / "export2",
            workspace_root=workspace,
            repository_root=repository,
        )
