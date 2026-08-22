"""Static ONNX export for the selected rescue-person detector.

The TensorRT engine is intentionally *not* built here.  This module produces a
portable FP32, batch-1 ONNX artifact.  The release FP16 engine must be built and
hashed on the actual Jetson Orin Nano.
"""

from __future__ import annotations

import ast
import math
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .artifact_io import (
    ArtifactIOError,
    atomic_create_json,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)
from .contracts import CandidateSpec, MODEL_ID


ONNX_EXPORT_SCHEMA = "veriswarm.rescue.onnx_export.v2"
ONNX_OPSET = 17


class OnnxExportError(RuntimeError):
    """The selected checkpoint cannot produce a qualified static ONNX file."""


def _copy_exclusive(source: Path, destination: Path) -> None:
    try:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except (OSError, FileExistsError) as error:
        raise OnnxExportError(f"cannot create export checkpoint copy: {error}") from error


def inspect_static_onnx_graph(path: Path) -> dict[str, Any]:
    """Load/check one ONNX graph and return its complete frozen contract."""

    try:
        import onnx
        from onnx import TensorProto
    except ImportError as error:
        raise OnnxExportError("onnx is unavailable for graph verification") from error
    try:
        graph = onnx.load(str(path))
        onnx.checker.check_model(graph, full_check=True)
        inputs = list(graph.graph.input)
        outputs = list(graph.graph.output)
        if len(inputs) != 1 or len(outputs) != 1:
            raise OnnxExportError(
                "ONNX must have exactly one input and exactly one output"
            )
        if inputs[0].name != "images":
            raise OnnxExportError(f"ONNX input must be named 'images', got {inputs[0].name!r}")
        if inputs[0].type.tensor_type.elem_type != TensorProto.FLOAT:
            raise OnnxExportError("ONNX input must be float32")

        def static_dimensions(value_info: Any, field: str) -> list[int]:
            dimensions = value_info.type.tensor_type.shape.dim
            values: list[int] = []
            for dimension in dimensions:
                if getattr(dimension, "dim_param", ""):
                    raise OnnxExportError(f"{field} contains a dynamic dimension")
                value = int(getattr(dimension, "dim_value", 0))
                if value <= 0:
                    raise OnnxExportError(f"{field} contains an unknown dimension")
                values.append(value)
            return values

        input_shape = static_dimensions(inputs[0], "ONNX input")
        if len(input_shape) != 4:
            raise OnnxExportError("ONNX input must be rank-4 NCHW")
        output_shape = static_dimensions(outputs[0], "ONNX output")
        if len(output_shape) != 3 or output_shape[0] != 1:
            raise OnnxExportError(
                "ONNX detect output must be static rank-3 with batch 1"
            )
        output_class_count = output_shape[1] - 4
        if output_class_count != 1:
            raise OnnxExportError(
                "ONNX detect output must encode exactly one class"
            )

        metadata = {item.key: item.value for item in graph.metadata_props}
        if metadata.get("task") != "detect":
            raise OnnxExportError("ONNX metadata task must be 'detect'")
        try:
            names = ast.literal_eval(metadata.get("names", ""))
        except (SyntaxError, ValueError) as error:
            raise OnnxExportError("ONNX class metadata is invalid") from error
        if names != {0: "person_candidate"} or len(names) != output_class_count:
            raise OnnxExportError(
                "ONNX graph must bind exactly one person_candidate class"
            )
        return {
            "input_name": inputs[0].name,
            "input_dtype": "float32",
            "input_shape_nchw": input_shape,
            "output_count": 1,
            "output_batch": output_shape[0],
            "class_count": output_class_count,
            "task": metadata["task"],
        }
    except OnnxExportError:
        raise
    except Exception as error:
        raise OnnxExportError(f"ONNX graph validation failed: {error}") from error


