"""Fail-closed Ultralytics training runner for ``sar-rgb-person-v1``.

The module deliberately performs no work at import time.  In particular, it
never asks Ultralytics to resolve a model name (which could download weights),
never initializes CUDA, and never creates a run directory until every local
input and runtime gate has passed.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_io import (
    ArtifactIOError,
    atomic_create_json,
    require_external_workspace,
    require_path_within_workspace,
    sha256_file,
    canonical_json_bytes,
)
from .contracts import CandidateSpec, TrainingPlan
from .batch_gate import (
    BatchDecisionError,
    authorized_batch,
    load_batch_decision,
    validate_batch_decision,
)
from .cloud_environment import (
    CloudEnvironmentError,
    load_cloud_environment,
    validate_cloud_environment,
)
from .review_gate import (
    DatasetQualificationError,
    validate_automated_dataset_qualification,
)
from tools.audit_yolo_dataset import (
    SplitSpec,
    audit_dataset,
    montage_membership,
    montage_membership_sha256,
    render_montage,
    select_montage_samples,
)


TRAINING_RUN_SCHEMA = "veriswarm.rescue.training_run.v1"
DATASET_AUDIT_SCHEMA = "veriswarm.dataset_audit.v1"
BASE_WEIGHTS_SCHEMA = "veriswarm.rescue.base_weights.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TrainingExecutionError(RuntimeError):
    """A training precondition or completed-run evidence gate failed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise TrainingExecutionError(f"JSON input must be one regular file: {path}")

    def reject_constant(value: str) -> None:
        raise TrainingExecutionError(f"non-finite JSON constant is forbidden: {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise TrainingExecutionError(
                    f"duplicate JSON object key is forbidden: {key!r}"
                )
            result[key] = item
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except TrainingExecutionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TrainingExecutionError(f"cannot read strict JSON {path}: {error}") from error
    if type(value) is not dict:
        raise TrainingExecutionError(f"JSON input must contain one object: {path}")
    return value


def _sha256(value: Any, field: str) -> str:
    if type(value) is not str:
        raise TrainingExecutionError(f"{field} must be a lowercase SHA-256 string")
    normalized = value.lower()
    if not SHA256_RE.fullmatch(normalized):
        raise TrainingExecutionError(f"{field} must be a lowercase SHA-256 string")
    return normalized


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise TrainingExecutionError(f"{field} must be an integer >= {minimum}")
    return value


