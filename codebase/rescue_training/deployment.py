"""Strict deployment lineage and Jetson qualification for ``sar-rgb-person-v1``.

Every release claim is derived from create-once, locally hash-verified evidence.
Cloud/Windows performance is deliberately incapable of satisfying the target
gate: the deployed identity is the exact FP16 TensorRT engine built and executed
on the Jetson Orin Nano 8GB at its displayed 15 W profile.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .artifact_io import (
    ArtifactIOError,
    canonical_json_bytes,
    create_external_json,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)
from .contracts import MODEL_ID
from .visdrone import VisDroneConversionError, validate_visdrone_conversion_report


ONNX_EXPORT_SCHEMA = "veriswarm.rescue.onnx_export.v2"
ENGINE_IDENTITY_SCHEMA = "veriswarm.rescue.tensorrt_engine.v2"
THRESHOLD_SELECTION_SCHEMA = "veriswarm.rescue.threshold_selection.v2"
VALIDATION_ACCURACY_EQUIVALENCE_SCHEMA = (
    "veriswarm.rescue.validation_accuracy_equivalence.v2"
)
UNTOUCHED_TEST_ACCURACY_SCHEMA = "veriswarm.rescue.untouched_test_accuracy.v2"
UNTOUCHED_TEST_USE_LEDGER_SCHEMA = "veriswarm.rescue.untouched_test_use.v1"
JETSON_BENCHMARK_SCHEMA = "veriswarm.rescue.jetson_benchmark.v2"
CANDIDATE_QUALIFICATION_SCHEMA = "veriswarm.rescue.candidate_qualification.v1"
MANDATORY_COMPLETION_SCHEMA = "veriswarm.rescue.mandatory_candidate_completion.v1"
FINAL_SELECTION_SCHEMA = "veriswarm.rescue.final_candidate_selection.v1"
DEPLOYMENT_MANIFEST_SCHEMA = "veriswarm.rescue.model_deployment.v2"

PERSON_CLASS_MAP = {"0": "person_candidate"}
JETSON_HARDWARE = "Jetson Orin Nano 8GB"
FP16 = "FP16"
MINIMUM_AVAILABLE_RAM_BYTES = 1024**3
MAX_SELECTED_FRAME_AGE_P95_MS = 100.0
MAX_SELECTED_FRAME_AGE_MAX_MS = 250.0

MANDATORY_CANDIDATES = frozenset({"yolov8n-640", "yolov8n-960"})
CANDIDATE_CONTRACTS: Mapping[str, tuple[str, tuple[int, int, int, int]]] = MappingProxyType(
    {
        "yolov8n-640": ("yolov8n.pt", (1, 3, 640, 640)),
        "yolov8n-960": ("yolov8n.pt", (1, 3, 960, 960)),
        "yolov8s-640": ("yolov8s.pt", (1, 3, 640, 640)),
    }
)
PIPELINE_STAGES = (
    "camera_decode",
    "preprocess",
    "tensorrt_fp16",
    "nms_tracking",
    "rescue_event_creation",
    "sqlite_outbox_enqueue",
    "model_receipt_bookkeeping",
)
SELECTION_RULE = "accuracy_first_among_jetson_qualified_candidates"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_WATT_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(\d+(?:\.\d+)?)\s*[Ww](?![A-Za-z0-9_])"
)
_QUALIFICATION_ISSUANCE_TOKEN = object()


class DeploymentValidationError(ValueError):
    """Evidence is malformed, inconsistent, or unsafe to trust."""


def _reject_constant(value: str) -> None:
    raise DeploymentValidationError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeploymentValidationError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def _object(value: Any, field: str, fields: set[str] | None = None) -> dict[str, Any]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise DeploymentValidationError(f"{field} must be a JSON object with string keys")
    if fields is not None and set(value) != fields:
        actual = set(value)
        raise DeploymentValidationError(
            f"{field} fields differ: missing={sorted(fields - actual)}, "
            f"unknown={sorted(actual - fields)}"
        )
    return value


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise DeploymentValidationError(f"{field} must be non-empty trimmed text")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise DeploymentValidationError(f"{field} must be boolean")
    return value


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise DeploymentValidationError(f"{field} must be an integer >= {minimum}")
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or type(value) not in {int, float}:
        raise DeploymentValidationError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise DeploymentValidationError(f"{field} is outside its permitted range")
    if maximum is not None and result > maximum:
        raise DeploymentValidationError(f"{field} is outside its permitted range")
    return result


def _hash(value: Any, field: str) -> str:
    digest = _text(value, field)
    if _SHA256.fullmatch(digest) is None:
        raise DeploymentValidationError(f"{field} must be a lowercase SHA-256")
    return digest


def _shape(value: Any, field: str) -> tuple[int, int, int, int]:
    if type(value) is not list or len(value) != 4:
        raise DeploymentValidationError(f"{field} must be a four-item JSON array")
    shape = tuple(
        _integer(item, f"{field}[{index}]", minimum=1)
        for index, item in enumerate(value)
    )
    if shape[0] != 1 or shape[1] != 3 or shape[2] != shape[3] or shape[2] not in {640, 960}:
        raise DeploymentValidationError(f"{field} must be static batch-1 RGB at 640 or 960")
    return shape  # type: ignore[return-value]


def _threshold(value: Any, field: str = "confidence_threshold") -> float:
    return _number(value, field, maximum=1.0)


def _class_map(value: Any, field: str = "class_map") -> None:
    if type(value) is not dict or value != PERSON_CLASS_MAP:
        raise DeploymentValidationError(
            f"{field} must be exactly {{'0': 'person_candidate'}}"
        )


def _candidate_contract(candidate: Any, architecture: Any, shape: Any) -> tuple[str, tuple[int, int, int, int]]:
    name = _text(candidate, "candidate")
    if name not in CANDIDATE_CONTRACTS:
        raise DeploymentValidationError(f"unknown deployment candidate: {name!r}")
    expected_architecture, expected_shape = CANDIDATE_CONTRACTS[name]
    if architecture != expected_architecture:
        raise DeploymentValidationError(f"candidate {name!r} architecture is not frozen")
    parsed_shape = _shape(shape, "input_shape_nchw")
    if parsed_shape != expected_shape:
        raise DeploymentValidationError(f"candidate {name!r} input shape is not frozen")
    return name, parsed_shape


def _regular_file(path_value: Any, field: str, *, suffix: str | None = None) -> Path:
    path = Path(_text(path_value, field)).expanduser()
    if not path.is_absolute():
        raise DeploymentValidationError(f"{field} must be absolute")
    if suffix is not None and path.suffix.lower() != suffix:
        raise DeploymentValidationError(f"{field} must end in {suffix}")
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise DeploymentValidationError(f"{field} must be one non-empty regular file")
    return path.resolve()


def _artifact(value: Any, field: str, *, suffix: str | None = None) -> tuple[Path, str]:
    artifact = _object(value, field, {"path", "sha256"})
    path = _regular_file(artifact["path"], f"{field}.path", suffix=suffix)
    digest = _hash(artifact["sha256"], f"{field}.sha256")
    try:
        actual = sha256_file(path)
    except ArtifactIOError as error:
        raise DeploymentValidationError(str(error)) from error
    if digest != actual:
        raise DeploymentValidationError(f"{field} hash mismatch: expected {digest}, got {actual}")
    return path, digest


def _load_json_file(path_value: str | os.PathLike[str], field: str) -> tuple[Path, str, dict[str, Any]]:
    path = _regular_file(str(path_value), field, suffix=".json")
    try:
        parsed = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except DeploymentValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DeploymentValidationError(f"cannot parse {field}: {error}") from error
    return path, sha256_file(path), _object(parsed, field)


def _report_reference(value: Any, field: str) -> tuple[Path, str, dict[str, Any]]:
    reference = _object(value, field, {"report_id", "path", "sha256"})
    path, actual_hash, report = _load_json_file(reference["path"], f"{field}.path")
    if _hash(reference["sha256"], f"{field}.sha256") != actual_hash:
        raise DeploymentValidationError(f"{field} report hash mismatch")
    report_id = _text(reference["report_id"], f"{field}.report_id")
    if "report_id" in report and report["report_id"] != report_id:
        raise DeploymentValidationError(f"{field} report ID does not match its file")
    return path, actual_hash, report


def _common_identity(report: Mapping[str, Any], prefix: str) -> tuple[tuple[int, int, int, int], float]:
    if report["model_id"] != MODEL_ID:
        raise DeploymentValidationError(f"{prefix}.model_id must be {MODEL_ID!r}")
    _class_map(report["class_map"], f"{prefix}.class_map")
    return (
        _shape(report["input_shape_nchw"], f"{prefix}.input_shape_nchw"),
        _threshold(report["confidence_threshold"], f"{prefix}.confidence_threshold"),
    )


def inspect_onnx_graph_contract(path: Path) -> dict[str, Any]:
    """Inspect the actual ONNX graph; imports ONNX only when called."""

    try:
        import onnx
        from onnx import TensorProto
    except ImportError as error:
        raise DeploymentValidationError("onnx is required for graph inspection") from error
    try:
        model = onnx.load(str(path))
        onnx.checker.check_model(model, full_check=True)
        inputs = list(model.graph.input)
        outputs = list(model.graph.output)
        if len(inputs) != 1 or len(outputs) != 1:
            raise DeploymentValidationError("ONNX must have exactly one input and one output")
        tensor_type = inputs[0].type.tensor_type
        if tensor_type.elem_type != TensorProto.FLOAT:
            raise DeploymentValidationError("ONNX input must be float32")

        def dimensions(value_info: Any, field: str) -> list[int]:
            result: list[int] = []
            for dimension in value_info.type.tensor_type.shape.dim:
                if getattr(dimension, "dim_param", ""):
                    raise DeploymentValidationError(f"{field} contains a dynamic dimension")
                item = int(getattr(dimension, "dim_value", 0))
                if item <= 0:
                    raise DeploymentValidationError(f"{field} contains an unknown dimension")
                result.append(item)
            return result

        input_shape = dimensions(inputs[0], "ONNX input")
        output_shape = dimensions(outputs[0], "ONNX output")
        if len(output_shape) != 3 or output_shape[0] != 1:
            raise DeploymentValidationError("ONNX output must be static rank-3 with batch 1")
        metadata = {item.key: item.value for item in model.metadata_props}
        if metadata.get("task") != "detect":
            raise DeploymentValidationError("ONNX metadata task must be 'detect'")
        try:
            names = ast.literal_eval(metadata.get("names", ""))
        except (SyntaxError, ValueError) as error:
            raise DeploymentValidationError("ONNX class metadata is invalid") from error
        output_class_count = output_shape[1] - 4
        if names != {0: "person_candidate"} or output_class_count != len(names):
            raise DeploymentValidationError("ONNX graph must bind one person_candidate class")
        return {
            "input_name": inputs[0].name,
            "input_dtype": "float32",
            "input_shape_nchw": input_shape,
            "output_count": 1,
            "output_batch": output_shape[0],
            "class_count": output_class_count,
            "task": metadata["task"],
        }
    except DeploymentValidationError:
        raise
    except Exception as error:
        raise DeploymentValidationError(f"ONNX graph inspection failed: {error}") from error


GraphInspector = Callable[[Path], Mapping[str, Any]]
_GRAPH_FIELDS = {
    "input_name", "input_dtype", "input_shape_nchw", "output_count",
    "output_batch", "class_count", "task",
}


def validate_onnx_export_report(
    report: Mapping[str, Any],
    *,
    graph_inspector: GraphInspector = inspect_onnx_graph_contract,
) -> dict[str, Any]:
    """Hash both artifacts and verify the actual static one-class ONNX graph."""

    value = _object(
        report, "onnx report",
        {"schema", "report_id", "candidate", "architecture", "model_id",
         "training_plan_sha256", "class_map", "input_shape_nchw",
         "confidence_threshold", "artifacts", "export", "graph_contract", "passed"},
    )
    if value["schema"] != ONNX_EXPORT_SCHEMA:
        raise DeploymentValidationError("unsupported ONNX export schema")
    _text(value["report_id"], "onnx report.report_id")
    shape, _ = _common_identity(value, "onnx report")
    _candidate_contract(value["candidate"], value["architecture"], value["input_shape_nchw"])
    _hash(value["training_plan_sha256"], "onnx report.training_plan_sha256")
    artifacts = _object(value["artifacts"], "onnx report.artifacts", {"source_pt", "onnx"})
    _artifact(artifacts["source_pt"], "onnx report.artifacts.source_pt", suffix=".pt")
    onnx_path, _ = _artifact(artifacts["onnx"], "onnx report.artifacts.onnx", suffix=".onnx")
    export = _object(
        value["export"], "onnx report.export",
        {"format", "batch", "dynamic", "opset", "succeeded"},
    )
    if not (
        export["format"] == "ONNX"
        and _integer(export["batch"], "onnx report.export.batch", minimum=1) == 1
        and _boolean(export["dynamic"], "onnx report.export.dynamic") is False
        and 13 <= _integer(export["opset"], "onnx report.export.opset", minimum=1) <= 21
        and _boolean(export["succeeded"], "onnx report.export.succeeded")
        and _boolean(value["passed"], "onnx report.passed")
    ):
        raise DeploymentValidationError("ONNX export is not a successful static batch-1 export")
    claimed = _object(value["graph_contract"], "onnx report.graph_contract", _GRAPH_FIELDS)
    inspected = _object(dict(graph_inspector(onnx_path)), "inspected ONNX graph", _GRAPH_FIELDS)
    expected = {
        "input_name": "images", "input_dtype": "float32",
        "input_shape_nchw": list(shape), "output_count": 1, "output_batch": 1,
        "class_count": 1, "task": "detect",
    }
    if claimed != inspected or inspected != expected:
        raise DeploymentValidationError("ONNX report does not match the actual frozen graph contract")
    return dict(value)


def _standalone_15w(display: Any, field: str) -> str:
    text = _text(display, field)
    watts = [float(match) for match in _WATT_TOKEN.findall(text)]
    if watts != [15.0]:
        raise DeploymentValidationError(f"{field} must contain exactly one standalone 15W token")
    return text


def _target_runtime(value: Any, field: str) -> dict[str, Any]:
    runtime = _object(
        value, field,
        {"hardware", "device_tree_model", "power_mode_display", "nv_tegra_release",
         "uname", "architecture", "nvpmodel_output", "package_runtime_output",
         "jetpack", "l4t", "cuda", "cudnn", "tensorrt"},
    )
    if runtime["hardware"] != JETSON_HARDWARE:
        raise DeploymentValidationError("Windows or RTX evidence cannot pass the Jetson gate")
    if "Jetson Orin Nano" not in _text(runtime["device_tree_model"], f"{field}.device_tree_model"):
        raise DeploymentValidationError("device-tree model is not a Jetson Orin Nano")
    _standalone_15w(runtime["power_mode_display"], f"{field}.power_mode_display")
    _standalone_15w(runtime["nvpmodel_output"], f"{field}.nvpmodel_output")
    if not _text(runtime["nv_tegra_release"], f"{field}.nv_tegra_release").startswith("# R"):
        raise DeploymentValidationError("raw nv_tegra_release evidence is invalid")
    if runtime["architecture"] != "aarch64" or "aarch64" not in _text(runtime["uname"], f"{field}.uname"):
        raise DeploymentValidationError("target uname/architecture must be aarch64")
    for name in {"package_runtime_output", "jetpack", "l4t", "cuda", "cudnn", "tensorrt"}:
        _text(runtime[name], f"{field}.{name}")
    return runtime


def _canonical_trtexec_argv(
    *, binary: Path, onnx_path: Path, engine_path: Path, input_name: str,
    shape: tuple[int, int, int, int],
) -> tuple[str, ...]:
    shape_text = "x".join(str(item) for item in shape)
    return (
        str(binary), f"--onnx={onnx_path}", f"--saveEngine={engine_path}", "--fp16",
        f"--minShapes={input_name}:{shape_text}",
        f"--optShapes={input_name}:{shape_text}",
        f"--maxShapes={input_name}:{shape_text}",
    )


def _trtexec_tool(value: Any, field: str) -> tuple[Path, str, str]:
    tool = _object(value, field, {"path", "sha256", "version"})
    path = _regular_file(tool["path"], f"{field}.path")
    if os.name != "nt" and not os.access(path, os.X_OK):
        raise DeploymentValidationError(f"{field}.path is not executable")
    digest = _hash(tool["sha256"], f"{field}.sha256")
    if sha256_file(path) != digest:
        raise DeploymentValidationError(f"{field} binary hash mismatch")
    return path, digest, _text(tool["version"], f"{field}.version")


def validate_engine_identity_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Verify exact target runtime, canonical build argv, and executed engine hash."""

    value = _object(
        report, "engine report",
        {"schema", "report_id", "candidate", "architecture", "model_id",
         "training_plan_sha256", "class_map", "input_shape_nchw",
         "confidence_threshold", "precision", "artifacts", "runtime", "build", "passed"},
    )
    if value["schema"] != ENGINE_IDENTITY_SCHEMA:
        raise DeploymentValidationError("unsupported TensorRT engine schema")
    _text(value["report_id"], "engine report.report_id")
    shape, _ = _common_identity(value, "engine report")
    _candidate_contract(value["candidate"], value["architecture"], value["input_shape_nchw"])
    _hash(value["training_plan_sha256"], "engine report.training_plan_sha256")
    if value["precision"] != FP16:
        raise DeploymentValidationError("deployed TensorRT precision must be FP16")
    artifacts = _object(
        value["artifacts"], "engine report.artifacts",
        {"source_pt", "onnx", "tensorrt_engine"},
    )
    _artifact(artifacts["source_pt"], "engine report.artifacts.source_pt", suffix=".pt")
    onnx_path, _ = _artifact(artifacts["onnx"], "engine report.artifacts.onnx", suffix=".onnx")
    engine_path, _ = _artifact(
        artifacts["tensorrt_engine"], "engine report.artifacts.tensorrt_engine", suffix=".engine"
    )
    _target_runtime(value["runtime"], "engine report.runtime")
    build = _object(
        value["build"], "engine report.build",
        {"built_on_target", "succeeded", "trtexec", "argv", "input_name",
         "min_shape", "opt_shape", "max_shape", "executed_engine_path"},
    )
    if not _boolean(build["built_on_target"], "engine report.build.built_on_target"):
        raise DeploymentValidationError("engine was not built on the actual target")
    if not _boolean(build["succeeded"], "engine report.build.succeeded"):
        raise DeploymentValidationError("engine build failed")
    binary, _, _ = _trtexec_tool(build["trtexec"], "engine report.build.trtexec")
    input_name = _text(build["input_name"], "engine report.build.input_name")
    if input_name != "images":
        raise DeploymentValidationError("TensorRT input name must be 'images'")
    for name in ("min_shape", "opt_shape", "max_shape"):
        if _shape(build[name], f"engine report.build.{name}") != shape:
            raise DeploymentValidationError("TensorRT min/opt/max shapes must equal static ONNX")
    executed = Path(_text(build["executed_engine_path"], "engine report.build.executed_engine_path"))
    if not executed.is_absolute() or executed.resolve() != engine_path:
        raise DeploymentValidationError("executed engine path differs from hashed engine")
    argv = build["argv"]
    if type(argv) is not list or not all(type(item) is str for item in argv):
        raise DeploymentValidationError("engine report.build.argv must be a JSON string array")
    expected_argv = _canonical_trtexec_argv(
        binary=binary, onnx_path=onnx_path, engine_path=engine_path,
        input_name=input_name, shape=shape,
    )
    if tuple(argv) != expected_argv:
        raise DeploymentValidationError(
            "trtexec argv is non-canonical, duplicated, contradictory, or not FP16"
        )
    if not _boolean(value["passed"], "engine report.passed"):
        raise DeploymentValidationError("engine identity report did not pass")
    return dict(value)


