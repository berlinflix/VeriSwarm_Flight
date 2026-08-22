"""Training and evaluation contract for ``sar-rgb-person-state-v1``.

The rescue application contains a person detector and this companion state
classifier, but their weights and evidence stay independent.  This module
never changes the detector's ``person_candidate`` class and never writes state
fields into Suyash's rescue-event schema.

Importing this module performs no I/O and does not import Ultralytics.  The
training entry point accepts a model factory so tests can prove the complete
contract without CUDA, downloads, or a training run.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import csv
import importlib.metadata
import platform
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_io import (
    ArtifactIOError,
    atomic_create_json,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
)
from .cloud_environment import (
    CloudEnvironmentError,
    load_cloud_environment,
)
from .state_dataset import (
    SPLITS,
    STATE_CLASSES,
    STATE_MODEL_ID,
    WEAK_SUPERVISION_EVIDENCE_STATUS,
    StateDatasetError,
    validate_state_dataset_manifest,
)


STATE_TRAINING_PLAN_SCHEMA = "veriswarm.rescue.person_state_training_plan.v1"
STATE_TRAINING_RUN_SCHEMA = "veriswarm.rescue.person_state_training_run.v2"
STATE_EVALUATION_SCHEMA = "veriswarm.rescue.person_state_evaluation.v2"
STATE_SELECTION_SCHEMA = "veriswarm.rescue.person_state_selection.v2"
STATE_TEST_REPORT_SCHEMA = "veriswarm.rescue.person_state_test_evaluation.v2"
STATE_INFERENCE_SCHEMA = "veriswarm.rescue.person_state_inference.v2"
STATE_TEST_ONCE_SCHEMA = "veriswarm.rescue.person_state_test_once.v1"
STATE_CLASSIFIER_BASE_WEIGHTS_SCHEMA = (
    "veriswarm.rescue.classifier_base_weights.v1"
)

INPUT_SIZE = 224
SAFE_LABEL = "safe_walking"
DISTRESS_LABEL = "disaster_stressed"
SUPPORTED_ARCHITECTURES = frozenset({"yolov8n-cls.pt"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

CLASSIFIER_BASE_WEIGHTS_MANIFEST_RELATIVE_PATH = Path(
    "codebase/config/ultralytics_yolov8_classifier_base_weights.json"
)
TRUSTED_CLASSIFIER_BASE_WEIGHTS = {
    "publisher": "Ultralytics",
    "repository": "https://github.com/ultralytics/assets",
    "release_tag": "v8.4.0",
    "architecture": "yolov8n-cls.pt",
    "url": (
        "https://github.com/ultralytics/assets/releases/download/"
        "v8.4.0/yolov8n-cls.pt"
    ),
    "bytes": 5_563_076,
    "sha256": "11fa19f2aea79bc960d680a13f82f22105982b325eb9e17a4a5e1a9f8245980a",
}

# These are deliberately classification-only arguments.  The exact ordered
# crop list is supplied separately from the validated state-dataset records.
# ``stream=True`` keeps validation/test inference bounded for large datasets.
FROZEN_STATE_INFERENCE_ARGUMENTS = {
    "imgsz": INPUT_SIZE,
    "batch": 64,
    "device": 0,
    "half": False,
    "augment": False,
    "save": False,
    "stream": True,
    "verbose": False,
}

_PLAN_FIELDS = frozenset(
    {
        "schema",
        "model_id",
        "candidate_id",
        "architecture",
        "input_size_hw",
        "classes",
        "epochs",
        "batch",
        "seed",
        "deterministic",
        "cache",
        "dataset_manifest_sha256",
        "base_weights_sha256",
        "cloud_environment_sha256",
        "safe_thresholds",
    }
)
_PREDICTION_FIELDS = frozenset(
    {
        "sample_id",
        "split",
        "model_sha256",
        "crop_path",
        "crop_sha256",
        "probabilities",
    }
)
_INFERENCE_FIELDS = frozenset(
    {
        "schema",
        "model_id",
        "candidate_id",
        "architecture",
        "input_size_hw",
        "classes",
        "state_evidence_status",
        "split",
        "test_used_for_selection",
        "dataset_manifest_path",
        "dataset_manifest_sha256",
        "dataset_records_path",
        "dataset_records_sha256",
        "split_records_sha256",
        "training_report_path",
        "training_report_sha256",
        "training_plan_sha256",
        "model_path",
        "model_sha256",
        "environment_manifest_path",
        "environment_manifest_sha256",
        "environment_identity",
        "live_runtime_identity",
        "source_code_sha256",
        "inference_arguments",
        "selection_report_path",
        "selection_report_sha256",
        "test_once_receipt_path",
        "test_once_receipt_sha256",
        "predictions_path",
        "predictions_sha256",
        "sample_count",
        "started_at_utc",
        "completed_at_utc",
    }
)
_TEST_ONCE_FIELDS = frozenset(
    {
        "schema",
        "model_id",
        "split",
        "selection_report_path",
        "selection_report_sha256",
        "dataset_manifest_path",
        "dataset_manifest_sha256",
        "training_report_path",
        "training_report_sha256",
        "model_path",
        "model_sha256",
        "inference_report_path",
        "predictions_path",
        "reserved_at_utc",
    }
)
_SELECTION_FIELDS = frozenset(
    {
        "schema",
        "model_id",
        "dataset_manifest_sha256",
        "state_evidence_status",
        "selected_candidate_id",
        "selected_architecture",
        "input_size_hw",
        "selected_model_sha256",
        "selected_training_plan_sha256",
        "safe_threshold",
        "validation_report_path",
        "validation_report_sha256",
        "ranking_policy",
        "candidate_validation_reports",
    }
)
_TRAINING_RUN_FIELDS = frozenset(
    {
        "schema",
        "model_id",
        "candidate_id",
        "architecture",
        "input_size_hw",
        "classes",
        "dataset_manifest_path",
        "dataset_manifest_sha256",
        "dataset_records_sha256",
        "source_dataset_archives",
        "dataset_origin_is_label",
        "state_evidence_status",
        "weak_supervision_records",
        "weak_supervision_provenance",
        "training_plan_path",
        "training_plan_sha256",
        "source_code_sha256",
        "source_identity",
        "base_weights_manifest_path",
        "base_weights_manifest_sha256",
        "base_weights_provenance",
        "base_weights_path",
        "base_weights_sha256",
        "staged_base_weights_path",
        "staged_base_weights_sha256",
        "model_path",
        "model_sha256",
        "environment_manifest_path",
        "environment_manifest_sha256",
        "environment_identity",
        "live_runtime_identity",
        "deterministic_config",
        "effective_arguments",
        "resolved_batch_size",
        "metrics",
        "artifacts",
        "test_used_during_training_or_selection",
        "started_at_utc",
        "completed_at_utc",
    }
)
_EVALUATION_FIELDS = frozenset(
    {
        "schema",
        "model_id",
        "candidate_id",
        "architecture",
        "input_size_hw",
        "state_evidence_status",
        "split",
        "test_used_for_selection",
        "dataset_manifest_path",
        "dataset_manifest_sha256",
        "dataset_records_sha256",
        "training_plan_sha256",
        "training_report_path",
        "training_report_sha256",
        "model_path",
        "model_sha256",
        "predictions_path",
        "predictions_sha256",
        "inference_report_path",
        "inference_report_sha256",
        "sample_count",
        "threshold_points",
    }
)
_THRESHOLD_POINT_FIELDS = frozenset(
    {
        "safe_threshold",
        "accuracy",
        "balanced_accuracy",
        "false_safe_rate",
        "per_class",
        "confusion_matrix",
    }
)


class StateClassifierError(ValueError):
    """Classifier input, training evidence, or split use violates the contract."""


def _reject_constant(value: str) -> None:
    raise StateClassifierError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StateClassifierError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def _load_json(path: Path, field: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise StateClassifierError(f"{field} must be one regular file: {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except StateClassifierError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StateClassifierError(f"cannot read strict {field} JSON: {error}") from error
    if type(value) is not dict:
        raise StateClassifierError(f"{field} must contain one JSON object")
    return value


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise StateClassifierError(
            f"{field} fields differ; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise StateClassifierError(f"{field} must be non-empty trimmed text")
    return value


def _sha256(value: Any, field: str) -> str:
    value = _text(value, field)
    if not SHA256_RE.fullmatch(value):
        raise StateClassifierError(f"{field} must be a lowercase SHA-256")
    return value


def _validate_classifier_base_weights_manifest(
    manifest_path: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Load the one canonical classifier checkpoint allowlist.

    The detector uses a different manifest and remains deliberately outside
    this contract.  Both the path and every JSON value are frozen so a caller
    cannot bless an arbitrary pickle checkpoint by supplying a matching hash.
    """

    repository = Path(repository_root).resolve(strict=False)
    expected_path = repository / CLASSIFIER_BASE_WEIGHTS_MANIFEST_RELATIVE_PATH
    supplied_path = Path(os.path.abspath(os.fspath(manifest_path)))
    if supplied_path != expected_path:
        raise StateClassifierError(
            "classifier base-weights manifest path is not canonical"
        )
    manifest = _load_json(supplied_path, "classifier base-weights manifest")
    _exact_fields(
        manifest,
        frozenset({"schema", "publisher", "repository", "release_tag", "entries"}),
        "classifier base-weights manifest",
    )
    if manifest["schema"] != STATE_CLASSIFIER_BASE_WEIGHTS_SCHEMA:
        raise StateClassifierError("unsupported classifier base-weights manifest schema")
    for field in ("publisher", "repository", "release_tag"):
        if manifest[field] != TRUSTED_CLASSIFIER_BASE_WEIGHTS[field]:
            raise StateClassifierError(
                f"classifier base-weights manifest {field} is not frozen"
            )
    entries = manifest["entries"]
    architecture = TRUSTED_CLASSIFIER_BASE_WEIGHTS["architecture"]
    if type(entries) is not dict or set(entries) != {architecture}:
        raise StateClassifierError(
            "classifier base-weights manifest entries are not frozen"
        )
    entry = entries[architecture]
    if type(entry) is not dict:
        raise StateClassifierError("classifier base-weights entry must be an object")
    _exact_fields(
        entry,
        frozenset({"url", "bytes", "sha256"}),
        "classifier base-weights entry",
    )
    if entry["url"] != TRUSTED_CLASSIFIER_BASE_WEIGHTS["url"]:
        raise StateClassifierError("classifier base-weights URL is not frozen")
    if (
        type(entry["bytes"]) is not int
        or entry["bytes"] != TRUSTED_CLASSIFIER_BASE_WEIGHTS["bytes"]
    ):
        raise StateClassifierError("classifier base-weights byte size is not frozen")
    entry_hash = _sha256(entry["sha256"], "classifier base-weights SHA-256")
    if entry_hash != TRUSTED_CLASSIFIER_BASE_WEIGHTS["sha256"]:
        raise StateClassifierError("classifier base-weights SHA-256 is not frozen")
    return {
        "manifest_path": str(supplied_path),
        "manifest_sha256": sha256_file(supplied_path),
        "publisher": manifest["publisher"],
        "repository": manifest["repository"],
        "release_tag": manifest["release_tag"],
        "architecture": architecture,
        "url": entry["url"],
        "bytes": entry["bytes"],
        "sha256": entry_hash,
    }