def verify_visdrone_audit(
    report_path: str | os.PathLike[str],
    *,
    dataset_yaml: str | os.PathLike[str] | None = None,
    expected_train_images: int = 6471,
    expected_val_images: int = 548,
    required_montage_samples: int = 100,
) -> dict[str, Any]:
    """Recompute and verify the complete one-class VisDrone audit.

    The saved report is provenance, not authority. Every call decodes every
    image, reparses every YOLO label, rehashes every image/label pair,
    recomputes class counts and byte-duplicate groups, deterministically
    selects the rendered sample set, and rerenders the montage before any
    claimed result is accepted.
    """

    expected_train_images = _integer(
        expected_train_images, "expected_train_images", minimum=1
    )
    expected_val_images = _integer(
        expected_val_images, "expected_val_images", minimum=1
    )
    required_montage_samples = _integer(
        required_montage_samples, "required_montage_samples", minimum=1
    )

    source_input = Path(report_path)
    if source_input.is_symlink():
        raise TrainingExecutionError("dataset audit report must not be a symbolic link")
    source = source_input.resolve(strict=False)
    try:
        report_hash_before = sha256_file(source)
    except ArtifactIOError as error:
        raise TrainingExecutionError(str(error)) from error
    report = _json_object(source)
    if report.get("schema") != DATASET_AUDIT_SCHEMA:
        raise TrainingExecutionError("unsupported dataset-audit schema")
    if report.get("passed") is not True:
        raise TrainingExecutionError("dataset audit did not pass")
    if report.get("class_map") != {"0": "person_candidate"}:
        raise TrainingExecutionError(
            "dataset audit class_map must be exactly {'0': 'person_candidate'}"
        )

    splits = report.get("splits")
    if type(splits) is not dict:
        raise TrainingExecutionError("dataset audit splits must be an object")
    if set(splits) != {"train", "val"}:
        raise TrainingExecutionError(
            "dataset audit must contain exactly the official train and val splits"
        )
    dataset_root: Path | None = None
    split_specs: list[SplitSpec] = []
    for split_name, expected in (
        ("train", expected_train_images),
        ("val", expected_val_images),
    ):
        split = splits.get(split_name)
        if type(split) is not dict:
            raise TrainingExecutionError(f"dataset audit is missing {split_name!r}")
        for field in ("images", "labels", "paired", "valid_samples"):
            actual = _integer(split.get(field), f"splits.{split_name}.{field}")
            if actual != expected:
                raise TrainingExecutionError(
                    f"{split_name} {field} mismatch: expected {expected}, got {actual}"
                )
        if _integer(split.get("missing_labels"), f"splits.{split_name}.missing_labels"):
            raise TrainingExecutionError(f"{split_name} has missing labels")
        if _integer(split.get("orphan_labels"), f"splits.{split_name}.orphan_labels"):
            raise TrainingExecutionError(f"{split_name} has orphan labels")
        image_root_value = split.get("image_root")
        label_root_value = split.get("label_root")
        if type(image_root_value) is not str or type(label_root_value) is not str:
            raise TrainingExecutionError(
                f"dataset audit {split_name} roots are missing"
            )
        image_root_input = Path(image_root_value)
        label_root_input = Path(label_root_value)
        if (
            not image_root_input.is_absolute()
            or not label_root_input.is_absolute()
            or image_root_input.is_symlink()
            or label_root_input.is_symlink()
        ):
            raise TrainingExecutionError(
                f"dataset audit {split_name} roots must be absolute non-symlink directories"
            )
        image_root = image_root_input.resolve(strict=False)
        label_root = label_root_input.resolve(strict=False)
        expected_root = image_root.parent.parent
        if image_root != expected_root / "images" / split_name:
            raise TrainingExecutionError(f"{split_name} image_root is not canonical")
        if label_root != expected_root / "labels" / split_name:
            raise TrainingExecutionError(f"{split_name} label_root is not canonical")
        if dataset_root is None:
            dataset_root = expected_root
        elif dataset_root != expected_root:
            raise TrainingExecutionError("train and val audit roots name different datasets")
        _sha256(
            split.get("sample_set_sha256"),
            f"splits.{split_name}.sample_set_sha256",
        )
        split_specs.append(SplitSpec(split_name, image_root, label_root))

    totals = report.get("totals")
    if type(totals) is not dict:
        raise TrainingExecutionError("dataset audit totals must be an object")
    if _integer(totals.get("errors"), "totals.errors") != 0:
        raise TrainingExecutionError("dataset audit contains errors")
    if _integer(
        totals.get("cross_split_leakage_groups"),
        "totals.cross_split_leakage_groups",
    ) != 0:
        raise TrainingExecutionError("dataset audit contains cross-split duplicates")
    if _integer(totals.get("valid_samples"), "totals.valid_samples") != (
        expected_train_images + expected_val_images
    ):
        raise TrainingExecutionError("dataset audit total sample count is inconsistent")

    montage = report.get("montage")
    if type(montage) is not dict:
        raise TrainingExecutionError("dataset audit montage evidence is missing")
    rendered = _integer(montage.get("rendered_samples"), "montage.rendered_samples")
    if rendered != required_montage_samples:
        raise TrainingExecutionError(
            f"label montage must contain exactly {required_montage_samples} samples"
        )
    relative = montage.get("path")
    if type(relative) is not str or not relative or Path(relative).is_absolute():
        raise TrainingExecutionError("montage.path must be a relative file path")
    montage_input = source.parent / relative
    if montage_input.is_symlink():
        raise TrainingExecutionError("label montage must not be a symbolic link")
    montage_path = montage_input.resolve(strict=False)
    if source.parent not in montage_path.parents:
        raise TrainingExecutionError("montage.path escapes the audit directory")
    expected_hash = _sha256(montage.get("sha256"), "montage.sha256")
    if not montage_path.is_file():
        raise TrainingExecutionError("label montage must be one regular file")

    authoritative = recompute_visdrone_dataset_integrity(
        tuple(split_specs),
        required_montage_samples=required_montage_samples,
    )
    _compare_claimed_audit(report, authoritative)
    try:
        actual_hash = sha256_file(montage_path)
        report_hash = sha256_file(source)
    except ArtifactIOError as error:
        raise TrainingExecutionError(str(error)) from error
    if report_hash != report_hash_before:
        raise TrainingExecutionError("dataset audit report changed during verification")
    if actual_hash != expected_hash:
        raise TrainingExecutionError("label montage SHA-256 mismatch")
    if actual_hash != authoritative["montage"]["sha256"]:
        raise TrainingExecutionError(
            "saved label montage differs from the authoritative rerender"
        )

    yaml_path: Path | None = None
    yaml_hash: str | None = None
    if dataset_yaml is not None:
        from .visdrone import render_dataset_yaml

        yaml_input = Path(dataset_yaml)
        if yaml_input.is_symlink():
            raise TrainingExecutionError("dataset YAML must not be a symbolic link")
        yaml_path = yaml_input.resolve(strict=False)
        if yaml_path != dataset_root / "dataset.yaml":
            raise TrainingExecutionError(
                "dataset YAML is not the audited dataset's generated dataset.yaml"
            )
        if not yaml_path.is_file():
            raise TrainingExecutionError(f"dataset YAML is missing or unsafe: {yaml_path}")
        try:
            actual_yaml = yaml_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise TrainingExecutionError(f"cannot read dataset YAML: {error}") from error
        expected_yaml = render_dataset_yaml(dataset_root)
        if actual_yaml != expected_yaml:
            raise TrainingExecutionError(
                "dataset YAML is not the canonical local-only one-class configuration"
            )
        if "download:" in actual_yaml.lower() or "://" in actual_yaml:
            raise TrainingExecutionError("dataset YAML must not contain a download or URL")
        yaml_hash = sha256_file(yaml_path)

    return {
        "schema": DATASET_AUDIT_SCHEMA,
        "report_path": str(source),
        "report_sha256": report_hash,
        "montage_path": str(montage_path),
        "montage_sha256": actual_hash,
        "train_images": expected_train_images,
        "val_images": expected_val_images,
        "class_map": {"0": "person_candidate"},
        "dataset_root": str(dataset_root),
        "dataset_yaml_path": str(yaml_path) if yaml_path is not None else None,
        "dataset_yaml_sha256": yaml_hash,
        "sample_set_sha256": {
            split: authoritative["splits"][split]["sample_set_sha256"]
            for split in ("train", "val")
        },
        "authoritative_integrity": authoritative,
        "authoritative_integrity_sha256": hashlib.sha256(
            canonical_json_bytes(authoritative)
        ).hexdigest(),
        "verified_at_utc": _utc_now(),
    }