_EVALUATOR_REFERENCE_FIELDS = {"path", "sha256"}
_ACCURACY_ARTIFACT_FIELDS = {
    "source_pt_sha256",
    "onnx_sha256",
    "tensorrt_engine_sha256",
}
_PROVENANCE_DOMAINS = ("real_aerial", "synthetic_disaster")


def _evidence_roots(
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> tuple[Path, Path]:
    try:
        workspace = require_external_workspace(workspace_root, repository_root)
    except ArtifactIOError as error:
        raise DeploymentValidationError(str(error)) from error
    return workspace, Path(repository_root).expanduser().resolve(strict=False)


def _load_evaluator_reference(
    value: Any,
    field: str,
    *,
    workspace: Path,
    repository: Path,
) -> tuple[dict[str, str], Any]:
    """Open, recursively rederive, and process-seal one evaluator report."""

    reference = _object(value, field, _EVALUATOR_REFERENCE_FIELDS)
    expected_hash = _hash(reference["sha256"], f"{field}.sha256")
    try:
        path = require_path_within_workspace(reference["path"], workspace)
    except (ArtifactIOError, TypeError) as error:
        raise DeploymentValidationError(f"{field}.path is unsafe") from error
    if _text(reference["path"], f"{field}.path") != str(path):
        raise DeploymentValidationError(f"{field}.path must be canonical and absolute")
    try:
        actual_hash = sha256_file(path)
    except ArtifactIOError as error:
        raise DeploymentValidationError(str(error)) from error
    if actual_hash != expected_hash:
        raise DeploymentValidationError(f"{field} evaluator report hash mismatch")
    try:
        from .evaluation import (
            EvaluationError,
            load_validation_report,
            revalidate_validation_report,
        )

        sealed = load_validation_report(
            path,
            workspace_root=workspace,
            repository_root=repository,
        )
        sealed = revalidate_validation_report(sealed)
    except (EvaluationError, ArtifactIOError) as error:
        raise DeploymentValidationError(
            f"{field} is not evaluator-sealed evidence: {error}"
        ) from error
    if (
        Path(sealed._evidence_report_path).resolve(strict=False) != path
        or sealed._evidence_report_sha256 != expected_hash
    ):
        raise DeploymentValidationError(f"{field} evaluator seal lineage differs")
    return {"path": str(path), "sha256": expected_hash}, sealed


def _metric_point(report: Any, threshold: float, field: str) -> Any:
    matches = [point for point in report.threshold_points if point.threshold == threshold]
    if len(matches) != 1:
        raise DeploymentValidationError(
            f"{field} does not contain the exact frozen confidence threshold"
        )
    return matches[0]


def _point_sort_key(point: Any) -> tuple[float, ...]:
    return (
        -point.recall,
        -point.small_person_recall,
        -point.precision,
        -point.map50,
    )


def _inference_payload(report: Any, field: str) -> dict[str, Any]:
    _, expected_hash, payload = _load_json_file(
        report.inference_evidence.report_path,
        f"{field}.inference_report",
    )
    if expected_hash != report.inference_evidence.report_sha256:
        raise DeploymentValidationError(f"{field} inference report hash changed")
    return payload


def _report_model_source(report: Any, field: str) -> Path:
    inference = _inference_payload(report, field)
    model = _object(
        inference.get("model"),
        f"{field}.inference_report.model",
        {"source_path", "staged_path", "sha256"},
    )
    if _hash(model["sha256"], f"{field}.inference_report.model.sha256") != report.model_sha256:
        raise DeploymentValidationError(f"{field} model hash differs from evaluator report")
    return _regular_file(model["source_path"], f"{field}.model.source_path")


def _validation_report_identity(
    report: Any,
    *,
    candidate: str,
    architecture: str,
    shape: tuple[int, int, int, int],
    plan_hash: str,
    split: str,
    domain: str,
    model_hash: str,
    field: str,
) -> None:
    if (
        report.candidate != candidate
        or report.candidate_stage != "full"
        or report.architecture != architecture
        or tuple(report.input_shape_nchw) != shape
        or report.training_plan_sha256 != plan_hash
        or report.split != split
        or report.data_kind != domain
        or report.model_sha256 != model_hash
    ):
        raise DeploymentValidationError(f"{field} split/domain/checkpoint lineage differs")


def _validate_threshold_selection_evidence(
    report: Mapping[str, Any],
    *,
    workspace: Path,
    repository: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, str]]]:
    value = _object(
        report,
        "threshold report",
        {
            "schema",
            "report_id",
            "candidate",
            "architecture",
            "model_id",
            "training_plan_sha256",
            "class_map",
            "input_shape_nchw",
            "split",
            "model_sha256",
            "confidence_threshold",
            "validation_reports",
            "passed",
        },
    )
    if value["schema"] != THRESHOLD_SELECTION_SCHEMA:
        raise DeploymentValidationError("unsupported threshold-selection schema")
    _text(value["report_id"], "threshold report.report_id")
    candidate, shape = _candidate_contract(
        value["candidate"], value["architecture"], value["input_shape_nchw"]
    )
    _common_identity(value, "threshold report")
    plan_hash = _hash(
        value["training_plan_sha256"], "threshold report.training_plan_sha256"
    )
    model_hash = _hash(value["model_sha256"], "threshold report.model_sha256")
    threshold = _threshold(
        value["confidence_threshold"], "threshold report.confidence_threshold"
    )
    if value["split"] != "val":
        raise DeploymentValidationError(
            "confidence threshold must be frozen from validation only"
        )
    raw_reports = _object(
        value["validation_reports"],
        "threshold report.validation_reports",
        set(_PROVENANCE_DOMAINS),
    )
    references: dict[str, dict[str, str]] = {}
    reports: dict[str, Any] = {}
    for domain in _PROVENANCE_DOMAINS:
        reference, sealed = _load_evaluator_reference(
            raw_reports[domain],
            f"threshold report.validation_reports.{domain}",
            workspace=workspace,
            repository=repository,
        )
        _validation_report_identity(
            sealed,
            candidate=candidate,
            architecture=value["architecture"],
            shape=shape,
            plan_hash=plan_hash,
            split="val",
            domain=domain,
            model_hash=model_hash,
            field=f"threshold report.validation_reports.{domain}",
        )
        references[domain] = reference
        reports[domain] = sealed
    if references["real_aerial"]["sha256"] == references["synthetic_disaster"]["sha256"]:
        raise DeploymentValidationError("threshold domains must use distinct evaluator reports")
    if reports["real_aerial"].dataset_sha256 == reports["synthetic_disaster"].dataset_sha256:
        raise DeploymentValidationError("real and synthetic validation datasets must remain distinct")
    if _report_model_source(
        reports["real_aerial"], "threshold.real_aerial"
    ) != _report_model_source(reports["synthetic_disaster"], "threshold.synthetic_disaster"):
        raise DeploymentValidationError("threshold domains must evaluate the same PT checkpoint")

    synthetic_points = {
        point.threshold: point for point in reports["synthetic_disaster"].threshold_points
    }
    common = [
        (real_point, synthetic_points[real_point.threshold])
        for real_point in reports["real_aerial"].threshold_points
        if real_point.threshold in synthetic_points
        and real_point.passes_accuracy_gates
        and synthetic_points[real_point.threshold].passes_accuracy_gates
    ]
    computed_pass = bool(common)
    if _boolean(value["passed"], "threshold report.passed") != computed_pass:
        raise DeploymentValidationError(
            "threshold passed claim disagrees with evaluator-rederived validation metrics"
        )
    if not common:
        raise DeploymentValidationError("threshold selection did not pass validation gates")
    selected = sorted(
        common,
        key=lambda pair: (
            *_point_sort_key(pair[0]),
            *_point_sort_key(pair[1]),
            pair[0].threshold,
        ),
    )[0][0].threshold
    if threshold != selected:
        raise DeploymentValidationError(
            "confidence threshold is not the deterministic evaluator-derived choice"
        )
    return dict(value), reports, references


