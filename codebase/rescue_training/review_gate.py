"""Create-once automated dataset qualification bound to audited artifacts.

This gate deliberately contains no human-review concept. A qualification is
valid only when it is bound to the exact dataset YAML, complete rehashed
train/validation sample sets, deterministic rendered audit, and the audit
report that proved the structural, label, corruption, and duplicate checks.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
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
from .visdrone import VisDroneConversionError, validate_visdrone_conversion_report


DATASET_QUALIFICATION_SCHEMA = "veriswarm.rescue.dataset_qualification.v3"
QUALIFICATION_MODE = "fully_automated"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_SPLIT_IMAGES = {"train": 6471, "val": 548}
REQUIRED_AUTOMATED_CHECKS: dict[str, bool | int] = {
    "canonical_structure_and_counts": True,
    "image_label_pairing_complete": True,
    "label_syntax_and_bounds_valid": True,
    "corrupt_or_unreadable_samples": 0,
    "cross_split_duplicate_groups": 0,
    "sample_sets_rehashed_before_training": True,
    "rendered_sample_artifact_hash_bound": True,
    "registered_archive_lineage_revalidated": True,
}
_TOP_LEVEL_FIELDS = {
    "schema",
    "qualification_mode",
    "accepted_by_human",
    "decision",
    "audit",
    "dataset",
    "source_conversion",
    "rendered_audit",
    "checks",
    "qualified_at_utc",
}


class DatasetQualificationError(ValueError):
    """Automated qualification evidence is missing, malformed, or tampered."""


def _parse_strict_object(path: Path, artifact_name: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DatasetQualificationError(
            f"{artifact_name} must be one regular file: {path}"
        )

    def reject_constant(value: str) -> None:
        raise DatasetQualificationError(
            f"non-finite JSON constant is forbidden: {value}"
        )

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DatasetQualificationError(
                    f"duplicate JSON key is forbidden: {key!r}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=unique,
        )
    except DatasetQualificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatasetQualificationError(
            f"cannot read {artifact_name}: {error}"
        ) from error
    if type(value) is not dict:
        raise DatasetQualificationError(f"{artifact_name} must contain one object")
    return value


def _load_strict_object(path: Path) -> dict[str, Any]:
    value = _parse_strict_object(path, "dataset qualification")
    if set(value) != _TOP_LEVEL_FIELDS:
        raise DatasetQualificationError(
            "dataset-qualification fields do not match the frozen v3 schema"
        )
    return value


def _sha256(value: Any, field: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        raise DatasetQualificationError(
            f"{field} must be a lowercase SHA-256 string"
        )
    return value


def _absolute_path(value: Any, field: str) -> Path:
    if type(value) is not str or not value:
        raise DatasetQualificationError(f"{field} must be an absolute path")
    source = Path(value)
    if not source.is_absolute():
        raise DatasetQualificationError(f"{field} must be an absolute path")
    return source.resolve(strict=False)


def _timestamp(value: Any) -> str:
    if type(value) is not str:
        raise DatasetQualificationError(
            "qualified_at_utc must be an ISO-8601 UTC timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DatasetQualificationError(
            "qualified_at_utc must be an ISO-8601 UTC timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise DatasetQualificationError("qualified_at_utc must use UTC")
    return value


def _split_evidence(value: Any) -> dict[str, dict[str, Any]]:
    if type(value) is not dict or set(value) != set(EXPECTED_SPLIT_IMAGES):
        raise DatasetQualificationError(
            "dataset splits must contain exactly the official train and val splits"
        )
    normalized: dict[str, dict[str, Any]] = {}
    for split, expected_images in EXPECTED_SPLIT_IMAGES.items():
        item = value[split]
        if type(item) is not dict or set(item) != {"images", "sample_set_sha256"}:
            raise DatasetQualificationError(
                f"dataset split {split!r} does not match the frozen schema"
            )
        if type(item["images"]) is not int or item["images"] != expected_images:
            raise DatasetQualificationError(
                f"dataset split {split!r} must contain exactly {expected_images} images"
            )
        normalized[split] = {
            "images": expected_images,
            "sample_set_sha256": _sha256(
                item["sample_set_sha256"],
                f"dataset.splits.{split}.sample_set_sha256",
            ),
        }
    return normalized


def _regular_file_hash(path: Path, field: str) -> str:
    try:
        return sha256_file(path)
    except ArtifactIOError as error:
        raise DatasetQualificationError(f"{field}: {error}") from error


def _exact_integer(value: Any, expected: int, field: str) -> None:
    if type(value) is not int or value != expected:
        raise DatasetQualificationError(f"{field} must be exactly {expected}")


def _verify_audit_claims(payload: Mapping[str, Any]) -> None:
    """Prove the frozen check outcomes before a receipt can be created."""

    audit = payload["audit"]
    dataset = payload["dataset"]
    rendered = payload["rendered_audit"]
    report_path = Path(audit["report_path"])
    report = _parse_strict_object(report_path, "dataset audit report")
    if report.get("schema") != "veriswarm.dataset_audit.v1":
        raise DatasetQualificationError("unsupported dataset-audit schema")
    if report.get("passed") is not True:
        raise DatasetQualificationError("dataset audit did not pass")
    if report.get("class_map") != {"0": "person_candidate"}:
        raise DatasetQualificationError(
            "dataset audit class_map must be exactly {'0': 'person_candidate'}"
        )

    report_splits = report.get("splits")
    if type(report_splits) is not dict or set(report_splits) != set(
        EXPECTED_SPLIT_IMAGES
    ):
        raise DatasetQualificationError(
            "dataset audit must contain exactly train and val splits"
        )
    for split, expected_images in EXPECTED_SPLIT_IMAGES.items():
        report_split = report_splits[split]
        if type(report_split) is not dict:
            raise DatasetQualificationError(f"dataset audit {split} split is invalid")
        for field in ("images", "labels", "paired", "valid_samples"):
            _exact_integer(
                report_split.get(field),
                expected_images,
                f"dataset audit splits.{split}.{field}",
            )
        for field in ("missing_labels", "orphan_labels"):
            _exact_integer(
                report_split.get(field),
                0,
                f"dataset audit splits.{split}.{field}",
            )
        expected_sample_hash = dataset["splits"][split]["sample_set_sha256"]
        if report_split.get("sample_set_sha256") != expected_sample_hash:
            raise DatasetQualificationError(
                f"dataset audit {split} sample-set SHA-256 mismatch"
            )
        dataset_root = Path(dataset["yaml_path"]).parent
        if Path(report_split.get("image_root", "")).resolve(strict=False) != (
            dataset_root / "images" / split
        ).resolve(strict=False):
            raise DatasetQualificationError(
                f"dataset audit {split} image root differs from the converted dataset"
            )
        if Path(report_split.get("label_root", "")).resolve(strict=False) != (
            dataset_root / "labels" / split
        ).resolve(strict=False):
            raise DatasetQualificationError(
                f"dataset audit {split} label root differs from the converted dataset"
            )

    totals = report.get("totals")
    if type(totals) is not dict:
        raise DatasetQualificationError("dataset audit totals are missing")
    _exact_integer(totals.get("errors"), 0, "dataset audit totals.errors")
    _exact_integer(
        totals.get("cross_split_leakage_groups"),
        0,
        "dataset audit totals.cross_split_leakage_groups",
    )
    _exact_integer(
        totals.get("valid_samples"),
        sum(EXPECTED_SPLIT_IMAGES.values()),
        "dataset audit totals.valid_samples",
    )
    boxes = totals.get("boxes")
    if type(boxes) is not int or boxes <= 0:
        raise DatasetQualificationError("dataset audit must contain person boxes")
    if totals.get("class_box_counts") != {"0": boxes}:
        raise DatasetQualificationError(
            "dataset audit class box counts must contain only person_candidate"
        )
    if report.get("errors") != []:
        raise DatasetQualificationError("dataset audit error details are not empty")
    if report.get("cross_split_leakage") != []:
        raise DatasetQualificationError(
            "dataset audit cross-split duplicate details are not empty"
        )

    report_montage = report.get("montage")
    if type(report_montage) is not dict:
        raise DatasetQualificationError("dataset audit rendered montage is missing")
    _exact_integer(
        report_montage.get("requested_samples"),
        100,
        "dataset audit montage.requested_samples",
    )
    _exact_integer(
        report_montage.get("rendered_samples"),
        100,
        "dataset audit montage.rendered_samples",
    )
    relative_montage = report_montage.get("path")
    if (
        type(relative_montage) is not str
        or not relative_montage
        or Path(relative_montage).is_absolute()
    ):
        raise DatasetQualificationError(
            "dataset audit montage.path must be a relative file path"
        )
    actual_montage_path = (report_path.parent / relative_montage).resolve(
        strict=False
    )
    if actual_montage_path != Path(rendered["montage_path"]):
        raise DatasetQualificationError("dataset audit names a different montage")
    if report_montage.get("sha256") != rendered["montage_sha256"]:
        raise DatasetQualificationError("dataset audit montage SHA-256 mismatch")


def _expected_payload(
    *,
    audit_report_path: str | os.PathLike[str],
    audit_report_sha256: str,
    dataset_yaml_path: str | os.PathLike[str],
    dataset_yaml_sha256: str,
    montage_path: str | os.PathLike[str],
    montage_sha256: str,
    splits: Mapping[str, Mapping[str, Any]],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    qualified_at_utc: str,
) -> dict[str, Any]:
    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        audit_path = require_path_within_workspace(
            _absolute_path(str(audit_report_path), "audit.report_path"), workspace
        )
        yaml_path = require_path_within_workspace(
            _absolute_path(str(dataset_yaml_path), "dataset.yaml_path"), workspace
        )
        rendered_path = require_path_within_workspace(
            _absolute_path(str(montage_path), "rendered_audit.montage_path"), workspace
        )
    except ArtifactIOError as error:
        raise DatasetQualificationError(str(error)) from error
    audit_hash = _sha256(audit_report_sha256, "audit.report_sha256")
    yaml_hash = _sha256(dataset_yaml_sha256, "dataset.yaml_sha256")
    rendered_hash = _sha256(montage_sha256, "rendered_audit.montage_sha256")
    normalized_splits = _split_evidence(dict(splits))

    for source, expected, field in (
        (audit_path, audit_hash, "audit report"),
        (yaml_path, yaml_hash, "dataset YAML"),
        (rendered_path, rendered_hash, "rendered audit montage"),
    ):
        if _regular_file_hash(source, field) != expected:
            raise DatasetQualificationError(f"{field} SHA-256 mismatch")

    conversion_report_path = yaml_path.parent / "conversion_report.json"
    try:
        source_conversion = validate_visdrone_conversion_report(
            conversion_report_path,
            workspace_root=workspace_root,
            repository_root=repository_root,
        )
    except VisDroneConversionError as error:
        raise DatasetQualificationError(
            f"registered VisDrone source lineage is invalid: {error}"
        ) from error
    if (
        source_conversion.get("dataset_yaml", {}).get("path") != str(yaml_path)
        or source_conversion.get("dataset_yaml", {}).get("sha256") != yaml_hash
    ):
        raise DatasetQualificationError(
            "source conversion names a different dataset YAML"
        )

    return {
        "schema": DATASET_QUALIFICATION_SCHEMA,
        "qualification_mode": QUALIFICATION_MODE,
        "accepted_by_human": False,
        "decision": "qualified",
        "audit": {
            "report_path": str(audit_path),
            "report_sha256": audit_hash,
        },
        "dataset": {
            "yaml_path": str(yaml_path),
            "yaml_sha256": yaml_hash,
            "class_map": {"0": "person_candidate"},
            "splits": normalized_splits,
        },
        "source_conversion": source_conversion,
        "rendered_audit": {
            "montage_path": str(rendered_path),
            "montage_sha256": rendered_hash,
            "rendered_samples": 100,
        },
        "checks": dict(REQUIRED_AUTOMATED_CHECKS),
        "qualified_at_utc": _timestamp(qualified_at_utc),
    }


def create_automated_dataset_qualification(
    target_path: str | os.PathLike[str],
    *,
    audit_report_path: str | os.PathLike[str],
    audit_report_sha256: str,
    dataset_yaml_path: str | os.PathLike[str],
    dataset_yaml_sha256: str,
    montage_path: str | os.PathLike[str],
    montage_sha256: str,
    splits: Mapping[str, Mapping[str, Any]],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
    qualified_at_utc: str | None = None,
) -> Path:
    """Create one immutable-by-policy automated qualification receipt.

    The caller supplies only artifact bindings already produced by the
    deterministic audit. Check outcomes are frozen by this module and cannot
    be changed into caller-authored pass flags.
    """

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        target = require_path_within_workspace(target_path, workspace)
    except ArtifactIOError as error:
        raise DatasetQualificationError(str(error)) from error
    timestamp = qualified_at_utc or datetime.now(timezone.utc).isoformat()
    payload = _expected_payload(
        audit_report_path=audit_report_path,
        audit_report_sha256=audit_report_sha256,
        dataset_yaml_path=dataset_yaml_path,
        dataset_yaml_sha256=dataset_yaml_sha256,
        montage_path=montage_path,
        montage_sha256=montage_sha256,
        splits=splits,
        workspace_root=workspace_root,
        repository_root=repository_root,
        qualified_at_utc=timestamp,
    )
    _verify_audit_claims(payload)
    try:
        return atomic_create_json(target.resolve(strict=False), payload)
    except ArtifactIOError as error:
        raise DatasetQualificationError(str(error)) from error


def validate_automated_dataset_qualification(
    qualification_path: str | os.PathLike[str],
    *,
    expected_audit_path: str | os.PathLike[str],
    expected_audit_sha256: str,
    expected_dataset_yaml_path: str | os.PathLike[str],
    expected_dataset_yaml_sha256: str,
    expected_montage_path: str | os.PathLike[str],
    expected_montage_sha256: str,
    expected_splits: Mapping[str, Mapping[str, Any]],
    workspace_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Validate and rehash every artifact named by an automated receipt."""

    try:
        workspace = require_external_workspace(workspace_root, repository_root)
        path = require_path_within_workspace(qualification_path, workspace)
    except ArtifactIOError as error:
        raise DatasetQualificationError(str(error)) from error
    value = _load_strict_object(path)
    if value["schema"] != DATASET_QUALIFICATION_SCHEMA:
        raise DatasetQualificationError("unsupported dataset-qualification schema")

    expected = _expected_payload(
        audit_report_path=expected_audit_path,
        audit_report_sha256=expected_audit_sha256,
        dataset_yaml_path=expected_dataset_yaml_path,
        dataset_yaml_sha256=expected_dataset_yaml_sha256,
        montage_path=expected_montage_path,
        montage_sha256=expected_montage_sha256,
        splits=expected_splits,
        workspace_root=workspace_root,
        repository_root=repository_root,
        qualified_at_utc=value.get("qualified_at_utc"),
    )
    if value != expected:
        raise DatasetQualificationError(
            "dataset qualification does not match the verified automated evidence"
        )
    return {
        **value,
        "qualification_path": str(path),
        "qualification_sha256": _regular_file_hash(path, "dataset qualification"),
    }


__all__ = [
    "DATASET_QUALIFICATION_SCHEMA",
    "EXPECTED_SPLIT_IMAGES",
    "QUALIFICATION_MODE",
    "REQUIRED_AUTOMATED_CHECKS",
    "DatasetQualificationError",
    "create_automated_dataset_qualification",
    "validate_automated_dataset_qualification",
]