def recompute_visdrone_dataset_integrity(
    specs: tuple[SplitSpec, ...],
    *,
    required_montage_samples: int,
) -> dict[str, Any]:
    """Return an authoritative, path-bound audit computed from dataset bytes."""

    try:
        import cv2
    except ImportError as error:
        raise TrainingExecutionError(
            "OpenCV is required for authoritative dataset verification"
        ) from error
    try:
        report, samples = audit_dataset(
            specs,
            {0: "person_candidate"},
            cv2,
        )
    except Exception as error:
        raise TrainingExecutionError(
            f"authoritative dataset recomputation failed: {error}"
        ) from error
    if report["passed"] is not True:
        reasons = [item.get("reason", "unknown") for item in report["errors"][:5]]
        raise TrainingExecutionError(
            "authoritative dataset recomputation failed: " + "; ".join(reasons)
        )
    selected = select_montage_samples(samples, required_montage_samples)
    if len(selected) != required_montage_samples:
        raise TrainingExecutionError(
            "authoritative montage sample set does not contain exactly "
            f"{required_montage_samples} samples"
        )
    members = montage_membership(selected)
    try:
        with tempfile.TemporaryDirectory(prefix="veriswarm-audit-") as temporary:
            rendered = Path(temporary) / "label_montage.jpg"
            render_montage(
                selected,
                {0: "person_candidate"},
                rendered,
                cv2,
            )
            rendered_sha256 = sha256_file(rendered)
    except Exception as error:
        raise TrainingExecutionError(
            f"authoritative montage rerender failed: {error}"
        ) from error

    return {
        "class_map": report["class_map"],
        "splits": report["splits"],
        "totals": report["totals"],
        "duplicates": report["duplicates"],
        "cross_split_leakage": report["cross_split_leakage"],
        "errors": report["errors"],
        "warnings": report["warnings"],
        "montage": {
            "requested_samples": required_montage_samples,
            "rendered_samples": len(selected),
            "samples": members,
            "sample_set_sha256": montage_membership_sha256(members),
            "sha256": rendered_sha256,
        },
    }


def _compare_claimed_audit(
    claimed: Mapping[str, Any],
    authoritative: Mapping[str, Any],
) -> None:
    """Reject any saved claim that differs from recomputed dataset evidence."""

    claimed_splits = claimed.get("splits")
    actual_splits = authoritative.get("splits")
    if not isinstance(actual_splits, dict) or set(actual_splits) != {"train", "val"}:
        raise TrainingExecutionError(
            "authoritative recomputation returned an invalid split set"
        )
    split_fields = {
        "image_root",
        "label_root",
        "images",
        "labels",
        "paired",
        "valid_samples",
        "boxes",
        "class_box_counts",
        "empty_label_samples",
        "missing_labels",
        "orphan_labels",
        "sample_set_sha256",
    }
    for split in ("train", "val"):
        claimed_split = claimed_splits.get(split) if isinstance(claimed_splits, dict) else None
        actual_split = actual_splits[split]
        if not isinstance(claimed_split, dict):
            raise TrainingExecutionError(f"dataset audit is missing {split!r}")
        for field in split_fields:
            if claimed_split.get(field) != actual_split[field]:
                raise TrainingExecutionError(
                    f"dataset audit {split}.{field} differs from authoritative recomputation"
                )

    for field in (
        "class_map",
        "totals",
        "duplicates",
        "cross_split_leakage",
        "errors",
        "warnings",
    ):
        if claimed.get(field) != authoritative[field]:
            raise TrainingExecutionError(
                f"dataset audit {field} differs from authoritative recomputation"
            )
    claimed_montage = claimed.get("montage")
    actual_montage = authoritative.get("montage")
    if not isinstance(claimed_montage, dict):
        raise TrainingExecutionError("dataset audit montage evidence is missing")
    if not isinstance(actual_montage, dict):
        raise TrainingExecutionError(
            "authoritative recomputation returned invalid montage evidence"
        )
    for field in (
        "requested_samples",
        "rendered_samples",
        "samples",
        "sample_set_sha256",
        "sha256",
    ):
        if claimed_montage.get(field) != actual_montage.get(field):
            raise TrainingExecutionError(
                f"dataset audit montage.{field} differs from authoritative recomputation"
            )