def validate_threshold_selection_report(
    report: Mapping[str, Any],
    *,
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Reopen both validation domains and rederive the threshold decision."""

    workspace, repository = _evidence_roots(workspace_root, repository_root)
    value, _, _ = _validate_threshold_selection_evidence(
        report,
        workspace=workspace,
        repository=repository,
    )
    return value


def _same_evaluation_inputs(pt_report: Any, fp16_report: Any, field: str) -> None:
    if (
        pt_report.dataset_id != fp16_report.dataset_id
        or pt_report.dataset_sha256 != fp16_report.dataset_sha256
        or pt_report.inference_evidence.ground_truth_records_path
        != fp16_report.inference_evidence.ground_truth_records_path
        or pt_report.inference_evidence.ground_truth_records_sha256
        != fp16_report.inference_evidence.ground_truth_records_sha256
        or pt_report.inference_evidence.ordered_image_ids_sha256
        != fp16_report.inference_evidence.ordered_image_ids_sha256
    ):
        raise DeploymentValidationError(f"{field} PT and FP16 inputs differ")
    pt_inference = _inference_payload(pt_report, f"{field}.pt")
    fp16_inference = _inference_payload(fp16_report, f"{field}.fp16")
    if pt_inference.get("dataset_manifest") != fp16_inference.get("dataset_manifest"):
        raise DeploymentValidationError(f"{field} PT and FP16 dataset manifests differ")
    if (
        pt_report.inference_evidence.report_sha256
        == fp16_report.inference_evidence.report_sha256
        or pt_report.inference_evidence.prediction_records_path
        == fp16_report.inference_evidence.prediction_records_path
    ):
        raise DeploymentValidationError(
            f"{field} PT and FP16 must have distinct inference executions"
        )


def _accuracy_gate(pt_point: Any, fp16_point: Any) -> bool:
    recall_loss = max(0.0, pt_point.recall - fp16_point.recall)
    map50_loss = max(0.0, pt_point.map50 - fp16_point.map50)
    return bool(
        fp16_point.recall >= 0.75
        and fp16_point.precision >= 0.60
        and fp16_point.map50 >= 0.70
        and fp16_point.small_person_recall >= 0.60
        and recall_loss <= 0.01
        and map50_loss <= 0.01
    )


def _validate_accuracy_evidence(
    report: Mapping[str, Any],
    *,
    expected_schema: str,
    expected_split: str,
    expected_identity: tuple[Any, ...],
    expected_artifacts: Mapping[str, str],
    expected_source_pt_path: Path,
    expected_engine_path: Path,
    expected_threshold_report_path: Path,
    expected_threshold_report_sha256: str,
    expected_selected_candidate_sha256: str | None,
    workspace: Path,
    repository: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """Reopen PT/FP16 evaluator corpora and derive all accuracy gates."""

    fields = {
        "schema",
        "report_id",
        "candidate",
        "architecture",
        "model_id",
        "training_plan_sha256",
        "class_map",
        "input_shape_nchw",
        "confidence_threshold",
        "threshold_selection",
        "split",
        "artifacts",
        "evaluation_reports",
        "passed",
    }
    if expected_selected_candidate_sha256 is not None:
        fields.add("selected_candidate_sha256")
    value = _object(report, "accuracy report", fields)
    if value["schema"] != expected_schema:
        raise DeploymentValidationError("unsupported accuracy-report schema")
    _text(value["report_id"], "accuracy report.report_id")
    candidate, shape = _candidate_contract(
        value["candidate"], value["architecture"], value["input_shape_nchw"]
    )
    _common_identity(value, "accuracy report")
    plan_hash = _hash(
        value["training_plan_sha256"], "accuracy report.training_plan_sha256"
    )
    threshold = _threshold(
        value["confidence_threshold"], "accuracy report.confidence_threshold"
    )
    if value["split"] != expected_split:
        raise DeploymentValidationError(
            f"accuracy evidence split must be exactly {expected_split!r}"
        )
    if expected_selected_candidate_sha256 is not None and _hash(
        value["selected_candidate_sha256"],
        "accuracy report.selected_candidate_sha256",
    ) != _hash(expected_selected_candidate_sha256, "expected_selected_candidate_sha256"):
        raise DeploymentValidationError(
            "untouched-test report does not bind the already selected candidate"
        )

    threshold_reference = _object(
        value["threshold_selection"],
        "accuracy report.threshold_selection",
        {"report_id", "path", "sha256"},
    )
    threshold_path, threshold_hash, threshold_raw = _load_json_file(
        threshold_reference["path"], "accuracy report.threshold_selection.path"
    )
    if (
        threshold_path != expected_threshold_report_path
        or threshold_hash
        != _hash(
            expected_threshold_report_sha256,
            "expected_threshold_report_sha256",
        )
        or _hash(
            threshold_reference["sha256"],
            "accuracy report.threshold_selection.sha256",
        )
        != threshold_hash
    ):
        raise DeploymentValidationError("accuracy threshold-selection file hash mismatch")
    threshold_evidence, threshold_reports, threshold_references = (
        _validate_threshold_selection_evidence(
            threshold_raw,
            workspace=workspace,
            repository=repository,
        )
    )
    if threshold_reference["report_id"] != threshold_evidence["report_id"]:
        raise DeploymentValidationError("accuracy threshold report ID mismatch")

    artifacts = _object(
        value["artifacts"], "accuracy report.artifacts", _ACCURACY_ARTIFACT_FIELDS
    )
    for name in artifacts:
        _hash(artifacts[name], f"accuracy report.artifacts.{name}")
    if artifacts != dict(expected_artifacts):
        raise DeploymentValidationError("accuracy artifacts differ from executed engine lineage")
    actual_identity = (
        value["candidate"],
        value["architecture"],
        value["model_id"],
        value["training_plan_sha256"],
        value["class_map"],
        value["input_shape_nchw"],
        value["confidence_threshold"],
    )
    if actual_identity != expected_identity:
        raise DeploymentValidationError("accuracy identity differs from frozen lineage")
    threshold_identity = (
        threshold_evidence["candidate"],
        threshold_evidence["architecture"],
        threshold_evidence["model_id"],
        threshold_evidence["training_plan_sha256"],
        threshold_evidence["class_map"],
        threshold_evidence["input_shape_nchw"],
        threshold_evidence["confidence_threshold"],
    )
    if threshold_identity != expected_identity:
        raise DeploymentValidationError("accuracy identity differs from threshold lineage")

    raw_evaluations = _object(
        value["evaluation_reports"],
        "accuracy report.evaluation_reports",
        set(_PROVENANCE_DOMAINS),
    )
    evaluation_reports: dict[str, dict[str, Any]] = {}
    computed_pass = True
    for domain in _PROVENANCE_DOMAINS:
        comparison = _object(
            raw_evaluations[domain],
            f"accuracy report.evaluation_reports.{domain}",
            {"pt", "fp16"},
        )
        pt_reference, pt_report = _load_evaluator_reference(
            comparison["pt"],
            f"accuracy report.evaluation_reports.{domain}.pt",
            workspace=workspace,
            repository=repository,
        )
        fp16_reference, fp16_report = _load_evaluator_reference(
            comparison["fp16"],
            f"accuracy report.evaluation_reports.{domain}.fp16",
            workspace=workspace,
            repository=repository,
        )
        if expected_split == "val" and pt_reference != threshold_references[domain]:
            raise DeploymentValidationError(
                f"accuracy {domain} PT report differs from threshold evidence"
            )
        _validation_report_identity(
            pt_report,
            candidate=candidate,
            architecture=value["architecture"],
            shape=shape,
            plan_hash=plan_hash,
            split=expected_split,
            domain=domain,
            model_hash=expected_artifacts["source_pt_sha256"],
            field=f"accuracy report.evaluation_reports.{domain}.pt",
        )
        _validation_report_identity(
            fp16_report,
            candidate=candidate,
            architecture=value["architecture"],
            shape=shape,
            plan_hash=plan_hash,
            split=expected_split,
            domain=domain,
            model_hash=expected_artifacts["tensorrt_engine_sha256"],
            field=f"accuracy report.evaluation_reports.{domain}.fp16",
        )
        if _report_model_source(pt_report, f"accuracy.{domain}.pt") != expected_source_pt_path:
            raise DeploymentValidationError("PT evaluator used a different checkpoint path")
        if _report_model_source(fp16_report, f"accuracy.{domain}.fp16") != expected_engine_path:
            raise DeploymentValidationError("FP16 evaluator used a different engine path")
        _same_evaluation_inputs(pt_report, fp16_report, f"accuracy {domain}")
        pt_point = _metric_point(pt_report, threshold, f"accuracy {domain} PT")
        fp16_point = _metric_point(fp16_report, threshold, f"accuracy {domain} FP16")
        computed_pass = computed_pass and _accuracy_gate(pt_point, fp16_point)
        evaluation_reports[domain] = {"pt": pt_report, "fp16": fp16_report}
    if (
        evaluation_reports["real_aerial"]["pt"].dataset_sha256
        == evaluation_reports["synthetic_disaster"]["pt"].dataset_sha256
    ):
        raise DeploymentValidationError("real and synthetic datasets must remain distinct")
    if _boolean(value["passed"], "accuracy report.passed") != computed_pass:
        raise DeploymentValidationError(
            "accuracy passed claim disagrees with evaluator-rederived FP16 gates"
        )
    return dict(value), evaluation_reports, threshold_evidence


def validate_validation_accuracy_equivalence_report(
    report: Mapping[str, Any],
    *,
    engine_report: Mapping[str, Any],
    threshold_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Revalidate validation-only PT/FP16 inference before selection."""

    workspace, repository = _evidence_roots(workspace_root, repository_root)
    engine = validate_engine_identity_report(engine_report)
    threshold_path, threshold_hash, threshold_raw = _load_json_file(
        threshold_report_path, "threshold_report_path"
    )
    artifacts = engine["artifacts"]
    expected_artifacts = {
        "source_pt_sha256": artifacts["source_pt"]["sha256"],
        "onnx_sha256": artifacts["onnx"]["sha256"],
        "tensorrt_engine_sha256": artifacts["tensorrt_engine"]["sha256"],
    }
    expected_identity = (
        engine["candidate"],
        engine["architecture"],
        engine["model_id"],
        engine["training_plan_sha256"],
        engine["class_map"],
        engine["input_shape_nchw"],
        engine["confidence_threshold"],
    )
    threshold_evidence, _, _ = _validate_threshold_selection_evidence(
        threshold_raw,
        workspace=workspace,
        repository=repository,
    )
    if threshold_evidence["model_sha256"] != expected_artifacts["source_pt_sha256"]:
        raise DeploymentValidationError("threshold selection used a different model")
    value, _, _ = _validate_accuracy_evidence(
        report,
        expected_schema=VALIDATION_ACCURACY_EQUIVALENCE_SCHEMA,
        expected_split="val",
        expected_identity=expected_identity,
        expected_artifacts=expected_artifacts,
        expected_source_pt_path=_regular_file(
            artifacts["source_pt"]["path"], "engine source PT path", suffix=".pt"
        ),
        expected_engine_path=_regular_file(
            artifacts["tensorrt_engine"]["path"],
            "engine artifact path",
            suffix=".engine",
        ),
        expected_threshold_report_path=threshold_path,
        expected_threshold_report_sha256=threshold_hash,
        expected_selected_candidate_sha256=None,
        workspace=workspace,
        repository=repository,
    )
    return value


def _validate_untouched_test_accuracy_evidence(
    report: Mapping[str, Any],
    *,
    selected_candidate: object,
    qualification: "CandidateQualification",
    workspace: Path,
    repository: Path,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
]:
    selected, selected_hash = _selected_payload(selected_candidate)
    if not isinstance(qualification, CandidateQualification):
        raise DeploymentValidationError("test evidence requires selected qualification")
    selected_artifacts = selected["artifacts"]
    if (
        qualification.candidate != selected["candidate"]
        or qualification.architecture != selected["architecture"]
        or list(qualification.input_shape_nchw) != selected["input_shape_nchw"]
        or qualification.training_plan_sha256 != selected["training_plan_sha256"]
        or qualification.confidence_threshold != selected["confidence_threshold"]
        or qualification.source_pt_sha256 != selected_artifacts["source_pt_sha256"]
        or qualification.onnx_sha256 != selected_artifacts["onnx_sha256"]
        or qualification.executed_engine_sha256
        != selected_artifacts["executed_engine_sha256"]
    ):
        raise DeploymentValidationError("selected candidate and qualification differ")
    threshold_reference = _object(
        report.get("threshold_selection") if isinstance(report, Mapping) else None,
        "accuracy report.threshold_selection",
        {"report_id", "path", "sha256"},
    )
    threshold_path, threshold_hash, threshold_raw = _load_json_file(
        threshold_reference["path"], "accuracy report.threshold_selection.path"
    )
    if threshold_hash != qualification.threshold_selection_report_sha256:
        raise DeploymentValidationError("accuracy threshold-selection file hash mismatch")
    _, validation_reports, _ = _validate_threshold_selection_evidence(
        threshold_raw,
        workspace=workspace,
        repository=repository,
    )
    pt_path = _report_model_source(validation_reports["real_aerial"], "selected validation PT")
    if _report_model_source(
        validation_reports["synthetic_disaster"], "selected synthetic validation PT"
    ) != pt_path:
        raise DeploymentValidationError("selected validation domains used different PT paths")
    expected_identity = (
        qualification.candidate,
        qualification.architecture,
        MODEL_ID,
        qualification.training_plan_sha256,
        PERSON_CLASS_MAP,
        list(qualification.input_shape_nchw),
        qualification.confidence_threshold,
    )
    value, test_reports, threshold_evidence = _validate_accuracy_evidence(
        report,
        expected_schema=UNTOUCHED_TEST_ACCURACY_SCHEMA,
        expected_split="test",
        expected_identity=expected_identity,
        expected_artifacts={
            "source_pt_sha256": qualification.source_pt_sha256,
            "onnx_sha256": qualification.onnx_sha256,
            "tensorrt_engine_sha256": qualification.executed_engine_sha256,
        },
        expected_source_pt_path=pt_path,
        expected_engine_path=_regular_file(
            qualification.executed_engine_path,
            "selected qualification engine path",
            suffix=".engine",
        ),
        expected_threshold_report_path=threshold_path,
        expected_threshold_report_sha256=threshold_hash,
        expected_selected_candidate_sha256=selected_hash,
        workspace=workspace,
        repository=repository,
    )
    return value, test_reports, threshold_evidence, validation_reports


def validate_untouched_test_accuracy_report(
    report: Mapping[str, Any],
    *,
    selected_candidate: object,
    qualification: "CandidateQualification",
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Revalidate the sole post-selection test inference without consuming it."""

    workspace, repository = _evidence_roots(workspace_root, repository_root)
    value, _, _, _ = _validate_untouched_test_accuracy_evidence(
        report,
        selected_candidate=selected_candidate,
        qualification=qualification,
        workspace=workspace,
        repository=repository,
    )
    return value


def _load_json_array_file(path_value: Any, field: str) -> tuple[Path, str, list[dict[str, Any]]]:
    path = _regular_file(path_value, field, suffix=".json")
    try:
        parsed = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except DeploymentValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DeploymentValidationError(f"cannot parse {field}: {error}") from error
    if type(parsed) is not list or not parsed or not all(type(item) is dict for item in parsed):
        raise DeploymentValidationError(f"{field} must be a non-empty JSON object array")
    return path, sha256_file(path), parsed


def _ground_truth_image_hashes(report: Any, field: str) -> set[str]:
    _, actual_hash, records = _load_json_array_file(
        report.inference_evidence.ground_truth_records_path,
        f"{field}.ground_truth_records",
    )
    if actual_hash != report.inference_evidence.ground_truth_records_sha256:
        raise DeploymentValidationError(f"{field} ground-truth record hash changed")
    hashes: set[str] = set()
    for index, record in enumerate(records):
        digest = _hash(
            record.get("image_sha256"),
            f"{field}.ground_truth_records[{index}].image_sha256",
        )
        if digest in hashes:
            raise DeploymentValidationError(f"{field} repeats image bytes")
        hashes.add(digest)
    return hashes


def _dataset_manifest_payload(
    report: Any,
    field: str,
    *,
    workspace: Path,
) -> dict[str, Any]:
    inference = _inference_payload(report, field)
    reference = _object(
        inference.get("dataset_manifest"),
        f"{field}.dataset_manifest",
        {"path", "sha256"},
    )
    try:
        manifest_path = require_path_within_workspace(reference["path"], workspace)
    except (ArtifactIOError, TypeError) as error:
        raise DeploymentValidationError(f"{field} dataset manifest path is unsafe") from error
    _, manifest_hash, manifest = _load_json_file(
        manifest_path, f"{field}.dataset_manifest.path"
    )
    if manifest_hash != _hash(reference["sha256"], f"{field}.dataset_manifest.sha256"):
        raise DeploymentValidationError(f"{field} dataset manifest hash changed")
    return manifest


def _empty_split_inventory() -> dict[str, dict[str, set[str]]]:
    return {
        split: {"images": set(), "groups": set()}
        for split in ("train", "val", "test")
    }


def _c2a_source_inventory(
    source: Mapping[str, Any],
    *,
    workspace: Path,
    field: str,
) -> dict[str, dict[str, set[str]]]:
    artifacts = _object(source.get("artifacts"), f"{field}.artifacts")
    try:
        inventory_path = require_path_within_workspace(
            artifacts.get("inventory_path"), workspace
        )
    except (ArtifactIOError, TypeError) as error:
        raise DeploymentValidationError(f"{field} C2A inventory path is unsafe") from error
    expected_hash = _hash(
        artifacts.get("inventory_sha256"), f"{field}.artifacts.inventory_sha256"
    )
    if sha256_file(inventory_path) != expected_hash:
        raise DeploymentValidationError(f"{field} C2A inventory hash mismatch")
    result = _empty_split_inventory()
    required = {
        "schema",
        "derived_split",
        "scene_group_id",
        "source_image_sha256",
        "derived_image_path",
    }
    try:
        lines = inventory_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise DeploymentValidationError(f"cannot read {field} C2A inventory: {error}") from error
    if not lines:
        raise DeploymentValidationError(f"{field} C2A inventory is empty")
    seen_paths: set[Path] = set()
    for index, line in enumerate(lines):
        try:
            record = json.loads(
                line,
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
        except (json.JSONDecodeError, DeploymentValidationError) as error:
            raise DeploymentValidationError(
                f"cannot parse {field} C2A inventory line {index + 1}: {error}"
            ) from error
        record = _object(record, f"{field}.inventory[{index}]")
        if not required <= set(record):
            raise DeploymentValidationError(f"{field} C2A inventory fields are incomplete")
        if record["schema"] != "veriswarm.rescue.c2a_inventory_record.v1":
            raise DeploymentValidationError(f"{field} C2A inventory schema differs")
        split = record["derived_split"]
        if split not in result:
            raise DeploymentValidationError(f"{field} C2A derived split is unsupported")
        digest = _hash(
            record["source_image_sha256"],
            f"{field}.inventory[{index}].source_image_sha256",
        )
        group = _text(
            record["scene_group_id"], f"{field}.inventory[{index}].scene_group_id"
        ).casefold()
        try:
            image_path = require_path_within_workspace(
                record["derived_image_path"], workspace
            )
        except (ArtifactIOError, TypeError) as error:
            raise DeploymentValidationError(
                f"{field} C2A derived image path is unsafe"
            ) from error
        if image_path in seen_paths or sha256_file(image_path) != digest:
            raise DeploymentValidationError(
                f"{field} C2A inventory image is duplicate or changed"
            )
        seen_paths.add(image_path)
        result[split]["images"].add(digest)
        result[split]["groups"].add(group)
    return result


def _visdrone_source_inventory(
    source: Mapping[str, Any],
    *,
    source_path: Path,
    workspace: Path,
    repository: Path,
    field: str,
) -> dict[str, dict[str, set[str]]]:
    try:
        validated = validate_visdrone_conversion_report(
            source_path,
            workspace_root=workspace,
            repository_root=repository,
        )
        output_root = require_path_within_workspace(
            validated.get("output_root"), workspace
        )
    except (ArtifactIOError, TypeError, VisDroneConversionError) as error:
        raise DeploymentValidationError(
            f"{field} VisDrone conversion lineage is invalid: {error}"
        ) from error
    result = _empty_split_inventory()
    supported = {".jpg", ".jpeg", ".png"}
    for split in ("train", "val"):
        image_root = output_root / "images" / split
        try:
            image_root = require_path_within_workspace(image_root, workspace)
        except ArtifactIOError as error:
            raise DeploymentValidationError(f"{field} VisDrone image root is unsafe") from error
        if image_root.is_symlink() or not image_root.is_dir():
            raise DeploymentValidationError(f"{field} VisDrone {split} image root is missing")
        paths = sorted(
            (
                path
                for path in image_root.rglob("*")
                if path.suffix.lower() in supported
            ),
            key=lambda path: path.as_posix().casefold(),
        )
        if not paths:
            raise DeploymentValidationError(f"{field} VisDrone {split} image inventory is empty")
        for path in paths:
            try:
                path = require_path_within_workspace(path, workspace)
            except ArtifactIOError as error:
                raise DeploymentValidationError(
                    f"{field} VisDrone image path is unsafe"
                ) from error
            result[split]["images"].add(sha256_file(path))
        if len(result[split]["images"]) != len(paths):
            raise DeploymentValidationError(
                f"{field} VisDrone {split} contains duplicate image bytes"
            )
    return result


def _available_split_inventory(
    report: Any,
    field: str,
    *,
    workspace: Path,
    repository: Path,
) -> dict[str, dict[str, set[str]]]:
    """Return all source-provenance inventories that can be revalidated now."""

    current_hashes = _ground_truth_image_hashes(report, field)
    result = _empty_split_inventory()
    result[report.split]["images"] = set(current_hashes)
    manifest = _dataset_manifest_payload(report, field, workspace=workspace)
    source_reference = _object(
        manifest.get("source_evidence"),
        f"{field}.source_evidence",
        {"path", "sha256", "schema"},
    )
    try:
        source_path = require_path_within_workspace(source_reference["path"], workspace)
    except (ArtifactIOError, TypeError) as error:
        raise DeploymentValidationError(f"{field} source evidence path is unsafe") from error
    _, source_hash, source = _load_json_file(source_path, f"{field}.source_evidence.path")
    if source_hash != _hash(
        source_reference["sha256"], f"{field}.source_evidence.sha256"
    ):
        raise DeploymentValidationError(f"{field} source evidence hash changed")
    schema = _text(source_reference["schema"], f"{field}.source_evidence.schema")
    if source.get("schema") != schema:
        raise DeploymentValidationError(f"{field} source evidence schema differs")
    if schema == "veriswarm.rescue.c2a_preparation.v1":
        result = _c2a_source_inventory(source, workspace=workspace, field=field)
    elif schema == "veriswarm.rescue.visdrone_conversion.v2":
        result = _visdrone_source_inventory(
            source,
            source_path=source_path,
            workspace=workspace,
            repository=repository,
            field=field,
        )
    elif schema == "veriswarm.rescue.visdrone_conversion.v1":
        raise DeploymentValidationError(
            f"{field} uses legacy VisDrone conversion evidence without archive lineage"
        )
    if result[report.split]["images"] != current_hashes:
        raise DeploymentValidationError(
            f"{field} evaluator images differ from the available source split inventory"
        )
    return result


def _merge_inventory(
    first: dict[str, dict[str, set[str]]],
    second: dict[str, dict[str, set[str]]],
    *,
    field: str,
) -> dict[str, dict[str, set[str]]]:
    result = _empty_split_inventory()
    for split in result:
        for kind in ("images", "groups"):
            left = first[split][kind]
            right = second[split][kind]
            if left and right and left != right:
                raise DeploymentValidationError(
                    f"{field} {split} {kind} inventories disagree"
                )
            result[split][kind] = set(left or right)
    return result


def _assert_no_evaluation_split_overlap(
    validation_reports: Mapping[str, Any],
    test_reports: Mapping[str, Mapping[str, Any]],
    *,
    workspace: Path,
    repository: Path,
) -> str:
    test_fingerprint: dict[str, Any] = {}
    global_images = {split: set() for split in ("train", "val", "test")}
    for domain in _PROVENANCE_DOMAINS:
        validation_inventory = _available_split_inventory(
            validation_reports[domain],
            f"validation.{domain}",
            workspace=workspace,
            repository=repository,
        )
        test_inventory = _available_split_inventory(
            test_reports[domain]["pt"],
            f"test.{domain}",
            workspace=workspace,
            repository=repository,
        )
        inventory = _merge_inventory(
            validation_inventory,
            test_inventory,
            field=domain,
        )
        for first, second in (("train", "val"), ("train", "test"), ("val", "test")):
            if inventory[first]["images"] & inventory[second]["images"]:
                raise DeploymentValidationError(
                    f"{domain} image overlap exists between {first} and {second}"
                )
            if inventory[first]["groups"] & inventory[second]["groups"]:
                raise DeploymentValidationError(
                    f"{domain} group overlap exists between {first} and {second}"
                )
        for split in global_images:
            global_images[split].update(inventory[split]["images"])
        pt_report = test_reports[domain]["pt"]
        test_fingerprint[domain] = {
            "dataset_id": pt_report.dataset_id,
            "dataset_sha256": pt_report.dataset_sha256,
            "ground_truth_records_sha256": (
                pt_report.inference_evidence.ground_truth_records_sha256
            ),
            "ordered_image_ids_sha256": (
                pt_report.inference_evidence.ordered_image_ids_sha256
            ),
            "image_sha256": sorted(inventory["test"]["images"]),
            "group_ids": sorted(inventory["test"]["groups"]),
        }
    for first, second in (("train", "val"), ("train", "test"), ("val", "test")):
        if global_images[first] & global_images[second]:
            raise DeploymentValidationError(
                f"cross-domain image overlap exists between {first} and {second}"
            )
    return _semantic_sha256(test_fingerprint)


def _test_use_ledger_payload(
    *,
    selected_candidate_sha256: str,
    test_accuracy_path: Path,
    test_accuracy_sha256: str,
    test_accuracy: Mapping[str, Any],
    split_fingerprint_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": UNTOUCHED_TEST_USE_LEDGER_SCHEMA,
        "purpose": "sole_post_selection_test_evaluation_and_admission",
        "use_count": 1,
        "split": "test",
        "selected_candidate_sha256": selected_candidate_sha256,
        "candidate": test_accuracy["candidate"],
        "training_plan_sha256": test_accuracy["training_plan_sha256"],
        "confidence_threshold": test_accuracy["confidence_threshold"],
        "test_split_fingerprint_sha256": split_fingerprint_sha256,
        "threshold_selection": dict(test_accuracy["threshold_selection"]),
        "test_accuracy_report": {
            "report_id": test_accuracy["report_id"],
            "path": str(test_accuracy_path),
            "sha256": test_accuracy_sha256,
        },
        "evaluation_reports": test_accuracy["evaluation_reports"],
        "passed": test_accuracy["passed"],
    }


def _test_use_ledger(
    *,
    workspace: Path,
    repository: Path,
    payload: Mapping[str, Any],
    split_fingerprint_sha256: str,
    consume: bool,
) -> tuple[Path, str]:
    ledger_path = (
        workspace
        / "manifests"
        / "untouched-test-use"
        / f"{split_fingerprint_sha256}.json"
    )
    if consume:
        try:
            create_external_json(
                ledger_path,
                payload,
                workspace_root=workspace,
                repository_root=repository,
            )
        except ArtifactIOError as error:
            raise DeploymentValidationError(
                "untouched test split has already been evaluated or admitted"
            ) from error
    _, ledger_hash, stored = _load_json_file(ledger_path, "untouched test use ledger")
    if stored != dict(payload):
        raise DeploymentValidationError("untouched test use ledger differs from live evidence")
    return ledger_path.resolve(), ledger_hash


def _latency(value: Any, field: str) -> dict[str, float]:
    latency = _object(value, field, {"p50", "p95", "p99", "max"})
    parsed = {
        name: _number(latency[name], f"{field}.{name}")
        for name in ("p50", "p95", "p99", "max")
    }
    if not (parsed["p50"] <= parsed["p95"] <= parsed["p99"] <= parsed["max"]):
        raise DeploymentValidationError(f"{field} percentiles are not monotonic")
    return parsed


def _safety_evidence(value: Any, field: str, test_name: str) -> tuple[bool, bool]:
    evidence = _object(
        value, field,
        {"report_id", "path", "sha256", "passed", "network_disabled"},
    )
    _, _, report = _report_reference(
        {name: evidence[name] for name in ("report_id", "path", "sha256")}, field
    )
    report = _object(
        report, f"{field}.report",
        {"schema", "report_id", "test", "result", "passed", "network_disabled"},
    )
    if report["schema"] != "veriswarm.rescue.safety_test.v1" or report["test"] != test_name:
        raise DeploymentValidationError(f"{field} safety-test identity is invalid")
    if report["result"] != "fail_closed":
        raise DeploymentValidationError(f"{field} did not demonstrate fail-closed behavior")
    passed = _boolean(evidence["passed"], f"{field}.passed")
    network_disabled = _boolean(
        evidence["network_disabled"], f"{field}.network_disabled"
    )
    if report["passed"] is not passed or report["network_disabled"] is not network_disabled:
        raise DeploymentValidationError(f"{field} summary differs from raw safety report")
    return passed, network_disabled


_TELEMETRY_FIELDS = {
    "monotonic_ns", "available_ram_bytes", "swap_used_bytes", "process_rss_bytes",
    "cpu_temperature_c", "gpu_temperature_c", "power_w", "cpu_clock_hz",
    "gpu_clock_hz", "throttled",
}


def _inspect_telemetry_log(path: Path) -> list[dict[str, Any]]:
    """Parse strict JSONL telemetry samples used to derive resource summaries."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise DeploymentValidationError(f"cannot read telemetry log: {error}") from error
    if not lines or any(not line.strip() for line in lines):
        raise DeploymentValidationError("telemetry log must contain non-empty JSONL records")
    samples: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        try:
            parsed = json.loads(
                line,
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
        except (json.JSONDecodeError, DeploymentValidationError) as error:
            raise DeploymentValidationError(
                f"telemetry sample {index} is invalid: {error}"
            ) from error
        sample = _object(parsed, f"telemetry sample {index}", _TELEMETRY_FIELDS)
        _integer(sample["monotonic_ns"], f"telemetry sample {index}.monotonic_ns", minimum=1)
        for name in ("available_ram_bytes", "swap_used_bytes", "process_rss_bytes"):
            _integer(sample[name], f"telemetry sample {index}.{name}")
        for name in ("cpu_temperature_c", "gpu_temperature_c", "power_w"):
            _number(sample[name], f"telemetry sample {index}.{name}")
        for name in ("cpu_clock_hz", "gpu_clock_hz"):
            _integer(sample[name], f"telemetry sample {index}.{name}", minimum=1)
        _boolean(sample["throttled"], f"telemetry sample {index}.throttled")
        samples.append(sample)
    timestamps = [sample["monotonic_ns"] for sample in samples]
    if timestamps != sorted(set(timestamps)):
        raise DeploymentValidationError("telemetry sample timestamps must strictly increase")
    return samples


def _inspect_environment_log(path: Path) -> dict[str, Any]:
    """Parse the raw environment capture and validate its exact target fields."""

    try:
        parsed = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DeploymentValidationError) as error:
        raise DeploymentValidationError(f"environment log is invalid: {error}") from error
    return _target_runtime(parsed, "environment log")


def validate_jetson_benchmark_report(
    report: Mapping[str, Any],
    *,
    engine_report: Mapping[str, Any],
    engine_report_sha256: str,
) -> dict[str, Any]:
    """Validate raw-evidence-bound, complete camera-to-event Nano execution."""

    engine = validate_engine_identity_report(engine_report)
    value = _object(
        report, "benchmark report",
        {"schema", "report_id", "candidate", "architecture", "model_id",
         "training_plan_sha256", "class_map", "input_shape_nchw",
         "confidence_threshold", "precision", "engine_identity", "artifacts",
         "target", "pipeline", "measurement", "latency_ms", "reliability",
         "resources", "safety_tests", "passed"},
    )
    if value["schema"] != JETSON_BENCHMARK_SCHEMA:
        raise DeploymentValidationError("unsupported Jetson benchmark schema")
    _text(value["report_id"], "benchmark report.report_id")
    _candidate_contract(value["candidate"], value["architecture"], value["input_shape_nchw"])
    _common_identity(value, "benchmark report")
    _hash(value["training_plan_sha256"], "benchmark report.training_plan_sha256")
    if value["precision"] != FP16:
        raise DeploymentValidationError("benchmark must execute FP16")
    engine_reference = _object(
        value["engine_identity"], "benchmark report.engine_identity",
        {"report_id", "sha256", "executed_engine_path", "executed_engine_sha256"},
    )
    if _hash(
        engine_reference["sha256"], "benchmark report.engine_identity.sha256"
    ) != _hash(engine_report_sha256, "engine_report_sha256"):
        raise DeploymentValidationError("benchmark engine-identity file hash mismatch")
    if engine_reference["report_id"] != engine["report_id"]:
        raise DeploymentValidationError("benchmark engine report ID mismatch")
    executed_path = Path(
        _text(
            engine_reference["executed_engine_path"],
            "benchmark report.engine_identity.executed_engine_path",
        )
    )
    engine_path = Path(engine["build"]["executed_engine_path"]).resolve()
    if not executed_path.is_absolute() or executed_path.resolve() != engine_path:
        raise DeploymentValidationError("benchmark executed a different engine path")
    engine_hash = _hash(
        engine_reference["executed_engine_sha256"],
        "benchmark report.engine_identity.executed_engine_sha256",
    )
    if engine_hash != engine["artifacts"]["tensorrt_engine"]["sha256"]:
        raise DeploymentValidationError("benchmark executed engine hash mismatch")
    if sha256_file(engine_path) != engine_hash:
        raise DeploymentValidationError("executed engine changed after benchmark evidence")

    raw_artifacts = _object(
        value["artifacts"], "benchmark report.artifacts",
        {"raw_capture", "pipeline_log", "telemetry_log", "tegrastats_log", "environment_log"},
    )
    artifact_hashes: set[str] = set()
    artifact_paths: dict[str, Path] = {}
    for name, artifact in raw_artifacts.items():
        artifact_path, digest = _artifact(artifact, f"benchmark report.artifacts.{name}")
        if digest in artifact_hashes:
            raise DeploymentValidationError("benchmark raw artifacts must be distinct")
        artifact_hashes.add(digest)
        artifact_paths[name] = artifact_path
    telemetry = _inspect_telemetry_log(artifact_paths["telemetry_log"])

    target = _target_runtime(value["target"], "benchmark report.target")
    if _inspect_environment_log(artifact_paths["environment_log"]) != target:
        raise DeploymentValidationError("target summary differs from raw environment log")
    if target != engine["runtime"]:
        raise DeploymentValidationError("benchmark runtime differs from engine-build runtime")
    pipeline = _object(
        value["pipeline"], "benchmark report.pipeline",
        {"stages", "newest_frame_policy", "cloud_or_network_dependency"},
    )
    if type(pipeline["stages"]) is not list or tuple(pipeline["stages"]) != PIPELINE_STAGES:
        raise DeploymentValidationError("benchmark does not contain the exact complete pipeline")
    if pipeline["newest_frame_policy"] != "process_newest_drop_stale":
        raise DeploymentValidationError("benchmark must process newest frames and drop stale frames")
    if _boolean(
        pipeline["cloud_or_network_dependency"],
        "benchmark report.pipeline.cloud_or_network_dependency",
    ):
        raise DeploymentValidationError("production pipeline must remain offline-capable")

    measurement = _object(
        value["measurement"], "benchmark report.measurement",
        {"monotonic_start_ns", "monotonic_end_ns", "duration_seconds", "sample_count",
         "warmup_frames", "completed_frames", "stale_replaced_frames",
         "error_dropped_frames", "invalid_frames", "total_input_frames",
         "capture_fps", "fps", "event_count", "outbox_enqueue_count",
         "model_receipt_bookkeeping_count"},
    )
    start_ns = _integer(
        measurement["monotonic_start_ns"], "benchmark report.measurement.monotonic_start_ns",
        minimum=1,
    )
    end_ns = _integer(
        measurement["monotonic_end_ns"], "benchmark report.measurement.monotonic_end_ns",
        minimum=1,
    )
    if end_ns <= start_ns:
        raise DeploymentValidationError("benchmark monotonic interval is not increasing")
    duration = _number(
        measurement["duration_seconds"], "benchmark report.measurement.duration_seconds"
    )
    monotonic_duration = (end_ns - start_ns) / 1_000_000_000
    if not math.isclose(duration, monotonic_duration, abs_tol=0.001):
        raise DeploymentValidationError("benchmark duration differs from monotonic timestamps")
    sample_count = _integer(
        measurement["sample_count"], "benchmark report.measurement.sample_count", minimum=2
    )
    warmups = _integer(
        measurement["warmup_frames"], "benchmark report.measurement.warmup_frames"
    )
    completed = _integer(
        measurement["completed_frames"], "benchmark report.measurement.completed_frames"
    )
    stale_replaced = _integer(
        measurement["stale_replaced_frames"],
        "benchmark report.measurement.stale_replaced_frames",
    )
    error_dropped = _integer(
        measurement["error_dropped_frames"],
        "benchmark report.measurement.error_dropped_frames",
    )
    invalid = _integer(
        measurement["invalid_frames"], "benchmark report.measurement.invalid_frames"
    )
    total = _integer(
        measurement["total_input_frames"], "benchmark report.measurement.total_input_frames",
        minimum=1,
    )
    if total != completed + stale_replaced + error_dropped + invalid:
        raise DeploymentValidationError(
            "total frames must equal completed+stale_replaced+error_dropped+invalid"
        )
    capture_fps = _number(
        measurement["capture_fps"], "benchmark report.measurement.capture_fps"
    )
    derived_capture_fps = total / duration
    if not math.isclose(
        capture_fps, derived_capture_fps, rel_tol=0.01, abs_tol=0.05
    ):
        raise DeploymentValidationError(
            "capture FPS differs from total input frames and duration"
        )
    fps = _number(measurement["fps"], "benchmark report.measurement.fps")
    derived_fps = completed / duration
    if not math.isclose(fps, derived_fps, rel_tol=0.01, abs_tol=0.05):
        raise DeploymentValidationError("FPS differs from completed frames and duration")
    events = _integer(
        measurement["event_count"], "benchmark report.measurement.event_count", minimum=1
    )
    outbox = _integer(
        measurement["outbox_enqueue_count"],
        "benchmark report.measurement.outbox_enqueue_count",
        minimum=1,
    )
    bookkeeping = _integer(
        measurement["model_receipt_bookkeeping_count"],
        "benchmark report.measurement.model_receipt_bookkeeping_count",
        minimum=1,
    )
    if events != outbox or events != bookkeeping or events > completed:
        raise DeploymentValidationError("every emitted event must be enqueued and identity-bookkept once")

    latencies = _object(
        value["latency_ms"], "benchmark report.latency_ms",
        {"selected_frame_age", "tensorrt", "camera_to_event"},
    )
    selected_frame_age = _latency(
        latencies["selected_frame_age"],
        "benchmark report.latency_ms.selected_frame_age",
    )
    tensorrt_latency = _latency(
        latencies["tensorrt"], "benchmark report.latency_ms.tensorrt"
    )
    event_latency = _latency(
        latencies["camera_to_event"], "benchmark report.latency_ms.camera_to_event"
    )
    if any(
        selected_frame_age[name] > event_latency[name]
        for name in ("p50", "p95", "p99", "max")
    ):
        raise DeploymentValidationError(
            "selected-frame age cannot exceed camera-to-event latency"
        )
    reliability = _object(
        value["reliability"], "benchmark report.reliability",
        {"errors", "oom_events", "process_restarts"},
    )
    errors = _integer(reliability["errors"], "benchmark report.reliability.errors")
    oom = _integer(reliability["oom_events"], "benchmark report.reliability.oom_events")
    restarts = _integer(
        reliability["process_restarts"], "benchmark report.reliability.process_restarts"
    )

    resources = _object(
        value["resources"], "benchmark report.resources",
        {"model_load_time_ms", "process_rss_peak_bytes", "min_available_ram_bytes",
         "swap_start_bytes", "swap_peak_bytes", "swap_end_bytes", "telemetry_sample_count",
         "max_cpu_temperature_c", "max_gpu_temperature_c", "power_min_w",
         "power_mean_w", "power_max_w", "cpu_clock_min_hz", "gpu_clock_min_hz",
         "thermal_throttling"},
    )
    _number(resources["model_load_time_ms"], "benchmark report.resources.model_load_time_ms")
    _integer(
        resources["process_rss_peak_bytes"],
        "benchmark report.resources.process_rss_peak_bytes",
        minimum=1,
    )
    minimum_ram = _integer(
        resources["min_available_ram_bytes"],
        "benchmark report.resources.min_available_ram_bytes",
    )
    swap_start = _integer(
        resources["swap_start_bytes"], "benchmark report.resources.swap_start_bytes"
    )
    swap_peak = _integer(
        resources["swap_peak_bytes"], "benchmark report.resources.swap_peak_bytes"
    )
    swap_end = _integer(
        resources["swap_end_bytes"], "benchmark report.resources.swap_end_bytes"
    )
    telemetry_samples = _integer(
        resources["telemetry_sample_count"],
        "benchmark report.resources.telemetry_sample_count",
        minimum=2,
    )
    if telemetry_samples != sample_count:
        raise DeploymentValidationError("resource sample count differs from measured sample count")
    if telemetry_samples != len(telemetry):
        raise DeploymentValidationError("resource sample count differs from raw telemetry")
    telemetry_times = [sample["monotonic_ns"] for sample in telemetry]
    if telemetry_times[0] < start_ns or telemetry_times[-1] > end_ns:
        raise DeploymentValidationError("telemetry samples fall outside measured interval")
    max_cpu_temperature = _number(
        resources["max_cpu_temperature_c"],
        "benchmark report.resources.max_cpu_temperature_c",
    )
    max_gpu_temperature = _number(
        resources["max_gpu_temperature_c"],
        "benchmark report.resources.max_gpu_temperature_c",
    )
    power_min = _number(resources["power_min_w"], "benchmark report.resources.power_min_w")
    power_mean = _number(resources["power_mean_w"], "benchmark report.resources.power_mean_w")
    power_max = _number(resources["power_max_w"], "benchmark report.resources.power_max_w")
    if not power_min <= power_mean <= power_max:
        raise DeploymentValidationError("power samples are not ordered min<=mean<=max")
    cpu_clock_min = _integer(
        resources["cpu_clock_min_hz"], "benchmark report.resources.cpu_clock_min_hz",
        minimum=1,
    )
    gpu_clock_min = _integer(
        resources["gpu_clock_min_hz"], "benchmark report.resources.gpu_clock_min_hz",
        minimum=1,
    )
    throttled = _boolean(
        resources["thermal_throttling"], "benchmark report.resources.thermal_throttling"
    )
    derived_resources = {
        "process_rss_peak_bytes": max(sample["process_rss_bytes"] for sample in telemetry),
        "min_available_ram_bytes": min(sample["available_ram_bytes"] for sample in telemetry),
        "swap_start_bytes": telemetry[0]["swap_used_bytes"],
        "swap_peak_bytes": max(sample["swap_used_bytes"] for sample in telemetry),
        "swap_end_bytes": telemetry[-1]["swap_used_bytes"],
        "max_cpu_temperature_c": max(sample["cpu_temperature_c"] for sample in telemetry),
        "max_gpu_temperature_c": max(sample["gpu_temperature_c"] for sample in telemetry),
        "power_min_w": min(sample["power_w"] for sample in telemetry),
        "power_mean_w": sum(sample["power_w"] for sample in telemetry) / len(telemetry),
        "power_max_w": max(sample["power_w"] for sample in telemetry),
        "cpu_clock_min_hz": min(sample["cpu_clock_hz"] for sample in telemetry),
        "gpu_clock_min_hz": min(sample["gpu_clock_hz"] for sample in telemetry),
        "thermal_throttling": any(sample["throttled"] for sample in telemetry),
    }
    claimed_resources = {
        "process_rss_peak_bytes": resources["process_rss_peak_bytes"],
        "min_available_ram_bytes": minimum_ram,
        "swap_start_bytes": swap_start,
        "swap_peak_bytes": swap_peak,
        "swap_end_bytes": swap_end,
        "max_cpu_temperature_c": max_cpu_temperature,
        "max_gpu_temperature_c": max_gpu_temperature,
        "power_min_w": power_min,
        "power_mean_w": power_mean,
        "power_max_w": power_max,
        "cpu_clock_min_hz": cpu_clock_min,
        "gpu_clock_min_hz": gpu_clock_min,
        "thermal_throttling": throttled,
    }
    for name, derived in derived_resources.items():
        claimed = claimed_resources[name]
        if isinstance(derived, float):
            matches = math.isclose(float(claimed), derived, rel_tol=1e-9, abs_tol=1e-9)
        else:
            matches = claimed == derived
        if not matches:
            raise DeploymentValidationError(
                f"resource summary {name} differs from raw telemetry samples"
            )
    safety = _object(
        value["safety_tests"], "benchmark report.safety_tests",
        {"offline_restart", "camera_timeout", "corrupt_frame"},
    )
    offline_passed, offline_network_disabled = _safety_evidence(
        safety["offline_restart"], "benchmark report.safety_tests.offline_restart",
        "offline_restart",
    )
    timeout_passed, _ = _safety_evidence(
        safety["camera_timeout"], "benchmark report.safety_tests.camera_timeout",
        "camera_timeout",
    )
    corrupt_passed, _ = _safety_evidence(
        safety["corrupt_frame"], "benchmark report.safety_tests.corrupt_frame",
        "corrupt_frame",
    )
    # Intentional replacement of an older pending frame is the required
    # newest-frame policy, not a reliability failure.  Only frames lost to an
    # invalid/corrupt input or an actual processing/capture error count here.
    bad_ratio = (error_dropped + invalid) / total

    identity = (
        value["candidate"], value["architecture"], value["model_id"],
        value["training_plan_sha256"], value["class_map"], value["input_shape_nchw"],
        value["confidence_threshold"],
    )
    engine_identity = (
        engine["candidate"], engine["architecture"], engine["model_id"],
        engine["training_plan_sha256"], engine["class_map"], engine["input_shape_nchw"],
        engine["confidence_threshold"],
    )
    if identity != engine_identity:
        raise DeploymentValidationError("benchmark identity differs from engine identity")

    computed_pass = (
        duration >= 900.0 and warmups >= 50 and completed >= 4500
        and derived_fps >= 5.0 and tensorrt_latency["p95"] <= 180.0
        and selected_frame_age["p95"] <= MAX_SELECTED_FRAME_AGE_P95_MS
        and selected_frame_age["max"] <= MAX_SELECTED_FRAME_AGE_MAX_MS
        and event_latency["p95"] <= 250.0 and bad_ratio <= 0.01
        and errors == 0 and oom == 0 and restarts == 0
        and minimum_ram >= MINIMUM_AVAILABLE_RAM_BYTES
        and swap_peak == swap_start == swap_end and not throttled
        and offline_passed and offline_network_disabled and timeout_passed and corrupt_passed
    )
    if _boolean(value["passed"], "benchmark report.passed") != computed_pass:
        raise DeploymentValidationError("benchmark passed claim disagrees with frozen gates")
    return dict(value)


def _validate_training_completion(
    report: Mapping[str, Any],
    *,
    report_path: Path,
    candidate: str,
    architecture: str,
    shape: tuple[int, int, int, int],
    training_plan_sha256: str,
) -> str:
    """Re-derive a completed full run from the runner's immutable artifacts.

    This is intentionally more than a shape check.  A deployment checkpoint is
    admitted only when the complete production report can be replayed against
    the raw dataset, cloud, base-weight, batch, CSV, YAML, and checkpoint
    evidence written by :func:`ultralytics_runner.run_training`.
    """

    from .batch_gate import BatchDecisionError, authorized_batch, load_batch_decision
    from .cloud_environment import (
        CONTROL_PLANE_TRUST_LEVEL,
        DURABLE_RELOAD_TRUST_LEVEL,
        CloudEnvironmentError,
        load_cloud_environment,
    )
    from .contracts import CandidateSpec, FROZEN_RUNTIME, TrainingContractError
    from .review_gate import (
        DatasetQualificationError,
        validate_automated_dataset_qualification,
    )
    from .ultralytics_runner import (
        TrainingExecutionError,
        _read_final_metrics,
        _verify_effective_arguments,
        frozen_train_arguments,
        verify_trusted_base_checkpoint,
    )

    top_level_fields = {
        "schema", "status", "accepted_by_human", "model_id",
        "training_plan_sha256", "source", "class_map", "candidate",
        "dataset", "base_checkpoint", "runtime", "cloud_environment",
        "batch_decision", "arguments", "effective_arguments",
        "resolved_batch_size", "started_at_utc", "finished_at_utc",
        "duration_seconds", "cuda_peak_bytes", "metrics", "smoke_gate",
        "artifacts",
    }
    value = _object(report, "training completion report", top_level_fields)
    if value["schema"] != "veriswarm.rescue.training_run.v1":
        raise DeploymentValidationError("unsupported training completion schema")
    if value["status"] != "completed":
        raise DeploymentValidationError("training candidate is not completed")
    if value["accepted_by_human"] is not False:
        raise DeploymentValidationError(
            "training completion must use automated dataset qualification"
        )
    if value["model_id"] != MODEL_ID:
        raise DeploymentValidationError("training completion model ID mismatch")
    plan_hash = _hash(
        value["training_plan_sha256"], "training completion training_plan_sha256"
    )
    if plan_hash != training_plan_sha256:
        raise DeploymentValidationError("training completion plan hash mismatch")
    _class_map(value["class_map"], "training completion class_map")

    source = _object(
        value["source"], "training completion source", {"commit", "branch", "clean"}
    )
    commit = _text(source["commit"], "training completion source.commit")
    if len(commit) != 40 or commit != commit.lower() or any(
        character not in "0123456789abcdef" for character in commit
    ):
        raise DeploymentValidationError("training completion source commit is invalid")
    _text(source["branch"], "training completion source.branch")
    if source["clean"] is not True:
        raise DeploymentValidationError("training completion source was not clean")

    spec_value = _object(
        value["candidate"],
        "training completion candidate",
        {"name", "architecture", "imgsz", "epochs", "batch", "required", "stage"},
    )
    try:
        spec = CandidateSpec.from_dict(spec_value)
    except TrainingContractError as error:
        raise DeploymentValidationError(str(error)) from error
    expected_required = candidate != "yolov8s-640"
    if (
        spec.name != candidate
        or spec.architecture != architecture
        or spec.imgsz != shape[2]
        or spec.epochs != 40
        or spec.required is not expected_required
        or spec.stage != "full"
    ):
        raise DeploymentValidationError("training completion candidate contract mismatch")

    artifacts = _object(
        value["artifacts"],
        "training completion artifacts",
        {"args_yaml", "results_csv", "best_pt", "last_pt"},
    )
    args_path, _ = _artifact(
        artifacts["args_yaml"], "training completion artifacts.args_yaml", suffix=".yaml"
    )
    results_path, _ = _artifact(
        artifacts["results_csv"], "training completion artifacts.results_csv", suffix=".csv"
    )
    best_path, best_hash = _artifact(
        artifacts["best_pt"], "training completion artifacts.best_pt", suffix=".pt"
    )
    last_path, _ = _artifact(
        artifacts["last_pt"], "training completion artifacts.last_pt", suffix=".pt"
    )
    run_directory = args_path.parent
    expected_artifact_paths = {
        "args_yaml": run_directory / "args.yaml",
        "results_csv": run_directory / "results.csv",
        "best_pt": run_directory / "weights" / "best.pt",
        "last_pt": run_directory / "weights" / "last.pt",
    }
    actual_artifact_paths = {
        "args_yaml": args_path,
        "results_csv": results_path,
        "best_pt": best_path,
        "last_pt": last_path,
    }
    if any(
        actual_artifact_paths[name] != expected.resolve(strict=False)
        for name, expected in expected_artifact_paths.items()
    ):
        raise DeploymentValidationError(
            "training completion artifacts are not in the canonical Ultralytics run layout"
        )
    expected_report_path = (run_directory / "veriswarm_training_run.json").resolve(
        strict=False
    )
    if report_path.resolve(strict=False) != expected_report_path:
        raise DeploymentValidationError(
            "training completion report is not in its canonical run directory"
        )

    def validate_checkpoint_container(path: Path, field: str) -> None:
        """Inspect the Torch ZIP container without deserializing its pickle."""

        if path.stat().st_size < 1024 or not zipfile.is_zipfile(path):
            raise DeploymentValidationError(
                f"{field} is not a non-trivial Torch checkpoint container"
            )
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                names = [item.filename for item in infos if not item.is_dir()]
                if not names or any(
                    item.flag_bits & 0x1
                    or item.file_size < 0
                    or item.file_size > 2 * 1024**3
                    or item.compress_size > 2 * 1024**3
                    for item in infos
                ):
                    raise DeploymentValidationError(
                        f"{field} contains an unsafe Torch ZIP member"
                    )
                normalized = [Path(name.replace("\\", "/")).parts for name in names]
                if any(
                    not parts or parts[0] in {"", ".", ".."} or ".." in parts
                    for parts in normalized
                ):
                    raise DeploymentValidationError(
                        f"{field} contains an unsafe Torch ZIP path"
                    )
                roots = {parts[0] for parts in normalized}
                if len(roots) != 1:
                    raise DeploymentValidationError(
                        f"{field} is not one Torch checkpoint archive"
                    )
                root = next(iter(roots))
                required = {
                    f"{root}/data.pkl",
                    f"{root}/version",
                    f"{root}/byteorder",
                }
                if not required <= set(names) or not any(
                    name.startswith(f"{root}/data/") for name in names
                ):
                    raise DeploymentValidationError(
                        f"{field} lacks required Torch checkpoint members"
                    )
                if archive.getinfo(f"{root}/data.pkl").file_size <= 0:
                    raise DeploymentValidationError(f"{field} has an empty data.pkl")
        except (OSError, zipfile.BadZipFile, KeyError) as error:
            raise DeploymentValidationError(
                f"cannot inspect {field} safely: {error}"
            ) from error

    validate_checkpoint_container(best_path, "training completion artifacts.best_pt")
    validate_checkpoint_container(last_path, "training completion artifacts.last_pt")

    dataset = _object(
        value["dataset"],
        "training completion dataset",
        {"yaml_path", "yaml_sha256", "audit", "automated_qualification"},
    )
    yaml_path = _regular_file(
        dataset["yaml_path"], "training completion dataset.yaml_path", suffix=".yaml"
    )
    yaml_hash = _hash(
        dataset["yaml_sha256"], "training completion dataset.yaml_sha256"
    )
    if sha256_file(yaml_path) != yaml_hash:
        raise DeploymentValidationError("training completion dataset YAML hash mismatch")

    audit = _object(
        dataset["audit"],
        "training completion dataset.audit",
        {
            "schema", "report_path", "report_sha256", "montage_path",
            "montage_sha256", "train_images", "val_images", "class_map",
            "dataset_root", "dataset_yaml_path", "dataset_yaml_sha256",
            "sample_set_sha256", "authoritative_integrity",
            "authoritative_integrity_sha256", "verified_at_utc",
            "pre_training_authoritative_integrity_sha256",
            "post_training_authoritative_integrity_sha256",
            "post_training_verified_at_utc", "integrity_unchanged",
        },
    )
    if audit["schema"] != "veriswarm.dataset_audit.v1":
        raise DeploymentValidationError("unsupported training dataset audit schema")
    _class_map(audit["class_map"], "training completion dataset.audit.class_map")
    if audit["train_images"] != 6471 or audit["val_images"] != 548:
        raise DeploymentValidationError("training dataset audit counts are not official")
    audit_path, audit_hash, audit_report = _load_json_file(
        audit["report_path"], "training completion dataset.audit.report_path"
    )
    if _hash(audit["report_sha256"], "training completion dataset.audit.report_sha256") != audit_hash:
        raise DeploymentValidationError("training dataset audit report hash mismatch")
    montage_path = _regular_file(
        audit["montage_path"], "training completion dataset.audit.montage_path"
    )
    montage_hash = _hash(
        audit["montage_sha256"], "training completion dataset.audit.montage_sha256"
    )
    if sha256_file(montage_path) != montage_hash:
        raise DeploymentValidationError("training dataset montage hash mismatch")
    if Path(_text(audit["dataset_yaml_path"], "training completion dataset.audit.dataset_yaml_path")).resolve(strict=False) != yaml_path:
        raise DeploymentValidationError("training dataset audit names a different YAML")
    if _hash(audit["dataset_yaml_sha256"], "training completion dataset.audit.dataset_yaml_sha256") != yaml_hash:
        raise DeploymentValidationError("training dataset audit YAML hash mismatch")
    sample_hashes = _object(
        audit["sample_set_sha256"],
        "training completion dataset.audit.sample_set_sha256",
        {"train", "val"},
    )
    sample_hashes = {
        split: _hash(digest, f"training completion dataset.audit.sample_set_sha256.{split}")
        for split, digest in sample_hashes.items()
    }

    audit_fields = {
        "schema", "generated_at_utc", "passed", "class_map", "splits",
        "totals", "duplicates", "cross_split_leakage", "errors", "warnings",
        "montage",
    }
    raw_audit = _object(audit_report, "training dataset audit report", audit_fields)
    if raw_audit["schema"] != "veriswarm.dataset_audit.v1" or raw_audit["passed"] is not True:
        raise DeploymentValidationError("training dataset audit did not pass")
    _class_map(raw_audit["class_map"], "training dataset audit report.class_map")
    raw_splits = _object(raw_audit["splits"], "training dataset audit report.splits", {"train", "val"})
    dataset_root = Path(
        _text(audit["dataset_root"], "training completion dataset.audit.dataset_root")
    ).resolve(strict=False)
    for split, expected_count in (("train", 6471), ("val", 548)):
        split_report = _object(
            raw_splits[split],
            f"training dataset audit report.splits.{split}",
            {
                "image_root", "label_root", "images", "labels", "paired",
                "valid_samples", "boxes", "class_box_counts",
                "empty_label_samples", "missing_labels", "orphan_labels",
                "sample_set_sha256",
            },
        )
        for count_field in ("images", "labels", "paired", "valid_samples"):
            if _integer(
                split_report[count_field],
                f"training dataset audit {split}.{count_field}",
            ) != expected_count:
                raise DeploymentValidationError(
                    f"training dataset audit {split}.{count_field} count mismatch"
                )
        empty_samples = _integer(
            split_report["empty_label_samples"],
            f"training dataset audit {split}.empty_label_samples",
        )
        boxes = _integer(
            split_report["boxes"], f"training dataset audit {split}.boxes", minimum=1
        )
        class_counts = _object(
            split_report["class_box_counts"],
            f"training dataset audit {split}.class_box_counts",
            {"0"},
        )
        if (
            empty_samples > expected_count
            or _integer(
                split_report["missing_labels"],
                f"training dataset audit {split}.missing_labels",
            ) != 0
            or _integer(
                split_report["orphan_labels"],
                f"training dataset audit {split}.orphan_labels",
            ) != 0
            or _integer(
                class_counts["0"],
                f"training dataset audit {split}.class_box_counts.0",
            ) != boxes
        ):
            raise DeploymentValidationError(f"training dataset audit {split} pairing failed")
        if _hash(split_report["sample_set_sha256"], f"training dataset audit {split} sample hash") != sample_hashes[split]:
            raise DeploymentValidationError(f"training dataset audit {split} sample hash mismatch")
        if Path(split_report["image_root"]).resolve(strict=False) != dataset_root / "images" / split:
            raise DeploymentValidationError(f"training dataset audit {split} image root mismatch")
        if Path(split_report["label_root"]).resolve(strict=False) != dataset_root / "labels" / split:
            raise DeploymentValidationError(f"training dataset audit {split} label root mismatch")
    if yaml_path != (dataset_root / "dataset.yaml").resolve(strict=False):
        raise DeploymentValidationError("training dataset YAML is outside the audited dataset")
    totals = _object(
        raw_audit["totals"],
        "training dataset audit report.totals",
        {
            "valid_samples", "boxes", "class_box_counts", "duplicate_groups",
            "cross_split_leakage_groups", "errors", "warnings",
        },
    )
    total_class_counts = _object(
        totals["class_box_counts"],
        "training dataset audit report.totals.class_box_counts",
        {"0"},
    )
    if (
        _integer(totals["valid_samples"], "training dataset audit totals.valid_samples")
        != 7019
        or _integer(totals["boxes"], "training dataset audit totals.boxes", minimum=1)
        != _integer(
            total_class_counts["0"],
            "training dataset audit totals.class_box_counts.0",
            minimum=1,
        )
        or _integer(totals["errors"], "training dataset audit totals.errors") != 0
        or _integer(
            totals["cross_split_leakage_groups"],
            "training dataset audit totals.cross_split_leakage_groups",
        ) != 0
        or any(
            type(raw_audit[field]) is not list
            for field in (
                "duplicates", "cross_split_leakage", "errors", "warnings"
            )
        )
        or raw_audit["errors"] != []
        or raw_audit["cross_split_leakage"] != []
    ):
        raise DeploymentValidationError("training dataset audit contains errors or leakage")
    montage = _object(
        raw_audit["montage"],
        "training dataset audit report.montage",
        {
            "requested_samples", "rendered_samples", "samples",
            "sample_set_sha256", "path", "sha256",
        },
    )
    if montage["requested_samples"] != 100 or montage["rendered_samples"] != 100:
        raise DeploymentValidationError("training dataset audit montage count mismatch")
    montage_samples = montage["samples"]
    if type(montage_samples) is not list or len(montage_samples) != 100:
        raise DeploymentValidationError("training dataset montage membership is incomplete")
    for index, sample_value in enumerate(montage_samples):
        sample = _object(
            sample_value,
            f"training dataset audit montage.samples[{index}]",
            {"split", "stem", "image_sha256", "label_sha256"},
        )
        if sample["split"] not in {"train", "val"}:
            raise DeploymentValidationError("training dataset montage split is invalid")
        _text(sample["stem"], f"training dataset audit montage.samples[{index}].stem")
        _hash(
            sample["image_sha256"],
            f"training dataset audit montage.samples[{index}].image_sha256",
        )
        _hash(
            sample["label_sha256"],
            f"training dataset audit montage.samples[{index}].label_sha256",
        )
    computed_membership_hash = hashlib.sha256(
        json.dumps(
            montage_samples,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if _hash(
        montage["sample_set_sha256"],
        "training dataset audit report.montage.sample_set_sha256",
    ) != computed_membership_hash:
        raise DeploymentValidationError("training dataset montage membership hash mismatch")
    relative_montage = Path(_text(montage["path"], "training dataset audit report.montage.path"))
    if relative_montage.is_absolute() or (audit_path.parent / relative_montage).resolve(strict=False) != montage_path:
        raise DeploymentValidationError("training dataset audit montage path mismatch")
    if _hash(montage["sha256"], "training dataset audit report.montage.sha256") != montage_hash:
        raise DeploymentValidationError("training dataset audit montage hash mismatch")

    authoritative = _object(
        audit["authoritative_integrity"],
        "training completion dataset.audit.authoritative_integrity",
        {
            "class_map", "splits", "totals", "duplicates",
            "cross_split_leakage", "errors", "warnings", "montage",
        },
    )
    expected_authoritative = {
        "class_map": raw_audit["class_map"],
        "splits": raw_audit["splits"],
        "totals": raw_audit["totals"],
        "duplicates": raw_audit["duplicates"],
        "cross_split_leakage": raw_audit["cross_split_leakage"],
        "errors": raw_audit["errors"],
        "warnings": raw_audit["warnings"],
        "montage": {
            key: montage[key]
            for key in (
                "requested_samples", "rendered_samples", "samples",
                "sample_set_sha256", "sha256",
            )
        },
    }
    if canonical_json_bytes(authoritative) != canonical_json_bytes(expected_authoritative):
        raise DeploymentValidationError(
            "training authoritative dataset evidence differs from the raw audit"
        )
    integrity_hash = hashlib.sha256(canonical_json_bytes(authoritative)).hexdigest()
    claimed_integrity_hash = _hash(
        audit["authoritative_integrity_sha256"],
        "training completion dataset.audit.authoritative_integrity_sha256",
    )
    pre_integrity_hash = _hash(
        audit["pre_training_authoritative_integrity_sha256"],
        "training completion dataset.audit.pre_training_authoritative_integrity_sha256",
    )
    post_integrity_hash = _hash(
        audit["post_training_authoritative_integrity_sha256"],
        "training completion dataset.audit.post_training_authoritative_integrity_sha256",
    )
    if (
        integrity_hash != claimed_integrity_hash
        or pre_integrity_hash != integrity_hash
        or post_integrity_hash != integrity_hash
        or audit["integrity_unchanged"] is not True
    ):
        raise DeploymentValidationError(
            "training dataset lacks matching pre/post authoritative integrity proof"
        )

    qualification = _object(
        dataset["automated_qualification"],
        "training completion dataset.automated_qualification",
    )
    qualification_path = _regular_file(
        qualification.get("qualification_path"),
        "training completion dataset.automated_qualification.qualification_path",
        suffix=".json",
    )
    expected_splits = {
        "train": {"images": 6471, "sample_set_sha256": sample_hashes["train"]},
        "val": {"images": 548, "sample_set_sha256": sample_hashes["val"]},
    }
    base = _object(
        value["base_checkpoint"],
        "training completion base_checkpoint",
        {"intake_path", "staged_path", "sha256", "trusted_intake"},
    )
    intake_path = _regular_file(
        base["intake_path"], "training completion base_checkpoint.intake_path", suffix=".pt"
    )
    staged_path = _regular_file(
        base["staged_path"], "training completion base_checkpoint.staged_path", suffix=".pt"
    )
    base_hash = _hash(base["sha256"], "training completion base_checkpoint.sha256")
    if intake_path.name != architecture or sha256_file(intake_path) != base_hash or sha256_file(staged_path) != base_hash:
        raise DeploymentValidationError("training base checkpoint identity mismatch")
    if staged_path != (
        run_directory.parent / f".{run_directory.name}.verified-inputs" / architecture
    ).resolve(strict=False):
        raise DeploymentValidationError("training staged base checkpoint path is not canonical")
    trusted_intake = _object(
        base["trusted_intake"],
        "training completion base_checkpoint.trusted_intake",
        {"manifest_path", "manifest_sha256", "publisher", "release_tag", "url", "bytes", "sha256"},
    )
    manifest_path = _regular_file(
        trusted_intake["manifest_path"],
        "training completion base_checkpoint.trusted_intake.manifest_path",
        suffix=".json",
    )
    repository_root = manifest_path.parents[2]
    try:
        verified_base = dict(
            verify_trusted_base_checkpoint(
                intake_path,
                architecture=architecture,
                repository_root=repository_root,
            )
        )
    except (TrainingExecutionError, OSError, IndexError) as error:
        raise DeploymentValidationError(str(error)) from error
    if (
        canonical_json_bytes(trusted_intake) != canonical_json_bytes(verified_base)
        or verified_base["sha256"] != base_hash
    ):
        raise DeploymentValidationError("training trusted base evidence differs from its manifest")

    cloud_record = _object(
        value["cloud_environment"],
        "training completion cloud_environment",
        {"path", "sha256", "test_seam", "manifest"},
    )
    if cloud_record["test_seam"] is not False:
        raise DeploymentValidationError("test-seam cloud evidence cannot qualify deployment")
    cloud_path, cloud_hash, cloud_payload = _load_json_file(
        cloud_record["path"], "training completion cloud_environment.path"
    )
    if _hash(cloud_record["sha256"], "training completion cloud_environment.sha256") != cloud_hash:
        raise DeploymentValidationError("training cloud-environment hash mismatch")
    try:
        verified_cloud = load_cloud_environment(cloud_path)
    except CloudEnvironmentError as error:
        raise DeploymentValidationError(str(error)) from error
    if (
        verified_cloud["control_plane_capture_trust"]
        != CONTROL_PLANE_TRUST_LEVEL
        or verified_cloud["durable_reload_trust"]
        != DURABLE_RELOAD_TRUST_LEVEL
    ):
        raise DeploymentValidationError(
            "training cloud evidence lacks the production RunPod trust markers"
        )
    if (
        canonical_json_bytes(cloud_record["manifest"])
        != canonical_json_bytes(cloud_payload)
        or canonical_json_bytes(cloud_payload) != canonical_json_bytes(verified_cloud)
    ):
        raise DeploymentValidationError("training cloud environment differs from hashed evidence")

    runtime = _object(
        value["runtime"],
        "training completion runtime",
        {
            "verified_at_utc", "python", "torch", "torchvision", "ultralytics",
            "cuda_runtime", "cudnn_version", "visible_gpu_count", "gpu_index",
            "gpu_name", "gpu_total_memory_bytes",
        },
    )

    def utc_timestamp(raw: Any, field: str) -> datetime:
        text = _text(raw, field)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as error:
            raise DeploymentValidationError(f"{field} must be an ISO-8601 timestamp") from error
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise DeploymentValidationError(f"{field} must identify UTC explicitly")
        return parsed

    audit_verified_at = utc_timestamp(
        audit["verified_at_utc"],
        "training completion dataset.audit.verified_at_utc",
    )
    post_training_verified_at = utc_timestamp(
        audit["post_training_verified_at_utc"],
        "training completion dataset.audit.post_training_verified_at_utc",
    )
    if post_training_verified_at < audit_verified_at:
        raise DeploymentValidationError(
            "post-training dataset verification precedes the pre-training verification"
        )
    utc_timestamp(runtime["verified_at_utc"], "training completion runtime.verified_at_utc")
    expected_runtime = verified_cloud["runtime"]
    for key in ("python", "torch", "torchvision", "ultralytics", "cuda_runtime"):
        if runtime[key] != expected_runtime[key]:
            raise DeploymentValidationError(f"training runtime {key} differs from cloud evidence")
    if any(runtime[key] != FROZEN_RUNTIME[key] for key in ("python", "torch", "torchvision", "ultralytics")):
        raise DeploymentValidationError("training runtime package line is not frozen")
    if (
        runtime["cuda_runtime"] != "13.0"
        or _integer(runtime["cudnn_version"], "training completion runtime.cudnn_version", minimum=1) <= 0
        or _integer(
            runtime["visible_gpu_count"],
            "training completion runtime.visible_gpu_count",
            minimum=1,
        ) != 1
        or _integer(
            runtime["gpu_index"], "training completion runtime.gpu_index"
        ) != 0
        or _text(runtime["gpu_name"], "training completion runtime.gpu_name")
        != verified_cloud["gpu"]["name"]
        or _integer(
            runtime["gpu_total_memory_bytes"],
            "training completion runtime.gpu_total_memory_bytes",
            minimum=1,
        ) != verified_cloud["gpu"]["memory_bytes"]
    ):
        raise DeploymentValidationError("training runtime is not the qualified RTX 5090 CUDA device")

    batch_record = _object(
        value["batch_decision"],
        "training completion batch_decision",
        {"path", "sha256", "test_seam", "decision"},
    )
    if batch_record["test_seam"] is not False:
        raise DeploymentValidationError("test-seam batch evidence cannot qualify deployment")
    batch_path, batch_hash, batch_payload = _load_json_file(
        batch_record["path"], "training completion batch_decision.path"
    )
    if _hash(batch_record["sha256"], "training completion batch_decision.sha256") != batch_hash:
        raise DeploymentValidationError("training batch-decision hash mismatch")
    try:
        workspace_root = Path(
            os.path.commonpath(
                [
                    str(run_directory), str(yaml_path), str(audit_path),
                    str(qualification_path), str(intake_path), str(batch_path),
                    str(cloud_path),
                ]
            )
        ).resolve(strict=False)
        verified_qualification = validate_automated_dataset_qualification(
            qualification_path,
            expected_audit_path=audit_path,
            expected_audit_sha256=audit_hash,
            expected_dataset_yaml_path=yaml_path,
            expected_dataset_yaml_sha256=yaml_hash,
            expected_montage_path=montage_path,
            expected_montage_sha256=montage_hash,
            expected_splits=expected_splits,
            workspace_root=workspace_root,
            repository_root=repository_root,
        )
        if canonical_json_bytes(qualification) != canonical_json_bytes(
            verified_qualification
        ):
            raise DeploymentValidationError(
                "training automated dataset qualification differs from its receipt"
            )
        verified_batch = load_batch_decision(
            batch_path,
            workspace_root=workspace_root,
            repository_root=repository_root,
        )
        permitted_batch = authorized_batch(verified_batch, candidate)
    except (BatchDecisionError, DatasetQualificationError, OSError, ValueError) as error:
        raise DeploymentValidationError(str(error)) from error
    if (
        canonical_json_bytes(batch_record["decision"])
        != canonical_json_bytes(batch_payload)
        or canonical_json_bytes(batch_payload) != canonical_json_bytes(verified_batch)
    ):
        raise DeploymentValidationError("training batch decision differs from hashed evidence")
    expected_lineage = {
        "training_plan_sha256": plan_hash,
        "source_commit": commit,
        "dataset_yaml_sha256": yaml_hash,
        "dataset_audit_sha256": audit_hash,
        "base_checkpoint_sha256": base_hash,
    }
    if verified_batch["lineage"] != expected_lineage:
        raise DeploymentValidationError("training batch decision has different lineage")

    resolved_batch = _integer(
        value["resolved_batch_size"], "training completion resolved_batch_size", minimum=1
    )
    if resolved_batch != permitted_batch or (
        spec.batch is not None and spec.batch != resolved_batch
    ):
        raise DeploymentValidationError("training resolved batch was not authorized")
    try:
        expected_arguments = frozen_train_arguments(
            spec,
            dataset_yaml=yaml_path,
            run_directory=run_directory,
            requested_batch=resolved_batch,
        )
    except TrainingExecutionError as error:
        raise DeploymentValidationError(str(error)) from error
    arguments = _object(value["arguments"], "training completion arguments")
    if canonical_json_bytes(arguments) != canonical_json_bytes(expected_arguments):
        raise DeploymentValidationError("training arguments differ from the frozen runner arguments")
    effective_arguments = _object(
        value["effective_arguments"], "training completion effective_arguments"
    )
    reported_metrics = _object(value["metrics"], "training completion metrics")

    def load_effective_arguments(path: Path) -> Any:
        text = path.read_text(encoding="utf-8")
        try:
            return json.loads(
                text,
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
        except json.JSONDecodeError:
            try:
                import yaml
            except ImportError as error:
                raise DeploymentValidationError(
                    "PyYAML is required to validate a non-JSON args.yaml"
                ) from error
            return yaml.safe_load(text)

    try:
        verified_effective = _verify_effective_arguments(
            args_path,
            expected_arguments,
            staged_model=staged_path,
            loader=load_effective_arguments,
        )
        verified_metrics = _read_final_metrics(results_path, spec.epochs)
    except (TrainingExecutionError, DeploymentValidationError, OSError, UnicodeError) as error:
        if isinstance(error, DeploymentValidationError):
            raise
        raise DeploymentValidationError(str(error)) from error
    if canonical_json_bytes(effective_arguments) != canonical_json_bytes(verified_effective):
        raise DeploymentValidationError("training effective arguments differ from args.yaml")
    for key, expected in expected_arguments.items():
        if canonical_json_bytes({"value": verified_effective[key]}) != canonical_json_bytes(
            {"value": expected}
        ):
            raise DeploymentValidationError(
                f"training effective argument {key!r} differs from the frozen value"
            )
    if canonical_json_bytes(reported_metrics) != canonical_json_bytes(verified_metrics):
        raise DeploymentValidationError("training metrics differ from results.csv")

    peaks = _object(
        value["cuda_peak_bytes"],
        "training completion cuda_peak_bytes",
        {"allocated", "reserved"},
    )
    allocated = _integer(peaks["allocated"], "training completion cuda_peak_bytes.allocated", minimum=1)
    reserved = _integer(peaks["reserved"], "training completion cuda_peak_bytes.reserved", minimum=1)
    if allocated > reserved or reserved > runtime["gpu_total_memory_bytes"]:
        raise DeploymentValidationError("training CUDA peak evidence is inconsistent")
    if value["smoke_gate"] is not None:
        raise DeploymentValidationError("full training completion must not contain a smoke gate")
    started = utc_timestamp(value["started_at_utc"], "training completion started_at_utc")
    finished = utc_timestamp(value["finished_at_utc"], "training completion finished_at_utc")
    if (
        audit_verified_at > started
        or finished > post_training_verified_at
        or finished < started
        or _number(
        value["duration_seconds"], "training completion duration_seconds", minimum=0.000001
        ) <= 0
    ):
        raise DeploymentValidationError("training completion duration is invalid")
    return best_hash


@dataclass(frozen=True, slots=True)
class CandidateQualification:
    """Immutable validation/runtime evidence admitted to model selection."""

    schema: str
    report_id: str
    candidate: str
    architecture: str
    input_shape_nchw: tuple[int, int, int, int]
    training_plan_sha256: str
    confidence_threshold: float
    source_pt_sha256: str
    onnx_sha256: str
    executed_engine_path: str
    executed_engine_sha256: str
    training_completion_report_sha256: str
    threshold_selection_report_sha256: str
    onnx_report_sha256: str
    engine_identity_report_sha256: str
    validation_accuracy_equivalence_report_sha256: str
    nano_benchmark_report_sha256: str
    runtime: Mapping[str, str]
    qualified: bool
    _issuance_token: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "report_id": self.report_id,
            "candidate": self.candidate,
            "architecture": self.architecture,
            "input_shape_nchw": list(self.input_shape_nchw),
            "training_plan_sha256": self.training_plan_sha256,
            "confidence_threshold": self.confidence_threshold,
            "source_pt_sha256": self.source_pt_sha256,
            "onnx_sha256": self.onnx_sha256,
            "executed_engine_path": self.executed_engine_path,
            "executed_engine_sha256": self.executed_engine_sha256,
            "training_completion_report_sha256": self.training_completion_report_sha256,
            "threshold_selection_report_sha256": self.threshold_selection_report_sha256,
            "onnx_report_sha256": self.onnx_report_sha256,
            "engine_identity_report_sha256": self.engine_identity_report_sha256,
            "validation_accuracy_equivalence_report_sha256": self.validation_accuracy_equivalence_report_sha256,
            "nano_benchmark_report_sha256": self.nano_benchmark_report_sha256,
            "runtime": dict(self.runtime),
            "qualified": self.qualified,
        }


def is_validated_candidate_qualification(value: object) -> bool:
    """Return true only for qualifications issued after full evidence checks."""

    return (
        isinstance(value, CandidateQualification)
        and value._issuance_token is _QUALIFICATION_ISSUANCE_TOKEN
    )


_QUALIFICATION_FIELDS = {
    "schema", "report_id", "candidate", "architecture", "input_shape_nchw",
    "training_plan_sha256", "confidence_threshold", "source_pt_sha256",
    "onnx_sha256", "executed_engine_path", "executed_engine_sha256",
    "training_completion_report_sha256", "threshold_selection_report_sha256",
    "onnx_report_sha256", "engine_identity_report_sha256",
    "validation_accuracy_equivalence_report_sha256", "nano_benchmark_report_sha256",
    "runtime", "qualified",
}


def parse_candidate_qualification(value: Mapping[str, Any]) -> CandidateQualification:
    """Parse structural qualification data; use ``validate_*`` for trust."""

    raw = _object(value, "candidate qualification", _QUALIFICATION_FIELDS)
    if raw["schema"] != CANDIDATE_QUALIFICATION_SCHEMA:
        raise DeploymentValidationError("unsupported candidate qualification schema")
    report_id = _text(raw["report_id"], "candidate qualification.report_id")
    candidate, shape = _candidate_contract(
        raw["candidate"], raw["architecture"], raw["input_shape_nchw"]
    )
    plan_hash = _hash(
        raw["training_plan_sha256"], "candidate qualification.training_plan_sha256"
    )
    threshold = _threshold(
        raw["confidence_threshold"], "candidate qualification.confidence_threshold"
    )
    hashes = {
        name: _hash(raw[name], f"candidate qualification.{name}")
        for name in (
            "source_pt_sha256", "onnx_sha256", "executed_engine_sha256",
            "training_completion_report_sha256", "threshold_selection_report_sha256",
            "onnx_report_sha256", "engine_identity_report_sha256",
            "validation_accuracy_equivalence_report_sha256", "nano_benchmark_report_sha256",
        )
    }
    engine_path = _regular_file(
        raw["executed_engine_path"],
        "candidate qualification.executed_engine_path",
        suffix=".engine",
    )
    if sha256_file(engine_path) != hashes["executed_engine_sha256"]:
        raise DeploymentValidationError("qualification executed engine hash mismatch")
    runtime = _target_runtime(raw["runtime"], "candidate qualification.runtime")
    qualified = _boolean(raw["qualified"], "candidate qualification.qualified")
    qualification = CandidateQualification(
        schema=CANDIDATE_QUALIFICATION_SCHEMA,
        report_id=report_id,
        candidate=candidate,
        architecture=raw["architecture"],
        input_shape_nchw=shape,
        training_plan_sha256=plan_hash,
        confidence_threshold=threshold,
        source_pt_sha256=hashes["source_pt_sha256"],
        onnx_sha256=hashes["onnx_sha256"],
        executed_engine_path=str(engine_path),
        executed_engine_sha256=hashes["executed_engine_sha256"],
        training_completion_report_sha256=hashes["training_completion_report_sha256"],
        threshold_selection_report_sha256=hashes["threshold_selection_report_sha256"],
        onnx_report_sha256=hashes["onnx_report_sha256"],
        engine_identity_report_sha256=hashes["engine_identity_report_sha256"],
        validation_accuracy_equivalence_report_sha256=hashes["validation_accuracy_equivalence_report_sha256"],
        nano_benchmark_report_sha256=hashes["nano_benchmark_report_sha256"],
        runtime=MappingProxyType({str(key): str(item) for key, item in runtime.items()}),
        qualified=qualified,
    )
    return qualification


def build_candidate_qualification(
    *,
    report_id: str,
    candidate: str,
    architecture: str,
    training_plan_sha256: str,
    training_completion_report_path: str | os.PathLike[str],
    threshold_selection_report_path: str | os.PathLike[str],
    onnx_report_path: str | os.PathLike[str],
    engine_identity_report_path: str | os.PathLike[str],
    validation_accuracy_equivalence_report_path: str | os.PathLike[str],
    nano_benchmark_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    graph_inspector: GraphInspector = inspect_onnx_graph_contract,
) -> CandidateQualification:
    """Load, hash, validate, and cross-bind all evidence for one candidate."""

    report_id = _text(report_id, "candidate qualification report_id")
    plan_hash = _hash(training_plan_sha256, "training_plan_sha256")
    candidate = _text(candidate, "candidate")
    architecture = _text(architecture, "architecture")
    expected_architecture, expected_shape = CANDIDATE_CONTRACTS.get(candidate, (None, None))
    if architecture != expected_architecture or expected_shape is None:
        raise DeploymentValidationError("candidate architecture is not frozen")

    training_path, training_hash, training = _load_json_file(
        training_completion_report_path, "training_completion_report_path"
    )
    source_pt_hash = _validate_training_completion(
        training,
        report_path=training_path,
        candidate=candidate,
        architecture=architecture,
        shape=expected_shape,
        training_plan_sha256=plan_hash,
    )
    _, threshold_hash, threshold = _load_json_file(
        threshold_selection_report_path, "threshold_selection_report_path"
    )
    threshold = validate_threshold_selection_report(
        threshold,
        workspace_root=workspace_root,
        repository_root=repository_root,
    )
    _, onnx_hash, onnx = _load_json_file(onnx_report_path, "onnx_report_path")
    onnx = validate_onnx_export_report(onnx, graph_inspector=graph_inspector)
    _, engine_hash, engine = _load_json_file(
        engine_identity_report_path, "engine_identity_report_path"
    )
    engine = validate_engine_identity_report(engine)
    _, accuracy_hash, accuracy = _load_json_file(
        validation_accuracy_equivalence_report_path,
        "validation_accuracy_equivalence_report_path",
    )
    accuracy = validate_validation_accuracy_equivalence_report(
        accuracy,
        engine_report=engine,
        threshold_report_path=threshold_selection_report_path,
        workspace_root=workspace_root,
        repository_root=repository_root,
    )
    _, benchmark_hash, benchmark = _load_json_file(
        nano_benchmark_report_path, "nano_benchmark_report_path"
    )
    benchmark = validate_jetson_benchmark_report(
        benchmark,
        engine_report=engine,
        engine_report_sha256=engine_hash,
    )

    common = (
        candidate, architecture, MODEL_ID, plan_hash, PERSON_CLASS_MAP,
        list(expected_shape), threshold["confidence_threshold"],
    )
    for evidence, label in (
        (threshold, "threshold"), (onnx, "ONNX"), (engine, "engine"),
        (accuracy, "accuracy"), (benchmark, "benchmark"),
    ):
        identity = (
            evidence["candidate"], evidence["architecture"], evidence["model_id"],
            evidence["training_plan_sha256"], evidence["class_map"],
            evidence["input_shape_nchw"], evidence["confidence_threshold"],
        )
        if identity != common:
            raise DeploymentValidationError(f"{label} report candidate lineage mismatch")
    if threshold["model_sha256"] != source_pt_hash:
        raise DeploymentValidationError("threshold report does not bind trained best.pt")
    if onnx["artifacts"]["source_pt"]["sha256"] != source_pt_hash:
        raise DeploymentValidationError("ONNX report does not bind trained best.pt")
    if engine["artifacts"]["source_pt"]["sha256"] != source_pt_hash:
        raise DeploymentValidationError("engine report does not bind trained best.pt")
    if engine["artifacts"]["onnx"]["sha256"] != onnx["artifacts"]["onnx"]["sha256"]:
        raise DeploymentValidationError("engine report does not bind exported ONNX")
    qualified = bool(accuracy["passed"] and benchmark["passed"])
    runtime = MappingProxyType(
        {str(key): str(item) for key, item in engine["runtime"].items()}
    )
    qualification = CandidateQualification(
        schema=CANDIDATE_QUALIFICATION_SCHEMA,
        report_id=report_id,
        candidate=candidate,
        architecture=architecture,
        input_shape_nchw=expected_shape,
        training_plan_sha256=plan_hash,
        confidence_threshold=float(threshold["confidence_threshold"]),
        source_pt_sha256=source_pt_hash,
        onnx_sha256=onnx["artifacts"]["onnx"]["sha256"],
        executed_engine_path=str(Path(engine["build"]["executed_engine_path"]).resolve()),
        executed_engine_sha256=engine["artifacts"]["tensorrt_engine"]["sha256"],
        training_completion_report_sha256=training_hash,
        threshold_selection_report_sha256=threshold_hash,
        onnx_report_sha256=onnx_hash,
        engine_identity_report_sha256=engine_hash,
        validation_accuracy_equivalence_report_sha256=accuracy_hash,
        nano_benchmark_report_sha256=benchmark_hash,
        runtime=runtime,
        qualified=qualified,
    )
    object.__setattr__(qualification, "_issuance_token", _QUALIFICATION_ISSUANCE_TOKEN)
    return qualification


def validate_candidate_qualification(
    value: Mapping[str, Any],
    *,
    training_completion_report_path: str | os.PathLike[str],
    threshold_selection_report_path: str | os.PathLike[str],
    onnx_report_path: str | os.PathLike[str],
    engine_identity_report_path: str | os.PathLike[str],
    validation_accuracy_equivalence_report_path: str | os.PathLike[str],
    nano_benchmark_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    graph_inspector: GraphInspector = inspect_onnx_graph_contract,
) -> CandidateQualification:
    """Rebuild a persisted qualification and reject any edited field."""

    parsed = parse_candidate_qualification(value)
    raw = parsed.to_dict()
    rebuilt = build_candidate_qualification(
        report_id=raw["report_id"],
        candidate=raw["candidate"],
        architecture=raw["architecture"],
        training_plan_sha256=raw["training_plan_sha256"],
        training_completion_report_path=training_completion_report_path,
        threshold_selection_report_path=threshold_selection_report_path,
        onnx_report_path=onnx_report_path,
        engine_identity_report_path=engine_identity_report_path,
        validation_accuracy_equivalence_report_path=validation_accuracy_equivalence_report_path,
        nano_benchmark_report_path=nano_benchmark_report_path,
        workspace_root=workspace_root,
        repository_root=repository_root,
        graph_inspector=graph_inspector,
    )
    if raw != rebuilt.to_dict():
        raise DeploymentValidationError("candidate qualification differs from hashed evidence")
    return rebuilt


def validate_mandatory_completion_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Prove both mandatory full candidates completed under one frozen plan."""

    value = _object(
        report, "mandatory completion report",
        {"schema", "report_id", "training_plan_sha256", "candidates", "passed"},
    )
    if value["schema"] != MANDATORY_COMPLETION_SCHEMA:
        raise DeploymentValidationError("unsupported mandatory-completion schema")
    _text(value["report_id"], "mandatory completion report.report_id")
    plan_hash = _hash(
        value["training_plan_sha256"], "mandatory completion report.training_plan_sha256"
    )
    candidates = _object(value["candidates"], "mandatory completion report.candidates")
    names = set(candidates)
    if not MANDATORY_CANDIDATES <= names or not names <= set(CANDIDATE_CONTRACTS):
        raise DeploymentValidationError("mandatory completion candidate set is incomplete or unknown")
    computed_pass = True
    for name, entry_value in candidates.items():
        entry = _object(
            entry_value, f"mandatory completion report.candidates.{name}",
            {"architecture", "input_shape_nchw", "training_report", "completed"},
        )
        expected_architecture, expected_shape = CANDIDATE_CONTRACTS[name]
        _candidate_contract(name, entry["architecture"], entry["input_shape_nchw"])
        training_path, _, training = _report_reference(
            entry["training_report"],
            f"mandatory completion report.candidates.{name}.training_report",
        )
        _validate_training_completion(
            training,
            report_path=training_path,
            candidate=name,
            architecture=expected_architecture,
            shape=expected_shape,
            training_plan_sha256=plan_hash,
        )
        completed = _boolean(
            entry["completed"], f"mandatory completion report.candidates.{name}.completed"
        )
        computed_pass = computed_pass and (completed or name not in MANDATORY_CANDIDATES)
    if _boolean(value["passed"], "mandatory completion report.passed") != computed_pass:
        raise DeploymentValidationError("mandatory completion passed claim is false")
    return dict(value)


_SELECTED_FIELDS = {
    "schema", "candidate", "architecture", "input_shape_nchw",
    "training_plan_sha256", "confidence_threshold", "artifacts", "runtime",
    "validation", "considered_candidates",
}
_CONSIDERED_FIELDS = {
    "candidate", "qualification_sha256", "qualified", "validation_passed",
    "confidence_threshold", "source_pt_sha256", "onnx_sha256",
    "executed_engine_sha256", "report_hashes",
}
_QUALIFICATION_REPORT_HASH_FIELDS = {
    "training_completion", "threshold_selection", "onnx_export", "engine_identity",
    "validation_accuracy_equivalence", "nano_benchmark",
}


def _semantic_sha256(value: Mapping[str, Any]) -> str:
    try:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except ArtifactIOError as error:
        raise DeploymentValidationError(f"evidence is not canonical JSON: {error}") from error


def _selected_payload(selected_candidate: object) -> tuple[dict[str, Any], str]:
    from .selection import InferenceEvidence, SelectedCandidate, SelectionError

    if type(selected_candidate) is not SelectedCandidate:
        raise DeploymentValidationError("selected_candidate must be selection.SelectedCandidate")
    payload = _object(selected_candidate.to_dict(), "selected candidate", _SELECTED_FIELDS)
    if payload["schema"] != "veriswarm.rescue.model_selection.v2":
        raise DeploymentValidationError("unsupported pure model-selection schema")
    _, selected_shape = _candidate_contract(
        payload["candidate"], payload["architecture"], payload["input_shape_nchw"]
    )
    _hash(payload["training_plan_sha256"], "selected candidate.training_plan_sha256")
    threshold = _threshold(
        payload["confidence_threshold"], "selected candidate.confidence_threshold"
    )
    artifacts = _object(
        payload["artifacts"], "selected candidate.artifacts",
        {"source_pt_sha256", "onnx_sha256", "executed_engine_path", "executed_engine_sha256"},
    )
    for name in ("source_pt_sha256", "onnx_sha256", "executed_engine_sha256"):
        _hash(artifacts[name], f"selected candidate.artifacts.{name}")
    engine_path = _regular_file(
        artifacts["executed_engine_path"],
        "selected candidate.artifacts.executed_engine_path",
        suffix=".engine",
    )
    if sha256_file(engine_path) != artifacts["executed_engine_sha256"]:
        raise DeploymentValidationError("selected candidate engine hash mismatch")
    _target_runtime(payload["runtime"], "selected candidate.runtime")
    validations = _object(
        payload["validation"], "selected candidate.validation",
        {"real_aerial", "synthetic_disaster"},
    )
    for domain in validations:
        evidence = _object(
            validations[domain], f"selected candidate.validation.{domain}",
            {
                "data_kind",
                "dataset_id",
                "dataset_sha256",
                "validation_report_sha256",
                "inference_evidence",
                "inference_evidence_sha256",
                "metrics",
            },
        )
        if evidence["data_kind"] != domain:
            raise DeploymentValidationError("selected validation provenance domain mismatch")
        _text(evidence["dataset_id"], f"selected candidate.validation.{domain}.dataset_id")
        _hash(evidence["dataset_sha256"], f"selected candidate.validation.{domain}.dataset_sha256")
        _hash(
            evidence["validation_report_sha256"],
            f"selected candidate.validation.{domain}.validation_report_sha256",
        )
        try:
            inference_evidence = InferenceEvidence.from_dict(
                evidence["inference_evidence"],
                expected_input_shape=selected_shape,
            )
        except SelectionError as error:
            raise DeploymentValidationError(
                f"selected candidate validation inference evidence is invalid: {error}"
            ) from error
        if _hash(
            evidence["inference_evidence_sha256"],
            f"selected candidate.validation.{domain}.inference_evidence_sha256",
        ) != inference_evidence.sha256:
            raise DeploymentValidationError(
                "selected validation inference-evidence SHA-256 differs"
            )
        metrics = _object(
            evidence["metrics"], f"selected candidate.validation.{domain}.metrics",
            {"threshold", "precision", "recall", "small_person_recall", "mAP50"},
        )
        if not math.isclose(_threshold(metrics["threshold"]), threshold, abs_tol=1e-12):
            raise DeploymentValidationError("selected validation threshold differs from deployment")
        if not (
            _number(metrics["precision"], "precision", maximum=1.0) >= 0.60
            and _number(metrics["recall"], "recall", maximum=1.0) >= 0.75
            and _number(metrics["mAP50"], "mAP50", maximum=1.0) >= 0.70
            and _number(metrics["small_person_recall"], "small_person_recall", maximum=1.0) >= 0.60
        ):
            raise DeploymentValidationError("selected validation metrics do not pass frozen gates")
    if validations["real_aerial"]["dataset_sha256"] == validations["synthetic_disaster"]["dataset_sha256"]:
        raise DeploymentValidationError("selected real/synthetic datasets must be distinct")
    considered = payload["considered_candidates"]
    if type(considered) is not list or not considered:
        raise DeploymentValidationError("selected candidate must record all considerations")
    seen: set[str] = set()
    for index, entry_value in enumerate(considered):
        entry = _object(entry_value, f"considered_candidates[{index}]", _CONSIDERED_FIELDS)
        name = _text(entry["candidate"], f"considered_candidates[{index}].candidate")
        if name in seen or name not in CANDIDATE_CONTRACTS:
            raise DeploymentValidationError("considered candidate is duplicate or unknown")
        seen.add(name)
        for key in ("qualification_sha256", "source_pt_sha256", "onnx_sha256", "executed_engine_sha256"):
            _hash(entry[key], f"considered_candidates[{index}].{key}")
        _boolean(entry["qualified"], f"considered_candidates[{index}].qualified")
        _boolean(entry["validation_passed"], f"considered_candidates[{index}].validation_passed")
        _threshold(entry["confidence_threshold"], f"considered_candidates[{index}].confidence_threshold")
        report_hashes = _object(
            entry["report_hashes"], f"considered_candidates[{index}].report_hashes",
            _QUALIFICATION_REPORT_HASH_FIELDS,
        )
        for key in report_hashes:
            _hash(report_hashes[key], f"considered_candidates[{index}].report_hashes.{key}")
    if not MANDATORY_CANDIDATES <= seen:
        raise DeploymentValidationError("pure selection omitted a mandatory candidate")
    semantic_hash = _semantic_sha256(payload)
    if selected_candidate.sha256 != semantic_hash:
        raise DeploymentValidationError("SelectedCandidate.sha256 is not canonical")
    return payload, semantic_hash


def _derive_final_selection_report(
    selected_candidate: object,
    *,
    report_id: str,
    qualification_report_paths: Mapping[str, str | os.PathLike[str]],
    mandatory_completion_report_path: str | os.PathLike[str],
    untouched_test_accuracy_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    consume_test_use: bool,
) -> dict[str, Any]:
    """Adapt pure selection output into a live-hash-verified release report.

    Candidate choice is already frozen by ``selected_candidate``.  This stage
    validates exactly one untouched-test report for that candidate and blocks
    release on failure without selecting or promoting any alternative.
    """

    workspace, repository = _evidence_roots(workspace_root, repository_root)
    selected, selected_hash = _selected_payload(selected_candidate)
    _, completion_hash, completion = _load_json_file(
        mandatory_completion_report_path, "mandatory_completion_report_path"
    )
    completion = validate_mandatory_completion_report(completion)
    if completion["training_plan_sha256"] != selected["training_plan_sha256"]:
        raise DeploymentValidationError("selection and completion plan hashes differ")
    if not isinstance(qualification_report_paths, Mapping) or not all(
        type(key) is str for key in qualification_report_paths
    ):
        raise DeploymentValidationError(
            "qualification_report_paths must map candidate strings to paths"
        )
    considered = {item["candidate"]: item for item in selected["considered_candidates"]}
    if set(qualification_report_paths) != set(considered):
        raise DeploymentValidationError("qualification paths must exactly match considered candidates")
    references: list[dict[str, Any]] = []
    qualifications: dict[str, CandidateQualification] = {}
    for candidate in sorted(considered):
        path, file_hash, raw = _load_json_file(
            qualification_report_paths[candidate], f"qualification_report_paths[{candidate}]"
        )
        qualification = parse_candidate_qualification(raw)
        if qualification.candidate != candidate:
            raise DeploymentValidationError("qualification path candidate mismatch")
        semantic_hash = _semantic_sha256(raw)
        expected = considered[candidate]
        report_hashes = {
            "training_completion": qualification.training_completion_report_sha256,
            "threshold_selection": qualification.threshold_selection_report_sha256,
            "onnx_export": qualification.onnx_report_sha256,
            "engine_identity": qualification.engine_identity_report_sha256,
            "validation_accuracy_equivalence": qualification.validation_accuracy_equivalence_report_sha256,
            "nano_benchmark": qualification.nano_benchmark_report_sha256,
        }
        expected_values = {
            "qualification_sha256": semantic_hash,
            "qualified": qualification.qualified,
            "confidence_threshold": qualification.confidence_threshold,
            "source_pt_sha256": qualification.source_pt_sha256,
            "onnx_sha256": qualification.onnx_sha256,
            "executed_engine_sha256": qualification.executed_engine_sha256,
            "report_hashes": report_hashes,
        }
        for key, expected_value in expected_values.items():
            if expected[key] != expected_value:
                raise DeploymentValidationError(
                    f"pure selection {candidate!r} field {key!r} differs from live qualification"
                )
        references.append(
            {"candidate": candidate, "report_id": qualification.report_id,
             "path": str(path), "file_sha256": file_hash,
             "semantic_sha256": semantic_hash, "qualified": qualification.qualified}
        )
        qualifications[candidate] = qualification
    selected_name = selected["candidate"]
    selected_qualification = qualifications[selected_name]
    selected_artifacts = selected["artifacts"]
    if not selected_qualification.qualified:
        raise DeploymentValidationError("pure selection chose an unqualified candidate")
    if (
        selected_qualification.architecture != selected["architecture"]
        or list(selected_qualification.input_shape_nchw) != selected["input_shape_nchw"]
        or selected_qualification.training_plan_sha256 != selected["training_plan_sha256"]
        or selected_qualification.confidence_threshold != selected["confidence_threshold"]
        or selected_qualification.source_pt_sha256 != selected_artifacts["source_pt_sha256"]
        or selected_qualification.onnx_sha256 != selected_artifacts["onnx_sha256"]
        or selected_qualification.executed_engine_path != str(Path(selected_artifacts["executed_engine_path"]).resolve())
        or selected_qualification.executed_engine_sha256 != selected_artifacts["executed_engine_sha256"]
        or dict(selected_qualification.runtime) != selected["runtime"]
    ):
        raise DeploymentValidationError("selected candidate differs from selected qualification")
    try:
        test_accuracy_path = require_path_within_workspace(
            untouched_test_accuracy_report_path, workspace
        )
    except ArtifactIOError as error:
        raise DeploymentValidationError(str(error)) from error
    test_accuracy_path, test_accuracy_hash, test_accuracy = _load_json_file(
        test_accuracy_path,
        "untouched_test_accuracy_report_path",
    )
    (
        test_accuracy,
        test_reports,
        _threshold_evidence,
        validation_reports,
    ) = _validate_untouched_test_accuracy_evidence(
        test_accuracy,
        selected_candidate=selected_candidate,
        qualification=selected_qualification,
        workspace=workspace,
        repository=repository,
    )
    split_fingerprint = _assert_no_evaluation_split_overlap(
        validation_reports,
        test_reports,
        workspace=workspace,
        repository=repository,
    )
    ledger_payload = _test_use_ledger_payload(
        selected_candidate_sha256=selected_hash,
        test_accuracy_path=test_accuracy_path,
        test_accuracy_sha256=test_accuracy_hash,
        test_accuracy=test_accuracy,
        split_fingerprint_sha256=split_fingerprint,
    )
    ledger_path, ledger_hash = _test_use_ledger(
        workspace=workspace,
        repository=repository,
        payload=ledger_payload,
        split_fingerprint_sha256=split_fingerprint,
        consume=consume_test_use,
    )
    return {
        "schema": FINAL_SELECTION_SCHEMA,
        "report_id": _text(report_id, "final selection report_id"),
        "training_plan_sha256": selected["training_plan_sha256"],
        "mandatory_completion_report_sha256": completion_hash,
        "selected_candidate_sha256": selected_hash,
        "qualification_reports": references,
        "selected_candidate": selected_name,
        "selection_rule": SELECTION_RULE,
        "untouched_test_accuracy_report_id": test_accuracy["report_id"],
        "untouched_test_accuracy_report_sha256": test_accuracy_hash,
        "untouched_test_use_ledger_path": str(ledger_path),
        "untouched_test_use_ledger_sha256": ledger_hash,
        "test_split_fingerprint_sha256": split_fingerprint,
        "passed": bool(test_accuracy["passed"]),
    }


def build_final_selection_report(
    selected_candidate: object,
    *,
    report_id: str,
    qualification_report_paths: Mapping[str, str | os.PathLike[str]],
    mandatory_completion_report_path: str | os.PathLike[str],
    untouched_test_accuracy_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Consume one untouched test split and build its immutable release result."""

    return _derive_final_selection_report(
        selected_candidate,
        report_id=report_id,
        qualification_report_paths=qualification_report_paths,
        mandatory_completion_report_path=mandatory_completion_report_path,
        untouched_test_accuracy_report_path=untouched_test_accuracy_report_path,
        workspace_root=workspace_root,
        repository_root=repository_root,
        consume_test_use=True,
    )


def validate_final_selection_report(
    report: Mapping[str, Any],
    *,
    selected_candidate: object,
    qualification_report_paths: Mapping[str, str | os.PathLike[str]],
    mandatory_completion_report_path: str | os.PathLike[str],
    untouched_test_accuracy_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Rebuild the adapter output and reject any edited release-selection field."""

    value = _object(report, "selection report")
    expected = _derive_final_selection_report(
        selected_candidate,
        report_id=value.get("report_id"),
        qualification_report_paths=qualification_report_paths,
        mandatory_completion_report_path=mandatory_completion_report_path,
        untouched_test_accuracy_report_path=untouched_test_accuracy_report_path,
        workspace_root=workspace_root,
        repository_root=repository_root,
        consume_test_use=False,
    )
    if value != expected:
        raise DeploymentValidationError("final selection report differs from live evidence")
    return dict(value)


def build_deployment_manifest(
    *,
    selected_candidate: object,
    qualification_report_paths: Mapping[str, str | os.PathLike[str]],
    mandatory_completion_report_path: str | os.PathLike[str],
    untouched_test_accuracy_report_path: str | os.PathLike[str],
    selection_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Build a final deployment manifest from pure selection plus live evidence."""

    selected, selected_hash = _selected_payload(selected_candidate)
    _, completion_hash, completion = _load_json_file(
        mandatory_completion_report_path, "mandatory_completion_report_path"
    )
    completion = validate_mandatory_completion_report(completion)
    _, selection_hash, selection = _load_json_file(selection_report_path, "selection_report_path")
    selection = validate_final_selection_report(
        selection,
        selected_candidate=selected_candidate,
        qualification_report_paths=qualification_report_paths,
        mandatory_completion_report_path=mandatory_completion_report_path,
        untouched_test_accuracy_report_path=untouched_test_accuracy_report_path,
        workspace_root=workspace_root,
        repository_root=repository_root,
    )
    selected_name = selected["candidate"]
    _, qualification_file_hash, qualification_raw = _load_json_file(
        qualification_report_paths[selected_name],
        f"qualification_report_paths[{selected_name}]",
    )
    qualification = parse_candidate_qualification(qualification_raw)
    passed = bool(completion["passed"] and selection["passed"] and qualification.qualified)
    artifacts = selected["artifacts"]
    return {
        "schema": DEPLOYMENT_MANIFEST_SCHEMA,
        "model_id": MODEL_ID,
        "candidate": selected_name,
        "architecture": selected["architecture"],
        "class_map": dict(PERSON_CLASS_MAP),
        "input_shape_nchw": selected["input_shape_nchw"],
        "precision": FP16,
        "confidence_threshold": selected["confidence_threshold"],
        "training_plan_sha256": selected["training_plan_sha256"],
        "artifacts": {
            "source_pt_sha256": artifacts["source_pt_sha256"],
            "onnx_sha256": artifacts["onnx_sha256"],
            "tensorrt_engine_path": str(Path(artifacts["executed_engine_path"]).resolve()),
            "tensorrt_engine_sha256": artifacts["executed_engine_sha256"],
        },
        "runtime": selected["runtime"],
        "evidence": {
            "selected_candidate_sha256": selected_hash,
            "candidate_qualification_report_id": qualification.report_id,
            "candidate_qualification_file_sha256": qualification_file_hash,
            "candidate_qualification_semantic_sha256": _semantic_sha256(qualification_raw),
            "mandatory_completion_report_id": completion["report_id"],
            "mandatory_completion_report_sha256": completion_hash,
            "final_selection_report_id": selection["report_id"],
            "final_selection_report_sha256": selection_hash,
            "threshold_selection_report_sha256": qualification.threshold_selection_report_sha256,
            "untouched_test_accuracy_report_id": selection["untouched_test_accuracy_report_id"],
            "untouched_test_accuracy_report_sha256": selection["untouched_test_accuracy_report_sha256"],
            "untouched_test_use_ledger_path": selection["untouched_test_use_ledger_path"],
            "untouched_test_use_ledger_sha256": selection["untouched_test_use_ledger_sha256"],
            "test_split_fingerprint_sha256": selection["test_split_fingerprint_sha256"],
            "nano_benchmark_report_sha256": qualification.nano_benchmark_report_sha256,
        },
        "validation": {"passed": passed},
    }


def validate_deployment_manifest(
    manifest: Mapping[str, Any],
    *,
    selected_candidate: object,
    qualification_report_paths: Mapping[str, str | os.PathLike[str]],
    mandatory_completion_report_path: str | os.PathLike[str],
    untouched_test_accuracy_report_path: str | os.PathLike[str],
    selection_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    expected = build_deployment_manifest(
        selected_candidate=selected_candidate,
        qualification_report_paths=qualification_report_paths,
        mandatory_completion_report_path=mandatory_completion_report_path,
        untouched_test_accuracy_report_path=untouched_test_accuracy_report_path,
        selection_report_path=selection_report_path,
        workspace_root=workspace_root,
        repository_root=repository_root,
    )
    if type(manifest) is not dict or manifest != expected:
        raise DeploymentValidationError("deployment manifest differs from validated evidence")
    return dict(manifest)


def build_jetson_trtexec_command(
    onnx_report: Mapping[str, Any],
    engine_path: str | os.PathLike[str],
    *,
    trtexec_binary: str | os.PathLike[str],
    trtexec_sha256: str,
    trtexec_version: str,
    jetson_marker_path: str | os.PathLike[str] = "/etc/nv_tegra_release",
    graph_inspector: GraphInspector = inspect_onnx_graph_contract,
) -> tuple[str, ...]:
    """Return the sole canonical FP16 argv after proving a target Jetson host."""

    onnx = validate_onnx_export_report(onnx_report, graph_inspector=graph_inspector)
    marker = _regular_file(str(jetson_marker_path), "jetson_marker_path")
    try:
        marker_text = marker.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as error:
        raise DeploymentValidationError(f"cannot read Jetson marker: {error}") from error
    if not marker_text.startswith("# R"):
        raise DeploymentValidationError("refusing TensorRT build: invalid Jetson marker")
    binary = _regular_file(str(trtexec_binary), "trtexec_binary")
    if os.name != "nt" and not os.access(binary, os.X_OK):
        raise DeploymentValidationError("trtexec_binary is not executable")
    expected_binary_hash = _hash(trtexec_sha256, "trtexec_sha256")
    if sha256_file(binary) != expected_binary_hash:
        raise DeploymentValidationError("trtexec binary hash mismatch")
    _text(trtexec_version, "trtexec_version")
    destination = Path(engine_path).expanduser()
    if not destination.is_absolute() or destination.suffix.lower() != ".engine":
        raise DeploymentValidationError("engine output must be an absolute .engine path")
    if os.path.lexists(destination):
        raise DeploymentValidationError("refusing to overwrite an existing TensorRT engine")
    if not destination.parent.is_dir():
        raise DeploymentValidationError("engine output directory must already exist")
    onnx_path = Path(onnx["artifacts"]["onnx"]["path"]).resolve()
    shape = _shape(onnx["input_shape_nchw"], "onnx report.input_shape_nchw")
    return _canonical_trtexec_argv(
        binary=binary,
        onnx_path=onnx_path,
        engine_path=destination.resolve(),
        input_name="images",
        shape=shape,
    )