def _verify_classifier_base_checkpoint(
    checkpoint: Path,
    *,
    architecture: str,
    plan_sha256: str,
    manifest_path: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    provenance = _validate_classifier_base_weights_manifest(
        manifest_path, repository_root
    )
    if architecture != provenance["architecture"]:
        raise StateClassifierError(
            "classifier architecture is absent from the canonical allowlist"
        )
    if checkpoint.is_symlink() or not checkpoint.is_file():
        raise StateClassifierError(
            f"base weights must be one local regular file: {checkpoint}"
        )
    if checkpoint.name != architecture:
        raise StateClassifierError("base weights filename does not match architecture")
    actual_bytes = checkpoint.stat().st_size
    if actual_bytes != provenance["bytes"]:
        raise StateClassifierError(
            "classifier base checkpoint byte-size mismatch: "
            f"expected {provenance['bytes']}, got {actual_bytes}"
        )
    actual_hash = sha256_file(checkpoint)
    if actual_hash != provenance["sha256"]:
        raise StateClassifierError(
            "classifier base checkpoint does not match the canonical allowlist"
        )
    if actual_hash != _sha256(plan_sha256, "training-plan base_weights_sha256"):
        raise StateClassifierError(
            "classifier base checkpoint SHA-256 differs from training plan"
        )
    return provenance


def _ratio(value: Any, field: str) -> float:
    if type(value) not in {int, float} or isinstance(value, bool):
        raise StateClassifierError(f"{field} must be a finite number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise StateClassifierError(f"{field} must be a finite number in [0, 1]")
    return result


def _strict_timestamp(value: str) -> str:
    value = _text(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise StateClassifierError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StateClassifierError("timestamp must include a timezone")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_state_training_plan(
    plan_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Load the create-once classifier plan and enforce its frozen semantics."""

    path = Path(plan_path).resolve(strict=False)
    plan = _load_json(path, "state training plan")
    _exact_fields(plan, _PLAN_FIELDS, "state training plan")
    if plan["schema"] != STATE_TRAINING_PLAN_SCHEMA:
        raise StateClassifierError("unsupported state training-plan schema")
    if plan["model_id"] != STATE_MODEL_ID:
        raise StateClassifierError("state training plan has the wrong model_id")
    candidate_id = _text(plan["candidate_id"], "candidate_id")
    architecture = plan["architecture"]
    if architecture not in SUPPORTED_ARCHITECTURES:
        raise StateClassifierError("architecture must be a frozen local YOLOv8 classifier")
    if plan["input_size_hw"] != [INPUT_SIZE, INPUT_SIZE]:
        raise StateClassifierError("state classifier input must be exactly 224x224")
    if plan["classes"] != STATE_CLASSES:
        raise StateClassifierError("state classifier classes are not frozen")
    for field in ("epochs", "batch"):
        if type(plan[field]) is not int or plan[field] <= 0:
            raise StateClassifierError(f"{field} must be a positive integer")
    if plan["seed"] != 0 or type(plan["seed"]) is not int:
        raise StateClassifierError("state classifier seed must be exactly 0")
    if plan["deterministic"] is not True:
        raise StateClassifierError("deterministic must be true")
    if plan["cache"] is not False:
        raise StateClassifierError("cache must be false for the first qualified run")
    for field in (
        "dataset_manifest_sha256",
        "base_weights_sha256",
        "cloud_environment_sha256",
    ):
        _sha256(plan[field], field)
    thresholds = plan["safe_thresholds"]
    if type(thresholds) is not list or not thresholds:
        raise StateClassifierError("safe_thresholds must be a non-empty JSON array")
    normalized = [_ratio(value, f"safe_thresholds[{index}]") for index, value in enumerate(thresholds)]
    if normalized != sorted(set(normalized)):
        raise StateClassifierError("safe_thresholds must be unique and strictly increasing")
    if normalized[0] <= 0.0 or normalized[-1] >= 1.0:
        raise StateClassifierError("safe_thresholds must lie strictly between 0 and 1")
    return {
        **plan,
        "candidate_id": candidate_id,
        "safe_thresholds": normalized,
        "plan_path": str(path),
        "training_plan_sha256": sha256_file(path),
    }


def _load_dataset_records(dataset_report: Mapping[str, Any]) -> dict[str, dict[str, dict[str, str]]]:
    """Reload the exact weak-label records needed for lineage and inference.

    The dataset validator has already checked every record and crop hash.  This
    second read retains the crop identity instead of reducing records to labels;
    otherwise a predictions file could name the right sample IDs while having
    been produced from unrelated image bytes.
    """

    result: dict[str, dict[str, dict[str, str]]] = {split: {} for split in SPLITS}
    records_path = Path(str(dataset_report["records_path"]))
    expected_hash = _sha256(dataset_report["records_sha256"], "dataset records SHA-256")
    if sha256_file(records_path) != expected_hash:
        raise StateClassifierError("state dataset records changed after validation")
    try:
        lines = records_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise StateClassifierError(f"cannot read state dataset records: {error}") from error
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise StateClassifierError(f"state dataset records contain blank line {line_number}")
        try:
            record = json.loads(
                line,
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
        except (json.JSONDecodeError, TypeError) as error:
            raise StateClassifierError(f"invalid state record line {line_number}: {error}") from error
        if type(record) is not dict:
            raise StateClassifierError(f"state record line {line_number} is not an object")
        split = record.get("split")
        sample_id = record.get("sample_id")
        label = record.get("state_label")
        crop_path = record.get("crop_path")
        crop_sha256 = record.get("crop_sha256")
        if (
            split not in SPLITS
            or type(sample_id) is not str
            or label not in STATE_CLASSES.values()
            or type(crop_path) is not str
        ):
            raise StateClassifierError(f"invalid state record identity at line {line_number}")
        _sha256(crop_sha256, f"state record line {line_number}.crop_sha256")
        if sample_id in result[split]:
            raise StateClassifierError(f"duplicate sample_id in {split}: {sample_id!r}")
        result[split][sample_id] = {
            "label": label,
            "source_dataset_id": str(record.get("source_dataset_id")),
            "source_group_id": str(record.get("source_group_id")),
            "crop_path": str(Path(crop_path).resolve(strict=False)),
            "crop_sha256": crop_sha256,
        }
    if sha256_file(records_path) != expected_hash:
        raise StateClassifierError("state dataset records changed while being read")
    return result


def _state_dataset(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        return validate_state_dataset_manifest(path)
    except StateDatasetError as error:
        raise StateClassifierError(f"state dataset rejected: {error}") from error


def _cloud_environment(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        return load_cloud_environment(path)
    except CloudEnvironmentError as error:
        raise StateClassifierError(f"cloud environment rejected: {error}") from error


def _live_runtime_identity(torch_module: Any | None = None) -> dict[str, Any]:
    """Inspect the exact interpreter, packages, CUDA runtime, and single GPU."""

    versions: dict[str, str] = {}
    try:
        for package in ("torch", "torchvision", "ultralytics"):
            versions[package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError as error:
        raise StateClassifierError(f"required training package is unavailable: {error}") from error
    if torch_module is None:
        try:
            import torch as torch_module  # type: ignore[no-redef]
        except ImportError as error:
            raise StateClassifierError("PyTorch is unavailable") from error
    try:
        if torch_module.cuda.is_available() is not True:
            raise StateClassifierError("CUDA is unavailable")
        device_count = int(torch_module.cuda.device_count())
        if device_count != 1:
            raise StateClassifierError(
                f"exactly one visible GPU is required, found {device_count}"
            )
        gpu_name = str(torch_module.cuda.get_device_name(0))
        memory_bytes = int(torch_module.cuda.get_device_properties(0).total_memory)
        cuda_runtime = str(torch_module.version.cuda)
        cudnn_version = int(torch_module.backends.cudnn.version())
    except StateClassifierError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise StateClassifierError(f"cannot inspect live CUDA runtime: {error}") from error
    return {
        "python": platform.python_version(),
        "torch": versions["torch"],
        "torchvision": versions["torchvision"],
        "ultralytics": versions["ultralytics"],
        "cuda_runtime": cuda_runtime,
        "cudnn_version": cudnn_version,
        "visible_gpu_count": device_count,
        "gpu_name": gpu_name,
        "gpu_total_memory_bytes": memory_bytes,
    }


def _verify_runtime_against_cloud(
    actual: Mapping[str, Any], cloud: Mapping[str, Any]
) -> dict[str, Any]:
    if type(actual) is not dict:
        raise StateClassifierError("live runtime evidence must be a JSON object")
    expected_runtime = cloud["runtime"]
    for key in ("python", "torch", "torchvision", "ultralytics", "cuda_runtime"):
        if actual.get(key) != expected_runtime[key]:
            raise StateClassifierError(
                f"live runtime {key!r} differs from cloud-environment evidence"
            )
    if actual.get("visible_gpu_count") != 1:
        raise StateClassifierError("live runtime must expose exactly one GPU")
    if actual.get("gpu_name") != cloud["gpu"]["name"]:
        raise StateClassifierError("live GPU identity differs from cloud manifest")
    if actual.get("gpu_total_memory_bytes") != cloud["gpu"]["memory_bytes"]:
        raise StateClassifierError("live GPU memory differs from cloud manifest")
    cudnn = actual.get("cudnn_version")
    if type(cudnn) is not int or cudnn <= 0:
        raise StateClassifierError("live cuDNN version is missing")
    return dict(actual)


def _source_identity(repository_root: Path) -> dict[str, Any]:
    try:
        status = subprocess.run(
            ["git", "-C", str(repository_root), "status", "--porcelain=v1"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if status.stdout.strip():
            raise StateClassifierError("training source checkout is dirty")
        commit = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip().lower()
        branch = subprocess.run(
            ["git", "-C", str(repository_root), "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
    except StateClassifierError:
        raise
    except (OSError, subprocess.CalledProcessError, UnicodeError) as error:
        raise StateClassifierError(f"cannot bind training source checkout: {error}") from error
    return _validate_source_identity({"commit": commit, "branch": branch, "clean": True})


def _validate_source_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"commit", "branch", "clean"}:
        raise StateClassifierError("source identity fields are not frozen")
    commit = value["commit"]
    if (
        type(commit) is not str
        or len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit.lower())
    ):
        raise StateClassifierError("source identity commit is invalid")
    branch = _text(value["branch"], "source identity branch")
    if value["clean"] is not True:
        raise StateClassifierError("training source checkout must be clean")
    return {"commit": commit.lower(), "branch": branch, "clean": True}


def _stage_checkpoint(source: Path, run_dir: Path, expected_hash: str) -> Path:
    staging = run_dir.parent / f".{run_dir.name}.verified-inputs"
    if os.path.lexists(staging):
        raise StateClassifierError(f"refusing to reuse training input staging: {staging}")
    destination = staging / source.name
    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir(exist_ok=False)
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except OSError as error:
        raise StateClassifierError(f"cannot stage verified base checkpoint: {error}") from error
    if sha256_file(destination) != expected_hash:
        raise StateClassifierError("staged base checkpoint SHA-256 mismatch")
    return destination


def _frozen_train_arguments(plan: Mapping[str, Any], crop_root: Path, run_dir: Path) -> dict[str, Any]:
    return {
        "data": str(crop_root),
        "imgsz": INPUT_SIZE,
        "epochs": plan["epochs"],
        "batch": plan["batch"],
        "seed": 0,
        "deterministic": True,
        "cache": False,
        "device": 0,
        "project": str(run_dir.parent),
        "name": run_dir.name,
        "exist_ok": False,
        "save": True,
        "save_period": 1,
        "amp": False,
        "val": True,
        "plots": False,
        "workers": 8,
        "resume": False,
    }


def _load_effective_arguments(
    path: Path,
    *,
    requested: Mapping[str, Any],
    staged_checkpoint: Path,
    loader: Callable[[Path], Any] | None,
) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise StateClassifierError(f"training args.yaml is missing: {path}")
    if loader is None:
        try:
            text = path.read_text(encoding="utf-8")
            try:
                value = json.loads(
                    text,
                    parse_constant=_reject_constant,
                    object_pairs_hook=_unique_object,
                )
            except json.JSONDecodeError:
                try:
                    import yaml
                except ImportError as error:
                    raise StateClassifierError("PyYAML is unavailable") from error
                value = yaml.safe_load(text)
        except Exception as error:
            if isinstance(error, StateClassifierError):
                raise
            raise StateClassifierError(f"cannot parse args.yaml: {error}") from error
    else:
        try:
            value = loader(path)
        except Exception as error:
            raise StateClassifierError(f"effective-argument loader failed: {error}") from error
    if type(value) is not dict:
        raise StateClassifierError("args.yaml must contain one mapping")
    for key, expected in requested.items():
        if key not in value:
            raise StateClassifierError(f"args.yaml is missing frozen argument {key!r}")
        actual = value[key]
        if key in {"data", "project"}:
            if Path(str(actual)).resolve(strict=False) != Path(str(expected)).resolve(strict=False):
                raise StateClassifierError(f"effective argument {key!r} changed")
        elif actual != expected:
            raise StateClassifierError(
                f"effective argument {key!r} changed: expected {expected!r}, got {actual!r}"
            )
    if Path(str(value.get("model"))).resolve(strict=False) != staged_checkpoint.resolve(strict=False):
        raise StateClassifierError("effective model is not the staged verified checkpoint")
    return {str(key): value[key] for key in sorted(value)}


def _read_classification_results(path: Path, expected_epochs: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise StateClassifierError(f"training results.csv is missing: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    except (OSError, UnicodeError, csv.Error) as error:
        raise StateClassifierError(f"cannot read results.csv: {error}") from error
    if len(rows) != expected_epochs:
        raise StateClassifierError(
            f"results.csv epoch count mismatch: expected {expected_epochs}, got {len(rows)}"
        )
    required = ("epoch", "train/loss", "val/loss", "metrics/accuracy_top1")
    parsed: list[dict[str, float]] = []
    for row_number, row in enumerate(rows, start=1):
        values: dict[str, float] = {}
        for field in required:
            if field not in row:
                raise StateClassifierError(f"results.csv is missing {field!r}")
            try:
                number = float(row[field])
            except (TypeError, ValueError) as error:
                raise StateClassifierError(f"results.csv {field!r} is not numeric") from error
            if not math.isfinite(number):
                raise StateClassifierError(f"results.csv {field!r} is non-finite")
            values[field] = number
        if int(values["epoch"]) != row_number:
            raise StateClassifierError("results.csv epochs must be complete and 1-based")
        if values["train/loss"] < 0 or values["val/loss"] < 0:
            raise StateClassifierError("results.csv loss is negative")
        if not 0.0 <= values["metrics/accuracy_top1"] <= 1.0:
            raise StateClassifierError("results.csv top-1 accuracy is outside [0, 1]")
        parsed.append(values)
    return {"epochs_completed": len(parsed), "all_values_finite": True, "final": parsed[-1]}


def run_state_classifier_training(
    *,
    training_plan_path: str | os.PathLike[str],
    state_dataset_manifest_path: str | os.PathLike[str],
    cloud_environment_path: str | os.PathLike[str],
    base_weights_path: str | os.PathLike[str],
    classifier_base_weights_manifest_path: str | os.PathLike[str],
    run_directory: str | os.PathLike[str],
    output_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    model_factory: Callable[[str], Any] | None = None,
    now: Callable[[], str] = _utc_now,
    runtime_evidence: Mapping[str, Any] | None = None,
    source_identity: Mapping[str, Any] | None = None,
    torch_module: Any | None = None,
    effective_arguments_loader: Callable[[Path], Any] | None = None,
) -> Path:
    """Run one deterministic classifier candidate after every evidence gate.

    ``model_factory`` is normally ``ultralytics.YOLO``.  Passing a local
    checkpoint path is intentional: architecture-name resolution is forbidden
    because it could initiate an unrecorded download.  The local checkpoint
    must match both the canonical classifier-only allowlist and the frozen plan
    hash before ``model_factory`` can deserialize it.
    """

    checkpoint_input = Path(base_weights_path).expanduser()
    if checkpoint_input.is_symlink():
        raise StateClassifierError(
            f"base weights must be one local regular file: {checkpoint_input}"
        )
    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        plan_path = require_path_within_workspace(training_plan_path, workspace)
        manifest_path = require_path_within_workspace(state_dataset_manifest_path, workspace)
        run_dir = require_path_within_workspace(run_directory, workspace)
        report_path = require_path_within_workspace(output_report_path, workspace)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error
    if run_dir.exists():
        raise StateClassifierError(f"refusing to reuse state classifier run: {run_dir}")

    plan = validate_state_training_plan(plan_path)
    dataset = _state_dataset(manifest_path)
    try:
        require_path_within_workspace(dataset["manifest_path"], workspace)
        require_path_within_workspace(dataset["records_path"], workspace)
        crop_root = require_path_within_workspace(dataset["crop_root"], workspace)
        for source in dataset["source_datasets"]:
            require_path_within_workspace(source["archive_path"], workspace)
        require_path_within_workspace(
            dataset["weak_supervision"]["labeler_artifact_path"], workspace
        )
        require_path_within_workspace(
            dataset["weak_supervision"]["policy_path"], workspace
        )
        environment_path = require_path_within_workspace(cloud_environment_path, workspace)
        checkpoint = require_path_within_workspace(checkpoint_input, workspace)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error
    environment = _cloud_environment(environment_path)
    if dataset["manifest_sha256"] != plan["dataset_manifest_sha256"]:
        raise StateClassifierError("state dataset manifest hash differs from training plan")
    base_provenance = _verify_classifier_base_checkpoint(
        checkpoint,
        architecture=plan["architecture"],
        plan_sha256=plan["base_weights_sha256"],
        manifest_path=classifier_base_weights_manifest_path,
        repository_root=repository_root,
    )
    if sha256_file(environment_path) != plan["cloud_environment_sha256"]:
        raise StateClassifierError("cloud environment SHA-256 differs from training plan")

    actual_runtime = _verify_runtime_against_cloud(
        dict(runtime_evidence) if runtime_evidence is not None else _live_runtime_identity(torch_module),
        environment,
    )
    source = _validate_source_identity(source_identity) if source_identity is not None else _source_identity(Path(repository_root).resolve(strict=False))
    staged_checkpoint = _stage_checkpoint(checkpoint, run_dir, plan["base_weights_sha256"])
    arguments = _frozen_train_arguments(plan, crop_root, run_dir)
    if model_factory is None:
        try:
            from ultralytics import YOLO
        except ImportError as error:
            raise StateClassifierError("Ultralytics is unavailable") from error
        model_factory = YOLO

    started = _strict_timestamp(now())
    try:
        model = model_factory(str(staged_checkpoint))
        result = model.train(**arguments)
    except Exception as error:
        raise StateClassifierError(f"state classifier training failed: {error}") from error

    trainer = getattr(model, "trainer", None)
    actual_save_dir = Path(
        str(getattr(trainer, "save_dir", getattr(result, "save_dir", run_dir)))
    ).resolve(strict=False)
    if actual_save_dir != run_dir.resolve(strict=False):
        raise StateClassifierError("trainer wrote outside the authorized run directory")
    artifacts = {
        "args_yaml": actual_save_dir / "args.yaml",
        "results_csv": actual_save_dir / "results.csv",
        "best_pt": actual_save_dir / "weights" / "best.pt",
        "last_pt": actual_save_dir / "weights" / "last.pt",
    }
    for label, path in artifacts.items():
        try:
            require_path_within_workspace(path, workspace)
        except ArtifactIOError as error:
            raise StateClassifierError(str(error)) from error
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise StateClassifierError(f"training artifact {label} is missing or empty: {path}")
    effective_arguments = _load_effective_arguments(
        artifacts["args_yaml"],
        requested=arguments,
        staged_checkpoint=staged_checkpoint,
        loader=effective_arguments_loader,
    )
    resolved_batch = getattr(trainer, "batch_size", None)
    if type(resolved_batch) is not int or resolved_batch <= 0:
        raise StateClassifierError("trainer did not expose a positive resolved batch size")
    if resolved_batch != plan["batch"]:
        raise StateClassifierError("trainer changed the frozen state-classifier batch size")
    metrics = _read_classification_results(artifacts["results_csv"], plan["epochs"])
    best = artifacts["best_pt"]

    # Detect manifest, crop, archive, or weak-label evidence mutation during training.
    dataset_after = _state_dataset(state_dataset_manifest_path)
    if dataset_after != dataset:
        raise StateClassifierError("state dataset evidence changed during training")
    base_provenance_after = _verify_classifier_base_checkpoint(
        checkpoint,
        architecture=plan["architecture"],
        plan_sha256=plan["base_weights_sha256"],
        manifest_path=classifier_base_weights_manifest_path,
        repository_root=repository_root,
    )
    if base_provenance_after != base_provenance:
        raise StateClassifierError("classifier base provenance changed during training")
    if sha256_file(staged_checkpoint) != plan["base_weights_sha256"]:
        raise StateClassifierError("staged base weights changed during training")
    if sha256_file(environment_path) != plan["cloud_environment_sha256"]:
        raise StateClassifierError("cloud environment evidence changed during training")
    if sha256_file(plan_path) != plan["training_plan_sha256"]:
        raise StateClassifierError("state training plan changed during training")

    payload = {
        "schema": STATE_TRAINING_RUN_SCHEMA,
        "model_id": STATE_MODEL_ID,
        "candidate_id": plan["candidate_id"],
        "architecture": plan["architecture"],
        "input_size_hw": [INPUT_SIZE, INPUT_SIZE],
        "classes": dict(STATE_CLASSES),
        "dataset_manifest_path": dataset["manifest_path"],
        "dataset_manifest_sha256": dataset["manifest_sha256"],
        "dataset_records_sha256": dataset["records_sha256"],
        "source_dataset_archives": [
            {"dataset_id": item["dataset_id"], "archive_sha256": item["archive_sha256"]}
            for item in dataset["source_datasets"]
        ],
        "dataset_origin_is_label": False,
        "state_evidence_status": dataset["state_evidence_status"],
        "weak_supervision_records": dataset["weak_supervision_records"],
        "weak_supervision_provenance": dataset["weak_supervision"],
        "training_plan_path": plan["plan_path"],
        "training_plan_sha256": plan["training_plan_sha256"],
        "source_code_sha256": sha256_file(Path(__file__).resolve()),
        "source_identity": source,
        "base_weights_manifest_path": base_provenance["manifest_path"],
        "base_weights_manifest_sha256": base_provenance["manifest_sha256"],
        "base_weights_provenance": base_provenance,
        "base_weights_path": str(checkpoint),
        "base_weights_sha256": plan["base_weights_sha256"],
        "staged_base_weights_path": str(staged_checkpoint),
        "staged_base_weights_sha256": sha256_file(staged_checkpoint),
        "model_path": str(best.resolve()),
        "model_sha256": sha256_file(best),
        "environment_manifest_path": str(environment_path),
        "environment_manifest_sha256": plan["cloud_environment_sha256"],
        "environment_identity": environment,
        "live_runtime_identity": actual_runtime,
        "deterministic_config": arguments,
        "effective_arguments": effective_arguments,
        "resolved_batch_size": resolved_batch,
        "metrics": metrics,
        "artifacts": {
            label: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for label, path in artifacts.items()
        },
        "test_used_during_training_or_selection": False,
        "started_at_utc": started,
        "completed_at_utc": _strict_timestamp(now()),
    }
    try:
        return atomic_create_json(report_path, payload)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error


def _read_predictions(
    path: Path,
    *,
    split: str,
    model_sha256: str,
    expected: Mapping[str, Mapping[str, str]],
) -> tuple[list[tuple[str, str, float]], str]:
    if path.is_symlink() or not path.is_file():
        raise StateClassifierError(f"predictions must be one regular JSONL file: {path}")
    initial_hash = sha256_file(path)
    rows: dict[str, tuple[str, str, float]] = {}
    observed_order: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise StateClassifierError(f"cannot read predictions: {error}") from error
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise StateClassifierError(f"predictions contain blank line {line_number}")
        try:
            raw = json.loads(line, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, TypeError) as error:
            raise StateClassifierError(f"invalid prediction line {line_number}: {error}") from error
        if type(raw) is not dict:
            raise StateClassifierError(f"prediction line {line_number} must be an object")
        _exact_fields(raw, _PREDICTION_FIELDS, f"prediction line {line_number}")
        sample_id = _text(raw["sample_id"], f"prediction line {line_number}.sample_id")
        if sample_id in rows:
            raise StateClassifierError(f"duplicate prediction sample_id: {sample_id!r}")
        if raw["split"] != split:
            raise StateClassifierError(f"prediction line {line_number} leaks another split")
        if raw["model_sha256"] != model_sha256:
            raise StateClassifierError(f"prediction line {line_number} has the wrong model hash")
        truth = expected.get(sample_id)
        if truth is None:
            raise StateClassifierError(f"prediction names unknown {split} sample {sample_id!r}")
        crop_path = Path(
            _text(raw["crop_path"], f"prediction line {line_number}.crop_path")
        ).resolve(strict=False)
        expected_crop_path = Path(truth["crop_path"]).resolve(strict=False)
        if crop_path != expected_crop_path:
            raise StateClassifierError(
                f"prediction line {line_number} is bound to the wrong crop path"
            )
        crop_hash = _sha256(
            raw["crop_sha256"], f"prediction line {line_number}.crop_sha256"
        )
        if crop_hash != truth["crop_sha256"] or sha256_file(crop_path) != crop_hash:
            raise StateClassifierError(
                f"prediction line {line_number} crop SHA-256 mismatch"
            )
        probabilities = raw["probabilities"]
        if type(probabilities) is not dict or set(probabilities) != set(STATE_CLASSES.values()):
            raise StateClassifierError("prediction probabilities must contain exactly both state classes")
        distress = _ratio(probabilities[DISTRESS_LABEL], "disaster_stressed probability")
        safe = _ratio(probabilities[SAFE_LABEL], "safe_walking probability")
        if not math.isclose(distress + safe, 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise StateClassifierError("prediction probabilities must sum to 1")
        rows[sample_id] = (sample_id, truth["label"], safe)
        observed_order.append(sample_id)
    if set(rows) != set(expected):
        missing = sorted(set(expected) - set(rows))
        raise StateClassifierError(f"predictions do not exactly cover {split}; missing={missing[:5]}")
    if observed_order != sorted(expected):
        raise StateClassifierError(
            f"predictions are not in the canonical {split} sample order"
        )
    final_hash = sha256_file(path)
    if final_hash != initial_hash:
        raise StateClassifierError("predictions changed while being evaluated")
    return [rows[key] for key in sorted(rows)], final_hash


def _metrics(rows: Sequence[tuple[str, str, float]], threshold: float) -> dict[str, Any]:
    threshold = _ratio(threshold, "safe threshold")
    matrix = {
        DISTRESS_LABEL: {DISTRESS_LABEL: 0, SAFE_LABEL: 0},
        SAFE_LABEL: {DISTRESS_LABEL: 0, SAFE_LABEL: 0},
    }
    for _sample_id, truth, safe_probability in rows:
        predicted = SAFE_LABEL if safe_probability >= threshold else DISTRESS_LABEL
        matrix[truth][predicted] += 1

    per_class: dict[str, dict[str, float]] = {}
    for label, other in ((DISTRESS_LABEL, SAFE_LABEL), (SAFE_LABEL, DISTRESS_LABEL)):
        tp = matrix[label][label]
        fp = matrix[other][label]
        fn = matrix[label][other]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        per_class[label] = {"precision": precision, "recall": recall}
    total = sum(sum(row.values()) for row in matrix.values())
    accuracy = (matrix[DISTRESS_LABEL][DISTRESS_LABEL] + matrix[SAFE_LABEL][SAFE_LABEL]) / total
    balanced = (per_class[DISTRESS_LABEL]["recall"] + per_class[SAFE_LABEL]["recall"]) / 2.0
    distress_total = sum(matrix[DISTRESS_LABEL].values())
    false_safe = matrix[DISTRESS_LABEL][SAFE_LABEL] / distress_total
    values = [accuracy, balanced, false_safe] + [
        metric for item in per_class.values() for metric in item.values()
    ]
    if not all(math.isfinite(value) for value in values):
        raise StateClassifierError("computed state-classifier metric is non-finite")
    return {
        "safe_threshold": threshold,
        "accuracy": accuracy,
        "balanced_accuracy": balanced,
        "false_safe_rate": false_safe,
        "per_class": per_class,
        "confusion_matrix": {
            "labels": [DISTRESS_LABEL, SAFE_LABEL],
            "rows_true_columns_predicted": [
                [matrix[DISTRESS_LABEL][DISTRESS_LABEL], matrix[DISTRESS_LABEL][SAFE_LABEL]],
                [matrix[SAFE_LABEL][DISTRESS_LABEL], matrix[SAFE_LABEL][SAFE_LABEL]],
            ],
        },
    }


def _workspace_path(
    value: Any, field: str, workspace: Path, *, file: bool = True
) -> Path:
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    try:
        path = require_path_within_workspace(_text(value, field), workspace)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error
    if file and (path.is_symlink() or not path.is_file()):
        raise StateClassifierError(f"{field} must be one regular file inside workspace")
    return path


def _validate_training_report(
    path: Path,
    workspace: Path,
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    report = _load_json(path, "state training report")
    _exact_fields(report, _TRAINING_RUN_FIELDS, "state training report")
    if report["schema"] != STATE_TRAINING_RUN_SCHEMA or report["model_id"] != STATE_MODEL_ID:
        raise StateClassifierError("unsupported state training report")
    if report["architecture"] not in SUPPORTED_ARCHITECTURES:
        raise StateClassifierError("training report architecture is unsupported")
    _text(report["candidate_id"], "candidate_id")
    if report["input_size_hw"] != [INPUT_SIZE, INPUT_SIZE] or report["classes"] != STATE_CLASSES:
        raise StateClassifierError("training report input/classes are not frozen")
    if report["dataset_origin_is_label"] is not False:
        raise StateClassifierError("training report used dataset origin as a label")
    if report["test_used_during_training_or_selection"] is not False:
        raise StateClassifierError("training report indicates test leakage")
    if report["state_evidence_status"] != WEAK_SUPERVISION_EVIDENCE_STATUS:
        raise StateClassifierError(
            "training report must mark state evidence weak_supervision_unverified"
        )
    if (
        type(report["weak_supervision_records"]) is not int
        or report["weak_supervision_records"] <= 0
    ):
        raise StateClassifierError("training report lacks weak-supervision records")
    for field in (
        "dataset_manifest_sha256",
        "dataset_records_sha256",
        "training_plan_sha256",
        "source_code_sha256",
        "base_weights_manifest_sha256",
        "base_weights_sha256",
        "staged_base_weights_sha256",
        "model_sha256",
        "environment_manifest_sha256",
    ):
        _sha256(report[field], field)
    if report["source_code_sha256"] != sha256_file(Path(__file__).resolve()):
        raise StateClassifierError("state-classifier source code differs from training evidence")
    _validate_source_identity(report["source_identity"])
    dataset_path = _workspace_path(report["dataset_manifest_path"], "dataset_manifest_path", workspace)
    plan_path = _workspace_path(report["training_plan_path"], "training_plan_path", workspace)
    base_path = _workspace_path(report["base_weights_path"], "base_weights_path", workspace)
    staged_path = _workspace_path(
        report["staged_base_weights_path"], "staged_base_weights_path", workspace
    )
    model_path = _workspace_path(report["model_path"], "model_path", workspace)
    environment_path = _workspace_path(
        report["environment_manifest_path"], "environment_manifest_path", workspace
    )
    for file_path, expected, field in (
        (dataset_path, report["dataset_manifest_sha256"], "dataset manifest"),
        (plan_path, report["training_plan_sha256"], "training plan"),
        (base_path, report["base_weights_sha256"], "base weights"),
        (staged_path, report["staged_base_weights_sha256"], "staged base weights"),
        (model_path, report["model_sha256"], "trained model"),
        (environment_path, report["environment_manifest_sha256"], "environment manifest"),
    ):
        if sha256_file(file_path) != expected:
            raise StateClassifierError(f"{field} SHA-256 mismatch")
    plan = validate_state_training_plan(plan_path)
    base_provenance = _verify_classifier_base_checkpoint(
        base_path,
        architecture=report["architecture"],
        plan_sha256=plan["base_weights_sha256"],
        manifest_path=_text(
            report["base_weights_manifest_path"], "base_weights_manifest_path"
        ),
        repository_root=repository_root,
    )
    if (
        report["base_weights_manifest_sha256"]
        != base_provenance["manifest_sha256"]
    ):
        raise StateClassifierError(
            "classifier base-weights manifest SHA-256 mismatch"
        )
    if report["base_weights_provenance"] != base_provenance:
        raise StateClassifierError("classifier base-weight provenance changed")
    if report["base_weights_sha256"] != base_provenance["sha256"]:
        raise StateClassifierError("training report base checkpoint is not allowlisted")
    dataset = _state_dataset(dataset_path)
    try:
        crop_root = require_path_within_workspace(dataset["crop_root"], workspace)
        require_path_within_workspace(dataset["records_path"], workspace)
        for source in dataset["source_datasets"]:
            require_path_within_workspace(source["archive_path"], workspace)
        require_path_within_workspace(
            dataset["weak_supervision"]["labeler_artifact_path"], workspace
        )
        require_path_within_workspace(
            dataset["weak_supervision"]["policy_path"], workspace
        )
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error
    if dataset["records_sha256"] != report["dataset_records_sha256"]:
        raise StateClassifierError("training report dataset records identity changed")
    if (
        report["state_evidence_status"] != dataset["state_evidence_status"]
        or report["weak_supervision_records"]
        != dataset["weak_supervision_records"]
        or report["weak_supervision_provenance"] != dataset["weak_supervision"]
    ):
        raise StateClassifierError(
            "training report weak-supervision provenance changed"
        )
    if (
        plan["candidate_id"] != report["candidate_id"]
        or plan["architecture"] != report["architecture"]
        or plan["dataset_manifest_sha256"] != report["dataset_manifest_sha256"]
        or plan["base_weights_sha256"] != report["base_weights_sha256"]
        or plan["cloud_environment_sha256"] != report["environment_manifest_sha256"]
    ):
        raise StateClassifierError("training report differs from its frozen plan")
    cloud = _cloud_environment(environment_path)
    if report["environment_identity"] != cloud:
        raise StateClassifierError("training report cloud environment identity changed")
    _verify_runtime_against_cloud(report["live_runtime_identity"], cloud)
    run_dir = model_path.parent.parent
    expected_arguments = _frozen_train_arguments(plan, crop_root, run_dir)
    if report["deterministic_config"] != expected_arguments:
        raise StateClassifierError("training report deterministic arguments changed")
    if report["resolved_batch_size"] != plan["batch"]:
        raise StateClassifierError("training report resolved batch differs from plan")
    artifacts = report["artifacts"]
    if type(artifacts) is not dict or set(artifacts) != {"args_yaml", "results_csv", "best_pt", "last_pt"}:
        raise StateClassifierError("training report artifact set is not frozen")
    for label, item in artifacts.items():
        if type(item) is not dict or set(item) != {"path", "sha256"}:
            raise StateClassifierError(f"training artifact {label} identity is malformed")
        artifact_path = _workspace_path(item["path"], f"artifacts.{label}.path", workspace)
        if sha256_file(artifact_path) != _sha256(item["sha256"], f"artifacts.{label}.sha256"):
            raise StateClassifierError(f"training artifact {label} SHA-256 mismatch")
    canonical_artifacts = {
        "args_yaml": run_dir / "args.yaml",
        "results_csv": run_dir / "results.csv",
        "best_pt": run_dir / "weights" / "best.pt",
        "last_pt": run_dir / "weights" / "last.pt",
    }
    for label, expected_path in canonical_artifacts.items():
        if Path(artifacts[label]["path"]).resolve(strict=False) != expected_path.resolve(strict=False):
            raise StateClassifierError(f"training artifact {label} path is not canonical")
    if Path(artifacts["best_pt"]["path"]).resolve(strict=False) != model_path:
        raise StateClassifierError("training report model_path is not its best.pt artifact")
    loaded_arguments = _load_effective_arguments(
        canonical_artifacts["args_yaml"],
        requested=expected_arguments,
        staged_checkpoint=staged_path,
        loader=None,
    )
    if report["effective_arguments"] != loaded_arguments:
        raise StateClassifierError("training report effective arguments differ from args.yaml")
    metrics = report["metrics"]
    if type(metrics) is not dict or metrics.get("epochs_completed") != plan["epochs"] or metrics.get("all_values_finite") is not True:
        raise StateClassifierError("training report does not prove complete finite epochs")
    _strict_timestamp(report["started_at_utc"])
    _strict_timestamp(report["completed_at_utc"])
    return report


def _split_records_sha256(records: Mapping[str, Mapping[str, str]]) -> str:
    payload = [
        {
            "sample_id": sample_id,
            "crop_path": str(Path(record["crop_path"]).resolve(strict=False)),
            "crop_sha256": record["crop_sha256"],
        }
        for sample_id, record in sorted(records.items())
    ]
    rendered = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _classification_names(value: Any, field: str) -> dict[int, str]:
    if not isinstance(value, Mapping):
        raise StateClassifierError(f"{field} must expose the exact state class map")
    normalized: dict[int, str] = {}
    for raw_key, raw_label in value.items():
        if isinstance(raw_key, bool):
            raise StateClassifierError(f"{field} contains a non-integer class index")
        if type(raw_key) is int:
            index = raw_key
        elif type(raw_key) is str and raw_key.isdigit():
            index = int(raw_key)
        else:
            raise StateClassifierError(f"{field} contains a non-integer class index")
        if index in normalized or type(raw_label) is not str:
            raise StateClassifierError(f"{field} contains a duplicate or invalid class")
        normalized[index] = raw_label
    expected = {int(index): label for index, label in STATE_CLASSES.items()}
    if normalized != expected:
        raise StateClassifierError(f"{field} differs from the frozen state class map")
    return normalized


def _classification_probabilities(result: Any, field: str) -> list[float]:
    probs = getattr(result, "probs", None)
    data = getattr(probs, "data", None)
    if data is None:
        raise StateClassifierError(f"{field} does not contain classification probabilities")
    for method in ("detach", "cpu"):
        operation = getattr(data, method, None)
        if callable(operation):
            data = operation()
    converter = getattr(data, "tolist", None)
    if callable(converter):
        data = converter()
    if not isinstance(data, (list, tuple)) or len(data) != len(STATE_CLASSES):
        raise StateClassifierError(f"{field} must contain exactly two probabilities")
    values: list[float] = []
    for index, raw in enumerate(data):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise StateClassifierError(f"{field}[{index}] must be numeric")
        value = float(raw)
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            raise StateClassifierError(f"{field}[{index}] must be finite and in [0, 1]")
        values.append(value)
    if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise StateClassifierError(f"{field} must sum to 1")
    return values


def _publish_create_once(temporary: str, destination: Path) -> None:
    try:
        if os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
    except FileExistsError as error:
        raise StateClassifierError(
            f"refusing to overwrite existing artifact: {destination}"
        ) from error
    except OSError as error:
        raise StateClassifierError(
            f"atomic create-once publish failed for {destination}: {error}"
        ) from error


def _run_prediction_stream(
    *,
    model: Any,
    records: Mapping[str, Mapping[str, str]],
    split: str,
    model_sha256: str,
    destination: Path,
) -> tuple[int, str]:
    """Run one ordered crop stream and atomically publish canonical JSONL."""

    if os.path.lexists(destination):
        raise StateClassifierError(f"refusing to overwrite existing artifact: {destination}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise StateClassifierError(
            f"cannot create predictions directory {destination.parent}: {error}"
        ) from error
    ordered = sorted(records.items())
    sources = [str(Path(record["crop_path"]).resolve(strict=False)) for _, record in ordered]
    _classification_names(getattr(model, "names", None), "loaded model names")
    try:
        results = iter(
            model.predict(
                source=sources,
                **dict(FROZEN_STATE_INFERENCE_ARGUMENTS),
            )
        )
    except Exception as error:
        raise StateClassifierError(f"state classifier inference failed: {error}") from error

    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            for index, (sample_id, record) in enumerate(ordered):
                crop_path = Path(record["crop_path"]).resolve(strict=False)
                if crop_path.is_symlink() or not crop_path.is_file():
                    raise StateClassifierError(
                        f"inference crop is missing or unsafe: {crop_path}"
                    )
                if sha256_file(crop_path) != record["crop_sha256"]:
                    raise StateClassifierError(
                        f"inference crop SHA-256 mismatch for {sample_id!r}"
                    )
                try:
                    result = next(results)
                except StopIteration as error:
                    raise StateClassifierError(
                        "model returned fewer classification results than input crops"
                    ) from error
                except Exception as error:
                    raise StateClassifierError(
                        f"state classifier inference failed at sample {sample_id!r}: {error}"
                    ) from error
                result_path = Path(
                    _text(getattr(result, "path", None), f"inference result {index}.path")
                ).resolve(strict=False)
                if result_path != crop_path:
                    raise StateClassifierError(
                        f"inference result {index} is bound to the wrong crop"
                    )
                result_names = getattr(result, "names", getattr(model, "names", None))
                _classification_names(result_names, f"inference result {index}.names")
                values = _classification_probabilities(
                    result, f"inference result {index}.probabilities"
                )
                row = {
                    "sample_id": sample_id,
                    "split": split,
                    "model_sha256": model_sha256,
                    "crop_path": str(crop_path),
                    "crop_sha256": record["crop_sha256"],
                    "probabilities": {
                        DISTRESS_LABEL: values[0],
                        SAFE_LABEL: values[1],
                    },
                }
                stream.write(
                    json.dumps(
                        row,
                        allow_nan=False,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            try:
                next(results)
            except StopIteration:
                pass
            except Exception as error:
                raise StateClassifierError(
                    f"state classifier inference failed after expected samples: {error}"
                ) from error
            else:
                raise StateClassifierError(
                    "model returned more classification results than input crops"
                )
            stream.flush()
            os.fsync(stream.fileno())
        _publish_create_once(temporary, destination)
    except StateClassifierError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise StateClassifierError(f"cannot create prediction evidence: {error}") from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass
    return len(ordered), sha256_file(destination)


def _test_once_receipt_path(workspace: Path, selection_hash: str) -> Path:
    return workspace / ".state-test-once" / f"{selection_hash}.json"


def _reserve_test_once(
    *,
    workspace: Path,
    selection_path: Path,
    selection_hash: str,
    dataset: Mapping[str, Any],
    training_path: Path,
    training_hash: str,
    model_path: Path,
    model_hash: str,
    inference_report_path: Path,
    predictions_path: Path,
    reserved_at: str,
) -> tuple[Path, str]:
    receipt_path = _test_once_receipt_path(workspace, selection_hash)
    if os.path.lexists(receipt_path):
        raise StateClassifierError(
            "untouched test inference was already reserved for this selection"
        )
    payload = {
        "schema": STATE_TEST_ONCE_SCHEMA,
        "model_id": STATE_MODEL_ID,
        "split": "test",
        "selection_report_path": str(selection_path),
        "selection_report_sha256": selection_hash,
        "dataset_manifest_path": dataset["manifest_path"],
        "dataset_manifest_sha256": dataset["manifest_sha256"],
        "training_report_path": str(training_path),
        "training_report_sha256": training_hash,
        "model_path": str(model_path),
        "model_sha256": model_hash,
        "inference_report_path": str(inference_report_path),
        "predictions_path": str(predictions_path),
        "reserved_at_utc": _strict_timestamp(reserved_at),
    }
    try:
        created = atomic_create_json(receipt_path, payload)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error
    return created, sha256_file(created)


def run_state_classifier_inference(
    *,
    training_report_path: str | os.PathLike[str],
    state_dataset_manifest_path: str | os.PathLike[str],
    split: str,
    predictions_output_path: str | os.PathLike[str],
    output_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    selection_report_path: str | os.PathLike[str] | None = None,
    model_factory: Callable[[str], Any] | None = None,
    now: Callable[[], str] = _utc_now,
    runtime_evidence: Mapping[str, Any] | None = None,
    torch_module: Any | None = None,
) -> Path:
    """Run the exact trained classifier over the exact validation or test crops.

    The evaluator never accepts probabilities directly.  This is the sole
    producer: it verifies model, dataset, crop, cloud-runtime and (for test)
    validation-selection lineage before lazy model deserialization.
    """

    if split not in {"val", "test"}:
        raise StateClassifierError("state inference split must be exactly val or test")
    repository = Path(repository_root).resolve(strict=False)
    workspace = require_external_workspace(workspace_root, repository)
    try:
        training_path = require_path_within_workspace(training_report_path, workspace)
        manifest_path = require_path_within_workspace(state_dataset_manifest_path, workspace)
        predictions_path = require_path_within_workspace(predictions_output_path, workspace)
        report_path = require_path_within_workspace(output_report_path, workspace)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error
    if predictions_path == report_path:
        raise StateClassifierError("predictions and inference report paths must be distinct")
    if predictions_path.suffix.lower() != ".jsonl":
        raise StateClassifierError("prediction evidence must use a .jsonl path")
    if report_path.suffix.lower() != ".json":
        raise StateClassifierError("inference report must use a .json path")
    for output in (predictions_path, report_path):
        if os.path.lexists(output):
            raise StateClassifierError(f"refusing to overwrite existing artifact: {output}")

    training = _validate_training_report(training_path, workspace, repository)
    dataset = _state_dataset(manifest_path)
    if training["dataset_manifest_sha256"] != dataset["manifest_sha256"]:
        raise StateClassifierError("training and inference dataset manifests differ")
    try:
        require_path_within_workspace(dataset["records_path"], workspace)
        require_path_within_workspace(dataset["crop_root"], workspace)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error
    records = _load_dataset_records(dataset)[split]
    if not records:
        raise StateClassifierError(f"state inference split {split!r} is empty")
    for sample_id, record in records.items():
        try:
            require_path_within_workspace(record["crop_path"], workspace)
        except ArtifactIOError as error:
            raise StateClassifierError(f"crop {sample_id!r}: {error}") from error

    model_path = _workspace_path(training["model_path"], "trained model path", workspace)
    model_hash = _sha256(training["model_sha256"], "trained model SHA-256")
    if sha256_file(model_path) != model_hash:
        raise StateClassifierError("trained model changed before inference")
    environment_path = _workspace_path(
        training["environment_manifest_path"], "environment manifest path", workspace
    )
    environment = _cloud_environment(environment_path)
    actual_runtime = _verify_runtime_against_cloud(
        dict(runtime_evidence)
        if runtime_evidence is not None
        else _live_runtime_identity(torch_module),
        environment,
    )

    selection_path: Path | None = None
    selection_hash: str | None = None
    receipt_path: Path | None = None
    receipt_hash: str | None = None
    if split == "val":
        if selection_report_path is not None:
            raise StateClassifierError("validation inference must not consume selection evidence")
    else:
        if selection_report_path is None:
            raise StateClassifierError("test inference requires frozen validation selection")
        selection_path = _workspace_path(
            selection_report_path, "state selection report path", workspace
        )
        selection = _validate_selection_report(selection_path, workspace, repository)
        selection_hash = sha256_file(selection_path)
        expected = {
            "selected_candidate_id": training["candidate_id"],
            "selected_architecture": training["architecture"],
            "selected_model_sha256": model_hash,
            "selected_training_plan_sha256": training["training_plan_sha256"],
            "dataset_manifest_sha256": dataset["manifest_sha256"],
            "state_evidence_status": training["state_evidence_status"],
        }
        for field, value in expected.items():
            if selection[field] != value:
                raise StateClassifierError(
                    f"test inference selection {field} differs from trained model lineage"
                )
        receipt_path, receipt_hash = _reserve_test_once(
            workspace=workspace,
            selection_path=selection_path,
            selection_hash=selection_hash,
            dataset=dataset,
            training_path=training_path,
            training_hash=sha256_file(training_path),
            model_path=model_path,
            model_hash=model_hash,
            inference_report_path=report_path,
            predictions_path=predictions_path,
            reserved_at=now(),
        )

    if model_factory is None:
        try:
            from ultralytics import YOLO
        except ImportError as error:
            raise StateClassifierError("Ultralytics is unavailable") from error
        model_factory = YOLO
    started = _strict_timestamp(now())
    try:
        model = model_factory(str(model_path))
    except Exception as error:
        raise StateClassifierError(f"state classifier model loading failed: {error}") from error
    sample_count, predictions_hash = _run_prediction_stream(
        model=model,
        records=records,
        split=split,
        model_sha256=model_hash,
        destination=predictions_path,
    )

    training_after = _validate_training_report(training_path, workspace, repository)
    dataset_after = _state_dataset(manifest_path)
    if training_after != training:
        raise StateClassifierError("training evidence changed during state inference")
    if dataset_after != dataset:
        raise StateClassifierError("dataset evidence changed during state inference")
    if sha256_file(model_path) != model_hash:
        raise StateClassifierError("trained model changed during state inference")
    rows, verified_predictions_hash = _read_predictions(
        predictions_path,
        split=split,
        model_sha256=model_hash,
        expected=records,
    )
    if verified_predictions_hash != predictions_hash or len(rows) != sample_count:
        raise StateClassifierError("prediction evidence changed after inference")

    payload = {
        "schema": STATE_INFERENCE_SCHEMA,
        "model_id": STATE_MODEL_ID,
        "candidate_id": training["candidate_id"],
        "architecture": training["architecture"],
        "input_size_hw": [INPUT_SIZE, INPUT_SIZE],
        "classes": dict(STATE_CLASSES),
        "state_evidence_status": training["state_evidence_status"],
        "split": split,
        "test_used_for_selection": False,
        "dataset_manifest_path": dataset["manifest_path"],
        "dataset_manifest_sha256": dataset["manifest_sha256"],
        "dataset_records_path": dataset["records_path"],
        "dataset_records_sha256": dataset["records_sha256"],
        "split_records_sha256": _split_records_sha256(records),
        "training_report_path": str(training_path),
        "training_report_sha256": sha256_file(training_path),
        "training_plan_sha256": training["training_plan_sha256"],
        "model_path": str(model_path),
        "model_sha256": model_hash,
        "environment_manifest_path": str(environment_path),
        "environment_manifest_sha256": sha256_file(environment_path),
        "environment_identity": environment,
        "live_runtime_identity": actual_runtime,
        "source_code_sha256": sha256_file(Path(__file__).resolve()),
        "inference_arguments": dict(FROZEN_STATE_INFERENCE_ARGUMENTS),
        "selection_report_path": None if selection_path is None else str(selection_path),
        "selection_report_sha256": selection_hash,
        "test_once_receipt_path": None if receipt_path is None else str(receipt_path),
        "test_once_receipt_sha256": receipt_hash,
        "predictions_path": str(predictions_path),
        "predictions_sha256": predictions_hash,
        "sample_count": sample_count,
        "started_at_utc": started,
        "completed_at_utc": _strict_timestamp(now()),
    }
    try:
        return atomic_create_json(report_path, payload)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error


def _validate_test_once_receipt(
    path: Path,
    *,
    workspace: Path,
    inference_report: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = _load_json(path, "state test-once receipt")
    _exact_fields(receipt, _TEST_ONCE_FIELDS, "state test-once receipt")
    if (
        receipt["schema"] != STATE_TEST_ONCE_SCHEMA
        or receipt["model_id"] != STATE_MODEL_ID
        or receipt["split"] != "test"
    ):
        raise StateClassifierError("unsupported state test-once receipt")
    expected = {
        "selection_report_path": inference_report["selection_report_path"],
        "selection_report_sha256": inference_report["selection_report_sha256"],
        "dataset_manifest_path": inference_report["dataset_manifest_path"],
        "dataset_manifest_sha256": inference_report["dataset_manifest_sha256"],
        "training_report_path": inference_report["training_report_path"],
        "training_report_sha256": inference_report["training_report_sha256"],
        "model_path": inference_report["model_path"],
        "model_sha256": inference_report["model_sha256"],
        "inference_report_path": inference_report["inference_report_path"],
        "predictions_path": inference_report["predictions_path"],
    }
    for field, value in expected.items():
        if receipt[field] != value:
            raise StateClassifierError(f"state test-once receipt {field} changed")
    _strict_timestamp(receipt["reserved_at_utc"])
    selection_hash = _sha256(
        receipt["selection_report_sha256"], "test-once selection SHA-256"
    )
    if path != _test_once_receipt_path(workspace, selection_hash):
        raise StateClassifierError("state test-once receipt path is not canonical")
    return receipt


def _validate_inference_report(
    path: Path,
    *,
    workspace: Path,
    repository_root: Path,
    expected_split: str,
) -> tuple[dict[str, Any], list[tuple[str, str, float]]]:
    initial_hash = sha256_file(path)
    report = _load_json(path, "state inference report")
    _exact_fields(report, _INFERENCE_FIELDS, "state inference report")
    if report["schema"] != STATE_INFERENCE_SCHEMA or report["model_id"] != STATE_MODEL_ID:
        raise StateClassifierError("unsupported state inference report")
    if report["split"] != expected_split or expected_split not in {"val", "test"}:
        raise StateClassifierError("state inference report uses the wrong split")
    if report["test_used_for_selection"] is not False:
        raise StateClassifierError("state inference evidence indicates test leakage")
    if report["state_evidence_status"] != WEAK_SUPERVISION_EVIDENCE_STATUS:
        raise StateClassifierError(
            "state inference must remain weak_supervision_unverified"
        )
    if report["input_size_hw"] != [INPUT_SIZE, INPUT_SIZE] or report["classes"] != STATE_CLASSES:
        raise StateClassifierError("state inference input/classes are not frozen")
    if report["architecture"] not in SUPPORTED_ARCHITECTURES:
        raise StateClassifierError("state inference architecture is unsupported")
    _text(report["candidate_id"], "state inference candidate_id")
    for field in (
        "dataset_manifest_sha256",
        "dataset_records_sha256",
        "split_records_sha256",
        "training_report_sha256",
        "training_plan_sha256",
        "model_sha256",
        "environment_manifest_sha256",
        "source_code_sha256",
        "predictions_sha256",
    ):
        _sha256(report[field], f"state inference {field}")
    if report["source_code_sha256"] != sha256_file(Path(__file__).resolve()):
        raise StateClassifierError("state inference source code identity changed")
    if report["inference_arguments"] != FROZEN_STATE_INFERENCE_ARGUMENTS:
        raise StateClassifierError("state inference arguments are not frozen")

    dataset_path = _workspace_path(
        report["dataset_manifest_path"], "inference dataset manifest path", workspace
    )
    records_path = _workspace_path(
        report["dataset_records_path"], "inference dataset records path", workspace
    )
    training_path = _workspace_path(
        report["training_report_path"], "inference training report path", workspace
    )
    model_path = _workspace_path(report["model_path"], "inference model path", workspace)
    environment_path = _workspace_path(
        report["environment_manifest_path"], "inference environment manifest path", workspace
    )
    predictions_path = _workspace_path(
        report["predictions_path"], "inference predictions path", workspace
    )
    for artifact, digest, label in (
        (dataset_path, report["dataset_manifest_sha256"], "dataset manifest"),
        (records_path, report["dataset_records_sha256"], "dataset records"),
        (training_path, report["training_report_sha256"], "training report"),
        (model_path, report["model_sha256"], "model"),
        (environment_path, report["environment_manifest_sha256"], "environment manifest"),
        (predictions_path, report["predictions_sha256"], "predictions"),
    ):
        if sha256_file(artifact) != digest:
            raise StateClassifierError(f"state inference {label} SHA-256 mismatch")

    dataset = _state_dataset(dataset_path)
    training = _validate_training_report(training_path, workspace, repository_root)
    bindings = {
        "candidate_id": training["candidate_id"],
        "architecture": training["architecture"],
        "dataset_manifest_sha256": training["dataset_manifest_sha256"],
        "dataset_records_sha256": training["dataset_records_sha256"],
        "training_plan_sha256": training["training_plan_sha256"],
        "model_path": training["model_path"],
        "model_sha256": training["model_sha256"],
        "environment_manifest_path": training["environment_manifest_path"],
        "environment_manifest_sha256": training["environment_manifest_sha256"],
        "state_evidence_status": training["state_evidence_status"],
    }
    for field, value in bindings.items():
        if report[field] != value:
            raise StateClassifierError(f"state inference {field} differs from training lineage")
    if dataset["manifest_sha256"] != report["dataset_manifest_sha256"]:
        raise StateClassifierError("state inference dataset identity changed")
    if dataset["records_path"] != report["dataset_records_path"]:
        raise StateClassifierError("state inference records path changed")
    environment = _cloud_environment(environment_path)
    if report["environment_identity"] != environment:
        raise StateClassifierError("state inference environment identity changed")
    _verify_runtime_against_cloud(report["live_runtime_identity"], environment)

    split_records = _load_dataset_records(dataset)[expected_split]
    if report["split_records_sha256"] != _split_records_sha256(split_records):
        raise StateClassifierError("state inference split-record identity changed")
    sample_count = report["sample_count"]
    if type(sample_count) is not int or sample_count != len(split_records):
        raise StateClassifierError("state inference sample_count differs from exact split")
    rows, prediction_hash = _read_predictions(
        predictions_path,
        split=expected_split,
        model_sha256=report["model_sha256"],
        expected=split_records,
    )
    if prediction_hash != report["predictions_sha256"]:
        raise StateClassifierError("state inference prediction identity changed")

    report_with_path = {**report, "inference_report_path": str(path)}
    if expected_split == "val":
        for field in (
            "selection_report_path",
            "selection_report_sha256",
            "test_once_receipt_path",
            "test_once_receipt_sha256",
        ):
            if report[field] is not None:
                raise StateClassifierError("validation inference contains test-selection evidence")
    else:
        selection_path = _workspace_path(
            report["selection_report_path"], "inference selection report path", workspace
        )
        selection_hash = _sha256(
            report["selection_report_sha256"], "inference selection report SHA-256"
        )
        if sha256_file(selection_path) != selection_hash:
            raise StateClassifierError("state inference selection report SHA-256 mismatch")
        selection = _validate_selection_report(
            selection_path, workspace, repository_root
        )
        selected_bindings = {
            "selected_candidate_id": report["candidate_id"],
            "selected_architecture": report["architecture"],
            "selected_model_sha256": report["model_sha256"],
            "selected_training_plan_sha256": report["training_plan_sha256"],
            "dataset_manifest_sha256": report["dataset_manifest_sha256"],
        }
        for field, value in selected_bindings.items():
            if selection[field] != value:
                raise StateClassifierError(
                    f"state inference selection {field} differs from inference lineage"
                )
        receipt_path = _workspace_path(
            report["test_once_receipt_path"], "test-once receipt path", workspace
        )
        receipt_hash = _sha256(
            report["test_once_receipt_sha256"], "test-once receipt SHA-256"
        )
        if sha256_file(receipt_path) != receipt_hash:
            raise StateClassifierError("state test-once receipt SHA-256 mismatch")
        _validate_test_once_receipt(
            receipt_path,
            workspace=workspace,
            inference_report=report_with_path,
        )
    _strict_timestamp(report["started_at_utc"])
    _strict_timestamp(report["completed_at_utc"])
    if sha256_file(path) != initial_hash:
        raise StateClassifierError("state inference report changed while being validated")
    return report, rows


def _validate_threshold_point(point: Any, sample_count: int, field: str) -> dict[str, Any]:
    if type(point) is not dict:
        raise StateClassifierError(f"{field} must be an object")
    _exact_fields(point, _THRESHOLD_POINT_FIELDS, field)
    threshold = _ratio(point["safe_threshold"], f"{field}.safe_threshold")
    matrix = point["confusion_matrix"]
    if type(matrix) is not dict or set(matrix) != {"labels", "rows_true_columns_predicted"}:
        raise StateClassifierError(f"{field}.confusion_matrix is malformed")
    if matrix["labels"] != [DISTRESS_LABEL, SAFE_LABEL]:
        raise StateClassifierError(f"{field}.confusion_matrix labels changed")
    rows = matrix["rows_true_columns_predicted"]
    if (
        type(rows) is not list
        or len(rows) != 2
        or any(type(row) is not list or len(row) != 2 for row in rows)
        or any(type(value) is not int or value < 0 for row in rows for value in row)
        or sum(value for row in rows for value in row) != sample_count
    ):
        raise StateClassifierError(f"{field}.confusion_matrix counts are invalid")
    synthetic_rows: list[tuple[str, str, float]] = []
    for truth_index, truth in enumerate((DISTRESS_LABEL, SAFE_LABEL)):
        for predicted_index, count in enumerate(rows[truth_index]):
            predicted_safe = predicted_index == 1
            probability = threshold if predicted_safe else max(0.0, threshold - 0.01)
            synthetic_rows.extend((f"{truth}-{len(synthetic_rows)}", truth, probability) for _ in range(count))
    recomputed = _metrics(synthetic_rows, threshold)
    for key in ("accuracy", "balanced_accuracy", "false_safe_rate"):
        actual = _ratio(point[key], f"{field}.{key}")
        if not math.isclose(actual, recomputed[key], rel_tol=0.0, abs_tol=1e-12):
            raise StateClassifierError(f"{field}.{key} disagrees with confusion matrix")
    if point["per_class"] != recomputed["per_class"]:
        raise StateClassifierError(f"{field}.per_class disagrees with confusion matrix")
    return point


def _validate_validation_report(
    path: Path,
    workspace: Path,
    repository_root: Path,
) -> dict[str, Any]:
    report = _load_json(path, "state validation report")
    if report.get("split") != "val" or report.get("test_used_for_selection") not in {False, None}:
        raise StateClassifierError("selection accepts validation reports only; test leakage forbidden")
    _exact_fields(report, _EVALUATION_FIELDS, "state validation report")
    if report["schema"] != STATE_EVALUATION_SCHEMA or report["model_id"] != STATE_MODEL_ID:
        raise StateClassifierError("unsupported state validation report")
    if report["split"] != "val" or report["test_used_for_selection"] is not False:
        raise StateClassifierError("selection accepts validation reports only; test leakage forbidden")
    if report["state_evidence_status"] != WEAK_SUPERVISION_EVIDENCE_STATUS:
        raise StateClassifierError(
            "validation evidence must remain weak_supervision_unverified"
        )
    if report["input_size_hw"] != [INPUT_SIZE, INPUT_SIZE] or report["architecture"] not in SUPPORTED_ARCHITECTURES:
        raise StateClassifierError("validation candidate identity is unsupported")
    _text(report["candidate_id"], "candidate_id")
    for field in (
        "dataset_manifest_sha256", "dataset_records_sha256", "training_plan_sha256",
        "training_report_sha256", "model_sha256", "predictions_sha256",
        "inference_report_sha256",
    ):
        _sha256(report[field], field)
    dataset_path = _workspace_path(report["dataset_manifest_path"], "dataset_manifest_path", workspace)
    training_path = _workspace_path(report["training_report_path"], "training_report_path", workspace)
    model_path = _workspace_path(report["model_path"], "model_path", workspace)
    predictions_path = _workspace_path(report["predictions_path"], "predictions_path", workspace)
    inference_path = _workspace_path(
        report["inference_report_path"], "inference_report_path", workspace
    )
    for file_path, expected, label in (
        (dataset_path, report["dataset_manifest_sha256"], "dataset manifest"),
        (training_path, report["training_report_sha256"], "training report"),
        (model_path, report["model_sha256"], "model"),
        (predictions_path, report["predictions_sha256"], "predictions"),
        (inference_path, report["inference_report_sha256"], "inference report"),
    ):
        if sha256_file(file_path) != expected:
            raise StateClassifierError(f"validation-linked {label} SHA-256 mismatch")
    dataset = _state_dataset(dataset_path)
    training = _validate_training_report(training_path, workspace, repository_root)
    inference, prediction_rows = _validate_inference_report(
        inference_path,
        workspace=workspace,
        repository_root=repository_root,
        expected_split="val",
    )
    bindings = {
        "candidate_id": training["candidate_id"],
        "architecture": training["architecture"],
        "input_size_hw": training["input_size_hw"],
        "dataset_manifest_sha256": training["dataset_manifest_sha256"],
        "dataset_records_sha256": training["dataset_records_sha256"],
        "training_plan_sha256": training["training_plan_sha256"],
        "model_path": training["model_path"],
        "model_sha256": training["model_sha256"],
        "state_evidence_status": training["state_evidence_status"],
    }
    for key, expected in bindings.items():
        if report[key] != expected:
            raise StateClassifierError(f"validation report {key} differs from training lineage")
    if dataset["manifest_sha256"] != report["dataset_manifest_sha256"]:
        raise StateClassifierError("validation dataset manifest identity changed")
    inference_bindings = {
        "candidate_id": report["candidate_id"],
        "architecture": report["architecture"],
        "dataset_manifest_sha256": report["dataset_manifest_sha256"],
        "dataset_records_sha256": report["dataset_records_sha256"],
        "training_plan_sha256": report["training_plan_sha256"],
        "training_report_path": report["training_report_path"],
        "training_report_sha256": report["training_report_sha256"],
        "model_path": report["model_path"],
        "model_sha256": report["model_sha256"],
        "state_evidence_status": report["state_evidence_status"],
        "predictions_path": report["predictions_path"],
        "predictions_sha256": report["predictions_sha256"],
    }
    for key, expected in inference_bindings.items():
        if inference[key] != expected:
            raise StateClassifierError(
                f"validation report {key} differs from sealed inference evidence"
            )
    sample_count = report["sample_count"]
    if type(sample_count) is not int or sample_count != sum(dataset["counts"]["val"].values()):
        raise StateClassifierError("validation sample_count differs from dataset")
    points = report["threshold_points"]
    if type(points) is not list or not points:
        raise StateClassifierError("validation report has no threshold points")
    thresholds = [
        _validate_threshold_point(point, sample_count, f"threshold_points[{index}]")["safe_threshold"]
        for index, point in enumerate(points)
    ]
    plan = validate_state_training_plan(training["training_plan_path"])
    if thresholds != plan["safe_thresholds"]:
        raise StateClassifierError("validation thresholds differ from the frozen training plan")
    recomputed_points = [_metrics(prediction_rows, threshold) for threshold in thresholds]
    if points != recomputed_points:
        raise StateClassifierError("validation threshold points differ from linked predictions")
    return report


def evaluate_state_classifier_validation(
    *,
    training_report_path: str | os.PathLike[str],
    state_dataset_manifest_path: str | os.PathLike[str],
    inference_report_path: str | os.PathLike[str],
    output_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> Path:
    """Evaluate every predeclared safe threshold on validation data only."""

    repository = Path(repository_root).resolve(strict=False)
    workspace = require_external_workspace(workspace_root, repository)
    try:
        output = require_path_within_workspace(output_report_path, workspace)
        training_path = require_path_within_workspace(training_report_path, workspace)
        manifest_path = require_path_within_workspace(state_dataset_manifest_path, workspace)
        inference_path = require_path_within_workspace(inference_report_path, workspace)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error
    training = _validate_training_report(training_path, workspace, repository)
    dataset = _state_dataset(manifest_path)
    if training.get("dataset_manifest_sha256") != dataset["manifest_sha256"]:
        raise StateClassifierError("training and validation dataset manifests differ")
    plan_path = Path(_text(training.get("training_plan_path"), "training_plan_path"))
    plan = validate_state_training_plan(plan_path)
    training_report_hash = sha256_file(training_path)
    if training.get("training_plan_sha256") != plan["training_plan_sha256"]:
        raise StateClassifierError("training-plan evidence was tampered")
    if plan["dataset_manifest_sha256"] != dataset["manifest_sha256"]:
        raise StateClassifierError("training plan and validation dataset manifests differ")
    model_path = Path(_text(training.get("model_path"), "model_path"))
    model_hash = _sha256(training.get("model_sha256"), "model_sha256")
    if sha256_file(model_path) != model_hash:
        raise StateClassifierError("trained model SHA-256 mismatch")
    inference, rows = _validate_inference_report(
        inference_path,
        workspace=workspace,
        repository_root=repository,
        expected_split="val",
    )
    expected_inference = {
        "candidate_id": training["candidate_id"],
        "architecture": training["architecture"],
        "dataset_manifest_path": dataset["manifest_path"],
        "dataset_manifest_sha256": dataset["manifest_sha256"],
        "dataset_records_sha256": dataset["records_sha256"],
        "training_report_path": str(training_path),
        "training_report_sha256": training_report_hash,
        "training_plan_sha256": training["training_plan_sha256"],
        "model_path": str(model_path.resolve()),
        "model_sha256": model_hash,
        "state_evidence_status": training["state_evidence_status"],
    }
    for field, expected in expected_inference.items():
        if inference[field] != expected:
            raise StateClassifierError(
                f"validation inference {field} differs from requested lineage"
            )
    prediction_path = Path(inference["predictions_path"])
    predictions_hash = inference["predictions_sha256"]
    points = [_metrics(rows, threshold) for threshold in plan["safe_thresholds"]]
    payload = {
        "schema": STATE_EVALUATION_SCHEMA,
        "model_id": STATE_MODEL_ID,
        "candidate_id": training["candidate_id"],
        "architecture": training["architecture"],
        "input_size_hw": [INPUT_SIZE, INPUT_SIZE],
        "state_evidence_status": training["state_evidence_status"],
        "split": "val",
        "test_used_for_selection": False,
        "dataset_manifest_path": dataset["manifest_path"],
        "dataset_manifest_sha256": dataset["manifest_sha256"],
        "dataset_records_sha256": dataset["records_sha256"],
        "training_plan_sha256": training["training_plan_sha256"],
        "training_report_path": str(training_path),
        "training_report_sha256": training_report_hash,
        "model_path": str(model_path.resolve()),
        "model_sha256": model_hash,
        "predictions_path": str(prediction_path),
        "predictions_sha256": predictions_hash,
        "inference_report_path": str(inference_path),
        "inference_report_sha256": sha256_file(inference_path),
        "sample_count": len(rows),
        "threshold_points": points,
    }
    try:
        return atomic_create_json(output, payload)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error


def _best_point(points: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not points:
        raise StateClassifierError("validation report has no threshold points")
    validated: list[Mapping[str, Any]] = []
    for index, point in enumerate(points):
        if type(point) is not dict:
            raise StateClassifierError(f"threshold_points[{index}] must be an object")
        for field in ("safe_threshold", "accuracy", "balanced_accuracy", "false_safe_rate"):
            _ratio(point.get(field), f"threshold_points[{index}].{field}")
        validated.append(point)
    # Accuracy first; ties favor fewer dangerous false-safe decisions, then a
    # higher threshold (the fail-closed direction), then distress recall.
    return max(
        validated,
        key=lambda point: (
            float(point["balanced_accuracy"]),
            -float(point["false_safe_rate"]),
            float(point["safe_threshold"]),
            float(point["per_class"][DISTRESS_LABEL]["recall"]),
        ),
    )


def _validate_selection_report(
    path: Path,
    workspace: Path,
    repository_root: Path,
) -> dict[str, Any]:
    initial_hash = sha256_file(path)
    selection = _load_json(path, "state selection report")
    _exact_fields(selection, _SELECTION_FIELDS, "state selection report")
    if selection["schema"] != STATE_SELECTION_SCHEMA or selection["model_id"] != STATE_MODEL_ID:
        raise StateClassifierError("unsupported state selection report")
    if selection["input_size_hw"] != [INPUT_SIZE, INPUT_SIZE]:
        raise StateClassifierError("selected classifier input size is not 224x224")
    if selection["state_evidence_status"] != WEAK_SUPERVISION_EVIDENCE_STATUS:
        raise StateClassifierError(
            "selection evidence must remain weak_supervision_unverified"
        )
    if selection["selected_architecture"] not in SUPPORTED_ARCHITECTURES:
        raise StateClassifierError("selected classifier architecture is unsupported")
    _text(selection["selected_candidate_id"], "selected_candidate_id")
    for field in (
        "dataset_manifest_sha256",
        "selected_model_sha256",
        "selected_training_plan_sha256",
        "validation_report_sha256",
    ):
        _sha256(selection[field], field)
    if (
        selection["ranking_policy"]
        != "max_balanced_accuracy_then_min_false_safe_then_distress_recall"
    ):
        raise StateClassifierError("state selection ranking policy changed")
    validation_path = _workspace_path(
        selection["validation_report_path"], "validation_report_path", workspace
    )
    if sha256_file(validation_path) != selection["validation_report_sha256"]:
        raise StateClassifierError("selected validation report was tampered")
    validation = _validate_validation_report(
        validation_path, workspace, repository_root
    )
    selected_point = _best_point(validation["threshold_points"])
    expected_bindings = {
        "selected_candidate_id": validation["candidate_id"],
        "selected_architecture": validation["architecture"],
        "input_size_hw": validation["input_size_hw"],
        "selected_model_sha256": validation["model_sha256"],
        "selected_training_plan_sha256": validation["training_plan_sha256"],
        "dataset_manifest_sha256": validation["dataset_manifest_sha256"],
        "safe_threshold": selected_point["safe_threshold"],
        "state_evidence_status": validation["state_evidence_status"],
    }
    for field, value in expected_bindings.items():
        if selection[field] != value:
            if field == "safe_threshold":
                raise StateClassifierError(
                    "selection safe threshold does not match the validation-selected threshold"
                )
            raise StateClassifierError(
                f"selection {field} does not match its validation evidence"
            )
    summaries = selection["candidate_validation_reports"]
    if type(summaries) is not list or not summaries:
        raise StateClassifierError("selection candidate summaries are missing")
    selected_summaries = [
        item
        for item in summaries
        if type(item) is dict
        and item.get("candidate_id") == selection["selected_candidate_id"]
    ]
    if len(selected_summaries) != 1:
        raise StateClassifierError("selection must contain one selected candidate summary")
    selected_summary = selected_summaries[0]
    if set(selected_summary) != {
        "candidate_id",
        "model_sha256",
        "validation_report_path",
        "validation_report_sha256",
        "best_validation_point",
    }:
        raise StateClassifierError("selected candidate summary fields changed")
    if (
        selected_summary["model_sha256"] != selection["selected_model_sha256"]
        or selected_summary["validation_report_path"] != str(validation_path)
        or selected_summary["validation_report_sha256"]
        != selection["validation_report_sha256"]
        or selected_summary["best_validation_point"] != selected_point
    ):
        raise StateClassifierError("selected candidate summary differs from validation evidence")
    if sha256_file(path) != initial_hash:
        raise StateClassifierError("state selection report changed while being validated")
    return selection


def select_state_classifier(
    *,
    validation_report_paths: Sequence[str | os.PathLike[str]],
    output_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> Path:
    """Choose candidate and safe threshold using validation evidence only."""

    if isinstance(validation_report_paths, (str, bytes)) or not validation_report_paths:
        raise StateClassifierError("at least one validation report is required")
    repository = Path(repository_root).resolve(strict=False)
    workspace = require_external_workspace(workspace_root, repository)
    try:
        output = require_path_within_workspace(output_report_path, workspace)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error
    candidates: list[tuple[dict[str, Any], Mapping[str, Any], Path, str]] = []
    seen: set[str] = set()
    dataset_hash: str | None = None
    evidence_status: str | None = None
    summaries: list[dict[str, Any]] = []
    for raw_path in validation_report_paths:
        path = _workspace_path(raw_path, "validation report path", workspace)
        report = _validate_validation_report(path, workspace, repository)
        candidate_id = _text(report.get("candidate_id"), "candidate_id")
        if candidate_id in seen:
            raise StateClassifierError(f"duplicate candidate validation report: {candidate_id!r}")
        seen.add(candidate_id)
        current_dataset_hash = _sha256(report.get("dataset_manifest_sha256"), "dataset_manifest_sha256")
        if dataset_hash is None:
            dataset_hash = current_dataset_hash
        elif current_dataset_hash != dataset_hash:
            raise StateClassifierError("candidate validation reports use different datasets")
        current_status = report["state_evidence_status"]
        if evidence_status is None:
            evidence_status = current_status
        elif current_status != evidence_status:
            raise StateClassifierError(
                "candidate validation reports use different evidence status"
            )
        model_hash = _sha256(report.get("model_sha256"), "model_sha256")
        point = _best_point(report.get("threshold_points"))
        report_hash = sha256_file(path)
        candidates.append((report, point, path, report_hash))
        summaries.append(
            {
                "candidate_id": candidate_id,
                "model_sha256": model_hash,
                "validation_report_path": str(path),
                "validation_report_sha256": report_hash,
                "best_validation_point": point,
            }
        )
    selected_report, selected_point, selected_path, selected_hash = max(
        candidates,
        key=lambda item: (
            float(item[1]["balanced_accuracy"]),
            -float(item[1]["false_safe_rate"]),
            float(item[1]["per_class"][DISTRESS_LABEL]["recall"]),
            float(item[1]["per_class"][SAFE_LABEL]["recall"]),
            item[0]["candidate_id"],
        ),
    )
    payload = {
        "schema": STATE_SELECTION_SCHEMA,
        "model_id": STATE_MODEL_ID,
        "dataset_manifest_sha256": dataset_hash,
        "state_evidence_status": evidence_status,
        "selected_candidate_id": selected_report["candidate_id"],
        "selected_architecture": selected_report["architecture"],
        "input_size_hw": [INPUT_SIZE, INPUT_SIZE],
        "selected_model_sha256": selected_report["model_sha256"],
        "selected_training_plan_sha256": selected_report["training_plan_sha256"],
        "safe_threshold": selected_point["safe_threshold"],
        "validation_report_path": str(selected_path),
        "validation_report_sha256": selected_hash,
        "ranking_policy": "max_balanced_accuracy_then_min_false_safe_then_distress_recall",
        "candidate_validation_reports": summaries,
    }
    try:
        return atomic_create_json(output, payload)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error


def evaluate_state_classifier_test(
    *,
    selection_report_path: str | os.PathLike[str],
    state_dataset_manifest_path: str | os.PathLike[str],
    inference_report_path: str | os.PathLike[str],
    output_report_path: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> Path:
    """Evaluate untouched test once at the already-frozen validation threshold."""

    repository = Path(repository_root).resolve(strict=False)
    workspace = require_external_workspace(workspace_root, repository)
    try:
        output = require_path_within_workspace(output_report_path, workspace)
        selection_path = require_path_within_workspace(selection_report_path, workspace)
        manifest_path = require_path_within_workspace(state_dataset_manifest_path, workspace)
        inference_path = require_path_within_workspace(inference_report_path, workspace)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error
    selection = _validate_selection_report(selection_path, workspace, repository)
    dataset = _state_dataset(manifest_path)
    if selection["dataset_manifest_sha256"] != dataset["manifest_sha256"]:
        raise StateClassifierError("selection and test dataset manifests differ")
    if selection["state_evidence_status"] != dataset["state_evidence_status"]:
        raise StateClassifierError("selection and test state evidence status differ")
    selected_model_hash = _sha256(selection["selected_model_sha256"], "selected_model_sha256")
    threshold = _ratio(selection["safe_threshold"], "safe_threshold")
    inference, rows = _validate_inference_report(
        inference_path,
        workspace=workspace,
        repository_root=repository,
        expected_split="test",
    )
    expected_inference = {
        "candidate_id": selection["selected_candidate_id"],
        "architecture": selection["selected_architecture"],
        "dataset_manifest_path": dataset["manifest_path"],
        "dataset_manifest_sha256": dataset["manifest_sha256"],
        "training_plan_sha256": selection["selected_training_plan_sha256"],
        "model_sha256": selected_model_hash,
        "selection_report_path": str(selection_path),
        "selection_report_sha256": sha256_file(selection_path),
        "state_evidence_status": selection["state_evidence_status"],
    }
    for field, expected in expected_inference.items():
        if inference[field] != expected:
            raise StateClassifierError(
                f"test inference {field} differs from frozen selection lineage"
            )
    prediction_path = Path(inference["predictions_path"])
    predictions_hash = inference["predictions_sha256"]
    metrics = _metrics(rows, threshold)
    payload = {
        "schema": STATE_TEST_REPORT_SCHEMA,
        "model_id": STATE_MODEL_ID,
        "candidate_id": selection["selected_candidate_id"],
        "architecture": selection["selected_architecture"],
        "input_size_hw": [INPUT_SIZE, INPUT_SIZE],
        "state_evidence_status": selection["state_evidence_status"],
        "split": "test",
        "test_used_for_selection": False,
        "threshold_source": "validation_selection_report",
        "safe_threshold": threshold,
        "dataset_manifest_sha256": dataset["manifest_sha256"],
        "model_sha256": selected_model_hash,
        "selection_report_path": str(selection_path),
        "selection_report_sha256": sha256_file(selection_path),
        "predictions_path": str(prediction_path),
        "predictions_sha256": predictions_hash,
        "inference_report_path": str(inference_path),
        "inference_report_sha256": sha256_file(inference_path),
        "sample_count": len(rows),
        "metrics": metrics,
    }
    try:
        return atomic_create_json(output, payload)
    except ArtifactIOError as error:
        raise StateClassifierError(str(error)) from error


__all__ = [
    "DISTRESS_LABEL",
    "INPUT_SIZE",
    "SAFE_LABEL",
    "STATE_CLASSIFIER_BASE_WEIGHTS_SCHEMA",
    "STATE_EVALUATION_SCHEMA",
    "STATE_INFERENCE_SCHEMA",
    "STATE_SELECTION_SCHEMA",
    "STATE_TEST_REPORT_SCHEMA",
    "STATE_TRAINING_PLAN_SCHEMA",
    "STATE_TRAINING_RUN_SCHEMA",
    "StateClassifierError",
    "evaluate_state_classifier_test",
    "evaluate_state_classifier_validation",
    "run_state_classifier_inference",
    "run_state_classifier_training",
    "select_state_classifier",
    "validate_state_training_plan",
]