def verify_runpod_runtime(
    plan: TrainingPlan,
    *,
    python_version: str | None = None,
    package_versions: Mapping[str, str] | None = None,
    torch_module: Any | None = None,
) -> dict[str, Any]:
    """Verify the exact pinned RTX 5090 runtime and return serializable evidence."""

    actual_python = python_version or platform.python_version()
    versions = (
        dict(package_versions)
        if package_versions is not None
        else {
            name: importlib.metadata.version(name)
            for name in ("torch", "torchvision", "ultralytics")
        }
    )
    expected = plan.runtime
    if actual_python != expected["python"]:
        raise TrainingExecutionError(
            f"Python mismatch: expected {expected['python']}, got {actual_python}"
        )
    for package in ("torch", "torchvision", "ultralytics"):
        if versions.get(package) != expected[package]:
            raise TrainingExecutionError(
                f"{package} mismatch: expected {expected[package]}, got {versions.get(package)}"
            )

    if torch_module is None:
        try:
            import torch as torch_module  # type: ignore[no-redef]
        except ImportError as error:
            raise TrainingExecutionError("PyTorch is unavailable") from error
    try:
        if torch_module.cuda.is_available() is not True:
            raise TrainingExecutionError("CUDA is unavailable")
        device_count = int(torch_module.cuda.device_count())
        if device_count != 1:
            raise TrainingExecutionError(
                f"exactly one visible GPU is required, found {device_count}"
            )
        gpu_name = str(torch_module.cuda.get_device_name(0))
        if expected["required_gpu_substring"].lower() not in gpu_name.lower():
            raise TrainingExecutionError(
                f"GPU mismatch: expected {expected['required_gpu_substring']!r}, got {gpu_name!r}"
            )
        properties = torch_module.cuda.get_device_properties(0)
        total_memory = int(properties.total_memory)
        cuda_runtime = str(torch_module.version.cuda)
        if cuda_runtime != "13.0":
            raise TrainingExecutionError(
                f"CUDA runtime mismatch: expected 13.0, got {cuda_runtime}"
            )
        cudnn_version = int(torch_module.backends.cudnn.version())
    except TrainingExecutionError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise TrainingExecutionError(f"cannot inspect CUDA runtime: {error}") from error

    return {
        "verified_at_utc": _utc_now(),
        "python": actual_python,
        "torch": versions["torch"],
        "torchvision": versions["torchvision"],
        "ultralytics": versions["ultralytics"],
        "cuda_runtime": cuda_runtime,
        "cudnn_version": cudnn_version,
        "visible_gpu_count": device_count,
        "gpu_index": 0,
        "gpu_name": gpu_name,
        "gpu_total_memory_bytes": total_memory,
    }


def _resolve_batch(candidate: CandidateSpec, requested_batch: int | None) -> float | int:
    if candidate.stage == "smoke":
        if requested_batch is not None:
            raise TrainingExecutionError("the smoke batch is frozen at 0.70")
        return 0.70
    configured = candidate.batch
    if requested_batch is not None and configured is not None and requested_batch != configured:
        raise TrainingExecutionError("requested batch conflicts with the frozen full-run batch")
    batch = configured if configured is not None else requested_batch
    if type(batch) is not int or batch <= 0:
        raise TrainingExecutionError(
            "a full candidate requires the positive integer batch measured by the smoke probe"
        )
    return batch


def verify_trusted_base_checkpoint(
    checkpoint: Path,
    *,
    architecture: str,
    repository_root: Path,
) -> dict[str, Any]:
    """Verify a base checkpoint against the committed Ultralytics allowlist."""

    manifest_path = (
        repository_root / "codebase" / "config" / "ultralytics_yolov8_base_weights.json"
    ).resolve(strict=False)
    expected_manifest_path = repository_root / "codebase" / "config" / "ultralytics_yolov8_base_weights.json"
    if manifest_path != expected_manifest_path.resolve(strict=False):
        raise TrainingExecutionError("base-weights manifest path is not canonical")
    manifest = _json_object(manifest_path)
    if set(manifest) != {"schema", "publisher", "repository", "release_tag", "entries"}:
        raise TrainingExecutionError("base-weights manifest fields are not frozen")
    if manifest["schema"] != BASE_WEIGHTS_SCHEMA:
        raise TrainingExecutionError("unsupported base-weights manifest schema")
    if manifest["publisher"] != "Ultralytics":
        raise TrainingExecutionError("base-weights publisher is not frozen")
    if manifest["repository"] != "https://github.com/ultralytics/assets":
        raise TrainingExecutionError("base-weights repository is not frozen")
    if manifest["release_tag"] != "v8.4.0":
        raise TrainingExecutionError("base-weights release tag is not frozen")
    entries = manifest["entries"]
    if type(entries) is not dict or set(entries) != {"yolov8n.pt", "yolov8s.pt"}:
        raise TrainingExecutionError("base-weights entries are not frozen")
    entry = entries.get(architecture)
    if type(entry) is not dict or set(entry) != {"url", "bytes", "sha256"}:
        raise TrainingExecutionError(f"trusted base entry is missing: {architecture}")
    expected_url = (
        f"https://github.com/ultralytics/assets/releases/download/v8.4.0/{architecture}"
    )
    if entry["url"] != expected_url:
        raise TrainingExecutionError("trusted base checkpoint URL is not canonical")
    expected_bytes = _integer(entry["bytes"], "base_weights.bytes", minimum=1)
    expected_hash = _sha256(entry["sha256"], "base_weights.sha256")
    if checkpoint.stat().st_size != expected_bytes:
        raise TrainingExecutionError(
            f"base checkpoint byte-size mismatch: expected {expected_bytes}, "
            f"got {checkpoint.stat().st_size}"
        )
    actual_hash = sha256_file(checkpoint)
    if actual_hash != expected_hash:
        raise TrainingExecutionError("base checkpoint does not match the committed allowlist")
    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "publisher": manifest["publisher"],
        "release_tag": manifest["release_tag"],
        "url": entry["url"],
        "bytes": expected_bytes,
        "sha256": actual_hash,
    }