def export_static_onnx(
    *,
    candidate: CandidateSpec,
    source_checkpoint: str | os.PathLike[str],
    expected_checkpoint_sha256: str,
    training_plan_sha256: str,
    confidence_threshold: float,
    report_id: str,
    export_directory: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    yolo_factory: Callable[[str], Any] | None = None,
    graph_inspector: Callable[[Path], dict[str, Any]] = inspect_static_onnx_graph,
) -> Path:
    """Export, inspect, hash, and report one static batch-1 ONNX artifact."""

    if candidate.stage != "full":
        raise OnnxExportError("smoke checkpoints cannot enter the deployment chain")
    if type(confidence_threshold) not in {int, float} or isinstance(confidence_threshold, bool):
        raise OnnxExportError("confidence_threshold must be finite in [0, 1]")
    threshold = float(confidence_threshold)
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise OnnxExportError("confidence_threshold must be finite in [0, 1]")
    if type(report_id) is not str or not report_id or report_id.strip() != report_id:
        raise OnnxExportError("report_id must be a non-empty trimmed string")

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        source = require_path_within_workspace(source_checkpoint, workspace)
        destination = require_path_within_workspace(export_directory, workspace)
    except ArtifactIOError as error:
        raise OnnxExportError(str(error)) from error
    if source.is_symlink() or not source.is_file() or source.suffix.lower() != ".pt":
        raise OnnxExportError(f"source checkpoint must be one regular .pt file: {source}")
    if destination.exists() or os.path.lexists(destination):
        raise OnnxExportError(f"refusing to reuse ONNX export directory: {destination}")
    expected_hash = expected_checkpoint_sha256.lower()
    if len(expected_hash) != 64 or any(character not in "0123456789abcdef" for character in expected_hash):
        raise OnnxExportError("expected_checkpoint_sha256 must be a lowercase SHA-256")
    actual_hash = sha256_file(source)
    if actual_hash != expected_hash:
        raise OnnxExportError("source checkpoint SHA-256 mismatch")
    plan_hash = training_plan_sha256.lower()
    if len(plan_hash) != 64 or any(
        character not in "0123456789abcdef" for character in plan_hash
    ):
        raise OnnxExportError("training_plan_sha256 must be a lowercase SHA-256")

    try:
        destination.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise OnnxExportError(f"cannot create ONNX export directory: {error}") from error
    copied_pt = destination / source.name
    _copy_exclusive(source, copied_pt)
    if sha256_file(copied_pt) != actual_hash:
        raise OnnxExportError("copied checkpoint SHA-256 mismatch")

    if yolo_factory is None:
        try:
            from ultralytics import YOLO
        except ImportError as error:
            raise OnnxExportError("Ultralytics is unavailable") from error
        yolo_factory = YOLO
    model = yolo_factory(str(copied_pt))
    names = getattr(model, "names", None)
    if names != {0: "person_candidate"}:
        raise OnnxExportError(
            "checkpoint class map must be exactly {0: 'person_candidate'}"
        )
    if getattr(model, "task", "detect") != "detect":
        raise OnnxExportError("checkpoint task must be object detection")

    exported = model.export(
        format="onnx",
        imgsz=candidate.imgsz,
        batch=1,
        dynamic=False,
        half=False,
        simplify=False,
        opset=ONNX_OPSET,
        nms=False,
        device="cpu",
    )
    onnx_path = Path(exported).resolve(strict=False)
    if onnx_path.parent != destination or onnx_path.suffix.lower() != ".onnx":
        raise OnnxExportError(f"Ultralytics returned an unexpected ONNX path: {onnx_path}")
    if onnx_path.is_symlink() or not onnx_path.is_file() or onnx_path.stat().st_size <= 0:
        raise OnnxExportError(f"ONNX output is missing or empty: {onnx_path}")
    expected_shape = (1, 3, candidate.imgsz, candidate.imgsz)
    graph_contract = graph_inspector(onnx_path)
    expected_graph_contract = {
        "input_name": "images",
        "input_dtype": "float32",
        "input_shape_nchw": list(expected_shape),
        "output_count": 1,
        "output_batch": 1,
        "class_count": 1,
        "task": "detect",
    }
    if graph_contract != expected_graph_contract:
        raise OnnxExportError(
            "ONNX graph contract mismatch: "
            f"expected {expected_graph_contract}, got {graph_contract}"
        )

    report = {
        "schema": ONNX_EXPORT_SCHEMA,
        "report_id": report_id,
        "candidate": candidate.name,
        "architecture": candidate.architecture,
        "model_id": MODEL_ID,
        "training_plan_sha256": plan_hash,
        "class_map": {"0": "person_candidate"},
        "input_shape_nchw": list(expected_shape),
        "confidence_threshold": threshold,
        "artifacts": {
            "source_pt": {"path": str(copied_pt), "sha256": actual_hash},
            "onnx": {"path": str(onnx_path), "sha256": sha256_file(onnx_path)},
        },
        "export": {
            "format": "ONNX",
            "batch": 1,
            "dynamic": False,
            "opset": ONNX_OPSET,
            "succeeded": True,
        },
        "graph_contract": graph_contract,
        "passed": True,
    }
    try:
        return atomic_create_json(destination / "onnx_export_report.json", report)
    except ArtifactIOError as error:
        raise OnnxExportError(str(error)) from error