def frozen_train_arguments(
    candidate: CandidateSpec,
    *,
    dataset_yaml: Path,
    run_directory: Path,
    requested_batch: int | None,
) -> dict[str, Any]:
    """Return the explicit pinned arguments passed to Ultralytics ``train``."""

    return {
        "data": str(dataset_yaml),
        "epochs": candidate.epochs,
        "imgsz": candidate.imgsz,
        "batch": _resolve_batch(candidate, requested_batch),
        "device": 0,
        "cache": False,
        "seed": 0,
        "deterministic": True,
        "save": True,
        "save_period": 1,
        # Ultralytics 8.4.56 check_amp constructs YOLO('yolo26n.pt') and may
        # download it.  Keep training fully local until a separately hashed
        # auxiliary AMP-check asset is admitted.
        "amp": False,
        "single_cls": True,
        "val": True,
        # Ultralytics plot/font initialization can perform an implicit network
        # fetch on a clean image.  Generate presentation plots later from the
        # hashed CSV in a separately controlled evidence step.
        "plots": False,
        "workers": 8,
        "resume": False,
        "project": str(run_directory.parent),
        "name": run_directory.name,
        "exist_ok": False,
    }


def _read_final_metrics(results_csv: Path, expected_epochs: int) -> dict[str, Any]:
    try:
        with results_csv.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    except (OSError, UnicodeError, csv.Error) as error:
        raise TrainingExecutionError(f"cannot read training results: {error}") from error
    if len(rows) != expected_epochs:
        raise TrainingExecutionError(
            f"results.csv epoch count mismatch: expected {expected_epochs}, got {len(rows)}"
        )
    required = (
        "epoch",
        "train/box_loss",
        "train/cls_loss",
        "train/dfl_loss",
        "metrics/precision(B)",
        "metrics/recall(B)",
        "metrics/mAP50(B)",
        "metrics/mAP50-95(B)",
    )
    parsed_rows: list[dict[str, float]] = []
    for row_index, row in enumerate(rows, start=1):
        parsed: dict[str, float] = {}
        for field in required:
            if field not in row:
                raise TrainingExecutionError(f"results.csv is missing {field!r}")
            try:
                number = float(row[field])
            except (TypeError, ValueError) as error:
                raise TrainingExecutionError(f"results.csv {field!r} is not numeric") from error
            if not math.isfinite(number):
                raise TrainingExecutionError(f"results.csv {field!r} is non-finite")
            parsed[field] = number
        for loss in ("train/box_loss", "train/cls_loss", "train/dfl_loss"):
            if parsed[loss] < 0:
                raise TrainingExecutionError(f"results.csv {loss!r} is negative")
        if int(parsed["epoch"]) != row_index:
            raise TrainingExecutionError(
                "results.csv epoch sequence must use Ultralytics' 1-based indexing"
            )
        parsed_rows.append(parsed)
    return {
        "epochs_completed": len(rows),
        "final": parsed_rows[-1],
        "losses_all_epochs_finite": True,
    }


def _peak_cuda_bytes(torch_module: Any | None) -> dict[str, int | None]:
    if torch_module is None:
        return {"allocated": None, "reserved": None}
    try:
        return {
            "allocated": int(torch_module.cuda.max_memory_allocated(0)),
            "reserved": int(torch_module.cuda.max_memory_reserved(0)),
        }
    except (AttributeError, TypeError, ValueError):
        return {"allocated": None, "reserved": None}


def _verify_effective_arguments(
    args_yaml: Path,
    requested: Mapping[str, Any],
    *,
    staged_model: Path,
    loader: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Parse Ultralytics' args.yaml and verify every frozen requested value."""

    if loader is None:
        try:
            import yaml
        except ImportError as error:
            raise TrainingExecutionError("PyYAML is unavailable for args verification") from error
        try:
            value = yaml.safe_load(args_yaml.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise TrainingExecutionError(f"cannot parse args.yaml: {error}") from error
    else:
        try:
            value = loader(args_yaml)
        except Exception as error:
            raise TrainingExecutionError(f"effective-argument loader failed: {error}") from error
    if type(value) is not dict:
        raise TrainingExecutionError("args.yaml must contain one mapping")
    for key, expected in requested.items():
        if key not in value:
            raise TrainingExecutionError(f"args.yaml is missing frozen argument {key!r}")
        actual = value[key]
        if key in {"data", "project"}:
            if Path(str(actual)).resolve(strict=False) != Path(str(expected)).resolve(strict=False):
                raise TrainingExecutionError(f"effective argument {key!r} changed")
        elif actual != expected:
            raise TrainingExecutionError(
                f"effective argument {key!r} changed: expected {expected!r}, got {actual!r}"
            )
    model_value = value.get("model")
    if Path(str(model_value)).resolve(strict=False) != staged_model.resolve(strict=False):
        raise TrainingExecutionError("effective model path is not the staged verified checkpoint")
    return {key: value[key] for key in sorted(value)}


def _source_identity(repository_root: Path) -> dict[str, Any]:
    """Require a clean Git checkout and bind a training run to its source commit."""

    try:
        status = subprocess.run(
            ["git", "-C", str(repository_root), "status", "--porcelain=v1"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if status.stdout.strip():
            raise TrainingExecutionError(
                "training source checkout is dirty; commit the reviewed code first"
            )
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
    except TrainingExecutionError:
        raise
    except (OSError, subprocess.CalledProcessError, UnicodeError) as error:
        raise TrainingExecutionError(f"cannot bind training source checkout: {error}") from error
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise TrainingExecutionError("Git returned an invalid source commit")
    if not branch:
        raise TrainingExecutionError("detached Git HEAD is forbidden for a new training run")
    return {"commit": commit, "branch": branch, "clean": True}


def _stage_verified_checkpoint(
    source: Path,
    *,
    run_path: Path,
    expected_sha256: str,
) -> Path:
    """Create an exclusive immutable-by-policy copy before PyTorch deserialization."""

    staging = run_path.parent / f".{run_path.name}.verified-inputs"
    if staging.exists() or os.path.lexists(staging):
        raise TrainingExecutionError(f"refusing to reuse training input staging: {staging}")
    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir(exist_ok=False)
        destination = staging / source.name
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except OSError as error:
        raise TrainingExecutionError(f"cannot stage verified checkpoint: {error}") from error
    if sha256_file(destination) != expected_sha256:
        raise TrainingExecutionError("staged checkpoint SHA-256 mismatch")
    return destination


def run_training(
    plan: TrainingPlan,
    *,
    candidate_name: str,
    dataset_yaml: str | os.PathLike[str],
    dataset_audit: str | os.PathLike[str],
    dataset_qualification: str | os.PathLike[str] | None = None,
    cloud_environment: str | os.PathLike[str] | None,
    base_checkpoint: str | os.PathLike[str],
    base_checkpoint_sha256: str,
    run_directory: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    batch_decision: str | os.PathLike[str] | None = None,
    yolo_factory: Callable[[str], Any] | None = None,
    runtime_evidence: Mapping[str, Any] | None = None,
    cloud_environment_evidence: Mapping[str, Any] | None = None,
    batch_decision_evidence: Mapping[str, Any] | None = None,
    source_identity: Mapping[str, Any] | None = None,
    trusted_base_verifier: Callable[..., Mapping[str, Any]] = verify_trusted_base_checkpoint,
    effective_arguments_loader: Callable[[Path], Any] | None = None,
    torch_module: Any | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Path:
    """Execute one declared training candidate and create its evidence report.

    ``yolo_factory`` and ``runtime_evidence`` are explicit unit-test seams.  The
    command-line interface never exposes either override.
    """

    candidate = plan.candidate(candidate_name)
    if dataset_qualification is None:
        raise TrainingExecutionError(
            "training requires automated dataset qualification evidence"
        )
    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        data_path = require_path_within_workspace(dataset_yaml, workspace)
        audit_path = require_path_within_workspace(dataset_audit, workspace)
        qualification_path = require_path_within_workspace(
            dataset_qualification, workspace
        )
        cloud_path = (
            require_path_within_workspace(cloud_environment, workspace)
            if cloud_environment is not None
            else None
        )
        weights_path = require_path_within_workspace(base_checkpoint, workspace)
        run_path = require_path_within_workspace(run_directory, workspace)
    except ArtifactIOError as error:
        raise TrainingExecutionError(str(error)) from error

    # ``runtime_evidence`` is an internal unit-test seam.  Production callers
    # must prove they are executing at the exact persistent RunPod path.
    if runtime_evidence is None and str(workspace).replace("\\", "/") != plan.workspace_root.rstrip("/"):
        raise TrainingExecutionError(
            f"workspace does not match frozen plan root: {workspace}"
        )
    for source, label in ((data_path, "dataset YAML"), (weights_path, "base checkpoint")):
        if source.is_symlink() or not source.is_file():
            raise TrainingExecutionError(f"{label} must be one regular file: {source}")
    if run_path.exists() or os.path.lexists(run_path):
        raise TrainingExecutionError(f"refusing to reuse training run path: {run_path}")
    if weights_path.name != candidate.architecture:
        raise TrainingExecutionError(
            f"candidate {candidate.name} requires {candidate.architecture}, got {weights_path.name}"
        )
    expected_base_hash = _sha256(base_checkpoint_sha256, "base_checkpoint_sha256")
    trusted_base = dict(trusted_base_verifier(
        weights_path,
        architecture=candidate.architecture,
        repository_root=Path(repository_root).resolve(strict=False),
    ))
    actual_base_hash = trusted_base["sha256"]
    if actual_base_hash != expected_base_hash:
        raise TrainingExecutionError("base checkpoint SHA-256 mismatch")
    audit_evidence = verify_visdrone_audit(
        audit_path,
        dataset_yaml=data_path,
    )
    split_evidence = {
        "train": {
            "images": audit_evidence["train_images"],
            "sample_set_sha256": audit_evidence["sample_set_sha256"]["train"],
        },
        "val": {
            "images": audit_evidence["val_images"],
            "sample_set_sha256": audit_evidence["sample_set_sha256"]["val"],
        },
    }
    try:
        qualification_evidence = validate_automated_dataset_qualification(
            qualification_path,
            expected_audit_path=audit_evidence["report_path"],
            expected_audit_sha256=audit_evidence["report_sha256"],
            expected_dataset_yaml_path=data_path,
            expected_dataset_yaml_sha256=audit_evidence["dataset_yaml_sha256"],
            expected_montage_path=audit_evidence["montage_path"],
            expected_montage_sha256=audit_evidence["montage_sha256"],
            expected_splits=split_evidence,
            workspace_root=workspace,
            repository_root=repository_root,
        )
    except DatasetQualificationError as error:
        raise TrainingExecutionError(str(error)) from error
    if qualification_evidence.get("accepted_by_human") is not False:
        raise TrainingExecutionError(
            "automated qualification must explicitly prove no human acceptance"
        )
    active_torch = torch_module
    if active_torch is None and runtime_evidence is None:
        try:
            import torch as active_torch
        except ImportError as error:
            raise TrainingExecutionError("PyTorch is unavailable") from error
    actual_runtime = (
        dict(runtime_evidence)
        if runtime_evidence is not None
        else verify_runpod_runtime(plan, torch_module=active_torch)
    )
    if not actual_runtime:
        raise TrainingExecutionError("runtime evidence is empty")
    try:
        if cloud_environment_evidence is not None:
            cloud_evidence = validate_cloud_environment(cloud_environment_evidence)
            cloud_evidence_record = {
                "path": None,
                "sha256": hashlib.sha256(
                    canonical_json_bytes(cloud_evidence)
                ).hexdigest(),
                "test_seam": True,
                "manifest": cloud_evidence,
            }
        else:
            if cloud_path is None:
                raise TrainingExecutionError(
                    "production training requires a cloud-environment manifest"
                )
            cloud_evidence = load_cloud_environment(cloud_path)
            cloud_evidence_record = {
                "path": str(cloud_path),
                "sha256": sha256_file(cloud_path),
                "test_seam": False,
                "manifest": cloud_evidence,
            }
    except CloudEnvironmentError as error:
        raise TrainingExecutionError(str(error)) from error
    expected_runtime = cloud_evidence["runtime"]
    for key in ("python", "torch", "torchvision", "ultralytics", "cuda_runtime"):
        if actual_runtime.get(key) != expected_runtime[key]:
            raise TrainingExecutionError(
                f"live runtime {key!r} differs from cloud-environment evidence"
            )
    if actual_runtime.get("gpu_name") != cloud_evidence["gpu"]["name"]:
        raise TrainingExecutionError(
            "live GPU identity differs from cloud-environment evidence"
        )
    if actual_runtime.get("gpu_total_memory_bytes") != cloud_evidence["gpu"]["memory_bytes"]:
        raise TrainingExecutionError(
            "live GPU memory differs from cloud-environment evidence"
        )
    source_evidence = (
        dict(source_identity)
        if source_identity is not None
        else _source_identity(Path(repository_root).resolve(strict=False))
    )
    if source_evidence.get("clean") is not True:
        raise TrainingExecutionError("training source identity must prove a clean checkout")
    source_commit = source_evidence.get("commit")
    if (
        type(source_commit) is not str
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit.lower())
    ):
        raise TrainingExecutionError("training source identity has an invalid commit")
    source_evidence["commit"] = source_commit.lower()

    training_plan_sha256 = hashlib.sha256(
        canonical_json_bytes(plan.to_dict())
    ).hexdigest()
    resolved_requested_batch: int | None = None
    batch_evidence_record: dict[str, Any] | None = None
    if candidate.stage == "smoke":
        if batch_decision is not None or batch_decision_evidence is not None:
            raise TrainingExecutionError("the smoke run must not consume a batch decision")
    else:
        try:
            if batch_decision_evidence is not None:
                batch_evidence = validate_batch_decision(
                    batch_decision_evidence,
                    workspace_root=workspace,
                    repository_root=repository_root,
                )
                batch_evidence_record = {
                    "path": None,
                    "sha256": hashlib.sha256(
                        canonical_json_bytes(batch_evidence)
                    ).hexdigest(),
                    "test_seam": True,
                    "decision": batch_evidence,
                }
            else:
                if batch_decision is None:
                    raise TrainingExecutionError(
                        "a full candidate requires the measured smoke batch-decision artifact"
                    )
                decision_path = require_path_within_workspace(batch_decision, workspace)
                batch_evidence = load_batch_decision(
                    decision_path,
                    workspace_root=workspace,
                    repository_root=repository_root,
                )
                batch_evidence_record = {
                    "path": str(decision_path),
                    "sha256": sha256_file(decision_path),
                    "test_seam": False,
                    "decision": batch_evidence,
                }
            expected_lineage = {
                "training_plan_sha256": training_plan_sha256,
                "source_commit": source_evidence["commit"],
                "dataset_yaml_sha256": sha256_file(data_path),
                "dataset_audit_sha256": audit_evidence["report_sha256"],
                "base_checkpoint_sha256": actual_base_hash,
            }
            if batch_evidence["lineage"] != expected_lineage:
                raise TrainingExecutionError(
                    "batch-decision lineage differs from this full training run"
                )
            resolved_requested_batch = authorized_batch(batch_evidence, candidate.name)
        except (ArtifactIOError, BatchDecisionError) as error:
            raise TrainingExecutionError(str(error)) from error
    staged_weights = _stage_verified_checkpoint(
        weights_path,
        run_path=run_path,
        expected_sha256=actual_base_hash,
    )

    arguments = frozen_train_arguments(
        candidate,
        dataset_yaml=data_path,
        run_directory=run_path,
        requested_batch=resolved_requested_batch,
    )
    if yolo_factory is None:
        try:
            from ultralytics import YOLO
        except ImportError as error:
            raise TrainingExecutionError("Ultralytics is unavailable") from error
        yolo_factory = YOLO

    if active_torch is not None:
        try:
            active_torch.cuda.reset_peak_memory_stats(0)
        except (AttributeError, TypeError, ValueError) as error:
            raise TrainingExecutionError(f"cannot reset CUDA peak metrics: {error}") from error

    started_at = _utc_now()
    started = monotonic()
    model = yolo_factory(str(staged_weights))
    model.train(**arguments)
    duration_seconds = monotonic() - started

    trainer = getattr(model, "trainer", None)
    actual_save_dir = Path(getattr(trainer, "save_dir", run_path)).resolve(strict=False)
    if actual_save_dir != run_path:
        raise TrainingExecutionError(
            f"Ultralytics wrote to unexpected run directory: {actual_save_dir}"
        )
    required_files = {
        "args_yaml": run_path / "args.yaml",
        "results_csv": run_path / "results.csv",
        "best_pt": run_path / "weights" / "best.pt",
        "last_pt": run_path / "weights" / "last.pt",
    }
    for label, path in required_files.items():
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise TrainingExecutionError(f"training artifact {label} is missing or empty: {path}")

    effective_arguments = _verify_effective_arguments(
        required_files["args_yaml"],
        arguments,
        staged_model=staged_weights,
        loader=effective_arguments_loader,
    )
    resolved_batch_size = getattr(trainer, "batch_size", None)
    if type(resolved_batch_size) is not int or resolved_batch_size <= 0:
        raise TrainingExecutionError("Ultralytics did not expose a positive resolved batch size")
    if candidate.stage == "full" and resolved_batch_size != arguments["batch"]:
        raise TrainingExecutionError(
            "Ultralytics changed the frozen full-run batch, likely after an OOM; run rejected"
        )
    metrics = _read_final_metrics(required_files["results_csv"], candidate.epochs)
    peak = _peak_cuda_bytes(active_torch)
    smoke_gate: dict[str, Any] | None = None
    if candidate.stage == "smoke":
        recall = metrics["final"]["metrics/recall(B)"]
        gpu_total = actual_runtime.get("gpu_total_memory_bytes")
        headroom_passed = (
            type(gpu_total) is int
            and gpu_total > 0
            and type(peak["reserved"]) is int
            and peak["reserved"] <= int(gpu_total * 0.90)
        )
        smoke_gate = {
            "passed": (
                recall > 0
                and peak["allocated"] is not None
                and headroom_passed
                and metrics["losses_all_epochs_finite"] is True
            ),
            "recall_is_positive": recall > 0,
            "cuda_peak_recorded": peak["allocated"] is not None,
            "vram_headroom_at_least_10_percent": headroom_passed,
            "all_epoch_losses_finite": metrics["losses_all_epochs_finite"],
        }

    post_training_audit = verify_visdrone_audit(
        audit_path,
        dataset_yaml=data_path,
    )
    pre_integrity_hash = audit_evidence["authoritative_integrity_sha256"]
    post_integrity_hash = post_training_audit["authoritative_integrity_sha256"]
    if pre_integrity_hash != post_integrity_hash:
        raise TrainingExecutionError(
            "dataset integrity changed between preflight and post-training verification"
        )
    for field in (
        "report_sha256",
        "montage_sha256",
        "dataset_yaml_sha256",
        "sample_set_sha256",
    ):
        if audit_evidence[field] != post_training_audit[field]:
            raise TrainingExecutionError(
                f"dataset {field} changed between preflight and post-training verification"
            )
    try:
        post_training_qualification = validate_automated_dataset_qualification(
            qualification_path,
            expected_audit_path=post_training_audit["report_path"],
            expected_audit_sha256=post_training_audit["report_sha256"],
            expected_dataset_yaml_path=data_path,
            expected_dataset_yaml_sha256=post_training_audit[
                "dataset_yaml_sha256"
            ],
            expected_montage_path=post_training_audit["montage_path"],
            expected_montage_sha256=post_training_audit["montage_sha256"],
            expected_splits=split_evidence,
            workspace_root=workspace,
            repository_root=repository_root,
        )
    except DatasetQualificationError as error:
        raise TrainingExecutionError(str(error)) from error
    if post_training_qualification != qualification_evidence:
        raise TrainingExecutionError(
            "dataset qualification changed during training"
        )

    audit_report_evidence = {
        **audit_evidence,
        "pre_training_verified_at_utc": audit_evidence["verified_at_utc"],
        "pre_training_authoritative_integrity_sha256": pre_integrity_hash,
        "post_training_authoritative_integrity_sha256": post_integrity_hash,
        "post_training_verified_at_utc": post_training_audit["verified_at_utc"],
        "integrity_unchanged": True,
    }
    finished_at = _utc_now()

    report = {
        "schema": TRAINING_RUN_SCHEMA,
        "status": "completed",
        "accepted_by_human": False,
        "model_id": plan.model_id,
        "training_plan_sha256": training_plan_sha256,
        "source": source_evidence,
        "class_map": {"0": "person_candidate"},
        "candidate": candidate.to_dict(),
        "dataset": {
            "yaml_path": str(data_path),
            "yaml_sha256": sha256_file(data_path),
            "audit": audit_report_evidence,
            "automated_qualification": qualification_evidence,
        },
        "base_checkpoint": {
            "intake_path": str(weights_path),
            "staged_path": str(staged_weights),
            "sha256": actual_base_hash,
            "trusted_intake": trusted_base,
        },
        "runtime": actual_runtime,
        "cloud_environment": cloud_evidence_record,
        "batch_decision": batch_evidence_record,
        "arguments": arguments,
        "effective_arguments": effective_arguments,
        "resolved_batch_size": resolved_batch_size,
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "duration_seconds": duration_seconds,
        "cuda_peak_bytes": peak,
        "metrics": metrics,
        "smoke_gate": smoke_gate,
        "artifacts": {
            label: {"path": str(path), "sha256": sha256_file(path)}
            for label, path in required_files.items()
        },
    }
    report_path = run_path / "veriswarm_training_run.json"
    try:
        return atomic_create_json(report_path, report)
    except ArtifactIOError as error:
        raise TrainingExecutionError(str(error)) from error
